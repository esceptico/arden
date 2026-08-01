import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime

from arden.agent import Usage
from arden.automation.scheduler import Scheduler
from arden.events.internal import RunCompleted, RunCompletionRejected, RunFailed
from arden.logging import get_logger
from arden.outbox import (
    OUTBOX_AGENT_RUN_REQUESTED,
    OUTBOX_AUTOMATION_SETTLED,
    OUTBOX_RUN_COMPLETED,
    OUTBOX_RUN_FAILED,
    OUTBOX_WIKI_PROJECTION_REQUESTED,
    AutomationSettled,
    OutboxEvent,
    OutboxWorker,
    agent_run_requested_from_payload,
    automation_settled_from_payload,
    run_completed_from_payload,
    run_failed_from_payload,
)
from arden.outbox.store import OutboxStore

_logger = get_logger(__name__)


class RuntimeOutbox:
    def __init__(
        self,
        *,
        outbox_store: OutboxStore,
        scheduler: Scheduler,
        on_area_run: Callable[[RunCompleted], Awaitable[bool]] | None = None,
        on_area_run_failed: Callable[[RunFailed], Awaitable[bool]] | None = None,
        on_automation_settled: Callable[[AutomationSettled], Awaitable[None]] | None = None,
        on_wiki_projection: Callable[[], Awaitable[None]] | None = None,
    ):
        self.outbox_store = outbox_store
        self.scheduler = scheduler
        self._on_area_run = on_area_run
        self._on_area_run_failed = on_area_run_failed
        self._on_automation_settled_callback = on_automation_settled
        self._on_wiki_projection_callback = on_wiki_projection
        self._on_agent_run_requested_callback: Callable[[str, str], Awaitable[None]] | None = None
        self.worker = OutboxWorker(
            outbox_store,
            excluded_event_types=(OUTBOX_WIKI_PROJECTION_REQUESTED, OUTBOX_AGENT_RUN_REQUESTED),
        )
        self.wiki_worker = OutboxWorker(
            outbox_store,
            batch_size=1,
            prune_batch_size=0,
            event_types=(OUTBOX_WIKI_PROJECTION_REQUESTED,),
        )
        self.agent_worker = OutboxWorker(
            outbox_store,
            batch_size=1,
            prune_batch_size=0,
            event_types=(OUTBOX_AGENT_RUN_REQUESTED,),
        )
        self._register_handlers()

    def set_agent_run_handler(self, handler: Callable[[str, str], Awaitable[None]]) -> None:
        self._on_agent_run_requested_callback = handler

    def start(self) -> None:
        self.worker.start()
        self.wiki_worker.start()
        self.agent_worker.start()

    async def stop(self) -> None:
        await asyncio.gather(self.worker.stop(), self.wiki_worker.stop(), self.agent_worker.stop())

    def _register_handlers(self) -> None:
        self.worker.register_handler(OUTBOX_AUTOMATION_SETTLED, self._on_automation_settled)
        self.worker.register_handler(OUTBOX_RUN_COMPLETED, self._on_run_completed)
        self.worker.register_handler(OUTBOX_RUN_FAILED, self._on_run_failed)
        self.wiki_worker.register_handler(
            OUTBOX_WIKI_PROJECTION_REQUESTED,
            self._on_wiki_projection,
        )
        self.agent_worker.register_handler(
            OUTBOX_AGENT_RUN_REQUESTED,
            self._on_agent_run_requested,
        )

    async def _on_run_completed(self, event: OutboxEvent) -> None:
        run_completed = run_completed_from_payload(event.payload)
        preserve_next_run = False
        if self._on_area_run:
            try:
                preserve_next_run = await self._on_area_run(run_completed)
            except RunCompletionRejected as exc:
                await self.scheduler.handle_run_failed(
                    RunFailed(
                        run_id=run_completed.run_id,
                        session_id=run_completed.session_id,
                        error=str(exc),
                        automation_task_id=run_completed.automation_task_id,
                    )
                )
                return
        await self.scheduler.handle_run_completed(
            run_completed,
            preserve_next_run=preserve_next_run,
        )

    async def _on_run_failed(self, event: OutboxEvent) -> None:
        await self.handle_run_failed(run_failed_from_payload(event.payload))

    async def handle_run_failed(self, run_failed: RunFailed) -> None:
        if self._on_area_run_failed is not None:
            accepted_report = await self._on_area_run_failed(run_failed)
            if accepted_report:
                await self.scheduler.handle_run_completed(
                    RunCompleted(
                        run_id=run_failed.run_id,
                        session_id=run_failed.session_id,
                        messages=(),
                        usage=Usage(),
                        result="Area report accepted before chat finalization failed.",
                        automation_task_id=run_failed.automation_task_id,
                    ),
                    preserve_next_run=True,
                )
                return
        await self.scheduler.handle_run_failed(run_failed)

    async def _on_automation_settled(self, event: OutboxEvent) -> None:
        if self._on_automation_settled_callback is None:
            return
        await self._on_automation_settled_callback(automation_settled_from_payload(event.payload))

    async def _on_wiki_projection(self, _event: OutboxEvent) -> None:
        if self._on_wiki_projection_callback is None:
            raise RuntimeError("wiki projection outbox handler is unavailable")
        await self._on_wiki_projection_callback()

    async def _on_agent_run_requested(self, event: OutboxEvent) -> None:
        if self._on_agent_run_requested_callback is None:
            raise RuntimeError("agent respawn outbox handler is unavailable")
        requested = agent_run_requested_from_payload(event.payload)
        # Fire-and-forget: the outbox event completes ON DISPATCH. A respawned
        # agent that dies with another restart re-enters via the next boot's
        # enqueue pass (bounded by spawn_attempts), not via outbox retry.
        asyncio.create_task(self._dispatch_agent_respawn(requested.session_id, requested.task_id))

    async def _dispatch_agent_respawn(self, session_id: str, task_id: str) -> None:
        try:
            await self._on_agent_run_requested_callback(session_id, task_id)
        except Exception:
            _logger.exception("Background agent respawn failed", session_id=session_id, task_id=task_id)

    async def get_status(self) -> dict:
        worker_running = self.worker.is_running
        wiki_worker_running = self.wiki_worker.is_running
        agent_worker_running = self.agent_worker.is_running
        return {
            "status": "running" if worker_running and wiki_worker_running and agent_worker_running else "stopped",
            "worker": {
                "running": worker_running,
                "worker_id": self.worker.worker_id,
            },
            "wiki_worker": {
                "running": wiki_worker_running,
                "worker_id": self.wiki_worker.worker_id,
            },
            "agent_worker": {
                "running": agent_worker_running,
                "worker_id": self.agent_worker.worker_id,
            },
            "events": await self.outbox_store.get_status(),
        }

    async def get_health(self) -> dict:
        status = await self.get_status()
        events = status.get("events", {})
        by_status = events.get("by_status", {})
        return {
            "worker_running": status.get("worker", {}).get("running", False)
            and status.get("wiki_worker", {}).get("running", False)
            and status.get("agent_worker", {}).get("running", False),
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
