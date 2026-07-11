from dataclasses import replace as dc_replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

import ntrp.database as database
from ntrp.automation.models import Automation
from ntrp.automation.scheduler import Scheduler
from ntrp.automation.store import AutomationStore
from ntrp.automation.triggers import TimeTrigger, parse_triggers


@pytest_asyncio.fixture
async def automation_store(tmp_path: Path):
    conn = await database.connect(tmp_path / "automation.db")
    store = AutomationStore(conn)
    await store.init_schema()
    yield store
    await conn.close()


async def test_run_history_records_start_then_finish(automation_store: AutomationStore):
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    run_id = await automation_store.record_run_start("task-1", t0)
    assert run_id > 0

    runs = await automation_store.list_runs("task-1")
    assert len(runs) == 1
    assert runs[0]["status"] == "running"
    assert runs[0]["ended_at"] is None

    await automation_store.record_run_finish(
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


async def test_init_rekeys_legacy_slice_custodian_rows(automation_store: AutomationStore):
    await automation_store.save(_automation("slice:health"))
    await automation_store.record_run_start("slice:health", datetime(2026, 1, 1, tzinfo=UTC))

    await automation_store.init_schema()

    assert await automation_store.get("slice:health") is None
    assert await automation_store.get("area:health") is not None
    assert len(await automation_store.list_runs("area:health")) == 1


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
    await automation_store.record_run_finish(r1, status="completed", result="ok", error=None, ended_at=base)
    r2 = await automation_store.record_run_start("A", base + timedelta(minutes=1))
    await automation_store.record_run_finish(r2, status="failed", result=None, error="x", ended_at=base + timedelta(minutes=1))
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


async def test_run_history_truncates_long_result(automation_store: AutomationStore):
    rid = await automation_store.record_run_start("task-3", datetime(2026, 1, 1, tzinfo=UTC))
    await automation_store.record_run_finish(
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
        description=f"{task_id} description",
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
        description="check CI",
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


@pytest.mark.asyncio
async def test_list_loops_by_session_filters_correctly(automation_store: AutomationStore):
    now = datetime.now(UTC)

    def _loop(task_id: str, session_id: str) -> Automation:
        return Automation(
            task_id=task_id,
            name=task_id,
            description="x",
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
        description="x",
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
        description="Posts to a channel",
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
async def test_seed_builtins_schedules_suggester_job(automation_store: AutomationStore):
    from ntrp.automation.builtins import seed_builtins
    from ntrp.constants import BUILTIN_AUTOMATION_SUGGESTER_DAILY_ID

    await seed_builtins(automation_store)

    suggester = await automation_store.get(BUILTIN_AUTOMATION_SUGGESTER_DAILY_ID)
    assert suggester is not None
    assert suggester.enabled is True
    assert suggester.next_run_at is not None


@pytest.mark.asyncio
async def test_seed_builtins_removes_retired_pipeline_jobs(automation_store: AutomationStore):
    from ntrp.automation.builtins import seed_builtins

    # A pattern_finder builtin seeded by an older version must be garbage-collected
    # now that its handler registration is gone (claims+lens pipeline removed).
    retired = _automation(
        "builtin:pattern-finder-daily",
        name="Pattern Finder Daily",
        handler="pattern_finder_daily",
        builtin=True,
        enabled=True,
        next_run_at=None,
    )
    await automation_store.save(retired)

    # The pre-rename slice suggester (superseded by builtin-area-suggester)
    # must be swept too — it dangles on the unregistered slice_suggester_daily.
    legacy_slice = _automation(
        "builtin-slice-suggester",
        name="Slice Suggester",
        handler="slice_suggester_daily",
        builtin=True,
        enabled=True,
        next_run_at=None,
    )
    await automation_store.save(legacy_slice)

    await seed_builtins(automation_store)

    assert await automation_store.get("builtin:pattern-finder-daily") is None
    assert await automation_store.get("builtin-slice-suggester") is None


@pytest.mark.asyncio
async def test_seed_builtins_repairs_missing_next_run_at(automation_store: AutomationStore):
    from ntrp.automation.builtins import seed_builtins
    from ntrp.constants import BUILTIN_AUTOMATION_SUGGESTER_DAILY_ID

    automation = _automation(
        BUILTIN_AUTOMATION_SUGGESTER_DAILY_ID,
        name="Automation Suggester Daily",
        handler="automation_suggester_daily",
        builtin=True,
        enabled=True,
        next_run_at=None,
    )
    await automation_store.save(automation)

    await seed_builtins(automation_store)

    repaired = await automation_store.get(BUILTIN_AUTOMATION_SUGGESTER_DAILY_ID)
    assert repaired is not None
    assert repaired.next_run_at is not None


@pytest.mark.asyncio
async def test_seed_builtins_respects_user_cadence_and_pause(automation_store: AutomationStore):
    """Triggers and enabled are user dials once the row exists — re-seeding
    must not revert an edited schedule or re-enable a paused builtin."""
    from ntrp.automation.builtins import seed_builtins
    from ntrp.constants import BUILTIN_AUTOMATION_SUGGESTER_DAILY_ID

    await seed_builtins(automation_store)
    seeded = await automation_store.get(BUILTIN_AUTOMATION_SUGGESTER_DAILY_ID)
    edited = dc_replace(seeded, triggers=[TimeTrigger(at="21:30")], enabled=False)
    await automation_store.update_metadata(edited)

    await seed_builtins(automation_store)

    kept = await automation_store.get(BUILTIN_AUTOMATION_SUGGESTER_DAILY_ID)
    assert kept.enabled is False
    assert [(t.at.hour, t.at.minute) for t in kept.triggers] == [(21, 30)]


@pytest.mark.asyncio
async def test_output_schema_round_trips(automation_store: AutomationStore):
    from ntrp.automation.output_schemas import resolve_output_schema

    auto = dc_replace(_automation("with-schema"), output_schema="area_ask")
    await automation_store.save(auto)
    loaded = await automation_store.get("with-schema")
    assert loaded.output_schema == "area_ask"
    assert resolve_output_schema("area_ask") is not None
    assert resolve_output_schema(None) is None
    with pytest.raises(ValueError, match="area_ask"):
        resolve_output_schema("nope")
