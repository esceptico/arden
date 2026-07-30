"""Tool-loop bridge for the constrained Wiki Maintenance reviewer."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from arden.wiki.maintenance.runner import (
    WikiMaintenance,
    WikiMaintenanceDecision,
    WikiMaintenanceError,
    WikiMaintenancePreparedReport,
    WikiMaintenanceResult,
)


@dataclass(frozen=True, slots=True)
class WikiMaintenanceReviewState:
    report: WikiMaintenancePreparedReport | None
    result: WikiMaintenanceResult | None


class WikiMaintenanceReviewService:
    """Lets one trusted agent review one pinned wiki change at a time."""

    def __init__(self, maintenance: WikiMaintenance) -> None:
        self._maintenance = maintenance
        self._task: asyncio.Task[WikiMaintenanceResult] | None = None
        self._report: WikiMaintenancePreparedReport | None = None
        self._decision: asyncio.Future[WikiMaintenanceDecision] | None = None
        self._ready = asyncio.Event()
        self._accepted_decisions = 0

    @property
    def accepted_decisions(self) -> int:
        return self._accepted_decisions

    @property
    def done(self) -> bool:
        return self._task is not None and self._task.done()

    async def next(self) -> WikiMaintenanceReviewState:
        if self._task is None:
            self._task = asyncio.create_task(self._maintenance.run())
        await self._wait_for_state()
        if self._task.done():
            return WikiMaintenanceReviewState(None, self._task.result())
        return WikiMaintenanceReviewState(self._report, None)

    async def decide(self, decision: WikiMaintenanceDecision) -> WikiMaintenanceReviewState:
        report = self._report
        pending = self._decision
        if report is None or pending is None:
            raise WikiMaintenanceError("request the current wiki maintenance report before deciding")
        self._maintenance.validate_prepared_decision(report, decision)
        self._report = None
        self._decision = None
        self._ready.clear()
        self._accepted_decisions += 1
        pending.set_result(decision)
        return await self.next()

    async def require_completed(self) -> WikiMaintenanceResult:
        if self._task is None or not self._task.done():
            raise WikiMaintenanceError("wiki maintenance agent exited before completing the review workflow")
        return self._task.result()

    async def aclose(self) -> None:
        if self._task is None or self._task.done():
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            return

    async def _review(self, report: WikiMaintenancePreparedReport) -> WikiMaintenanceDecision:
        if self._decision is not None:
            raise WikiMaintenanceError("wiki maintenance reviewer received overlapping reports")
        self._report = report
        self._decision = asyncio.get_running_loop().create_future()
        self._ready.set()
        return await self._decision

    async def _wait_for_state(self) -> None:
        if self._task is None:
            raise RuntimeError("wiki maintenance task was not started")
        ready = asyncio.create_task(self._ready.wait())
        try:
            done, _ = await asyncio.wait({self._task, ready}, return_when=asyncio.FIRST_COMPLETED)
            if self._task in done:
                self._task.result()
        finally:
            if not ready.done():
                ready.cancel()
                try:
                    await ready
                except asyncio.CancelledError:
                    pass

    @classmethod
    def create(
        cls,
        maintenance_factory: Callable[
            [Callable[[WikiMaintenancePreparedReport], Awaitable[WikiMaintenanceDecision]]], WikiMaintenance | None
        ],
    ) -> "WikiMaintenanceReviewService | None":
        service: WikiMaintenanceReviewService | None = None

        async def review(report: WikiMaintenancePreparedReport) -> WikiMaintenanceDecision:
            if service is None:
                raise RuntimeError("wiki maintenance review service was not initialized")
            return await service._review(report)

        maintenance = maintenance_factory(review)
        if maintenance is None:
            return None
        service = cls(maintenance)
        return service
