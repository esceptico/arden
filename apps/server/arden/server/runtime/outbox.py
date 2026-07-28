from collections.abc import Awaitable, Callable
from datetime import datetime

from arden.automation.scheduler import Scheduler
from arden.automation.store import AutomationStore
from arden.outbox import (
    OUTBOX_RUN_COMPLETED,
    OUTBOX_RUN_FAILED,
    OutboxEvent,
    OutboxWorker,
    run_completed_from_payload,
    run_failed_from_payload,
)
from arden.outbox.store import OutboxStore
from arden.server.indexer import Indexer


class RuntimeOutbox:
    def __init__(
        self,
        *,
        outbox_store: OutboxStore,
        automation_store: AutomationStore,
        scheduler: Scheduler,
        indexer: Indexer | None,
        on_area_run: Callable[[object], Awaitable[None]] | None = None,
    ):
        self.worker = OutboxWorker(outbox_store)
        self.outbox_store = outbox_store
        self.automation_store = automation_store
        self.scheduler = scheduler
        self.indexer = indexer
        self._on_area_run = on_area_run
        self._register_handlers()

    def start(self) -> None:
        self.worker.start()

    async def stop(self) -> None:
        await self.worker.stop()

    def _register_handlers(self) -> None:
        self.worker.register_handler(OUTBOX_RUN_COMPLETED, self._on_run_completed)
        self.worker.register_handler(OUTBOX_RUN_FAILED, self._on_run_failed)

    async def _on_run_completed(self, event: OutboxEvent) -> None:
        run_completed = run_completed_from_payload(event.payload)
        if self._on_area_run:
            await self._on_area_run(run_completed)
        await self.scheduler.handle_run_completed(run_completed)

    async def _on_run_failed(self, event: OutboxEvent) -> None:
        await self.scheduler.handle_run_failed(run_failed_from_payload(event.payload))

    async def get_status(self) -> dict:
        worker_running = self.worker.is_running
        return {
            "status": "running" if worker_running else "stopped",
            "worker": {
                "running": worker_running,
                "worker_id": self.worker.worker_id,
            },
            "events": await self.outbox_store.get_status(),
        }

    async def get_health(self) -> dict:
        status = await self.get_status()
        events = status.get("events", {})
        by_status = events.get("by_status", {})
        return {
            "worker_running": status.get("worker", {}).get("running", False),
            "pending": by_status.get("pending", 0),
            "ready": events.get("ready", 0),
            "running": by_status.get("running", 0),
            "dead": by_status.get("dead", 0),
        }

    async def replay_dead_events(self, event_ids: list[int]) -> dict:
        result = await self.outbox_store.replay_dead(event_ids)
        return {"status": "queued" if result["replayed"] else "unchanged", **result}

    async def prune_completed(self, *, before: datetime, limit: int) -> dict:
        deleted = await self.outbox_store.prune_completed(before=before, limit=limit)
        return {
            "status": "deleted",
            "deleted": deleted,
            "before": before.isoformat(),
            "limit": limit,
        }
