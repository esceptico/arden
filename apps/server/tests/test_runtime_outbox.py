from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio

import arden.database as database
from arden.agent import Usage
from arden.events.internal import RunCompleted, RunCompletionRejected, RunFailed
from arden.outbox import (
    OUTBOX_AUTOMATION_SETTLED,
    OUTBOX_RUN_COMPLETED,
    OUTBOX_RUN_FAILED,
    OUTBOX_WIKI_PROJECTION_REQUESTED,
    AutomationSettled,
    OutboxEvent,
    OutboxStore,
    automation_settled_payload,
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


@pytest_asyncio.fixture
async def persisted_outbox_store(tmp_path: Path):
    conn = await database.connect(tmp_path / "outbox.db")
    store = OutboxStore(conn)
    await store.init_schema()
    yield store
    await conn.close()


class _Scheduler:
    def __init__(self):
        self.completed = []
        self.failed = []
        self.preserved_next_runs = []

    async def handle_run_completed(self, event, *, preserve_next_run=False):
        self.completed.append(event)
        self.preserved_next_runs.append(preserve_next_run)

    async def handle_run_failed(self, event):
        self.failed.append(event)


def _runtime_outbox(
    on_area_run=None,
    on_area_run_failed=None,
    on_automation_settled=None,
    on_wiki_projection=None,
):
    outbox_store = _OutboxStore()
    scheduler = _Scheduler()
    runtime_outbox = RuntimeOutbox(
        outbox_store=outbox_store,
        scheduler=scheduler,
        on_area_run=on_area_run,
        on_area_run_failed=on_area_run_failed,
        on_automation_settled=on_automation_settled,
        on_wiki_projection=on_wiki_projection,
    )
    return runtime_outbox, outbox_store, scheduler


@pytest.mark.asyncio
async def test_runtime_outbox_routes_run_completed_to_scheduler():
    runtime_outbox, _, scheduler = _runtime_outbox()
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
async def test_runtime_outbox_routes_run_completed_to_area_hook_before_scheduler():
    seen: list[str] = []

    async def on_area_run(run_completed):
        seen.append(f"area:{run_completed.run_id}")
        return True

    runtime_outbox, _, scheduler = _runtime_outbox(on_area_run=on_area_run)
    payload = run_completed_payload(
        RunCompleted(
            run_id="run-1",
            session_id="sess-1",
            messages=(),
            usage=Usage(),
            result="done",
        )
    )

    await runtime_outbox._on_run_completed(_event(OUTBOX_RUN_COMPLETED, payload))

    assert seen == ["area:run-1"]
    assert [event.run_id for event in scheduler.completed] == ["run-1"]
    assert scheduler.preserved_next_runs == [True]


@pytest.mark.asyncio
async def test_runtime_outbox_retries_the_whole_completion_when_area_hook_fails():
    async def fail_area_run(_run_completed):
        raise RuntimeError("area state unavailable")

    runtime_outbox, _, scheduler = _runtime_outbox(on_area_run=fail_area_run)
    payload = run_completed_payload(
        RunCompleted(
            run_id="run-1",
            session_id="sess-1",
            messages=(),
            usage=Usage(),
            result="done",
        )
    )

    with pytest.raises(RuntimeError, match="area state unavailable"):
        await runtime_outbox._on_run_completed(_event(OUTBOX_RUN_COMPLETED, payload))

    assert scheduler.completed == []


@pytest.mark.asyncio
async def test_runtime_outbox_fails_run_when_area_report_is_rejected():
    async def reject_area_run(_run_completed):
        raise RunCompletionRejected("AreaWorkReportError: invalid work change")

    runtime_outbox, _, scheduler = _runtime_outbox(on_area_run=reject_area_run)
    completed = RunCompleted(
        run_id="run-1",
        session_id="sess-1",
        messages=(),
        usage=Usage(),
        result="done",
        automation_task_id="area:health",
    )

    await runtime_outbox._on_run_completed(_event(OUTBOX_RUN_COMPLETED, run_completed_payload(completed)))

    assert scheduler.completed == []
    assert scheduler.failed == [
        RunFailed(
            run_id="run-1",
            session_id="sess-1",
            error="AreaWorkReportError: invalid work change",
            automation_task_id="area:health",
        )
    ]


@pytest.mark.asyncio
async def test_runtime_outbox_routes_run_failed_to_scheduler():
    runtime_outbox, _, scheduler = _runtime_outbox()
    failed = RunFailed(run_id="run-1", session_id="sess-1", error="provider error")

    await runtime_outbox._on_run_failed(_event(OUTBOX_RUN_FAILED, run_failed_payload(failed)))

    assert scheduler.failed == [failed]


@pytest.mark.asyncio
async def test_runtime_outbox_completes_run_when_failed_chat_already_submitted_area_report():
    seen: list[str] = []

    async def project_accepted_report(failed: RunFailed) -> bool:
        seen.append(failed.run_id)
        return True

    runtime_outbox, _, scheduler = _runtime_outbox(on_area_run_failed=project_accepted_report)
    failed = RunFailed(
        run_id="run-1",
        session_id="area-session",
        error="chat finalization failed",
        automation_task_id="area:health",
    )

    await runtime_outbox.handle_run_failed(failed)

    assert seen == ["run-1"]
    assert scheduler.failed == []
    assert len(scheduler.completed) == 1
    assert scheduler.completed[0].run_id == "run-1"
    assert scheduler.completed[0].automation_task_id == "area:health"
    assert scheduler.preserved_next_runs == [True]


@pytest.mark.asyncio
async def test_runtime_outbox_fails_run_when_failed_chat_has_no_area_report():
    async def no_report(_failed: RunFailed) -> bool:
        return False

    runtime_outbox, _, scheduler = _runtime_outbox(on_area_run_failed=no_report)
    failed = RunFailed(
        run_id="run-1",
        session_id="area-session",
        error="provider error",
        automation_task_id="area:health",
    )

    await runtime_outbox.handle_run_failed(failed)

    assert scheduler.completed == []
    assert scheduler.failed == [failed]


@pytest.mark.asyncio
async def test_runtime_outbox_routes_automation_settled_to_callback():
    seen: list[AutomationSettled] = []

    async def on_automation_settled(event):
        seen.append(event)

    runtime_outbox, _, _ = _runtime_outbox(on_automation_settled=on_automation_settled)
    settled = AutomationSettled(automation_run_id=42, task_id="daily-summary", success=True)

    await runtime_outbox._on_automation_settled(_event(OUTBOX_AUTOMATION_SETTLED, automation_settled_payload(settled)))

    assert seen == [settled]


@pytest.mark.asyncio
async def test_runtime_outbox_routes_durable_wiki_projection_request():
    calls = 0

    async def project() -> None:
        nonlocal calls
        calls += 1

    runtime_outbox, _, _ = _runtime_outbox(on_wiki_projection=project)

    await runtime_outbox._on_wiki_projection(_event(OUTBOX_WIKI_PROJECTION_REQUESTED, {"revision": "a" * 64}))

    assert calls == 1


@pytest.mark.asyncio
async def test_wiki_projection_request_is_persisted_once_per_revision(persisted_outbox_store: OutboxStore):
    revision = "a" * 64

    assert await persisted_outbox_store.enqueue_wiki_projection(revision) is True
    assert await persisted_outbox_store.enqueue_wiki_projection(revision) is False

    events = await persisted_outbox_store.claim_batch(worker_id="test-worker", limit=10)
    assert len(events) == 1
    assert events[0].event_type == OUTBOX_WIKI_PROJECTION_REQUESTED
    assert events[0].aggregate_type == "wiki"
    assert events[0].aggregate_id == revision
    assert events[0].payload == {"revision": revision}


@pytest.mark.asyncio
async def test_wiki_projection_does_not_block_run_completion(persisted_outbox_store: OutboxStore):
    projection_calls = 0

    async def project() -> None:
        nonlocal projection_calls
        projection_calls += 1

    scheduler = _Scheduler()
    runtime_outbox = RuntimeOutbox(
        outbox_store=persisted_outbox_store,
        scheduler=scheduler,
        on_wiki_projection=project,
    )
    await persisted_outbox_store.enqueue_wiki_projection("a" * 64)
    await persisted_outbox_store.enqueue_run_completed(
        RunCompleted(
            run_id="run-1",
            session_id="session-1",
            messages=(),
            usage=Usage(),
            result="done",
        )
    )

    assert await runtime_outbox.worker.process_once() is True
    assert [event.run_id for event in scheduler.completed] == ["run-1"]
    assert projection_calls == 0

    assert await runtime_outbox.wiki_worker.process_once() is True
    assert projection_calls == 1


@pytest.mark.asyncio
async def test_runtime_outbox_propagates_automation_settled_callback_error():
    async def fail(_event):
        raise RuntimeError("observer unavailable")

    runtime_outbox, _, _ = _runtime_outbox(on_automation_settled=fail)
    settled = AutomationSettled(automation_run_id=42, task_id="daily-summary", success=False)

    with pytest.raises(RuntimeError, match="observer unavailable"):
        await runtime_outbox._on_automation_settled(
            _event(OUTBOX_AUTOMATION_SETTLED, automation_settled_payload(settled))
        )


@pytest.mark.asyncio
async def test_runtime_outbox_retries_automation_settled_callback(persisted_outbox_store: OutboxStore):
    attempts = 0

    async def retry_once(_event):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("observer unavailable")

    runtime_outbox = RuntimeOutbox(
        outbox_store=persisted_outbox_store,
        scheduler=_Scheduler(),
        on_automation_settled=retry_once,
    )
    runtime_outbox.worker.retry_base_seconds = 0
    runtime_outbox.worker.retry_max_seconds = 0
    await persisted_outbox_store.enqueue_automation_settled_in_transaction(
        automation_run_id=42,
        task_id="daily-summary",
        success=True,
    )
    await persisted_outbox_store.conn.commit()

    assert await runtime_outbox.worker.process_once() is True
    assert await runtime_outbox.worker.process_once() is True
    assert attempts == 2
    assert await persisted_outbox_store.claim_batch(worker_id="test-worker", limit=10) == []


@pytest.mark.asyncio
async def test_runtime_outbox_status_and_repair_controls_delegate_to_store():
    runtime_outbox, outbox_store, _ = _runtime_outbox()
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
