import asyncio
import json
from dataclasses import replace as dc_replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

import arden.database as database
from arden.automation.models import Automation
from arden.automation.scheduler import Scheduler
from arden.automation.store import AutomationStore
from arden.automation.triggers import TimeTrigger, parse_triggers
from arden.outbox import (
    OUTBOX_AUTOMATION_SETTLED,
    AutomationSettled,
    OutboxStore,
    automation_settled_from_payload,
)
from arden.server.runtime.outbox import RuntimeOutbox


@pytest_asyncio.fixture
async def automation_store(tmp_path: Path):
    conn = await database.connect(tmp_path / "automation.db")
    store = AutomationStore(conn)
    await store.init_schema()
    yield store
    await conn.close()


@pytest_asyncio.fixture
async def automation_store_with_outbox(tmp_path: Path):
    path = tmp_path / "automation-outbox.db"
    conn = await database.connect(path)
    settlement_conn = await database.connect(path)
    outbox = OutboxStore(conn)
    await outbox.init_schema()
    settlement_outbox = OutboxStore(settlement_conn)
    store = AutomationStore(conn, settlement_outbox, settlement_conn=settlement_conn)
    await store.init_schema()
    yield store, outbox
    await settlement_conn.close()
    await conn.close()


async def _finish_run(
    store: AutomationStore,
    task_id: str,
    run_id: int,
    *,
    status: str,
    result: str | None,
    error: str | None,
    ended_at: datetime,
) -> None:
    clipped_result = result if result is None or len(result) <= 4000 else result[:4000] + "…"
    clipped_error = error if error is None or len(error) <= 4000 else error[:4000] + "…"
    cursor = await store.conn.execute(
        """
        UPDATE automation_runs
        SET ended_at = ?, status = ?, result = ?, error = ?
        WHERE id = ? AND task_id = ? AND status = 'running'
        """,
        (ended_at.isoformat(), status, clipped_result, clipped_error, run_id, task_id),
    )
    await store.conn.commit()
    assert cursor.rowcount == 1


async def test_orphaned_running_rows_fail_terminal(automation_store: AutomationStore):
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    await automation_store.record_run_start("loop-1", t0)
    await automation_store.record_run_start("loop-2", t0)

    swept = await automation_store.fail_orphaned_runs(t0 + timedelta(minutes=5), "interrupted by server restart")
    assert swept == 2
    for task in ("loop-1", "loop-2"):
        runs = await automation_store.list_runs(task)
        assert runs[0]["status"] == "failed"
        assert runs[0]["error"] == "interrupted by server restart"


async def test_restart_binds_latest_unbound_automation_run_to_interrupted_chat(
    automation_store: AutomationStore,
):
    started_at = datetime(2026, 1, 1, tzinfo=UTC)
    await automation_store.save(_automation("area:health"))
    assert await automation_store.try_mark_running("area:health", started_at)
    await automation_store.record_run_start("area:health", started_at)

    assert await automation_store.bind_interrupted_chat_run(
        "area:health",
        "chat-run",
        "area-session",
    )
    assert not await automation_store.bind_interrupted_chat_run(
        "area:health",
        "other-chat-run",
        "area-session",
    )
    refreshed_at = started_at + timedelta(hours=4)
    assert await automation_store.refresh_detached_chat_run(
        "area:health",
        "chat-run",
        "area-session",
        refreshed_at,
    )
    bound = await automation_store.get_running_detached_chat_run("area:health")
    assert bound is not None
    assert bound[:2] == ("chat-run", "area-session")
    assert bound[2] == refreshed_at


async def test_update_last_run_preserve_result(automation_store: AutomationStore):
    await automation_store.save(_automation("loop-1"))
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    await automation_store.update_last_run("loop-1", t0, None, result="real report")
    await automation_store.update_last_run("loop-1", t0 + timedelta(hours=1), None, preserve_result=True)
    auto = await automation_store.get("loop-1")
    assert auto.last_result == "real report"
    assert auto.last_run_at == t0 + timedelta(hours=1)


async def test_update_last_run_if_next_run_preserves_external_reschedule(automation_store: AutomationStore):
    initial = datetime(2026, 1, 1, tzinfo=UTC)
    prewritten = initial + timedelta(hours=6)
    external = initial + timedelta(minutes=5)
    await automation_store.save(_automation("loop-1", next_run_at=prewritten))
    await automation_store.set_next_run("loop-1", external)

    await automation_store.update_last_run_if_next_run(
        "loop-1",
        initial,
        initial + timedelta(hours=12),
        prewritten,
        result="completed",
    )

    auto = await automation_store.get("loop-1")
    assert auto is not None
    assert auto.last_run_at == initial
    assert auto.last_result == "completed"
    assert auto.next_run_at == external


async def test_update_last_run_if_null_next_run_preserves_external_reschedule(automation_store: AutomationStore):
    initial = datetime(2026, 1, 1, tzinfo=UTC)
    external = initial + timedelta(minutes=5)
    await automation_store.save(_automation("loop-1", next_run_at=None))
    await automation_store.set_next_run("loop-1", external)

    await automation_store.update_last_run_if_next_run(
        "loop-1",
        initial,
        initial + timedelta(hours=12),
        None,
        result="completed",
    )

    auto = await automation_store.get("loop-1")
    assert auto is not None
    assert auto.last_run_at == initial
    assert auto.last_result == "completed"
    assert auto.next_run_at == external


async def test_compare_and_set_next_run_supports_null_expected_value(automation_store: AutomationStore):
    due = datetime(2026, 1, 1, tzinfo=UTC)
    await automation_store.save(_automation("loop-1", next_run_at=None))

    assert await automation_store.compare_and_set_next_run("loop-1", None, due)
    assert not await automation_store.compare_and_set_next_run("loop-1", None, due + timedelta(hours=1))
    auto = await automation_store.get("loop-1")
    assert auto is not None and auto.next_run_at == due


async def test_run_history_records_start_then_finish(automation_store: AutomationStore):
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    run_id = await automation_store.record_run_start("task-1", t0)
    assert run_id > 0

    runs = await automation_store.list_runs("task-1")
    assert len(runs) == 1
    assert runs[0]["status"] == "running"
    assert runs[0]["ended_at"] is None

    await _finish_run(
        automation_store,
        "task-1",
        run_id,
        status="completed",
        result="did the thing",
        error=None,
        ended_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
    )
    runs = await automation_store.list_runs("task-1")
    assert runs[0]["status"] == "completed"
    assert runs[0]["result"] == "did the thing"
    assert runs[0]["ended_at"] is not None


async def test_run_history_newest_first_and_limited(automation_store: AutomationStore):
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(5):
        await automation_store.record_run_start("task-2", base + timedelta(minutes=i))
    runs = await automation_store.list_runs("task-2", limit=3)
    assert len(runs) == 3
    assert runs[0]["started_at"] > runs[1]["started_at"] > runs[2]["started_at"]


async def test_recent_run_statuses_newest_first_per_task(automation_store: AutomationStore):
    base = datetime(2026, 1, 1, tzinfo=UTC)
    r1 = await automation_store.record_run_start("A", base)
    await _finish_run(automation_store, "A", r1, status="completed", result="ok", error=None, ended_at=base)
    r2 = await automation_store.record_run_start("A", base + timedelta(minutes=1))
    await _finish_run(
        automation_store,
        "A",
        r2,
        status="failed",
        result=None,
        error="x",
        ended_at=base + timedelta(minutes=1),
    )
    await automation_store.record_run_start("A", base + timedelta(minutes=2))  # still running
    await automation_store.record_run_start("B", base)

    res = await automation_store.recent_run_statuses(["A", "B", "C"], per_task=4)
    assert res["A"] == ["running", "failed", "completed"]  # newest first
    assert res["B"] == ["running"]
    assert res["C"] == []  # unknown task → empty, not missing


async def test_recent_run_statuses_respects_per_task_cap(automation_store: AutomationStore):
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(6):
        await automation_store.record_run_start("A", base + timedelta(minutes=i))
    res = await automation_store.recent_run_statuses(["A"], per_task=4)
    assert len(res["A"]) == 4


async def test_latest_completed_runs_ignores_newer_failed_and_running_attempts(automation_store: AutomationStore):
    base = datetime(2026, 1, 1, tzinfo=UTC)
    completed = await automation_store.record_run_start("A", base)
    await _finish_run(
        automation_store,
        "A",
        completed,
        status="completed",
        result="ok",
        error=None,
        ended_at=base + timedelta(minutes=1),
    )
    failed = await automation_store.record_run_start("A", base + timedelta(minutes=2))
    await _finish_run(
        automation_store,
        "A",
        failed,
        status="failed",
        result=None,
        error="boom",
        ended_at=base + timedelta(minutes=3),
    )
    await automation_store.record_run_start("A", base + timedelta(minutes=4))

    assert await automation_store.latest_completed_runs(("A", "missing")) == {"A": base + timedelta(minutes=1)}


async def test_run_history_truncates_long_result(automation_store: AutomationStore):
    rid = await automation_store.record_run_start("task-3", datetime(2026, 1, 1, tzinfo=UTC))
    await _finish_run(
        automation_store,
        "task-3",
        rid,
        status="failed",
        result="x" * 5000,
        error="boom",
        ended_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    runs = await automation_store.list_runs("task-3")
    assert runs[0]["status"] == "failed"
    assert runs[0]["error"] == "boom"
    assert len(runs[0]["result"]) <= 4001  # 4000 chars + ellipsis


@pytest.mark.asyncio
async def test_settle_run_commits_one_automation_settled_event(automation_store_with_outbox):
    store, outbox = automation_store_with_outbox
    ended_at = datetime(2026, 1, 1, tzinfo=UTC)
    next_run = ended_at + timedelta(hours=6)
    await store.save(_automation("task-1", next_run_at=next_run))
    assert await store.try_mark_running("task-1", ended_at)
    run_id = await store.record_run_start("task-1", ended_at)

    assert await store.settle_run(
        task_id="task-1",
        run_id=run_id,
        status="completed",
        result="done",
        error=None,
        ended_at=ended_at,
        next_run=next_run,
        expected_next_run=None,
        preserve_external_reschedule=False,
        preserve_result=False,
        disable_one_shot=False,
    )
    assert not await store.settle_run(
        task_id="task-1",
        run_id=run_id,
        status="failed",
        result=None,
        error="late duplicate",
        ended_at=ended_at,
        next_run=next_run,
        expected_next_run=None,
        preserve_external_reschedule=False,
        preserve_result=False,
        disable_one_shot=False,
    )

    events = await outbox.claim_batch(worker_id="test", limit=10)
    assert len(events) == 1
    assert events[0].event_type == OUTBOX_AUTOMATION_SETTLED
    event = automation_settled_from_payload(events[0].payload)
    assert event.automation_run_id == run_id
    assert event.task_id == "task-1"
    assert event.success is True
    assert await store.latest_completed_runs(("task-1",)) == {"task-1": ended_at}


@pytest.mark.asyncio
async def test_settlement_observer_retries_after_committed_run(automation_store_with_outbox):
    store, outbox = automation_store_with_outbox
    ended_at = datetime(2026, 1, 1, tzinfo=UTC)
    await store.save(_automation("task-1"))
    assert await store.try_mark_running("task-1", ended_at)
    run_id = await store.record_run_start("task-1", ended_at)
    assert await store.settle_run(
        task_id="task-1",
        run_id=run_id,
        status="completed",
        result="done",
        error=None,
        ended_at=ended_at,
        next_run=None,
        expected_next_run=None,
        preserve_external_reschedule=False,
        preserve_result=False,
        disable_one_shot=False,
    )

    attempts = 0
    observed: list[dict[str, datetime]] = []

    async def observe(event: AutomationSettled) -> None:
        nonlocal attempts
        attempts += 1
        observed.append(await store.latest_completed_runs((event.task_id,)))
        if attempts == 1:
            raise RuntimeError("health unavailable")

    runtime_outbox = RuntimeOutbox(
        outbox_store=outbox,
        scheduler=Scheduler(store=store, build_deps=lambda: None),
        on_automation_settled=observe,
    )
    runtime_outbox.worker.retry_base_seconds = 0
    runtime_outbox.worker.retry_max_seconds = 0

    assert await runtime_outbox.worker.process_once()
    assert await runtime_outbox.worker.process_once()
    assert observed == [{"task-1": ended_at}, {"task-1": ended_at}]
    assert (await store.list_runs("task-1"))[0]["status"] == "completed"


@pytest.mark.asyncio
async def test_settle_run_rolls_back_when_outbox_insert_fails(tmp_path: Path):
    class FailingOutbox(OutboxStore):
        async def enqueue_automation_settled_in_transaction(
            self,
            *,
            automation_run_id: int,
            task_id: str,
            success: bool,
        ) -> bool:
            raise RuntimeError("outbox unavailable")

    conn = await database.connect(tmp_path / "failed-settlement.db")
    outbox = FailingOutbox(conn)
    await outbox.init_schema()
    store = AutomationStore(conn, outbox)
    await store.init_schema()
    ended_at = datetime(2026, 1, 1, tzinfo=UTC)
    await store.save(_automation("task-1"))
    assert await store.try_mark_running("task-1", ended_at)
    run_id = await store.record_run_start("task-1", ended_at)

    try:
        with pytest.raises(RuntimeError, match="outbox unavailable"):
            await store.settle_run(
                task_id="task-1",
                run_id=run_id,
                status="completed",
                result="done",
                error=None,
                ended_at=ended_at,
                next_run=None,
                expected_next_run=None,
                preserve_external_reschedule=False,
                preserve_result=False,
                disable_one_shot=False,
            )

        assert (await store.list_runs("task-1"))[0]["status"] == "running"
        automation = await store.get("task-1")
        assert automation is not None
        assert automation.running_since is not None
        assert automation.last_run_at is None
        assert await conn.execute_fetchall("SELECT id FROM outbox_events") == []
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_settlement_transaction_cannot_be_committed_by_another_store(
    automation_store_with_outbox,
    monkeypatch: pytest.MonkeyPatch,
):
    store, outbox = automation_store_with_outbox
    started_at = datetime(2026, 1, 1, tzinfo=UTC)
    await store.save(_automation("task-1"))
    assert await store.try_mark_running("task-1", started_at)
    run_id = await store.record_run_start("task-1", started_at)

    cadence_started = asyncio.Event()
    release_cadence = asyncio.Event()

    async def fail_after_run_update(*_args) -> None:
        cadence_started.set()
        await release_cadence.wait()
        raise RuntimeError("forced settlement rollback")

    monkeypatch.setattr(store, "_record_successful_cadence", fail_after_run_update)
    settlement = asyncio.create_task(
        store.settle_run(
            task_id="task-1",
            run_id=run_id,
            status="completed",
            result="done",
            error=None,
            ended_at=started_at,
            next_run=None,
            expected_next_run=None,
            preserve_external_reschedule=False,
            preserve_result=False,
            disable_one_shot=False,
        )
    )
    await cadence_started.wait()

    unrelated_write = asyncio.create_task(
        outbox.enqueue(
            event_type="test.unrelated",
            payload={},
            idempotency_key="test.unrelated:1",
        )
    )
    await asyncio.sleep(0)
    assert not unrelated_write.done()

    release_cadence.set()
    with pytest.raises(RuntimeError, match="forced settlement rollback"):
        await settlement
    assert await unrelated_write

    assert (await store.list_runs("task-1"))[0]["status"] == "running"
    automation = await store.get("task-1")
    assert automation is not None and automation.running_since is not None
    events = await outbox.claim_batch(worker_id="test", limit=10)
    assert [event.event_type for event in events] == ["test.unrelated"]


def _automation(
    task_id: str,
    *,
    name: str | None = None,
    enabled: bool = True,
    next_run_at: datetime | None = None,
    running_since: datetime | None = None,
    handler: str | None = None,
    builtin: bool = False,
) -> Automation:
    return Automation(
        task_id=task_id,
        name=name or task_id,
        description=None,
        description_source=None,
        prompt=f"Run {task_id}.",
        model=None,
        triggers=[TimeTrigger(at="09:00")],
        enabled=enabled,
        created_at=datetime.now(UTC),
        next_run_at=next_run_at,
        last_run_at=None,
        last_result=None,
        running_since=running_since,
        auto_approve=False,
        handler=handler,
        builtin=builtin,
    )


@pytest.mark.asyncio
async def test_count_state_is_persistent_and_clearable(automation_store: AutomationStore):
    now = datetime.now(UTC)

    assert await automation_store.increment_count("task-1", "session-1", now) == 1
    assert await automation_store.increment_count("task-1", "session-1", now) == 2
    assert await automation_store.increment_count("task-1", "session-2", now) == 1

    await automation_store.clear_count("task-1", "session-1")

    assert await automation_store.increment_count("task-1", "session-1", now) == 1
    assert await automation_store.increment_count("task-1", "session-2", now) == 2


def test_legacy_knowledge_event_triggers_are_ignored():
    triggers = parse_triggers(
        '[{"type":"knowledge_event","object_types":["episode"],"actions":["created"],"statuses":["active"]}]'
    )

    assert triggers == []


@pytest.mark.asyncio
async def test_status_summarizes_scheduler_owned_state(automation_store: AutomationStore):
    now = datetime.now(UTC)
    await automation_store.save(_automation("due", next_run_at=now - timedelta(minutes=5)))
    await automation_store.save(_automation("future", next_run_at=now + timedelta(hours=1)))
    await automation_store.save(_automation("disabled", enabled=False, next_run_at=now - timedelta(minutes=5)))
    await automation_store.save(
        _automation("running", next_run_at=now - timedelta(minutes=5), running_since=now - timedelta(minutes=10))
    )

    await automation_store.enqueue_event("event-task", "event-1", "{}", now - timedelta(minutes=3))
    await automation_store.enqueue_event("event-task", "event-2", "{}", now - timedelta(minutes=2))
    claimed = await automation_store.claim_next_event("event-task", now)
    await automation_store.fail_event(claimed[0], "try later", now + timedelta(minutes=30))
    await automation_store.claim_next_event("event-task", now)

    await automation_store.increment_count("count-task", "session-1", now - timedelta(minutes=20))
    await automation_store.enqueue_event("event-task", "event-dead", "{}", now - timedelta(minutes=1))
    dead_claim = await automation_store.claim_next_event("event-task", now)
    await automation_store.dead_letter_event(dead_claim[0], "gave up", now)

    status = await automation_store.get_status(now)

    assert status["tasks"]["total"] == 4
    assert status["tasks"]["enabled"] == 3
    assert status["tasks"]["disabled"] == 1
    assert status["tasks"]["running"] == 1
    assert status["tasks"]["due"] == 1
    assert status["tasks"]["next_run_at"] is not None
    assert status["tasks"]["oldest_running_since"] is not None
    assert status["event_queue"]["total"] == 2
    assert status["event_queue"]["ready"] == 0
    assert status["event_queue"]["scheduled"] == 1
    assert status["event_queue"]["claimed"] == 1
    assert status["count_state"]["total"] == 1
    assert status["dead_letters"]["total"] == 1
    assert status["dead_letters"]["newest_failed_at"] is not None


@pytest.mark.asyncio
async def test_event_dead_letter_preserves_failed_payload_and_removes_from_queue(automation_store: AutomationStore):
    now = datetime.now(UTC)
    await automation_store.enqueue_event("event-task", "event-dead", '{"ok": true}', now)
    claimed = await automation_store.claim_next_event("event-task", now)

    await automation_store.dead_letter_event(claimed[0], "too many failures", now + timedelta(seconds=1))

    assert await automation_store.claim_next_event("event-task", now + timedelta(seconds=2)) is None
    rows = await automation_store.conn.execute_fetchall("SELECT * FROM automation_event_dead_letter")
    assert len(rows) == 1
    assert rows[0]["task_id"] == "event-task"
    assert rows[0]["event_key"] == "event-dead"
    assert rows[0]["context"] == '{"ok": true}'
    assert rows[0]["last_error"] == "too many failures"
    assert rows[0]["attempt_count"] == 1


@pytest.mark.asyncio
async def test_dead_letter_event_rolls_back_copy_when_queue_delete_fails(automation_store: AutomationStore):
    now = datetime.now(UTC)
    await automation_store.enqueue_event("event-task", "event-dead", '{"ok": true}', now)
    claimed = await automation_store.claim_next_event("event-task", now)
    await automation_store.conn.execute(
        """
        CREATE TRIGGER fail_event_queue_delete
        BEFORE DELETE ON automation_event_queue
        BEGIN
            SELECT RAISE(ABORT, 'queue delete failed');
        END
        """
    )
    await automation_store.conn.commit()

    with pytest.raises(Exception, match="queue delete failed"):
        await automation_store.dead_letter_event(claimed[0], "too many failures", now)
    await automation_store.conn.commit()

    dead_rows = await automation_store.conn.execute_fetchall("SELECT * FROM automation_event_dead_letter")
    queue_rows = await automation_store.conn.execute_fetchall("SELECT * FROM automation_event_queue")

    assert dead_rows == []
    assert len(queue_rows) == 1


@pytest.mark.asyncio
async def test_claim_and_enqueue_event_dedupes_in_one_store_call(automation_store: AutomationStore):
    now = datetime.now(UTC)

    assert await automation_store.claim_and_enqueue_event("event-task", "event-1", '{"ok": true}', now) is True
    assert await automation_store.claim_and_enqueue_event("event-task", "event-1", '{"ok": false}', now) is False

    queue_rows = await automation_store.conn.execute_fetchall(
        "SELECT task_id, event_key, context FROM automation_event_queue"
    )
    dedupe_rows = await automation_store.conn.execute_fetchall("SELECT task_id, event_key FROM automation_event_dedupe")

    assert [dict(row) for row in queue_rows] == [
        {"task_id": "event-task", "event_key": "event-1", "context": '{"ok": true}'}
    ]
    assert [dict(row) for row in dedupe_rows] == [{"task_id": "event-task", "event_key": "event-1"}]


@pytest.mark.asyncio
async def test_claim_and_enqueue_event_rolls_back_dedupe_when_enqueue_fails(automation_store: AutomationStore):
    now = datetime.now(UTC)
    await automation_store.conn.execute(
        """
        CREATE TRIGGER fail_event_queue_insert
        BEFORE INSERT ON automation_event_queue
        BEGIN
            SELECT RAISE(ABORT, 'queue insert failed');
        END
        """
    )
    await automation_store.conn.commit()

    with pytest.raises(Exception, match="queue insert failed"):
        await automation_store.claim_and_enqueue_event("event-task", "event-1", "{}", now)

    rows = await automation_store.conn.execute_fetchall(
        "SELECT * FROM automation_event_dedupe WHERE task_id = ?",
        ("event-task",),
    )

    assert rows == []


@pytest.mark.asyncio
async def test_loop_fields_roundtrip(automation_store: AutomationStore):
    now = datetime.now(UTC)
    loop = Automation(
        task_id="loop-foo",
        name="Loop: check CI",
        description=None,
        description_source=None,
        prompt="Check CI.",
        model=None,
        triggers=[TimeTrigger(every="5m")],
        enabled=True,
        created_at=now,
        next_run_at=now + timedelta(minutes=5),
        last_run_at=None,
        last_result=None,
        running_since=None,
        auto_approve=True,
        kind="loop",
        thread_id="sess-1",
        max_iterations=3,
        iteration_count=0,
        stop_when="when green",
    )
    await automation_store.save(loop)

    loaded = await automation_store.get("loop-foo")
    assert loaded is not None
    assert loaded.kind == "loop"
    assert loaded.thread_id == "sess-1"
    assert loaded.max_iterations == 3
    assert loaded.iteration_count == 0
    assert loaded.stop_when == "when green"
    assert loaded.prompt == "Check CI."


@pytest.mark.asyncio
async def test_list_loops_by_session_filters_correctly(automation_store: AutomationStore):
    now = datetime.now(UTC)

    def _loop(task_id: str, session_id: str) -> Automation:
        return Automation(
            task_id=task_id,
            name=task_id,
            description=None,
            description_source=None,
            prompt=f"Run {task_id}.",
            model=None,
            triggers=[TimeTrigger(every="5m")],
            enabled=True,
            created_at=now,
            next_run_at=now,
            last_run_at=None,
            last_result=None,
            running_since=None,
            auto_approve=True,
            kind="loop",
            thread_id=session_id,
        )

    await automation_store.save(_loop("loop-a", "sess-1"))
    await automation_store.save(_loop("loop-b", "sess-1"))
    await automation_store.save(_loop("loop-c", "sess-2"))
    # Non-loop in same session should not appear.
    await automation_store.save(_automation("not-a-loop"))

    by_one = await automation_store.list_loops_by_session("sess-1")
    assert sorted(a.task_id for a in by_one) == ["loop-a", "loop-b"]

    by_two = await automation_store.list_loops_by_session("sess-2")
    assert [a.task_id for a in by_two] == ["loop-c"]


@pytest.mark.asyncio
async def test_increment_iteration_advances_count(automation_store: AutomationStore):
    now = datetime.now(UTC)
    loop = Automation(
        task_id="loop-iter",
        name="x",
        description=None,
        description_source=None,
        prompt="Run the loop iteration.",
        model=None,
        triggers=[TimeTrigger(every="5m")],
        enabled=True,
        created_at=now,
        next_run_at=now,
        last_run_at=None,
        last_result=None,
        running_since=None,
        auto_approve=True,
        kind="loop",
        thread_id="sess",
    )
    await automation_store.save(loop)

    await automation_store.increment_iteration("loop-iter")
    await automation_store.increment_iteration("loop-iter")

    loaded = await automation_store.get("loop-iter")
    assert loaded is not None
    assert loaded.iteration_count == 2


@pytest.mark.asyncio
async def test_v5_fields_roundtrip(automation_store: AutomationStore):
    """All new v5 fields must serialize/deserialize through save/get."""
    now = datetime.now(UTC)
    automation = Automation(
        task_id="post-mode-foo",
        name="Post offer update",
        description=None,
        description_source=None,
        prompt="Post the offer update to the channel.",
        model=None,
        triggers=[TimeTrigger(every="4h")],
        enabled=True,
        created_at=now,
        next_run_at=now + timedelta(hours=4),
        last_run_at=None,
        last_result=None,
        running_since=None,
        auto_approve=True,
        thread_id="channel-sess-1",
        read_history=False,
        parent_automation_id="watcher-1",
        idempotency_key="offer-42",
        idempotency_scope="global",
    )
    await automation_store.save(automation)

    loaded = await automation_store.get("post-mode-foo")
    assert loaded is not None
    assert loaded.thread_id == "channel-sess-1"
    assert loaded.read_history is False
    assert loaded.parent_automation_id == "watcher-1"
    assert loaded.idempotency_key == "offer-42"
    assert loaded.idempotency_scope == "global"


@pytest.mark.asyncio
async def test_v5_fields_default_to_none_or_false(automation_store: AutomationStore):
    """Existing call sites that don't set v5 fields still roundtrip cleanly."""
    automation = _automation("plain")
    await automation_store.save(automation)

    loaded = await automation_store.get("plain")
    assert loaded is not None
    assert loaded.thread_id is None
    assert loaded.read_history is False
    assert loaded.parent_automation_id is None
    assert loaded.idempotency_key is None
    assert loaded.idempotency_scope is None


@pytest.mark.asyncio
async def test_update_metadata_persists_v5_identity_fields(automation_store: AutomationStore):
    """update_metadata() must persist parent_automation_id / idempotency_key / idempotency_scope."""
    automation = _automation("identity-fields")
    await automation_store.save(automation)

    automation.parent_automation_id = "watcher-7"
    automation.idempotency_key = "offer-99"
    automation.idempotency_scope = "thread"
    await automation_store.update_metadata(automation)

    loaded = await automation_store.get("identity-fields")
    assert loaded is not None
    assert loaded.parent_automation_id == "watcher-7"
    assert loaded.idempotency_key == "offer-99"
    assert loaded.idempotency_scope == "thread"


@pytest.mark.asyncio
async def test_v4_to_v10_migration_backfills_loop_rows(tmp_path: Path):
    """v4 → v10: loop rows get thread_id = target_session_id, read_history = True,
    and the legacy target_session_id / loop_prompt columns are dropped."""
    db_path = tmp_path / "v4.db"
    conn = await database.connect(db_path)

    # Manually build a v4 schema: scheduled_tasks with loop columns, no v5 columns.
    await conn.executescript(
        """
        CREATE TABLE automation_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE scheduled_tasks (
            task_id TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL,
            model TEXT,
            triggers TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            last_run_at TEXT,
            next_run_at TEXT,
            notifiers TEXT,
            last_result TEXT,
            running_since TEXT,
            writable INTEGER NOT NULL DEFAULT 0,
            handler TEXT,
            builtin INTEGER NOT NULL DEFAULT 0,
            cooldown_minutes INTEGER,
            kind TEXT NOT NULL DEFAULT 'automation',
            target_session_id TEXT,
            loop_prompt TEXT,
            max_iterations INTEGER,
            iteration_count INTEGER NOT NULL DEFAULT 0,
            stop_when TEXT,
            max_age_days INTEGER
        );
        INSERT INTO automation_meta (key, value) VALUES ('schema_version', '4');
        """
    )
    now = datetime.now(UTC).isoformat()
    await conn.execute(
        """
        INSERT INTO scheduled_tasks (
            task_id, name, description, model, triggers, enabled, created_at,
            kind, target_session_id, loop_prompt
        ) VALUES (?, '', 'loop a', NULL, '[]', 1, ?, 'loop', 'sess-A', 'prompt a')
        """,
        ("loop-row", now),
    )
    await conn.execute(
        """
        INSERT INTO scheduled_tasks (
            task_id, name, description, model, triggers, enabled, created_at,
            kind, target_session_id, loop_prompt
        ) VALUES (?, '', 'plain', NULL, '[]', 1, ?, 'automation', NULL, NULL)
        """,
        ("plain-row", now),
    )
    await conn.commit()

    # Run migration via init_schema.
    store = AutomationStore(conn)
    await store.init_schema()

    loaded_loop = await store.get("loop-row")
    assert loaded_loop is not None
    assert loaded_loop.thread_id == "sess-A"
    assert loaded_loop.read_history is True

    loaded_plain = await store.get("plain-row")
    assert loaded_plain is not None
    assert loaded_plain.thread_id is None
    assert loaded_plain.read_history is False

    rows = await conn.execute_fetchall("PRAGMA table_info(scheduled_tasks)")
    col_names = {r["name"] for r in rows}
    assert "loop_prompt" not in col_names
    assert "target_session_id" not in col_names

    await conn.close()


@pytest.mark.asyncio
async def test_migration_is_idempotent(tmp_path: Path):
    """Running migration twice doesn't double-write or fail across v4 → v10."""
    db_path = tmp_path / "v4.db"
    conn = await database.connect(db_path)
    await conn.executescript(
        """
        CREATE TABLE automation_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE scheduled_tasks (
            task_id TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL,
            model TEXT,
            triggers TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            last_run_at TEXT,
            next_run_at TEXT,
            notifiers TEXT,
            last_result TEXT,
            running_since TEXT,
            writable INTEGER NOT NULL DEFAULT 0,
            handler TEXT,
            builtin INTEGER NOT NULL DEFAULT 0,
            cooldown_minutes INTEGER,
            kind TEXT NOT NULL DEFAULT 'automation',
            target_session_id TEXT,
            loop_prompt TEXT,
            max_iterations INTEGER,
            iteration_count INTEGER NOT NULL DEFAULT 0,
            stop_when TEXT,
            max_age_days INTEGER
        );
        INSERT INTO automation_meta (key, value) VALUES ('schema_version', '4');
        """
    )
    now = datetime.now(UTC).isoformat()
    await conn.execute(
        """
        INSERT INTO scheduled_tasks (
            task_id, name, description, model, triggers, enabled, created_at,
            kind, target_session_id, loop_prompt
        ) VALUES (?, '', 'loop a', NULL, '[]', 1, ?, 'loop', 'sess-A', 'prompt a')
        """,
        ("loop-row", now),
    )
    await conn.commit()

    store = AutomationStore(conn)
    await store.init_schema()
    # After v5, this row has thread_id='sess-A', read_history=True.
    # Now hand-modify thread_id to simulate a write that should NOT be clobbered
    # by a second migration pass.
    await conn.execute(
        "UPDATE scheduled_tasks SET thread_id = ? WHERE task_id = ?",
        ("user-edited-thread", "loop-row"),
    )
    await conn.commit()

    # Re-run init_schema; should be a no-op for the backfill since version is now 10.
    await store.init_schema()
    loaded = await store.get("loop-row")
    assert loaded is not None
    assert loaded.thread_id == "user-edited-thread"

    await conn.close()


@pytest.mark.asyncio
async def test_migration_surfaces_invalid_trigger_json(tmp_path: Path):
    conn = await database.connect(tmp_path / "invalid-migration.db")
    store = AutomationStore(conn)
    await store.init_schema()
    await store.save(_automation("invalid-trigger"))
    await conn.execute("UPDATE scheduled_tasks SET triggers = '{' WHERE task_id = 'invalid-trigger'")
    await conn.execute("UPDATE automation_meta SET value = '1' WHERE key = 'schema_version'")
    await conn.commit()

    with pytest.raises(json.JSONDecodeError):
        await store.init_schema()

    rows = await conn.execute_fetchall("SELECT value FROM automation_meta WHERE key = 'schema_version'")
    assert rows[0]["value"] == "1"
    await conn.close()


@pytest.mark.asyncio
async def test_v5_indexes_created(automation_store: AutomationStore):
    """The three new v5 indexes must exist after init_schema."""
    rows = await automation_store.conn.execute_fetchall("SELECT name FROM sqlite_master WHERE type = 'index'")
    names = {row["name"] for row in rows}
    assert "idx_scheduled_tasks_parent" in names
    assert "idx_scheduled_tasks_thread_kind" in names
    assert "idx_scheduled_tasks_idempotency" in names
    assert "idx_scheduled_tasks_kind_thread" in names


def test_scheduler_constructor_has_no_learning_recorder(automation_store: AutomationStore):
    async def record_learning_event(**event):
        raise AssertionError("scheduler should not write continual-learning events")

    with pytest.raises(TypeError):
        Scheduler(
            store=automation_store,
            build_deps=lambda: None,
            record_learning_event=record_learning_event,
        )


@pytest.mark.asyncio
async def test_seed_builtins_seeds_required_workers_and_optional_dream(automation_store: AutomationStore):
    from arden.automation.builtins import seed_builtins
    from arden.constants import (
        BUILTIN_MEMORY_CONSOLIDATE_ID,
        BUILTIN_MEMORY_DREAM_ID,
        BUILTIN_MEMORY_RETENTION_ID,
        BUILTIN_MEMORY_STORAGE_MAINTENANCE_ID,
        BUILTIN_MEMORY_SYNTHESIZE_ID,
        BUILTIN_WIKI_MAINTENANCE_ID,
    )

    await seed_builtins(
        automation_store,
        memory_model="memory-model",
        include_managed_history_collection=True,
    )
    await seed_builtins(
        automation_store,
        memory_model="memory-model",
        include_managed_history_collection=True,
    )

    rows = {row.task_id: row for row in await automation_store.list_all()}
    assert set(rows) == {
        BUILTIN_MEMORY_CONSOLIDATE_ID,
        BUILTIN_MEMORY_DREAM_ID,
        BUILTIN_MEMORY_RETENTION_ID,
        BUILTIN_MEMORY_STORAGE_MAINTENANCE_ID,
        BUILTIN_MEMORY_SYNTHESIZE_ID,
        BUILTIN_WIKI_MAINTENANCE_ID,
    }
    maintenance = rows[BUILTIN_MEMORY_CONSOLIDATE_ID]
    assert maintenance.handler == "memory_maintenance"
    assert maintenance.triggers == [TimeTrigger(at="03:00", days="daily")]
    assert "Never create or rewrite fact text" in maintenance.prompt

    retention = rows[BUILTIN_MEMORY_RETENTION_ID]
    assert retention.handler is None
    assert retention.model == "memory-model"
    assert retention.triggers == [TimeTrigger(at="03:45", days="daily")]
    assert retention.tool_scope == [
        "search_facts",
        "get_fact",
        "get_fact_history",
        "get_due_fact_reviews",
        "plan_fact_changes",
        "commit_fact_changes",
    ]
    assert "Never treat silence or age alone as evidence." in retention.prompt

    synthesis = rows[BUILTIN_MEMORY_SYNTHESIZE_ID]
    assert synthesis.handler == "memory_synthesize"
    assert synthesis.triggers == [TimeTrigger(every="6h")]
    assert "Canonical fact synthesis" in synthesis.prompt

    wiki = rows[BUILTIN_WIKI_MAINTENANCE_ID]
    assert wiki.handler == "wiki_maintenance"
    assert wiki.triggers == [TimeTrigger(every="6h")]

    dream = rows[BUILTIN_MEMORY_DREAM_ID]
    assert dream.handler == "memory_dream"
    assert dream.triggers == [TimeTrigger(at="04:00", days="daily")]
    assert dream.auto_approve is True
    assert dream.enabled is False
    assert dream.next_run_at is None

    storage = rows[BUILTIN_MEMORY_STORAGE_MAINTENANCE_ID]
    assert storage.handler == "managed_history_collection"
    assert storage.model is None
    assert storage.triggers == [TimeTrigger(every="7d")]

    assert all(row.next_run_at is not None for row in rows.values() if row.enabled)


@pytest.mark.asyncio
async def test_seed_builtins_skips_storage_maintenance_without_canonical_memory(
    automation_store: AutomationStore,
) -> None:
    from arden.automation.builtins import seed_builtins
    from arden.constants import BUILTIN_MEMORY_STORAGE_MAINTENANCE_ID

    await seed_builtins(automation_store, memory_model="memory-model")

    assert await automation_store.get(BUILTIN_MEMORY_STORAGE_MAINTENANCE_ID) is None


@pytest.mark.asyncio
async def test_seed_builtins_without_memory_model_still_seeds_storage_maintenance(
    automation_store: AutomationStore,
) -> None:
    from arden.automation.builtins import seed_builtins
    from arden.constants import BUILTIN_MEMORY_STORAGE_MAINTENANCE_ID

    await seed_builtins(
        automation_store,
        memory_model=None,
        include_managed_history_collection=True,
    )

    rows = await automation_store.list_all()
    assert [row.task_id for row in rows] == [BUILTIN_MEMORY_STORAGE_MAINTENANCE_ID]
    assert rows[0].handler == "managed_history_collection"
    assert rows[0].model is None
    assert rows[0].triggers == [TimeTrigger(every="7d")]


@pytest.mark.asyncio
async def test_seed_builtins_removes_retired_system_workers(automation_store: AutomationStore):
    from arden.automation.builtins import seed_builtins
    from arden.constants import RETIRED_BUILTIN_AUTOMATION_IDS

    for task_id in RETIRED_BUILTIN_AUTOMATION_IDS:
        await automation_store.save(dc_replace(_automation(task_id), builtin=True))

    await seed_builtins(automation_store, memory_model="memory-model")

    retired = [await automation_store.get(task_id) for task_id in RETIRED_BUILTIN_AUTOMATION_IDS]
    assert retired == [None, None]


@pytest.mark.asyncio
async def test_seed_builtins_enforces_system_timing_but_preserves_pause_and_history(
    automation_store: AutomationStore,
):
    from arden.automation.builtins import seed_builtins
    from arden.constants import (
        BUILTIN_MEMORY_CONSOLIDATE_ID,
        BUILTIN_MEMORY_DREAM_ID,
        BUILTIN_MEMORY_RETENTION_ID,
        BUILTIN_MEMORY_SYNTHESIZE_ID,
        BUILTIN_WIKI_MAINTENANCE_ID,
    )

    await seed_builtins(automation_store, memory_model="memory-model")
    last_run = datetime(2026, 6, 17, 9, tzinfo=UTC)
    for task_id in (
        BUILTIN_MEMORY_CONSOLIDATE_ID,
        BUILTIN_MEMORY_DREAM_ID,
        BUILTIN_MEMORY_RETENTION_ID,
        BUILTIN_MEMORY_SYNTHESIZE_ID,
        BUILTIN_WIKI_MAINTENANCE_ID,
    ):
        row = await automation_store.get(task_id)
        assert row is not None
        await automation_store.save(
            dc_replace(
                row,
                triggers=[TimeTrigger(every="2h")],
                cooldown_minutes=17,
                enabled=False,
                last_run_at=last_run,
                last_result="Previous result.",
            )
        )
    run_id = await automation_store.record_run_start(BUILTIN_MEMORY_SYNTHESIZE_ID, last_run)
    await _finish_run(
        automation_store,
        BUILTIN_MEMORY_SYNTHESIZE_ID,
        run_id,
        status="completed",
        result="Historical synthesis result.",
        error=None,
        ended_at=last_run,
    )

    await seed_builtins(automation_store, memory_model="memory-model")

    expected = {
        BUILTIN_MEMORY_CONSOLIDATE_ID: [TimeTrigger(at="03:00", days="daily")],
        BUILTIN_MEMORY_DREAM_ID: [TimeTrigger(at="04:00", days="daily")],
        BUILTIN_MEMORY_RETENTION_ID: [TimeTrigger(at="03:45", days="daily")],
        BUILTIN_MEMORY_SYNTHESIZE_ID: [TimeTrigger(every="6h")],
        BUILTIN_WIKI_MAINTENANCE_ID: [TimeTrigger(every="6h")],
    }
    for task_id, triggers in expected.items():
        row = await automation_store.get(task_id)
        assert row is not None
        assert row.triggers == triggers
        assert row.cooldown_minutes is None
        assert row.enabled is False
        assert row.last_run_at == last_run
        assert row.last_result == "Previous result."
    assert (await automation_store.list_runs(BUILTIN_MEMORY_SYNTHESIZE_ID))[0]["result"] == (
        "Historical synthesis result."
    )


@pytest.mark.asyncio
async def test_seed_builtins_preserves_user_enabled_optional_dream(automation_store: AutomationStore):
    from arden.automation.builtins import seed_builtins
    from arden.constants import BUILTIN_MEMORY_DREAM_ID

    await seed_builtins(automation_store, memory_model="memory-model")
    await automation_store.set_enabled(BUILTIN_MEMORY_DREAM_ID, True)

    await seed_builtins(automation_store, memory_model="memory-model")

    dream = await automation_store.get(BUILTIN_MEMORY_DREAM_ID)
    assert dream is not None
    assert dream.enabled is True
    assert dream.next_run_at is not None
