"""Tool-loop bridge for the constrained Memory Maintenance reviewer."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from arden.memory.facts.maintenance.runner import (
    FactMaintenance,
    FactMaintenanceDecision,
    FactMaintenancePreparedCluster,
    FactMaintenanceResult,
)
from arden.memory.facts.maintenance.store import FactMaintenanceError


@dataclass(frozen=True, slots=True)
class FactMaintenanceReviewState:
    cluster: FactMaintenancePreparedCluster | None
    result: FactMaintenanceResult | None


class FactMaintenanceReviewService:
    """Lets one trusted agent advance a strict maintenance run cluster by cluster."""

    def __init__(self, maintenance: FactMaintenance) -> None:
        self._maintenance = maintenance
        self._task: asyncio.Task[FactMaintenanceResult] | None = None
        self._cluster: FactMaintenancePreparedCluster | None = None
        self._decision: asyncio.Future[FactMaintenanceDecision] | None = None
        self._ready = asyncio.Event()
        self._accepted: list[tuple[FactMaintenancePreparedCluster, FactMaintenanceDecision]] = []

    @property
    def accepted_decisions(self) -> int:
        return len(self._accepted)

    @property
    def done(self) -> bool:
        return self._task is not None and self._task.done()

    async def next(self) -> FactMaintenanceReviewState:
        if self._task is None:
            self._task = asyncio.create_task(self._maintenance.run())
        await self._wait_for_state()
        if self._task.done():
            return FactMaintenanceReviewState(None, self._task.result())
        return FactMaintenanceReviewState(self._cluster, None)

    async def decide(self, decision: FactMaintenanceDecision) -> FactMaintenanceReviewState:
        cluster = self._cluster
        pending = self._decision
        if cluster is None or pending is None:
            raise FactMaintenanceError("request the current maintenance cluster before deciding")
        FactMaintenance.validate_cluster_decision(cluster, decision)
        FactMaintenance.validate_accepted_decisions((*self._accepted, (cluster, decision)))
        self._cluster = None
        self._decision = None
        self._ready.clear()
        self._accepted.append((cluster, decision))
        pending.set_result(decision)
        return await self.next()

    async def require_completed(self) -> FactMaintenanceResult:
        if self._task is None or not self._task.done():
            raise FactMaintenanceError("maintenance agent exited before completing the review workflow")
        return self._task.result()

    async def aclose(self) -> None:
        if self._task is None or self._task.done():
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            return

    async def _review(self, cluster: FactMaintenancePreparedCluster) -> FactMaintenanceDecision:
        if self._decision is not None:
            raise FactMaintenanceError("maintenance reviewer received overlapping clusters")
        self._cluster = cluster
        self._decision = asyncio.get_running_loop().create_future()
        self._ready.set()
        return await self._decision

    async def _wait_for_state(self) -> None:
        if self._task is None:
            raise RuntimeError("maintenance task was not started")
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
            [Callable[[FactMaintenancePreparedCluster], Awaitable[FactMaintenanceDecision]]],
            FactMaintenance | None,
        ],
    ) -> "FactMaintenanceReviewService | None":
        service: FactMaintenanceReviewService | None = None

        async def review(cluster: FactMaintenancePreparedCluster) -> FactMaintenanceDecision:
            if service is None:
                raise RuntimeError("maintenance review service was not initialized")
            return await service._review(cluster)

        maintenance = maintenance_factory(review)
        if maintenance is None:
            return None
        service = cls(maintenance)
        return service
