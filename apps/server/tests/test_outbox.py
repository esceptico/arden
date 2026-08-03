import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

import arden.database as database
from arden.agent import Usage
from arden.events.internal import RunCompleted, RunFailed
from arden.outbox import (
    OUTBOX_AUTOMATION_SETTLED,
    OUTBOX_RUN_COMPLETED,
    OUTBOX_RUN_FAILED,
    OUTBOX_WIKI_PROJECTION_REQUESTED,
    AutomationSettled,
    OutboxStore,
    OutboxWorker,
    automation_settled_from_payload,
    run_completed_from_payload,
    run_failed_from_payload,
)


def _run_completed(run_id: str = "run-1") -> RunCompleted:
    return RunCompleted(
        run_id=run_id,
        session_id="session-1",
        messages=(
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "done"},
        ),
        usage=Usage(prompt_tokens=3, completion_tokens=5, cache_read_tokens=7, cache_write_tokens=11),
        result="done",
        source_refs=({"kind": "tool", "ref": "memory:1"},),
        automation_task_id="loop-1",
    )


@pytest_asyncio.fixture
async def outbox_store(tmp_path: Path):
    conn = await database.connect(tmp_path / "outbox.db")
    store = OutboxStore(conn)
    await store.init_schema()
    yield store
    await conn.close()


@pytest.mark.asyncio
async def test_enqueue_run_completed_is_idempotent_and_claims_payload(outbox_store: OutboxStore):
    event = _run_completed()

    assert await outbox_store.enqueue_run_completed(event) is True
    assert await outbox_store.enqueue_run_completed(event) is False

    claimed = await outbox_store.claim_batch(worker_id="test-worker", limit=10)

    assert len(claimed) == 1
    assert claimed[0].event_type == OUTBOX_RUN_COMPLETED
    assert claimed[0].attempts == 1
    restored = run_completed_from_payload(claimed[0].payload)
    assert restored == event

    await outbox_store.mark_completed(claimed[0].id)
    assert await outbox_store.claim_batch(worker_id="test-worker", limit=10) == []
    rows = await outbox_store.conn.execute_fetchall(
        "SELECT payload, receipt_at FROM outbox_events WHERE id = ?",
        (claimed[0].id,),
    )
    receipt = json.loads(rows[0]["payload"])
    assert receipt["receipt_version"] == 1
    assert receipt["payload_bytes"] > 0
    assert len(receipt["payload_sha256"]) == 64
    assert rows[0]["receipt_at"] is not None


@pytest.mark.asyncio
async def test_enqueue_run_failed_is_idempotent_and_claims_payload(outbox_store: OutboxStore):
    event = RunFailed(
        run_id="run-1",
        session_id="session-1",
        error="provider rejected history",
        automation_task_id="loop-1",
    )

    assert await outbox_store.enqueue_run_failed(event) is True
    assert await outbox_store.enqueue_run_failed(event) is False

    claimed = await outbox_store.claim_batch(worker_id="test-worker", limit=10)
    assert claimed[0].event_type == OUTBOX_RUN_FAILED
    assert run_failed_from_payload(claimed[0].payload) == event


@pytest.mark.asyncio
async def test_claim_batch_filters_event_types(outbox_store: OutboxStore):
    revision = "a" * 64
    await outbox_store.enqueue_wiki_projection(revision)
    await outbox_store.enqueue_run_completed(_run_completed())

    main = await outbox_store.claim_batch(
        worker_id="main-worker",
        limit=10,
        excluded_event_types=(OUTBOX_WIKI_PROJECTION_REQUESTED,),
    )
    wiki = await outbox_store.claim_batch(
        worker_id="wiki-worker",
        limit=10,
        event_types=(OUTBOX_WIKI_PROJECTION_REQUESTED,),
    )

    assert [event.event_type for event in main] == [OUTBOX_RUN_COMPLETED]
    assert [event.event_type for event in wiki] == [OUTBOX_WIKI_PROJECTION_REQUESTED]


@pytest.mark.asyncio
async def test_enqueue_automation_settled_uses_callers_transaction(outbox_store: OutboxStore):
    await outbox_store.conn.execute("BEGIN")

    assert (
        await outbox_store.enqueue_automation_settled_in_transaction(
            automation_run_id=42,
            task_id="daily-summary",
            success=True,
        )
        is True
    )
    assert (
        await outbox_store.enqueue_automation_settled_in_transaction(
            automation_run_id=42,
            task_id="daily-summary",
            success=True,
        )
        is False
    )
    assert outbox_store.conn.in_transaction

    await outbox_store.conn.commit()
    claimed = await outbox_store.claim_batch(worker_id="test-worker", limit=10)

    assert len(claimed) == 1
    assert claimed[0].event_type == OUTBOX_AUTOMATION_SETTLED
    assert claimed[0].aggregate_type == "automation_run"
    assert claimed[0].aggregate_id == "42"
    assert automation_settled_from_payload(claimed[0].payload) == AutomationSettled(
        automation_run_id=42,
        task_id="daily-summary",
        success=True,
    )


@pytest.mark.asyncio
async def test_status_reports_backlog_and_dead_letters(outbox_store: OutboxStore):
    await outbox_store.enqueue_run_completed(_run_completed())
    await outbox_store.enqueue(
        event_type="custom.scheduled",
        payload={"value": 1},
        idempotency_key="custom.scheduled:1",
        available_at=datetime.now(UTC) + timedelta(hours=1),
    )

    claimed = await outbox_store.claim_batch(worker_id="test-worker", limit=1)
    await outbox_store.mark_failed(claimed[0].id, error="bad payload", retry_at=None, dead=True)

    status = await outbox_store.get_status(now=datetime.now(UTC))

    assert status["total"] == 2
    assert status["ready"] == 0
    assert status["scheduled"] == 1
    assert status["by_status"] == {
        "pending": 1,
        "running": 0,
        "completed": 0,
        "dead": 1,
    }
    assert status["by_event_type"][OUTBOX_RUN_COMPLETED]["dead"] == 1
    assert status["by_event_type"]["custom.scheduled"]["pending"] == 1
    assert status["next_pending_available_at"] is not None
    assert status["newest_dead_updated_at"] is not None
    assert len(status["recent_dead"]) == 1

    dead = status["recent_dead"][0]
    assert dead["id"] == claimed[0].id
    assert dead["event_type"] == OUTBOX_RUN_COMPLETED
    assert dead["aggregate_type"] == "run"
    assert dead["aggregate_id"] == "run-1"
    assert dead["attempts"] == 1
    assert dead["last_error"] == "bad payload"
    assert dead["created_at"] is not None
    assert dead["updated_at"] is not None


@pytest.mark.asyncio
async def test_replay_dead_resets_event_for_processing(outbox_store: OutboxStore):
    await outbox_store.enqueue_run_completed(_run_completed())
    claimed = await outbox_store.claim_batch(worker_id="test-worker", limit=1)
    await outbox_store.mark_failed(claimed[0].id, error="bad payload", retry_at=None, dead=True)

    result = await outbox_store.replay_dead([claimed[0].id, 999])

    assert result == {
        "requested": [claimed[0].id, 999],
        "replayed": [claimed[0].id],
        "missing": [999],
        "skipped": [],
    }

    replayed = await outbox_store.claim_batch(worker_id="test-worker", limit=1)
    assert len(replayed) == 1
    assert replayed[0].id == claimed[0].id
    assert replayed[0].status == "running"
    assert replayed[0].attempts == 1
    assert replayed[0].last_error is None


@pytest.mark.asyncio
async def test_replay_dead_skips_non_dead_events(outbox_store: OutboxStore):
    await outbox_store.enqueue_run_completed(_run_completed())
    row = (await outbox_store.conn.execute_fetchall("SELECT id FROM outbox_events"))[0]

    result = await outbox_store.replay_dead([row["id"]])

    assert result == {
        "requested": [row["id"]],
        "replayed": [],
        "missing": [],
        "skipped": [{"id": row["id"], "status": "pending"}],
    }


@pytest.mark.asyncio
async def test_prune_completed_deletes_only_old_completed_rows_up_to_limit(outbox_store: OutboxStore):
    for run_id in ("old-1", "old-2", "new-1"):
        await outbox_store.enqueue_run_completed(_run_completed(run_id))
        claimed = await outbox_store.claim_batch(worker_id="test-worker", limit=1)
        await outbox_store.mark_completed(claimed[0].id)

    old = datetime.now(UTC) - timedelta(days=30)
    new = datetime.now(UTC)
    await outbox_store.conn.execute(
        "UPDATE outbox_events SET updated_at = ? WHERE aggregate_id IN ('old-1', 'old-2')",
        (old.isoformat(),),
    )
    await outbox_store.conn.execute(
        "UPDATE outbox_events SET updated_at = ? WHERE aggregate_id = 'new-1'",
        (new.isoformat(),),
    )
    await outbox_store.conn.commit()

    cutoff = datetime.now(UTC) - timedelta(days=7)

    assert await outbox_store.prune_completed(before=cutoff, limit=1) == 1

    rows = await outbox_store.conn.execute_fetchall("SELECT aggregate_id FROM outbox_events ORDER BY aggregate_id")
    assert [row["aggregate_id"] for row in rows] == ["new-1", "old-2"]

    assert await outbox_store.prune_completed(before=cutoff, limit=10) == 1
    rows = await outbox_store.conn.execute_fetchall("SELECT aggregate_id FROM outbox_events ORDER BY aggregate_id")
    assert [row["aggregate_id"] for row in rows] == ["new-1"]


@pytest.mark.asyncio
async def test_compact_completed_payloads_backfills_legacy_rows_only(outbox_store: OutboxStore):
    await outbox_store.enqueue_run_completed(_run_completed("completed"))
    completed = (await outbox_store.claim_batch(worker_id="test-worker", limit=1))[0]
    await outbox_store.conn.execute(
        "UPDATE outbox_events SET status = 'completed', locked_at = NULL, locked_by = NULL WHERE id = ?",
        (completed.id,),
    )
    await outbox_store.enqueue_run_completed(_run_completed("dead"))
    dead = (await outbox_store.claim_batch(worker_id="test-worker", limit=1))[0]
    await outbox_store.mark_failed(dead.id, error="keep me", retry_at=None, dead=True)
    await outbox_store.conn.commit()

    assert await outbox_store.compact_completed_payloads(limit=10) == 1

    rows = await outbox_store.conn.execute_fetchall(
        "SELECT aggregate_id, payload, receipt_at FROM outbox_events ORDER BY aggregate_id"
    )
    completed_row, dead_row = rows
    assert json.loads(completed_row["payload"])["receipt_version"] == 1
    assert completed_row["receipt_at"] is not None
    assert "messages" in json.loads(dead_row["payload"])
    assert dead_row["receipt_at"] is None


@pytest.mark.asyncio
async def test_worker_prunes_old_completed_rows_automatically(outbox_store: OutboxStore):
    for run_id in ("old-1", "new-1"):
        await outbox_store.enqueue_run_completed(_run_completed(run_id))
        claimed = await outbox_store.claim_batch(worker_id="test-worker", limit=1)
        await outbox_store.mark_completed(claimed[0].id)

    await outbox_store.enqueue_run_completed(_run_completed("dead-1"))
    claimed = await outbox_store.claim_batch(worker_id="test-worker", limit=1)
    await outbox_store.mark_failed(claimed[0].id, error="boom", retry_at=None, dead=True)

    old = datetime.now(UTC) - timedelta(days=40)
    new = datetime.now(UTC)
    await outbox_store.conn.execute(
        "UPDATE outbox_events SET updated_at = ? WHERE aggregate_id IN ('old-1', 'dead-1')",
        (old.isoformat(),),
    )
    await outbox_store.conn.execute(
        "UPDATE outbox_events SET updated_at = ? WHERE aggregate_id = 'new-1'",
        (new.isoformat(),),
    )
    await outbox_store.conn.commit()

    worker = OutboxWorker(
        outbox_store,
        worker_id="test-worker",
        completed_retention_days=30,
        prune_batch_size=10,
        prune_interval_seconds=3600,
    )

    assert await worker.process_once() is False

    rows = await outbox_store.conn.execute_fetchall(
        "SELECT aggregate_id, status FROM outbox_events ORDER BY aggregate_id"
    )
    assert [(row["aggregate_id"], row["status"]) for row in rows] == [
        ("dead-1", "dead"),
        ("new-1", "completed"),
    ]


@pytest.mark.asyncio
async def test_worker_dispatches_and_marks_completed(outbox_store: OutboxStore):
    event = _run_completed()
    await outbox_store.enqueue_run_completed(event)
    handled: list[RunCompleted] = []

    async def handle(row):
        handled.append(run_completed_from_payload(row.payload))

    worker = OutboxWorker(outbox_store, worker_id="test-worker")
    worker.register_handler(OUTBOX_RUN_COMPLETED, handle)

    assert await worker.process_once() is True
    assert handled == [event]
    assert await outbox_store.claim_batch(worker_id="test-worker", limit=10) == []


@pytest.mark.asyncio
async def test_worker_retries_then_dead_letters(outbox_store: OutboxStore):
    await outbox_store.enqueue_run_completed(_run_completed())

    async def fail(_row):
        raise RuntimeError("boom")

    worker = OutboxWorker(
        outbox_store,
        worker_id="test-worker",
        max_retries=2,
        retry_base_seconds=0,
        retry_max_seconds=0,
    )
    worker.register_handler(OUTBOX_RUN_COMPLETED, fail)

    assert await worker.process_once() is True
    assert await worker.process_once() is True
    assert await worker.process_once() is False

    rows = await outbox_store.conn.execute_fetchall("SELECT status, attempts, last_error FROM outbox_events")
    assert len(rows) == 1
    assert rows[0]["status"] == "dead"
    assert rows[0]["attempts"] == 2
    assert "RuntimeError: boom" in rows[0]["last_error"]


@pytest.mark.asyncio
async def test_worker_stop_releases_claimed_rows_immediately(outbox_store: OutboxStore):
    await outbox_store.enqueue_run_completed(_run_completed())
    started = asyncio.Event()
    blocked = asyncio.Event()

    async def handle(_row):
        started.set()
        await blocked.wait()

    worker = OutboxWorker(outbox_store, worker_id="stopping-worker", poll_interval=0.01)
    worker.register_handler(OUTBOX_RUN_COMPLETED, handle)
    worker.start()
    await asyncio.wait_for(started.wait(), timeout=1)

    await worker.stop()

    rows = await outbox_store.conn.execute_fetchall("SELECT status, locked_at, locked_by FROM outbox_events")
    assert [(row["status"], row["locked_at"], row["locked_by"]) for row in rows] == [("pending", None, None)]
