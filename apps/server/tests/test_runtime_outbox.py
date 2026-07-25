from datetime import UTC, datetime

import pytest

from arden.agent import Usage
from arden.events.internal import RunCompleted, RunFailed
from arden.outbox import (
    OUTBOX_RUN_COMPLETED,
    OUTBOX_RUN_FAILED,
    OutboxEvent,
    run_completed_payload,
    run_failed_payload,
)
from arden.server.runtime.outbox import RuntimeOutbox


def _event(event_type: str, payload: dict) -> OutboxEvent:
    now = datetime(2026, 4, 28, tzinfo=UTC)
    return OutboxEvent(
        id=1,
        event_type=event_type,
        payload=payload,
        idempotency_key="key",
        status="running",
        attempts=1,
        available_at=now,
        created_at=now,
        updated_at=now,
    )


class _OutboxStore:
    def __init__(self):
        self.replayed = None
        self.pruned = None

    async def get_status(self):
        return {
            "ready": 1,
            "by_status": {
                "pending": 2,
                "running": 0,
                "dead": 1,
            },
        }

    async def replay_dead(self, event_ids):
        self.replayed = event_ids
        return {"requested": event_ids, "replayed": event_ids, "missing": [], "skipped": []}

    async def prune_completed(self, *, before, limit):
        self.pruned = {"before": before, "limit": limit}
        return 7


class _AutomationStore:
    pass


class _Scheduler:
    def __init__(self):
        self.completed = []
        self.failed = []

    async def handle_run_completed(self, event):
        self.completed.append(event)

    async def handle_run_failed(self, event):
        self.failed.append(event)


class _Index:
    def __init__(self):
        self.upserts = []
        self.deletes = []
        self.cleared = []

    async def upsert(self, **kwargs):
        self.upserts.append(kwargs)

    async def delete(self, source, source_id):
        self.deletes.append((source, source_id))

    async def clear_source(self, source):
        self.cleared.append(source)


class _Indexer:
    def __init__(self):
        self.index = _Index()


def _runtime_outbox(indexer=None, chat_connector=None):
    outbox_store = _OutboxStore()
    automation_store = _AutomationStore()
    scheduler = _Scheduler()
    runtime_outbox = RuntimeOutbox(
        outbox_store=outbox_store,
        automation_store=automation_store,
        scheduler=scheduler,
        indexer=indexer,
        get_chat_connector=lambda: chat_connector,
    )
    return runtime_outbox, outbox_store, automation_store, scheduler


@pytest.mark.asyncio
async def test_runtime_outbox_routes_run_completed_to_scheduler():
    runtime_outbox, _, _, scheduler = _runtime_outbox()
    payload = run_completed_payload(
        RunCompleted(
            run_id="run-1",
            session_id="sess-1",
            messages=({"role": "user", "content": "hi"},),
            usage=Usage(),
            result="done",
        )
    )

    await runtime_outbox._on_run_completed(_event(OUTBOX_RUN_COMPLETED, payload))

    assert scheduler.completed[0].run_id == "run-1"


@pytest.mark.asyncio
async def test_runtime_outbox_routes_run_failed_to_scheduler():
    runtime_outbox, _, _, scheduler = _runtime_outbox()
    failed = RunFailed(run_id="run-1", session_id="sess-1", error="provider error")

    await runtime_outbox._on_run_failed(_event(OUTBOX_RUN_FAILED, run_failed_payload(failed)))

    assert scheduler.failed == [failed]


@pytest.mark.asyncio
async def test_runtime_outbox_status_and_repair_controls_delegate_to_store():
    runtime_outbox, outbox_store, _, _ = _runtime_outbox()
    before = datetime(2026, 4, 1, tzinfo=UTC)

    status = await runtime_outbox.get_status()
    health = await runtime_outbox.get_health()
    replay = await runtime_outbox.replay_dead_events([3, 4])
    prune = await runtime_outbox.prune_completed(before=before, limit=25)

    assert status["status"] == "stopped"
    assert status["events"]["ready"] == 1
    assert health == {"worker_running": False, "pending": 2, "ready": 1, "running": 0, "dead": 1}
    assert replay["status"] == "queued"
    assert outbox_store.replayed == [3, 4]
    assert prune["deleted"] == 7
    assert outbox_store.pruned == {"before": before, "limit": 25}
