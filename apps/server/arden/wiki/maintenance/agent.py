"""Tool-loop bridge for the constrained Wiki Maintenance reviewer."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from arden.wiki.exceptions import GeneratedRegionConflictError, WikiAmbiguityError, WikiValidationError
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
    failure: "WikiMaintenanceReviewFailure | None" = None


@dataclass(frozen=True, slots=True)
class WikiMaintenanceReviewFailure:
    code: str
    message: str
    recovery_action: str


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
            return self._completed_state()
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
                self._completed_state()
        finally:
            if not ready.done():
                ready.cancel()
                try:
                    await ready
                except asyncio.CancelledError:
                    pass

    def _completed_state(self) -> WikiMaintenanceReviewState:
        if self._task is None or not self._task.done():
            raise RuntimeError("wiki maintenance task is not complete")
        try:
            result = self._task.result()
        except WikiValidationError as exc:
            if isinstance(exc, GeneratedRegionConflictError):
                code = "wiki_generated_region_conflict"
                recovery_action = (
                    "Stop this review. Restore the producer-owned generated region or route the page for "
                    "manual repair before starting Wiki Maintenance again."
                )
            elif isinstance(exc, WikiAmbiguityError):
                code = "wiki_identity_ambiguous"
                recovery_action = "Stop this review. Resolve the ambiguous page title or alias before starting Wiki Maintenance again."
            else:
                code = "wiki_validation_conflict"
                recovery_action = (
                    "Stop this review. Repair the reported wiki invariant before starting Wiki Maintenance again."
                )
            return WikiMaintenanceReviewState(
                report=None,
                result=None,
                failure=WikiMaintenanceReviewFailure(
                    code=code,
                    message=str(exc),
                    recovery_action=recovery_action,
                ),
            )
        return WikiMaintenanceReviewState(report=None, result=result)

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
