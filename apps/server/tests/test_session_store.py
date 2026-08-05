"""Session store tests — real SQLite, round-trip persistence."""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

import arden.database as database
from arden.agent import ToolResult, Usage
from arden.constants import RAW_TOOL_RESULT_INLINE_MAX_BYTES
from arden.context.models import BackgroundStartDisposition, SessionState
from arden.context.store import SessionDataCorruptionError, SessionStore, StaleSessionContextError
from arden.core.compactor import _build_compacted_messages
from arden.core.public_refs import public_ref
from arden.core.raw_tool_results import RAW_TOOL_RESULT_DATA_KEY, persist_raw_tool_result, read_raw_tool_result
from arden.events.internal import RunCompleted, RunFailed
from arden.events.sse import ThinkingEvent, ToolCallResultEvent
from arden.outbox.store import OutboxStore
from arden.server.bus import StreamRecord
from arden.services.session import SessionService


@pytest_asyncio.fixture
async def store(tmp_path: Path):
    conn = await database.connect(tmp_path / "sessions.db")
    completion_conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    s = SessionStore(conn, read_conn, completion_conn)
    await s.init_schema()
    await OutboxStore(completion_conn).init_schema()
    yield s
    await read_conn.close()
    await completion_conn.close()
    await conn.close()


def _make_state(session_id: str = "test-session", name: str | None = None) -> SessionState:
    return SessionState(
        session_id=session_id,
        started_at=datetime.now(UTC),
        name=name,
    )


def _agent_ref(session_id: str, task_id: str, title: str = "agent") -> str:
    return public_ref(title, f"{session_id}:{task_id}", empty_slug="agent")


async def _provision_agent_session(store: SessionStore, session_id: str, agent_ref: str) -> None:
    await store.save_session(
        SessionState(
            session_id=session_id,
            public_ref=agent_ref,
            started_at=datetime.now(UTC),
            session_type="agent",
        ),
        [],
    )


@pytest.mark.asyncio
async def test_session_public_ref_is_stable_and_conflicts_fail(store: SessionStore):
    original = _make_state("stable-ref", name="Original title")
    await store.save_session(original, [])
    original_ref = original.public_ref

    renamed = _make_state("stable-ref", name="Renamed title")
    await store.save_session(renamed, [])
    assert renamed.public_ref == original_ref

    conflicting = _make_state("stable-ref", name="Renamed title")
    conflicting.public_ref = public_ref("different", "different-id", empty_slug="chat")
    with pytest.raises(ValueError, match="immutable"):
        await store.save_session(conflicting, [])

    loaded = await store.load_session("stable-ref")
    assert loaded is not None
    assert loaded.state.public_ref == original_ref


@pytest.mark.asyncio
async def test_session_ref_resolution_enforces_optional_area_scope(store: SessionStore):
    ops = await store.create_area(name="Ops")
    finance = await store.create_area(name="Finance")
    ops_state = _make_state("ops-session", name="Ops chat")
    ops_state.area_id = ops["area_id"]
    finance_state = _make_state("finance-session", name="Finance chat")
    finance_state.area_id = finance["area_id"]
    plain_state = _make_state("plain-session", name="Plain chat")
    await store.save_session(ops_state, [])
    await store.save_session(finance_state, [])
    await store.save_session(plain_state, [])

    assert await store.get_session_id_by_ref(ops_state.public_ref, area_id=ops["area_id"]) == "ops-session"
    assert await store.get_session_id_by_ref(finance_state.public_ref, area_id=ops["area_id"]) is None
    assert await store.get_session_id_by_ref(plain_state.public_ref, area_id=None) == "plain-session"
    assert await store.get_session_id_by_ref(ops_state.public_ref, area_id=None) is None
    assert await store.get_session_id_by_ref(finance_state.public_ref) == "finance-session"


@pytest.mark.asyncio
async def test_area_round_trip_and_session_scoping(store: SessionStore):
    area = await store.create_area(
        name="arden",
        default_cwd=" /Users/me/src/arden ",
        instructions="Prefer small focused changes.",
    )

    assert area["area_id"].startswith("area_")
    assert area["area_ref"].startswith("arden~")
    assert area["name"] == "arden"
    assert area["default_cwd"] == "/Users/me/src/arden"
    assert area["instructions"] == "Prefer small focused changes."
    assert "knowledge_scope" not in area
    unique_ref_indexes = await store.conn.execute_fetchall(
        "SELECT indexes.name FROM pragma_index_list('areas') AS indexes "
        'WHERE indexes."unique" = 1 '
        "AND (SELECT group_concat(name, ',') FROM pragma_index_info(indexes.name)) = 'area_ref'"
    )
    assert len(unique_ref_indexes) == 1

    area_state = _make_state(session_id="area-session", name="Area chat")
    area_state.area_id = area["area_id"]
    inbox_state = _make_state(session_id="inbox-session", name="Inbox chat")
    await store.save_session(area_state, [{"role": "user", "content": "area"}])
    await store.save_session(inbox_state, [{"role": "user", "content": "inbox"}])

    loaded = await store.load_session("area-session")
    assert loaded is not None
    assert loaded.state.area_id == area["area_id"]

    area_sessions = await store.list_sessions(area_id=area["area_id"])
    assert [row["session_id"] for row in area_sessions] == ["area-session"]
    assert area_sessions[0]["area_id"] == area["area_id"]

    inbox_sessions = await store.list_sessions(area_id=None)
    assert [row["session_id"] for row in inbox_sessions] == ["inbox-session"]

    stale_state = _make_state(session_id="area-session", name="Area chat")
    stale_state.area_id = area["area_id"]
    assert await store.update_session_area("area-session", None)
    await store.update_progress(stale_state, [{"role": "user", "content": "still running"}])
    loaded_after_move = await store.load_session("area-session")
    assert loaded_after_move is not None
    assert loaded_after_move.state.area_id is None
    await store.save_session(stale_state, [{"role": "user", "content": "finished"}])
    loaded_after_save = await store.load_session("area-session")
    assert loaded_after_save is not None
    assert loaded_after_save.state.area_id is None

    archived_state = _make_state(session_id="archived-area-session", name="Archived area chat")
    archived_state.area_id = area["area_id"]
    await store.save_session(archived_state, [{"role": "user", "content": "archive area"}])

    assert await store.archive_area(area["area_id"])
    assert await store.get_area(area["area_id"]) is None
    assert area["area_id"] not in {row["area_id"] for row in await store.list_areas()}
    assert not await store.archive_area(area["area_id"])

    loaded_after_area_archive = await store.load_session("archived-area-session")
    assert loaded_after_area_archive is not None
    assert loaded_after_area_archive.state.area_id == area["area_id"]

    restored = await store.restore_area(area["area_id"])
    assert restored is not None
    assert restored["area_id"] == area["area_id"]
    assert await store.get_area(area["area_id"]) is not None


@pytest.mark.asyncio
async def test_area_ref_is_immutable_across_rename(store: SessionStore):
    created = await store.create_area(name="Health")

    renamed = await store.update_area(created["area_id"], name="Wellbeing")

    assert renamed is not None
    assert renamed["area_ref"] == created["area_ref"]
    assert await store.get_area_by_ref(created["area_ref"]) == renamed
    assert await store.get_area_by_ref(created["area_id"]) is None


@pytest.mark.asyncio
async def test_exact_session_scope_survives_beyond_recent_fifty(store: SessionStore):
    area = await store.create_area(name="Old area")
    old = _make_state(session_id="old-area-session")
    old.area_id = area["area_id"]
    await store.save_session(old, [{"role": "user", "content": "old"}])
    for index in range(51):
        await store.save_session(
            _make_state(session_id=f"recent-{index:02d}"),
            [{"role": "user", "content": "recent"}],
        )

    recent_ids = {row["session_id"] for row in await store.recent_session_scopes(50)}
    service = SessionService(store)

    assert old.session_id not in recent_ids
    assert await store.session_scope(old.session_id) == {
        "session_id": old.session_id,
        "area_id": area["area_id"],
        "session_type": "chat",
        "origin_automation_id": None,
    }
    assert await service.session_scope(old.session_id) == await store.session_scope(old.session_id)


@pytest.mark.asyncio
async def test_area_names_are_unique_case_insensitively(store: SessionStore):
    first = await store.create_area(name="Health")

    with pytest.raises(ValueError, match="already exists"):
        await store.create_area(name=" health ")

    second = await store.create_area(name="Immigration")
    with pytest.raises(ValueError, match="already exists"):
        await store.update_area(second["area_id"], name="HEALTH")

    assert (await store.get_area(first["area_id"]))["name"] == "Health"
    assert (await store.get_area(second["area_id"]))["name"] == "Immigration"


@pytest.mark.asyncio
async def test_area_page_has_one_owner_including_archived_areas(store: SessionStore):
    first = await store.create_area(name="Health", page_path="topics/health.md")

    with pytest.raises(ValueError, match="already attached"):
        await store.create_area(name="Medical", page_path="topics/health.md")

    second = await store.create_area(name="Medical")
    with pytest.raises(ValueError, match="already attached"):
        await store.update_area(second["area_id"], page_path="topics/health.md")

    assert await store.archive_area(first["area_id"])
    with pytest.raises(ValueError, match="already attached"):
        await store.update_area(second["area_id"], page_path="topics/health.md")









@pytest.mark.asyncio
async def test_chat_model_persists_and_updates(store: SessionStore):
    state = _make_state(session_id="s-model")
    state.chat_model = "anthropic/claude-opus"
    await store.save_session(state, [])
    loaded = await store.load_session("s-model")
    assert loaded is not None
    assert loaded.state.chat_model == "anthropic/claude-opus"

    await store.update_session_chat_model("s-model", "openai/gpt-5")
    loaded = await store.load_session("s-model")
    assert loaded.state.chat_model == "openai/gpt-5"
    rows = await store.list_sessions()
    assert next(r for r in rows if r["session_id"] == "s-model")["chat_model"] == "openai/gpt-5"


@pytest.mark.asyncio
async def test_chat_model_defaults_none_for_legacy(store: SessionStore):
    await store.save_session(_make_state(session_id="s-legacy"), [])
    loaded = await store.load_session("s-legacy")
    assert loaded is not None
    assert loaded.state.chat_model is None


@pytest.mark.asyncio
async def test_save_and_load_round_trip(store: SessionStore):
    state = _make_state(name="my chat")
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
    ]

    await store.save_session(state, messages)
    loaded = await store.load_session("test-session")

    assert loaded is not None
    assert loaded.state.session_id == "test-session"
    assert loaded.state.name == "my chat"
    assert len(loaded.messages) == 3
    assert loaded.messages[1]["content"] == "Hello"
    assert loaded.messages[2]["content"] == "Hi there!"


@pytest.mark.asyncio
async def test_chat_run_ledger(store: SessionStore):
    await store.record_chat_run_started("run-1", "sess-1")
    await store.record_chat_run_status("run-1", "completed", last_seq=99)

    completed = await store.get_chat_run("run-1")
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["last_seq"] == 99
    assert completed["ended_at"] is not None


@pytest.mark.asyncio
async def test_completed_chat_run_and_outbox_commit_together(store: SessionStore):
    await store.record_chat_run_started("run-complete", "sess-1")
    event = RunCompleted("run-complete", "sess-1", (), Usage(), "done")

    await store.record_chat_run_completed_with_outbox(event, stop_reason="end_turn", last_seq=12)

    completed = await store.get_chat_run("run-complete")
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["stop_reason"] == "end_turn"
    assert completed["last_seq"] == 12
    rows = await store.conn.execute_fetchall(
        "SELECT event_type, aggregate_id FROM outbox_events WHERE aggregate_id = ?",
        ("run-complete",),
    )
    assert [(row["event_type"], row["aggregate_id"]) for row in rows] == [("run.completed", "run-complete")]


@pytest.mark.asyncio
async def test_completed_chat_run_rolls_back_when_outbox_insert_fails(store: SessionStore):
    await store.record_chat_run_started("run-rollback", "sess-1")
    await store.chat_completion_conn.execute(
        """
        CREATE TRIGGER fail_chat_completion_outbox
        AFTER INSERT ON outbox_events
        BEGIN
            SELECT RAISE(ABORT, 'outbox unavailable');
        END
        """
    )
    await store.chat_completion_conn.commit()

    event = RunCompleted("run-rollback", "sess-1", (), Usage(), "done")
    with pytest.raises(Exception, match="outbox unavailable"):
        await store.record_chat_run_completed_with_outbox(event, stop_reason="end_turn", last_seq=12)

    run = await store.get_chat_run("run-rollback")
    assert run is not None
    assert run["status"] == "pending"
    rows = await store.conn.execute_fetchall("SELECT id FROM outbox_events WHERE aggregate_id = ?", ("run-rollback",))
    assert rows == []


@pytest.mark.asyncio
async def test_completed_chat_run_rejects_unknown_run_without_outbox(store: SessionStore):
    event = RunCompleted("missing", "sess-1", (), Usage(), "done")

    with pytest.raises(KeyError, match="unknown chat run"):
        await store.record_chat_run_completed_with_outbox(event, stop_reason="end_turn", last_seq=12)

    rows = await store.conn.execute_fetchall("SELECT id FROM outbox_events WHERE aggregate_id = ?", ("missing",))
    assert rows == []


@pytest.mark.asyncio
async def test_failed_chat_run_and_outbox_commit_together(store: SessionStore):
    await store.record_chat_run_started("run-failed", "sess-1")
    event = RunFailed("run-failed", "sess-1", "provider failed", automation_task_id="loop-1")

    await store.record_chat_run_failed_with_outbox(
        event,
        status="error",
        stop_reason="provider failed",
        last_seq=8,
        error_code="provider_error",
        error_message="Provider failed.",
    )

    failed = await store.get_chat_run("run-failed")
    assert failed is not None
    assert failed["status"] == "error"
    assert failed["last_seq"] == 8
    assert failed["error_code"] == "provider_error"
    rows = await store.conn.execute_fetchall(
        "SELECT event_type, payload FROM outbox_events WHERE aggregate_id = ?",
        ("run-failed",),
    )
    assert len(rows) == 1
    assert rows[0]["event_type"] == "run.failed"
    assert json.loads(rows[0]["payload"])["automation_task_id"] == "loop-1"


@pytest.mark.asyncio
async def test_failed_chat_run_rolls_back_when_outbox_insert_fails(store: SessionStore):
    await store.record_chat_run_started("run-failed-rollback", "sess-1")
    await store.chat_completion_conn.execute(
        """
        CREATE TRIGGER fail_chat_failure_outbox
        AFTER INSERT ON outbox_events
        BEGIN
            SELECT RAISE(ABORT, 'outbox unavailable');
        END
        """
    )
    await store.chat_completion_conn.commit()

    event = RunFailed("run-failed-rollback", "sess-1", "provider failed", automation_task_id="loop-1")
    with pytest.raises(Exception, match="outbox unavailable"):
        await store.record_chat_run_failed_with_outbox(
            event,
            status="error",
            stop_reason="provider failed",
            last_seq=8,
        )

    run = await store.get_chat_run("run-failed-rollback")
    assert run is not None
    assert run["status"] == "pending"
    rows = await store.conn.execute_fetchall(
        "SELECT id FROM outbox_events WHERE aggregate_id = ?",
        ("run-failed-rollback",),
    )
    assert rows == []


@pytest.mark.asyncio
async def test_ingested_idempotency_receipt_is_terminal(store: SessionStore):
    await store.claim_chat_idempotency_key(
        session_id="sess-1",
        client_id="cid-ingested",
        request_hash="hash-ingested",
    )
    await store.update_chat_idempotency_key(
        session_id="sess-1",
        client_id="cid-ingested",
        status="queued",
        run_id="run-1",
    )
    await store.update_chat_idempotency_key(
        session_id="sess-1",
        client_id="cid-ingested",
        status="ingested",
        run_id="run-1",
    )

    receipt = await store.get_chat_idempotency_key("sess-1", "cid-ingested")
    assert receipt is not None
    assert receipt["status"] == "ingested"
    assert receipt["run_id"] == "run-1"
    assert receipt["expires_at"] is not None


@pytest.mark.asyncio
async def test_cancelled_idempotency_key_cannot_be_reopened(store: SessionStore):
    await store.claim_chat_idempotency_key(
        session_id="sess-1",
        client_id="cid-cancelled",
        request_hash="hash-cancelled",
    )
    assert (
        await store.cancel_chat_idempotency_key(
            session_id="sess-1",
            client_id="cid-cancelled",
            run_id="run-1",
        )
        == "cancelled"
    )
    claimed, receipt = await store.claim_chat_idempotency_key(
        session_id="sess-1",
        client_id="cid-cancelled",
        request_hash="different-request",
    )
    assert claimed is False
    assert receipt["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancelling_absent_message_creates_terminal_tombstone(store: SessionStore):
    assert (
        await store.cancel_chat_idempotency_key(
            session_id="sess-1",
            client_id="cid-before-post",
        )
        == "cancelled"
    )

    claimed, receipt = await store.claim_chat_idempotency_key(
        session_id="sess-1",
        client_id="cid-before-post",
        request_hash="hash-post",
    )
    assert claimed is False
    assert receipt["status"] == "cancelled"
    assert receipt["run_id"] is None


@pytest.mark.asyncio
async def test_interrupted_run_releases_unconsumed_queued_receipt(store: SessionStore):
    await store.record_chat_run_started("run-1", "sess-1")
    await store.claim_chat_idempotency_key(
        session_id="sess-1",
        client_id="cid-queued",
        request_hash="hash-queued",
    )
    await store.update_chat_idempotency_key(
        session_id="sess-1",
        client_id="cid-queued",
        status="queued",
        run_id="run-1",
    )

    await store.mark_interrupted_chat_runs()
    assert await store.release_interrupted_queued_receipts() == 1
    assert await store.get_chat_idempotency_key("sess-1", "cid-queued") is None


@pytest.mark.asyncio
async def test_chat_idempotency_key_round_trip(store: SessionStore):
    claimed, row = await store.claim_chat_idempotency_key(
        session_id="sess-1",
        client_id="cid-1",
        request_hash="hash-a",
    )
    assert claimed is True
    assert row["status"] == "accepted"
    assert row["run_id"] is None

    updated = await store.update_chat_idempotency_key(
        session_id="sess-1",
        client_id="cid-1",
        status="running",
        run_id="run-1",
    )
    assert updated is not None
    assert updated["run_id"] == "run-1"
    assert updated["status"] == "running"

    claimed_again, duplicate = await store.claim_chat_idempotency_key(
        session_id="sess-1",
        client_id="cid-1",
        request_hash="hash-a",
    )
    assert claimed_again is False
    assert duplicate["run_id"] == "run-1"
    assert duplicate["request_hash"] == "hash-a"

    claimed_conflict, conflict = await store.claim_chat_idempotency_key(
        session_id="sess-1",
        client_id="cid-1",
        request_hash="hash-b",
    )
    assert claimed_conflict is False
    assert conflict["request_hash"] == "hash-a"


@pytest.mark.asyncio
async def test_chat_run_status_preserves_structured_error(store: SessionStore):
    await store.record_chat_run_started("run-err", "sess-1", metadata={"client_id": "cid-err"})
    await store.record_chat_run_status(
        "run-err",
        "failed",
        error_code="tool_crash",
        error_message="boom",
    )

    row = await store.get_chat_run("run-err")
    assert row is not None
    assert row["status"] == "failed"
    assert row["client_id"] == "cid-err"
    assert row["error_code"] == "tool_crash"
    assert row["error_message"] == "boom"
    assert row["ended_at"] is not None


@pytest.mark.asyncio
async def test_chat_idempotency_prunes_only_expired_terminal_rows(store: SessionStore):
    now = datetime.now(UTC)
    old = (now.replace(year=now.year - 1)).isoformat()

    await store.claim_chat_idempotency_key(
        session_id="sess-1",
        client_id="cid-terminal",
        request_hash="hash-a",
        expires_at=old,
    )
    await store.update_chat_idempotency_key(
        session_id="sess-1",
        client_id="cid-terminal",
        status="completed",
        run_id="run-1",
    )
    await store.conn.execute(
        "UPDATE chat_idempotency_keys SET expires_at = ? WHERE session_id = ? AND client_id = ?",
        (old, "sess-1", "cid-terminal"),
    )

    await store.claim_chat_idempotency_key(
        session_id="sess-1",
        client_id="cid-running",
        request_hash="hash-b",
        expires_at=old,
    )
    await store.update_chat_idempotency_key(
        session_id="sess-1",
        client_id="cid-running",
        status="running",
        run_id="run-2",
    )
    await store.conn.execute(
        "UPDATE chat_idempotency_keys SET expires_at = ? WHERE session_id = ? AND client_id = ?",
        (old, "sess-1", "cid-running"),
    )
    await store.conn.commit()

    pruned = await store.prune_expired_chat_idempotency_keys(now)

    assert pruned == 1
    assert await store.get_chat_idempotency_key("sess-1", "cid-terminal") is None
    assert await store.get_chat_idempotency_key("sess-1", "cid-running") is not None


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_tool_call_round_trip(store: SessionStore):
    await store.record_tool_call_started(
        run_id="run-1",
        session_id="s-1",
        tool_call_id="call-1",
        tool_name="read_state",
        action="read",
        scope="internal",
        args_hash="abc123",
    )
    await store.record_tool_call_finished(
        run_id="run-1",
        tool_call_id="call-1",
        status="success",
        result_preview="ok",
        outcome={
            "status": "succeeded",
            "effect": {"operation": "read", "target": "state:1"},
            "receipt": "receipt-1",
        },
    )

    rows = await store.list_tool_calls(run_id="run-1")

    assert rows[0]["status"] == "success"
    assert rows[0]["result_preview"] == "ok"
    assert rows[0]["args_hash"] == "abc123"
    assert rows[0]["outcome"] == {
        "status": "succeeded",
        "effect": {"operation": "read", "target": "state:1"},
        "receipt": "receipt-1",
    }
    assert rows[0]["ended_at"] is not None


@pytest.mark.asyncio
async def test_tool_approval_request_to_approved(store: SessionStore):
    await store.record_tool_approval_requested(
        run_id="run-1",
        session_id="s-1",
        tool_call_id="call-1",
        tool_name="file_write",
        action="write",
        scope="internal",
        preview="write a file",
        diff="diff",
        expires_at="2026-05-16T12:00:00+00:00",
    )
    await store.resolve_tool_approval(
        run_id="run-1",
        tool_call_id="call-1",
        status="approved",
        result_feedback="ok",
    )

    row = await store.get_tool_approval(run_id="run-1", tool_call_id="call-1")

    assert row is not None
    assert row["status"] == "approved"
    assert row["result_feedback"] == "ok"
    assert row["resolved_at"] is not None
    assert row["preview"] == "write a file"
    assert row["diff"] == "diff"


@pytest.mark.asyncio
async def test_tool_approval_request_to_rejected(store: SessionStore):
    await store.record_tool_approval_requested(
        run_id="run-1",
        session_id="s-1",
        tool_call_id="call-1",
        tool_name="bash",
        action="execute",
        scope="internal",
    )
    await store.resolve_tool_approval(
        run_id="run-1",
        tool_call_id="call-1",
        status="rejected",
        result_feedback="no",
    )

    row = await store.get_tool_approval(run_id="run-1", tool_call_id="call-1")

    assert row is not None
    assert row["status"] == "rejected"
    assert row["result_feedback"] == "no"
    assert row["resolved_at"] is not None


@pytest.mark.asyncio
async def test_run_suspension_round_trip(store: SessionStore):
    await store.record_run_suspension(
        run_id="run-1",
        session_id="s-1",
        suspension_id="approval-1",
        kind="tool_approval",
        payload={"tool_name": "bash", "action": "execute", "scope": "internal"},
    )
    assert await store.resolve_run_suspension(
        run_id="run-1",
        suspension_id="approval-1",
        status="approved",
        resolution={"approved": True, "result": "ok"},
    )

    row = await store.get_run_suspension(run_id="run-1", suspension_id="approval-1")

    assert row is not None
    assert row["kind"] == "tool_approval"
    assert row["payload"]["tool_name"] == "bash"
    assert row["status"] == "approved"
    assert row["resolution"] == {"approved": True, "result": "ok"}


@pytest.mark.asyncio
async def test_run_suspension_duplicate_record_preserves_terminal_resolution(store: SessionStore):
    request = {
        "run_id": "run-1",
        "session_id": "s-1",
        "suspension_id": "approval-1",
        "kind": "tool_approval",
        "payload": {"tool_name": "bash", "action": "execute", "scope": "internal"},
    }
    await store.record_run_suspension(**request)
    await store.resolve_run_suspension(
        run_id="run-1",
        suspension_id="approval-1",
        status="rejected",
        resolution={"approved": False, "result": "no"},
    )

    await store.record_run_suspension(**request)

    row = await store.get_run_suspension(run_id="run-1", suspension_id="approval-1")
    assert row is not None
    assert row["status"] == "rejected"
    assert row["resolution"] == {"approved": False, "result": "no"}


@pytest.mark.asyncio
async def test_pending_tool_approvals_exclude_other_suspension_kinds(store: SessionStore):
    await store.record_run_suspension(
        run_id="run-1",
        session_id="s-1",
        suspension_id="input-1",
        kind="user_input",
        payload={"prompt": "Choose one"},
    )

    assert await store.list_pending_tool_approvals("s-1") == []
    suspensions = await store.list_pending_run_suspensions("s-1")
    assert [row["suspension_id"] for row in suspensions] == ["input-1"]


@pytest.mark.asyncio
async def test_session_goal_lifecycle(store: SessionStore):
    goal = await store.set_goal("sess-1", "Ship the goal feature")

    assert goal["session_id"] == "sess-1"
    assert goal["objective"] == "Ship the goal feature"
    assert goal["status"] == "active"
    assert goal["evidence"] == []

    updated = await store.update_goal(
        "sess-1",
        status="blocked",
        blocked_reason="Needs approval",
        evidence="Write operation requested",
    )
    assert updated is not None
    assert updated["status"] == "blocked"
    assert updated["blocked_reason"] == "Needs approval"
    assert updated["evidence"][-1]["text"] == "Write operation requested"

    completed = await store.update_goal("sess-1", status="complete", evidence="Tests passed")
    assert completed is not None
    assert completed["status"] == "complete"
    assert completed["blocked_reason"] is None
    assert completed["evidence"][-1]["text"] == "Tests passed"

    assert await store.clear_goal("sess-1") is True
    assert await store.get_goal("sess-1") is None


@pytest.mark.asyncio
async def test_todo_override_lifecycle(store: SessionStore):
    assert await store.get_todo_override("sess-1") is None

    items = [{"content": "a", "status": "completed"}, {"content": "b", "status": "pending"}]
    saved = await store.set_todo_override("sess-1", items, explanation="user edit")
    assert saved["items"] == items
    assert saved["explanation"] == "user edit"

    loaded = await store.get_todo_override("sess-1")
    assert loaded is not None
    assert loaded["items"] == items

    # Upsert replaces.
    await store.set_todo_override("sess-1", [{"content": "c", "status": "in_progress"}])
    assert (await store.get_todo_override("sess-1"))["items"] == [{"content": "c", "status": "in_progress"}]

    assert await store.clear_todo_override("sess-1") is True
    assert await store.get_todo_override("sess-1") is None
    assert await store.clear_todo_override("sess-1") is False


@pytest.mark.asyncio
async def test_session_goal_budget_and_goal_id_guard(store: SessionStore):
    goal = await store.set_goal("sess-1", "Ship the goal feature", token_budget=100)

    stale = await store.update_goal("sess-1", goal_id="old-goal", tokens_used_delta=50)
    assert stale is None
    assert (await store.get_goal("sess-1"))["tokens_used"] == 0

    updated = await store.update_goal(
        "sess-1",
        goal_id=goal["goal_id"],
        tokens_used_delta=120,
        time_used_seconds_delta=3,
    )
    assert updated is not None
    assert updated["tokens_used"] == 120
    assert updated["time_used_seconds"] == 3
    assert updated["status"] == "budget_limited"


@pytest.mark.asyncio
async def test_tool_approval_request_to_expired(store: SessionStore):
    await store.record_tool_approval_requested(
        run_id="run-1",
        session_id="s-1",
        tool_call_id="call-1",
        tool_name="bash",
        action="execute",
        scope="internal",
        expires_at="2026-05-16T12:00:00+00:00",
    )
    await store.expire_tool_approval(
        run_id="run-1",
        tool_call_id="call-1",
        result_feedback="Approval timed out",
    )

    row = await store.get_tool_approval(run_id="run-1", tool_call_id="call-1")

    assert row is not None
    assert row["status"] == "expired"
    assert row["result_feedback"] == "Approval timed out"
    assert row["resolved_at"] is not None
    assert row["expires_at"] == "2026-05-16T12:00:00+00:00"


@pytest.mark.asyncio
async def test_tool_approval_late_result_does_not_overwrite_expired(store: SessionStore):
    await store.record_tool_approval_requested(
        run_id="run-1",
        session_id="s-1",
        tool_call_id="call-1",
        tool_name="bash",
        action="execute",
        scope="internal",
    )
    assert await store.expire_tool_approval(
        run_id="run-1",
        tool_call_id="call-1",
        result_feedback="Approval timed out",
    )

    resolved = await store.resolve_tool_approval(
        run_id="run-1",
        tool_call_id="call-1",
        status="approved",
        result_feedback="late ok",
    )
    row = await store.get_tool_approval(run_id="run-1", tool_call_id="call-1")

    assert resolved is False
    assert row is not None
    assert row["status"] == "expired"
    assert row["result_feedback"] == "Approval timed out"


@pytest.mark.asyncio
async def test_tool_approval_late_result_does_not_overwrite_cancelled(store: SessionStore):
    await store.record_tool_approval_requested(
        run_id="run-1",
        session_id="s-1",
        tool_call_id="call-1",
        tool_name="bash",
        action="execute",
        scope="internal",
    )
    assert await store.resolve_tool_approval(
        run_id="run-1",
        tool_call_id="call-1",
        status="cancelled",
        result_feedback="Approval cancelled",
    )

    resolved = await store.resolve_tool_approval(
        run_id="run-1",
        tool_call_id="call-1",
        status="approved",
        result_feedback="late ok",
    )
    row = await store.get_tool_approval(run_id="run-1", tool_call_id="call-1")

    assert resolved is False
    assert row is not None
    assert row["status"] == "cancelled"
    assert row["result_feedback"] == "Approval cancelled"


@pytest.mark.asyncio
async def test_tool_call_audit_is_scoped_by_run(store: SessionStore):
    await store.record_tool_call_started(
        run_id="run-1",
        session_id="s-1",
        tool_call_id="call-1",
        tool_name="read_state",
        action="read",
        scope="internal",
        args_hash="run1",
    )
    await store.record_tool_call_started(
        run_id="run-2",
        session_id="s-2",
        tool_call_id="call-1",
        tool_name="read_state",
        action="read",
        scope="internal",
        args_hash="run2",
    )

    rows_1 = await store.list_tool_calls(run_id="run-1")
    rows_2 = await store.list_tool_calls(run_id="run-2")

    assert rows_1[0]["args_hash"] == "run1"
    assert rows_2[0]["args_hash"] == "run2"


@pytest.mark.asyncio
async def test_tool_call_checkpoint_and_suspension_state_transitions(store: SessionStore):
    await store.record_tool_call_created(
        run_id="run-1",
        session_id="s-1",
        tool_call_id="call-1",
        tool_name="bash",
        action="execute",
        scope="internal",
        args_hash="hash-1",
    )
    await store.record_tool_call_started(
        run_id="run-1",
        session_id="s-1",
        tool_call_id="call-1",
        tool_name="bash",
        action="execute",
        scope="internal",
        args_hash="hash-1",
    )
    await store.record_run_suspension(
        run_id="run-1",
        session_id="s-1",
        suspension_id="call-1",
        kind="tool_approval",
        payload={"tool_name": "bash", "action": "execute", "scope": "internal"},
    )

    assert (await store.list_tool_calls(run_id="run-1"))[0]["status"] == "awaiting"

    await store.mark_run_suspension_consumed(run_id="run-1", suspension_id="call-1")
    assert (await store.list_tool_calls(run_id="run-1"))[0]["status"] == "running"


@pytest.mark.asyncio
async def test_duplicate_tool_checkpoint_does_not_overwrite_terminal_call(store: SessionStore):
    kwargs = {
        "run_id": "run-1",
        "session_id": "s-1",
        "tool_call_id": "call-1",
        "tool_name": "read_state",
        "action": "read",
        "scope": "internal",
        "args_hash": "hash-1",
    }
    await store.record_tool_call_created(**kwargs)
    await store.record_tool_call_started(**kwargs)
    await store.record_tool_call_finished(
        run_id="run-1",
        tool_call_id="call-1",
        status="success",
        result_preview="done",
    )

    await store.record_tool_call_created(**kwargs)

    row = (await store.list_tool_calls(run_id="run-1"))[0]
    assert row["status"] == "success"
    assert row["result_preview"] == "done"


@pytest.mark.asyncio
async def test_run_sidecars_persist_context_and_derive_evidence_from_durable_facts(store: SessionStore):
    await store.record_run_context_manifest(
        run_id="run-1",
        session_id="s-1",
        manifest=[{"context_id": "ctx-1", "source": "memory", "ref": "record-1"}],
    )
    await store.record_tool_call_started(
        run_id="run-1",
        session_id="s-1",
        tool_call_id="call-1",
        tool_name="email_send",
        action="write",
        scope="external",
    )
    await store.record_tool_call_finished(
        run_id="run-1",
        tool_call_id="call-1",
        status="success",
        result_preview="sent",
        outcome={
            "status": "succeeded",
            "effect": {"operation": "send", "target": "message:42"},
            "verification": {"postcondition": "message exists", "observed": "message:42", "confidence": 1.0},
            "receipt": "provider:42",
        },
    )
    await store.record_tool_approval_requested(
        run_id="run-1",
        session_id="s-1",
        tool_call_id="call-1",
        tool_name="email_send",
        action="write",
        scope="external",
    )
    await store.resolve_tool_approval(run_id="run-1", tool_call_id="call-1", status="approved")

    evidence = await store.record_run_evidence(
        run_id="run-1",
        session_id="s-1",
        source_refs=[{"provider": "gmail", "kind": "message", "ref": "message:42", "title": "Sent email"}],
    )
    sidecars = await store.get_run_sidecars("run-1")

    assert sidecars["context_manifest"][0]["context_id"] == "ctx-1"
    assert evidence["sources"][0]["ref"] == "message:42"
    assert evidence["approvals"][0]["status"] == "approved"
    assert evidence["receipts"] == [{"tool_call_id": "call-1", "receipt": "provider:42"}]
    assert evidence["checks"][0]["observed"] == "message:42"
    assert evidence["effects"][0]["target"] == "message:42"
    assert evidence["limitations"] == []


@pytest.mark.asyncio
async def test_run_sidecars_resolve_exact_initiating_and_meta_turns(store: SessionStore):
    async def seed(run_id: str, session_id: str, client_id: str) -> None:
        await store.record_chat_run_started(run_id, session_id, metadata={"client_id": client_id})
        await store.record_run_context_manifest(
            run_id=run_id,
            session_id=session_id,
            manifest=[
                {
                    "context_id": f"ctx-{run_id}",
                    "content_type": "memory",
                    "source": "memory",
                    "ref": f"record:{run_id}",
                    "freshness": "current",
                    "selection_reason": "relevant",
                    "size_bytes": 42,
                }
            ],
        )

    await seed("run-1", "s-1", "turn-1")
    await seed("run-2", "s-1", "turn-2")
    initiating = await store.get_run_sidecars_for_turn(session_id="s-1", turn_id="turn-1")
    meta = await store.get_run_sidecars_for_turn(session_id="s-1", turn_id="meta-user-run-2")

    assert initiating and initiating["run_id"] == "run-1"
    assert meta and meta["run_id"] == "run-2"
    assert await store.get_run_sidecars_for_turn(session_id="other", turn_id="turn-1") is None
    assert await store.get_run_sidecars_for_turn(session_id="s-1", turn_id="missing") is None

@pytest.mark.asyncio
async def test_list_tool_call_outcomes_returns_only_requested_session_calls(store: SessionStore):
    for run_id, session_id, call_id in (
        ("run-1", "s-1", "call-1"),
        ("run-2", "s-2", "call-2"),
    ):
        await store.record_tool_call_started(
            run_id=run_id,
            session_id=session_id,
            tool_call_id=call_id,
            tool_name="file_write",
            action="write",
            scope="internal",
        )
        await store.record_tool_call_finished(
            run_id=run_id,
            tool_call_id=call_id,
            status="success",
            result_preview="done",
            outcome={"status": "succeeded", "receipt": f"receipt:{call_id}"},
        )

    outcomes = await store.list_tool_call_outcomes(
        session_id="s-1",
        tool_call_ids=["call-1", "call-1", "call-2", "missing"],
    )

    assert outcomes == {"call-1": {"status": "succeeded", "receipt": "receipt:call-1"}}


@pytest.mark.asyncio
async def test_list_interrupted_chat_runs_returns_restart_candidates(store: SessionStore):
    await store.record_chat_run_started(run_id="run-1", session_id="s-1")
    await store.record_chat_run_started(run_id="run-2", session_id="s-2")
    await store.record_chat_run_status(run_id="run-1", status="interrupted", stop_reason="server_restart")
    await store.record_chat_run_status(run_id="run-2", status="completed")

    rows = await store.list_interrupted_chat_runs()

    assert [(row["run_id"], row["session_id"]) for row in rows] == [("run-1", "s-1")]

    await store.record_chat_run_status(run_id="run-1", status="running")
    resumed = await store.get_chat_run("run-1")
    assert resumed["ended_at"] is None



@pytest.mark.asyncio
async def test_latest_session_checkpoint_uses_chat_run_last_seq(store: SessionStore):
    await store.record_chat_run_started("run-1", "sess-1")
    await store.record_chat_run_status("run-1", "running", last_seq=12)
    await store.record_chat_run_started("run-2", "sess-1")
    await store.record_chat_run_status("run-2", "running")
    await store.record_chat_run_started("run-other", "sess-2")
    await store.record_chat_run_status("run-other", "running", last_seq=99)

    assert await store.get_latest_session_checkpoint_seq("sess-1") == 12
    assert await store.get_latest_session_checkpoint_seq("missing") == 0


@pytest.mark.asyncio
async def test_marks_interrupted_chat_runs_on_startup(store: SessionStore):
    await store.record_chat_run_started("run-1", "sess-1")
    await store.record_chat_run_status("run-1", "running")

    changed = await store.mark_interrupted_chat_runs()
    run = await store.get_chat_run("run-1")

    assert changed == 1
    assert run is not None
    assert run["status"] == "interrupted"
    assert run["stop_reason"] == "server_restart"
    assert run["ended_at"] is not None


@pytest.mark.asyncio
async def test_starting_new_chat_run_interrupts_stale_foreground_runs(store: SessionStore):
    await store.record_chat_run_started("run-1", "sess-1")
    await store.record_chat_run_status("run-1", "running", last_seq=12)

    await store.record_chat_run_started("run-2", "sess-1")

    old_run = await store.get_chat_run("run-1")
    new_run = await store.get_chat_run("run-2")

    assert old_run is not None
    assert old_run["status"] == "interrupted"
    assert old_run["stop_reason"] == "superseded"
    assert old_run["error_code"] == "run_superseded"
    assert old_run["last_seq"] == 12
    assert old_run["ended_at"] is not None
    assert new_run is not None
    assert new_run["status"] == "pending"


@pytest.mark.asyncio
async def test_chat_run_id_collision_never_reopens_historical_run(store: SessionStore):
    await store.record_chat_run_started("same-run", "old-session")
    await store.record_chat_run_status("same-run", "completed")

    with pytest.raises(sqlite3.IntegrityError):
        await store.record_chat_run_started("same-run", "new-session")

    historical = await store.get_chat_run("same-run")
    assert historical is not None
    assert historical["session_id"] == "old-session"
    assert historical["status"] == "completed"


@pytest.mark.asyncio
async def test_background_agent_run_lifecycle(store: SessionStore):
    agent_ref = _agent_ref("sess-1", "bg-1", "research task")
    await _provision_agent_session(store, "sess-child-1", agent_ref)
    await store.record_background_agent_started(
        task_id="bg-1",
        agent_ref=agent_ref,
        session_id="sess-1",
        parent_run_id="run-1",
        parent_tool_call_id="call-background",
        child_session_id="sess-child-1",
        agent_type="background_research",
        wait=False,
        command="research task",
    )
    await store.record_background_agent_event(
        task_id="bg-1",
        session_id="sess-1",
        status="activity",
        detail="read files",
    )
    await store.record_background_agent_finished(
        task_id="bg-1",
        session_id="sess-1",
        status="completed",
        result_ref="bg_results/bg-1.txt",
        detail="done",
        result_text="full result",
    )

    runs = await store.list_background_agent_runs("sess-1")
    assert runs[0]["task_id"] == "bg-1"
    assert runs[0]["child_run_id"] == "bg-1"
    assert runs[0]["child_session_id"] == "sess-child-1"
    assert runs[0]["parent_tool_call_id"] == "call-background"
    assert runs[0]["agent_type"] == "background_research"
    assert runs[0]["wait"] is False
    assert runs[0]["status"] == "completed"
    assert runs[0]["result_ref"] == "bg_results/bg-1.txt"
    assert await store.get_background_agent_result("sess-1", "bg-1") == "full result"

    events = await store.list_background_agent_events("sess-1", after_seq=0)
    assert [e["status"] for e in events] == ["started", "activity", "completed"]
    assert events[-1]["terminal"] is True


@pytest.mark.asyncio
async def test_background_completion_claim_is_atomic_and_idempotent(store: SessionStore):
    agent_ref = _agent_ref("sess-1", "bg-1", "research task")
    await store.record_background_agent_started(
        task_id="bg-1",
        agent_ref=agent_ref,
        session_id="sess-1",
        parent_run_id="run-1",
        command="research task",
    )

    first = await store.claim_background_agent_completion(
        task_id="bg-1",
        session_id="sess-1",
        status="completed",
        result_ref="background://bg-1",
        result_text="first result",
        completion_id="bg:bg-1:completed",
    )
    await store.record_background_agent_started(
        task_id="bg-1",
        agent_ref=agent_ref,
        session_id="sess-1",
        parent_run_id="run-1",
        command="duplicate start",
    )
    second = await store.claim_background_agent_completion(
        task_id="bg-1",
        session_id="sess-1",
        status="failed",
        result_ref="background://bg-1",
        result_text="duplicate result",
        completion_id="bg:bg-1:failed",
    )

    assert first["claimed"] is True
    assert second["claimed"] is False
    assert second["completion_id"] == "bg:bg-1:completed"
    assert second["status"] == "completed"
    assert await store.get_background_agent_result("sess-1", "bg-1") == "first result"
    events = await store.list_background_agent_events("sess-1")
    assert [event["status"] for event in events] == ["started", "completed"]
    assert [row["completion_id"] for row in await store.list_undelivered_background_completions()] == [
        "bg:bg-1:completed"
    ]

    assert await store.mark_background_completion_delivered(
        session_id="sess-1",
        task_id="bg-1",
        completion_id="bg:bg-1:completed",
    )
    run = (await store.list_background_agent_runs("sess-1"))[0]
    events = await store.list_background_agent_events("sess-1")
    assert run["notified_at"] is not None
    assert events[-1]["delivered_at"] is not None
    assert await store.list_undelivered_background_completions() == []


@pytest.mark.asyncio
async def test_background_agent_task_ids_are_session_scoped(store: SessionStore):
    await store.record_background_agent_started(
        task_id="bg-1",
        agent_ref=_agent_ref("sess-1", "bg-1", "first"),
        session_id="sess-1",
        parent_run_id="run-1",
        command="first",
    )
    await store.record_background_agent_started(
        task_id="bg-1",
        agent_ref=_agent_ref("sess-2", "bg-1", "second"),
        session_id="sess-2",
        parent_run_id="run-2",
        command="second",
    )

    assert (await store.list_background_agent_runs("sess-1"))[0]["command"] == "first"
    assert (await store.list_background_agent_runs("sess-2"))[0]["command"] == "second"


@pytest.mark.asyncio
async def test_background_agent_cancel_request_is_session_scoped_and_evented(store: SessionStore):
    await store.record_background_agent_started(
        task_id="bg-1",
        agent_ref=_agent_ref("sess-1", "bg-1", "first"),
        session_id="sess-1",
        parent_run_id="run-1",
        command="first",
    )
    await store.record_background_agent_started(
        task_id="bg-1",
        agent_ref=_agent_ref("sess-2", "bg-1", "second"),
        session_id="sess-2",
        parent_run_id="run-2",
        command="second",
    )

    assert await store.request_background_agent_cancel("sess-1", "bg-1") is True

    assert (await store.list_background_agent_runs("sess-1"))[0]["status"] == "cancel_requested"
    assert (await store.list_background_agent_runs("sess-2"))[0]["status"] == "running"
    events = await store.list_background_agent_events("sess-1")
    assert [e["status"] for e in events] == ["started", "cancel_requested"]
    assert events[-1]["terminal"] is False


@pytest.mark.asyncio
async def test_background_agent_cancel_cascade_uses_durable_spawn_edges_and_is_idempotent(store: SessionStore):
    root_ref = _agent_ref("parent", "root")
    child_ref = _agent_ref("parent::child", "child")
    await _provision_agent_session(store, "parent::child", root_ref)
    await _provision_agent_session(store, "parent::grandchild", child_ref)
    await store.record_background_agent_started(
        task_id="root",
        agent_ref=root_ref,
        session_id="parent",
        parent_run_id="run",
        child_session_id="parent::child",
        command="root",
    )
    await store.record_background_agent_started(
        task_id="child",
        agent_ref=child_ref,
        session_id="parent::child",
        parent_run_id="run",
        child_session_id="parent::grandchild",
        command="child",
    )
    await store.record_background_agent_started(
        task_id="grandchild",
        agent_ref=_agent_ref("parent::grandchild", "grandchild"),
        session_id="parent::grandchild",
        parent_run_id="run",
        command="grandchild",
    )

    first = await store.request_background_agent_cancel_cascade(
        session_id="parent",
        task_id="root",
        actor="user",
        cause="user_cancelled",
        idempotency_key="cancel-1",
    )
    repeated = await store.request_background_agent_cancel_cascade(
        session_id="parent",
        task_id="root",
        actor="user",
        cause="user_cancelled",
        idempotency_key="cancel-1",
    )

    assert sorted(first) == [
        ("parent", "root"),
        ("parent::child", "child"),
        ("parent::grandchild", "grandchild"),
    ]
    assert repeated == []
    for session_id in ("parent", "parent::child", "parent::grandchild"):
        row = (await store.list_background_agent_runs(session_id))[0]
        assert row["status"] == "cancel_requested"
        assert row["cancel_actor"] == "user"
        assert row["terminal_cause"] == "user_cancelled"
        assert row["cancel_generation"] == 1


@pytest.mark.asyncio
async def test_late_descendant_inherits_existing_session_cancellation(store: SessionStore):
    root_ref = _agent_ref("parent", "root")
    late_ref = _agent_ref("parent::child", "late-child")
    await _provision_agent_session(store, "parent::child", root_ref)
    await _provision_agent_session(store, "parent::grandchild", late_ref)
    await store.record_background_agent_started(
        task_id="root",
        agent_ref=root_ref,
        session_id="parent",
        parent_run_id="run",
        child_session_id="parent::child",
        command="root",
    )
    await store.request_background_agent_cancel_cascade(
        session_id="parent",
        task_id="root",
        actor="user",
        cause="user_cancelled",
        idempotency_key="cancel-1",
    )

    disposition = await store.record_background_agent_started(
        task_id="late-child",
        agent_ref=late_ref,
        session_id="parent::child",
        parent_run_id="child-run",
        child_session_id="parent::grandchild",
        command="late",
    )

    assert disposition is BackgroundStartDisposition.CANCELLED
    row = (await store.list_background_agent_runs("parent::child"))[0]
    assert row["status"] == "cancelled"
    assert row["terminal_cause"] == "user_cancelled"
    assert row["completion_id"].startswith("cancel-before-start:")
    events = await store.list_background_agent_events("parent::child")
    assert [(event["status"], event["terminal"]) for event in events] == [("cancelled", True)]
    scope = await store.read_conn.execute_fetchall(
        "SELECT cause FROM execution_cancellation_scopes WHERE session_id = 'parent::grandchild'"
    )
    assert scope[0]["cause"] == "user_cancelled"


@pytest.mark.asyncio
async def test_awaited_child_completion_atomically_resolves_parent_suspension(store: SessionStore):
    agent_ref = _agent_ref("parent", "child", "research")
    await _provision_agent_session(store, "parent::child", agent_ref)
    await store.record_run_suspension(
        run_id="parent-run",
        session_id="parent",
        suspension_id="spawn-1",
        kind="subagent_result",
        payload={"task": "research"},
    )
    await store.record_background_agent_started(
        task_id="child",
        agent_ref=agent_ref,
        session_id="parent",
        parent_run_id="parent-run",
        parent_tool_call_id="tool-call",
        suspension_id="spawn-1",
        child_session_id="parent::child",
        wait=True,
        command="research",
    )

    await store.record_background_agent_finished(
        task_id="child",
        session_id="parent",
        status="completed",
        result_text="child result",
    )

    child = (await store.list_background_agent_runs("parent"))[0]
    suspension = await store.get_run_suspension(run_id="parent-run", suspension_id="spawn-1")
    assert child["completion_id"] == "bg:child:completed"
    assert suspension is not None
    assert suspension["status"] == "completed"
    assert suspension["resolution"] == {"status": "completed", "result": "child result"}













@pytest.mark.asyncio
async def test_marks_running_background_agents_interrupted_on_startup(store: SessionStore):
    await store.record_background_agent_started(
        task_id="bg-1",
        agent_ref=_agent_ref("sess-1", "bg-1", "research task"),
        session_id="sess-1",
        parent_run_id="run-1",
        command="research task",
    )
    await store.record_background_agent_event(
        task_id="bg-1",
        session_id="sess-1",
        status="activity",
        detail="read 12 files",
    )

    changed = await store.mark_interrupted_background_agent_runs()
    runs = await store.list_background_agent_runs("sess-1")

    assert changed == 1
    assert runs[0]["status"] == "interrupted"
    assert runs[0]["detail"] == "read 12 files"


@pytest.mark.asyncio
async def test_startup_preserves_durable_cancel_cause(store: SessionStore):
    await store.record_background_agent_started(
        task_id="bg-1",
        agent_ref=_agent_ref("sess-1", "bg-1", "research task"),
        session_id="sess-1",
        parent_run_id="run-1",
        command="research task",
    )
    await store.request_background_agent_cancel_cascade(
        session_id="sess-1",
        task_id="bg-1",
        actor="user",
        cause="user_cancelled",
        idempotency_key="cancel-1",
    )

    await store.mark_interrupted_background_agent_runs()
    run = (await store.list_background_agent_runs("sess-1"))[0]
    events = await store.list_background_agent_events("sess-1")

    assert run["status"] == "cancelled"
    assert run["detail"] == "user_cancelled"
    assert events[-1]["status"] == "cancelled"
    assert events[-1]["detail"] == "user_cancelled"


@pytest.mark.asyncio
async def test_startup_cancel_resolves_awaited_parent_suspension(store: SessionStore):
    agent_ref = _agent_ref("parent", "child", "research")
    await _provision_agent_session(store, "parent::child", agent_ref)
    await store.record_run_suspension(
        run_id="parent-run",
        session_id="parent",
        suspension_id="spawn-1",
        kind="subagent_result",
        payload={"child_run_id": "child"},
    )
    await store.record_background_agent_started(
        task_id="child",
        agent_ref=agent_ref,
        session_id="parent",
        parent_run_id="parent-run",
        suspension_id="spawn-1",
        child_session_id="parent::child",
        wait=True,
        command="research",
    )
    await store.request_background_agent_cancel_cascade(
        session_id="parent",
        task_id="child",
        actor="user",
        cause="user_cancelled",
        idempotency_key="cancel-1",
    )

    await store.mark_interrupted_background_agent_runs()

    suspension = await store.get_run_suspension(run_id="parent-run", suspension_id="spawn-1")
    assert suspension is not None
    assert suspension["status"] == "cancelled"
    assert suspension["resolution"] == {"status": "cancelled", "result": "user_cancelled"}
    row = (await store.list_background_agent_runs("parent"))[0]
    assert row["completion_id"].startswith("restart-cancel:")


@pytest.mark.asyncio
async def test_marks_running_agent_sessions_interrupted_on_startup(store: SessionStore):
    running = SessionState(
        session_id="parent::abc",
        started_at=datetime.now(UTC),
        session_type="agent",
        parent_session_id="parent",
        agent_status="running",
    )
    done = SessionState(
        session_id="parent::def",
        started_at=datetime.now(UTC),
        session_type="agent",
        parent_session_id="parent",
        agent_status="completed",
    )
    chat = SessionState(session_id="parent", started_at=datetime.now(UTC))
    await store.save_session(running, [{"role": "user", "content": "x"}])
    await store.save_session(done, [{"role": "user", "content": "x"}])
    await store.save_session(chat, [{"role": "user", "content": "x"}])

    changed = await store.mark_interrupted_agent_sessions()

    rows = {row["session_id"]: row for row in await store.list_sessions()}
    assert changed == 1
    assert rows["parent::abc"]["agent_status"] == "interrupted"
    # A finished agent session and a plain chat session are left untouched.
    assert rows["parent::def"]["agent_status"] == "completed"
    assert not rows["parent"]["agent_status"]


@pytest.mark.asyncio
async def test_session_events_round_trip_with_sequence(store: SessionStore):
    await store.record_session_event(
        StreamRecord(seq=7, session_id="sess-1", event=ThinkingEvent(status="processing")),
    )

    events = await store.list_session_events("sess-1", after_seq=6)

    assert [record.seq for record in events] == [7]
    assert events[0].session_id == "sess-1"
    assert isinstance(events[0].event, ThinkingEvent)
    assert events[0].event.status == "processing"
    assert await store.get_latest_session_event_seq("sess-1") == 7


@pytest.mark.asyncio
async def test_large_tool_result_event_spills_raw_content_to_manifest(store: SessionStore):
    raw = "raw evidence line\n" * ((RAW_TOOL_RESULT_INLINE_MAX_BYTES // len("raw evidence line\n")) + 10)
    await store.record_tool_call_started(
        run_id="run-1",
        session_id="sess-raw",
        tool_call_id="call-raw",
        tool_name="bash",
        action="read",
        scope="internal",
    )

    await store.record_session_event(
        StreamRecord(
            seq=12,
            session_id="sess-raw",
            event=ToolCallResultEvent(
                tool_call_id="call-raw",
                name="bash",
                content=raw,
                preview="raw evidence",
            ),
        )
    )

    rows = await store.read_conn.execute_fetchall("SELECT event_json FROM session_events WHERE session_id = 'sess-raw'")
    payload = json.loads(rows[0]["event_json"])
    assert payload["type"] == "TOOL_CALL_RESULT"
    assert payload["content"] != raw
    assert "raw_ref" in payload
    assert "sha256" not in payload["content"]
    assert "content_sha256" not in payload
    assert payload["content_bytes"] == len(raw.encode("utf-8"))
    assert len(rows[0]["event_json"]) < RAW_TOOL_RESULT_INLINE_MAX_BYTES

    manifest_rows = await store.read_conn.execute_fetchall(
        "SELECT session_id, tool_call_id, blob_path, compression FROM tool_results WHERE tool_result_id = ?",
        (payload["raw_ref"],),
    )
    assert len(manifest_rows) == 1
    manifest = manifest_rows[0]
    assert manifest["session_id"] == "sess-raw"
    assert manifest["tool_call_id"] == "call-raw"
    assert await asyncio.to_thread(read_raw_tool_result, manifest["blob_path"], compression=manifest["compression"]) == raw

    tool_calls = await store.list_tool_calls(run_id="run-1")
    assert tool_calls[0]["result_ref"] == payload["raw_ref"]

    events = await store.list_session_events("sess-raw", after_seq=0)
    assert events[0].event.tool_call_id == "call-raw"
    assert events[0].event.raw_ref == payload["raw_ref"]
    assert events[0].event.content != raw


@pytest.mark.asyncio
async def test_large_file_search_result_gets_short_retention(store: SessionStore):
    raw = "search evidence\n" * 5000
    before = datetime.now(UTC)

    await store.record_session_event(
        StreamRecord(
            seq=1,
            session_id="sess-search",
            event=ToolCallResultEvent(
                tool_call_id="call-search",
                name="file_search_text",
                content=raw,
            ),
        )
    )

    row = (
        await store.read_conn.execute_fetchall(
            "SELECT retention_class, expires_at FROM tool_results WHERE session_id = 'sess-search'"
        )
    )[0]
    assert row["retention_class"] == "search_temporary"
    expiry = datetime.fromisoformat(row["expires_at"])
    assert before + timedelta(days=6) < expiry < before + timedelta(days=8)


@pytest.mark.asyncio
async def test_tool_result_manifest_obeys_declared_expiry_and_prunes_durably(store: SessionStore):
    raw = "expiring evidence\n" * 5000
    await store.record_session_event(
        StreamRecord(
            seq=1,
            session_id="sess-expiring",
            event=ToolCallResultEvent(tool_call_id="call-expiring", name="bash", content=raw),
        )
    )
    rows = await store.read_conn.execute_fetchall(
        "SELECT tool_result_id, content_sha256 FROM tool_results WHERE session_id = 'sess-expiring'"
    )
    tool_result_id = rows[0]["tool_result_id"]
    content_sha256 = rows[0]["content_sha256"]
    future = datetime.now(UTC) + timedelta(hours=1)
    await store.conn.execute(
        "UPDATE tool_results SET retention_class = 'temporary', expires_at = ? WHERE tool_result_id = ?",
        (future.isoformat(), tool_result_id),
    )
    await store.conn.commit()

    assert await store.prune_expired_tool_results(now=future - timedelta(seconds=1)) == 0
    assert content_sha256 in await store.list_tool_result_content_hashes()

    after_expiry = datetime.now(UTC) - timedelta(seconds=1)
    await store.conn.execute(
        "UPDATE tool_results SET expires_at = ? WHERE tool_result_id = ?",
        (after_expiry.isoformat(), tool_result_id),
    )
    await store.conn.commit()
    assert await store.prune_expired_tool_results(now=after_expiry) == 1
    assert content_sha256 not in await store.list_tool_result_content_hashes()


@pytest.mark.asyncio
async def test_small_tool_result_event_stays_inline(store: SessionStore):
    await store.record_session_event(
        StreamRecord(
            seq=13,
            session_id="sess-small",
            event=ToolCallResultEvent(tool_call_id="call-small", name="file_read", content="small raw result"),
        )
    )

    rows = await store.read_conn.execute_fetchall(
        "SELECT event_json FROM session_events WHERE session_id = 'sess-small'"
    )
    payload = json.loads(rows[0]["event_json"])
    assert payload["content"] == "small raw result"
    assert "raw_ref" not in payload


@pytest.mark.asyncio
async def test_tool_result_live_data_stays_rich_while_durable_data_is_allowlisted(store: SessionStore):
    event = ToolCallResultEvent(
        tool_call_id="call-rich",
        name="mcp_search",
        content="small raw result",
        data={
            "structuredContent": {"results": [{"secret": "must-not-persist"}]},
            "_meta": {"bearer": "must-not-persist"},
            "matches": [{"text": "must-not-persist"}],
            "child_agent": {"child_run_id": "child-1", "wait": False},
            "workflow": "Audit",
            "workflow_id": "workflow-1",
            "usage": {"prompt": 2, "completion": 3, "total": 5},
            "cost": 0.01,
            "html": "<p>Safe widget</p>",
            "title": "Widget",
            "mode": "display",
        },
        source_refs=[
            {
                "provider": "demo",
                "kind": "search_result",
                "ref": "doc-1",
                "title": "Document",
            }
        ],
    )

    live_payload = json.loads(event.to_sse()["data"])
    assert live_payload["data"]["structuredContent"]["results"][0]["secret"] == "must-not-persist"
    assert live_payload["data"]["_meta"]["bearer"] == "must-not-persist"

    await store.record_session_event(StreamRecord(seq=15, session_id="sess-rich", event=event))

    rows = await store.read_conn.execute_fetchall(
        "SELECT event_json FROM session_events WHERE session_id = 'sess-rich'"
    )
    payload = json.loads(rows[0]["event_json"])
    assert payload["source_refs"] == live_payload["source_refs"]
    assert payload["data"] == {
        "child_agent": {"child_run_id": "child-1", "wait": False},
        "workflow": "Audit",
        "workflow_id": "workflow-1",
        "usage": {"prompt": 2, "completion": 3, "total": 5},
        "cost": 0.01,
        "html": "<p>Safe widget</p>",
        "title": "Widget",
        "mode": "display",
    }
    assert "must-not-persist" not in rows[0]["event_json"]


@pytest.mark.asyncio
async def test_research_provenance_round_trips_in_durable_tool_result_data(store: SessionStore):
    provenance = {
        "query": "audit harness",
        "window": {"depth": "deep", "current_depth": 0, "max_depth": 3},
        "derivation": {
            "research_tool_call_id": "research-1",
            "child_run_id": "child-1",
            "child_tool_call_ids": ["child-call-1"],
        },
        "workspace_ref": "research-1:_provenance.json",
    }
    await store.record_session_event(
        StreamRecord(
            seq=17,
            session_id="sess-provenance",
            event=ToolCallResultEvent(
                tool_call_id="research-1",
                name="research",
                content="summary",
                data={"provenance": provenance, "arbitrary": {"drop": True}},
            ),
        )
    )

    rows = await store.read_conn.execute_fetchall(
        "SELECT event_json FROM session_events WHERE session_id = 'sess-provenance'"
    )
    payload = json.loads(rows[0]["event_json"])
    assert payload["data"] == {"provenance": provenance}


@pytest.mark.asyncio
async def test_durable_tool_result_data_is_allowlisted_without_a_tool_call_id(store: SessionStore):
    event = ToolCallResultEvent(
        tool_call_id="",
        name="mcp_search",
        content="small result",
        data={
            "structuredContent": {"secret": "must-not-persist"},
            "child_agent": {"child_run_id": "child-1"},
        },
    )

    await store.record_session_event(StreamRecord(seq=16, session_id="sess-no-call", event=event))

    rows = await store.read_conn.execute_fetchall(
        "SELECT event_json FROM session_events WHERE session_id = 'sess-no-call'"
    )
    payload = json.loads(rows[0]["event_json"])
    assert payload["data"] == {"child_agent": {"child_run_id": "child-1"}}
    assert "must-not-persist" not in rows[0]["event_json"]


@pytest.mark.asyncio
async def test_offloaded_tool_result_event_registers_existing_raw_blob(store: SessionStore):
    raw = "offloaded raw line\n" * 5000
    blob = persist_raw_tool_result(raw)

    await store.record_session_event(
        StreamRecord(
            seq=14,
            session_id="sess-offload",
            event=ToolCallResultEvent(
                tool_call_id="call-offload",
                name="bash",
                content="compact head/tail preview",
                preview="compact",
                data=blob.to_internal_data(),
            ),
        )
    )

    rows = await store.read_conn.execute_fetchall(
        "SELECT event_json FROM session_events WHERE session_id = 'sess-offload'"
    )
    payload = json.loads(rows[0]["event_json"])
    assert payload["content"] == "compact head/tail preview"
    assert payload["raw_ref"].startswith("tr_")
    assert "content_sha256" not in payload
    assert RAW_TOOL_RESULT_DATA_KEY not in payload.get("data", {})

    manifest_rows = await store.read_conn.execute_fetchall(
        "SELECT blob_path, compression FROM tool_results WHERE tool_result_id = ?",
        (payload["raw_ref"],),
    )
    assert len(manifest_rows) == 1
    manifest = manifest_rows[0]
    assert await asyncio.to_thread(read_raw_tool_result, manifest["blob_path"], compression=manifest["compression"]) == raw

    # Replaying the same result under a new transport sequence must reuse the
    # manifest ID instead of emitting a pointer to an INSERT-ignored row.
    await store.record_session_event(
        StreamRecord(
            seq=15,
            session_id="sess-offload",
            event=ToolCallResultEvent(
                tool_call_id="call-offload",
                name="bash",
                content="compact head/tail preview",
                preview="compact",
                data=blob.to_internal_data(),
            ),
        )
    )
    replay_rows = await store.read_conn.execute_fetchall(
        "SELECT event_json FROM session_events WHERE session_id = 'sess-offload' ORDER BY seq"
    )
    replay_refs = [json.loads(row["event_json"])["raw_ref"] for row in replay_rows]
    assert replay_refs == [payload["raw_ref"], payload["raw_ref"]]
    manifest_rows = await store.read_conn.execute_fetchall(
        "SELECT tool_result_id FROM tool_results WHERE session_id = 'sess-offload'"
    )
    assert [row["tool_result_id"] for row in manifest_rows] == [payload["raw_ref"]]


@pytest.mark.asyncio
async def test_session_resume_rehydrates_pruned_offload_file_from_manifest(store: SessionStore, tmp_path, monkeypatch):
    import arden.core.tool_result_files as tool_result_files

    monkeypatch.setattr(tool_result_files, "RESULTS_BASE", tmp_path / "tool-results")
    session_id = "sess-rehydrate"
    tool_call_id = "call-rehydrate"
    raw = "durable raw result\n" * 5000
    blob = persist_raw_tool_result(raw)
    await store.record_session_event(
        StreamRecord(
            seq=1,
            session_id=session_id,
            event=ToolCallResultEvent(
                tool_call_id=tool_call_id,
                name="bash",
                content="bounded preview",
                preview="bounded",
                data=blob.to_internal_data(),
            ),
        )
    )
    path = tool_result_files.result_file_path(session_id, tool_call_id)
    state = _make_state(session_id=session_id)
    await store.save_session(
        state,
        [
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "client_id": "tool-message",
                "content": f"bounded preview\nUse file_read(path={str(path)!r}, offset=N).",
            }
        ],
    )
    assert not path.exists()

    loaded = await store.load_session(session_id)

    assert loaded is not None
    assert path.read_text() == raw


@pytest.mark.asyncio
async def test_session_resume_rehydrates_distinct_raw_and_payload_files(store: SessionStore, tmp_path, monkeypatch):
    import arden.core.tool_result_files as tool_result_files

    monkeypatch.setattr(tool_result_files, "RESULTS_BASE", tmp_path / "tool-results")
    session_id = "sess-rehydrate-payload"
    tool_call_id = "call-rehydrate-payload"
    raw = "durable raw result\n" * 5_000
    exact_payload = ToolResult(content=raw, preview="raw").serialized_payload()
    blob = persist_raw_tool_result(exact_payload)
    await store.record_session_event(
        StreamRecord(
            seq=1,
            session_id=session_id,
            event=ToolCallResultEvent(
                tool_call_id=tool_call_id,
                name="bash",
                content="bounded preview",
                preview="bounded",
                data=blob.to_internal_data(),
            ),
        )
    )
    raw_path = tool_result_files.result_file_path(session_id, tool_call_id)
    payload_path = tool_result_files.result_payload_file_path(session_id, tool_call_id)
    state = _make_state(session_id=session_id)
    await store.save_session(
        state,
        [
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "client_id": "tool-message",
                "content": (
                    f"bounded preview\nUse file_read(path={str(raw_path)!r}). "
                    f"Exact payload: file_read(path={str(payload_path)!r})."
                ),
            }
        ],
    )

    loaded = await store.load_session(session_id)

    assert loaded is not None
    assert raw_path.read_text() == raw
    assert payload_path.read_text() == exact_payload


@pytest.mark.asyncio
async def test_session_resume_rejects_unreadable_durable_tool_result(store: SessionStore, tmp_path, monkeypatch):
    import arden.core.tool_result_files as tool_result_files

    monkeypatch.setattr(tool_result_files, "RESULTS_BASE", tmp_path / "tool-results")
    session_id = "sess-corrupt-result"
    tool_call_id = "call-corrupt-result"
    blob = persist_raw_tool_result("durable result")
    await store.record_session_event(
        StreamRecord(
            seq=1,
            session_id=session_id,
            event=ToolCallResultEvent(
                tool_call_id=tool_call_id,
                name="bash",
                content="bounded preview",
                preview="bounded",
                data=blob.to_internal_data(),
            ),
        )
    )
    raw_path = tool_result_files.result_file_path(session_id, tool_call_id)
    await store.save_session(
        _make_state(session_id=session_id),
        [
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "client_id": "tool-message",
                "content": f"Use file_read(path={str(raw_path)!r}).",
            }
        ],
    )
    await store.conn.execute(
        "UPDATE tool_results SET blob_path = ? WHERE session_id = ? AND tool_call_id = ?",
        (str(tmp_path / "missing.gz"), session_id, tool_call_id),
    )
    await store.conn.commit()

    with pytest.raises(SessionDataCorruptionError, match="cannot be rehydrated"):
        await store.load_session(session_id)


@pytest.mark.asyncio
async def test_active_search_result_pins_expired_manifest_until_context_drops_it(
    store: SessionStore,
    tmp_path,
    monkeypatch,
):
    import arden.core.tool_result_files as tool_result_files

    monkeypatch.setattr(tool_result_files, "RESULTS_BASE", tmp_path / "tool-results")
    session_id = "sess-active-search"
    tool_call_id = "call-search"
    raw = "search evidence\n" * 5000
    blob = persist_raw_tool_result(raw)
    await store.record_session_event(
        StreamRecord(
            seq=1,
            session_id=session_id,
            event=ToolCallResultEvent(
                tool_call_id=tool_call_id,
                name="file_search_text",
                content="bounded search preview",
                preview="bounded",
                data=blob.to_internal_data(),
            ),
        )
    )
    path = tool_result_files.result_file_path(session_id, tool_call_id)
    active_tool_message = {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "client_id": "active-search-result",
        "content": f"bounded preview\nUse file_read(path={str(path)!r}, offset=N).",
    }
    state = _make_state(session_id=session_id)
    await store.save_session(state, [active_tool_message])
    expired = datetime.now(UTC) - timedelta(days=1)
    await store.conn.execute(
        "UPDATE tool_results SET expires_at = ? WHERE session_id = ? AND tool_call_id = ?",
        (expired.isoformat(), session_id, tool_call_id),
    )
    await store.conn.commit()

    assert await store.prune_expired_tool_results(now=datetime.now(UTC)) == 0
    loaded = await store.load_session(session_id)
    assert loaded is not None
    assert path.read_text() == raw

    await store.save_session(state, [{"role": "user", "content": "new epoch", "client_id": "new-user"}])
    assert await store.prune_expired_tool_results(now=datetime.now(UTC)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("broken_projection", ["{broken", "{}"])
async def test_invalid_active_projection_fails_closed_when_pruning_tool_results(
    store: SessionStore,
    tmp_path,
    monkeypatch,
    broken_projection: str,
):
    import arden.core.raw_tool_results as raw_tool_results
    import arden.core.tool_result_files as tool_result_files

    monkeypatch.setattr(tool_result_files, "RESULTS_BASE", tmp_path / "tool-results")
    monkeypatch.setattr(raw_tool_results, "RAW_TOOL_RESULTS_BASE", tmp_path / "raw-results")
    session_id = "sess-invalid-projection"
    tool_call_id = "call-invalid-projection"
    raw = "recoverable evidence\n" * 500
    blob = persist_raw_tool_result(raw)
    await store.record_session_event(
        StreamRecord(
            seq=1,
            session_id=session_id,
            event=ToolCallResultEvent(
                tool_call_id=tool_call_id,
                name="file_search_text",
                content="bounded preview",
                preview="bounded",
                data=blob.to_internal_data(),
            ),
        )
    )
    path = tool_result_files.result_file_path(session_id, tool_call_id)
    state = _make_state(session_id=session_id)
    await store.save_session(
        state,
        [
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "client_id": "active-result",
                "content": f"bounded preview\nUse file_read(path={str(path)!r}).",
            }
        ],
    )
    await store.conn.execute(
        "UPDATE tool_results SET expires_at = ? WHERE session_id = ? AND tool_call_id = ?",
        ((datetime.now(UTC) - timedelta(days=1)).isoformat(), session_id, tool_call_id),
    )
    await store.conn.execute(
        "UPDATE sessions SET messages = ? WHERE session_id = ?",
        (broken_projection, session_id),
    )
    await store.conn.commit()
    path.unlink(missing_ok=True)

    assert await store.prune_expired_tool_results(now=datetime.now(UTC)) == 0
    recovered = await store.load_session(session_id)
    assert recovered is not None
    assert recovered.messages[0]["tool_call_id"] == tool_call_id
    assert path.read_text() == raw


@pytest.mark.asyncio
async def test_compacted_result_ref_survives_branch_and_source_deletion(
    store: SessionStore,
    tmp_path,
    monkeypatch,
):
    import arden.core.raw_tool_results as raw_tool_results
    import arden.core.tool_result_files as tool_result_files
    import arden.services.session as session_service_module

    monkeypatch.setattr(tool_result_files, "RESULTS_BASE", tmp_path / "tool-results")
    monkeypatch.setattr(raw_tool_results, "RAW_TOOL_RESULTS_BASE", tmp_path / "raw-results")
    source_id = "source-with-offload"
    tool_call_id = "call-compacted-search"
    raw = "compacted search evidence\n" * 5_000
    blob = persist_raw_tool_result(raw)
    await store.record_session_event(
        StreamRecord(
            seq=1,
            session_id=source_id,
            event=ToolCallResultEvent(
                tool_call_id=tool_call_id,
                name="file_search_text",
                content="bounded search preview",
                preview="bounded",
                data=blob.to_internal_data(),
            ),
        )
    )
    source_path = tool_result_files.result_file_path(source_id, tool_call_id)
    original = [
        {"role": "system", "content": "system", "client_id": "sys"},
        {"role": "user", "content": "search", "client_id": "u-1"},
        {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": f"bounded preview\nUse file_read(path={str(source_path)!r}).",
            "client_id": "tool-1",
        },
        {"role": "assistant", "content": "tail", "client_id": "a-1"},
    ]
    source_state = _make_state(source_id, name="Source")
    await store.save_session(source_state, original)
    compacted = _build_compacted_messages(
        original,
        1,
        3,
        f"Search evidence remains at file_read(path={str(source_path)!r}).",
    )
    await store.save_session(source_state, compacted)
    expired = datetime.now(UTC) - timedelta(days=1)
    await store.conn.execute(
        "UPDATE tool_results SET expires_at = ? WHERE session_id = ? AND tool_call_id = ?",
        (expired.isoformat(), source_id, tool_call_id),
    )
    await store.conn.commit()

    assert compacted[1]["compaction"]["tool_result_refs"] == [tool_call_id]
    assert await store.prune_expired_tool_results(now=datetime.now(UTC)) == 0

    def fail_cache_copy(*_args):
        raise OSError("simulated branch cache miss")

    monkeypatch.setattr(session_service_module, "clone_result_files", fail_cache_copy)

    service = SessionService(store)
    branch_state = await service.branch(source_id, name="Independent branch")
    assert branch_state is not None
    branch_id = branch_state.session_id
    branch_path = tool_result_files.result_file_path(branch_id, tool_call_id)
    branched = await service.load(branch_id)
    assert branched is not None
    assert branched.messages[1]["compaction"]["reason"] == "session_branch"
    assert branched.messages[1]["compaction"]["tool_result_refs"] == [tool_call_id]
    assert str(branch_path) in branched.messages[1]["content"]
    assert str(source_path) not in branched.messages[1]["content"]
    assert await store.prune_expired_tool_results(now=datetime.now(UTC)) == 0

    assert await service.permanently_delete_current(source_id)
    branch_path.unlink(missing_ok=True)
    payload_path = tool_result_files.result_payload_file_path(branch_id, tool_call_id)
    payload_path.unlink(missing_ok=True)

    restored = await service.load(branch_id)

    assert restored is not None
    assert branch_path.read_text() == raw
    branch_manifests = await store.read_conn.execute_fetchall(
        "SELECT tool_result_id FROM tool_results WHERE session_id = ? AND tool_call_id = ?",
        (branch_id, tool_call_id),
    )
    assert len(branch_manifests) == 1


@pytest.mark.asyncio
async def test_compacted_background_result_survives_branch_and_source_deletion(store: SessionStore):
    source_id = "source-with-background-result"
    task_id = "bg-branch-result"
    agent_ref = _agent_ref(source_id, task_id, "research")
    await _provision_agent_session(store, "source-child", agent_ref)
    await store.record_background_agent_started(
        task_id=task_id,
        agent_ref=agent_ref,
        session_id=source_id,
        parent_run_id="source-run",
        parent_tool_call_id="source-call",
        child_session_id="source-child",
        wait=True,
        command="research",
        spawn_spec='{"task":"research"}',
    )
    await store.record_background_agent_finished(
        task_id=task_id,
        session_id=source_id,
        status="completed",
        result_ref=f"background://{agent_ref}",
        result_text="durable branch evidence",
    )
    original = [
        {"role": "system", "content": "system", "client_id": "sys"},
        {
            "role": "user",
            "content": "hidden completion",
            "client_id": f"bg:{task_id}:completed",
            "background_result_ref": agent_ref,
        },
        {"role": "assistant", "content": "tail", "client_id": "a-1"},
    ]
    compacted = _build_compacted_messages(original, 1, 2, "Background research completed.")
    await store.save_session(_make_state(source_id, name="Source"), compacted)

    service = SessionService(store)
    branch_state = await service.branch(source_id, name="Independent branch")
    assert branch_state is not None
    branch_id = branch_state.session_id
    branched = await service.load(branch_id)
    assert branched is not None
    assert branched.messages[1]["compaction"]["background_result_refs"] == [agent_ref]

    cloned = await store.get_background_agent_run(branch_id, task_id)
    assert cloned is not None
    assert cloned["agent_ref"] == agent_ref
    assert cloned["parent_run_id"] is None
    assert cloned["parent_tool_call_id"] is None
    assert cloned["child_session_id"] is None
    assert cloned["wait"] is False
    assert cloned["notified_at"] is not None
    assert cloned["completion_id"].startswith(f"branch:{branch_id}:")
    assert cloned["spawn_spec"] is None
    assert await store.list_background_agent_events(branch_id) == []

    assert await service.permanently_delete_current(source_id)
    assert await store.get_background_agent_result(branch_id, task_id) == "durable branch evidence"
    assert await store.get_background_agent_result_by_ref(branch_id, agent_ref) == "durable branch evidence"
    assert await store.list_undelivered_background_completions() == []
    assert await store.list_respawnable_background_agent_runs(max_attempts=3) == []


@pytest.mark.asyncio
async def test_branch_rolls_back_when_referenced_background_result_is_missing(
    store: SessionStore,
    monkeypatch,
):
    source_id = "source-with-missing-background-result"
    missing_task_id = "bg-missing"
    missing_agent_ref = _agent_ref(source_id, missing_task_id)
    tool_call_id = "call-before-rollback"
    blob = persist_raw_tool_result("branch rollback evidence")
    await store.record_session_event(
        StreamRecord(
            seq=1,
            session_id=source_id,
            event=ToolCallResultEvent(
                tool_call_id=tool_call_id,
                name="file_search_text",
                content="bounded preview",
                preview="bounded preview",
                data=blob.to_internal_data(),
            ),
        )
    )
    source_state = _make_state(source_id, name="Source")
    messages = [
        {"role": "system", "content": "system", "client_id": "sys"},
        {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": "bounded preview",
            "client_id": "tool-before-rollback",
        },
        {
            "role": "user",
            "content": "hidden completion with a missing durable result",
            "client_id": f"bg:{missing_task_id}:completed",
            "background_result_ref": missing_agent_ref,
        },
    ]
    await store.save_session(source_state, messages)
    target_state = _make_state("rolled-back-branch", name="Branch")
    service = SessionService(store)
    monkeypatch.setattr(service, "create", lambda **_kwargs: target_state)

    with pytest.raises(ValueError, match="background results unavailable"):
        await service.branch(source_id)

    assert await store.load_session(target_state.session_id) is None
    assert not await store.read_conn.execute_fetchall(
        "SELECT 1 FROM session_messages WHERE session_id = ?",
        (target_state.session_id,),
    )
    assert not await store.read_conn.execute_fetchall(
        "SELECT 1 FROM background_agent_runs WHERE session_id = ?",
        (target_state.session_id,),
    )
    assert not await store.read_conn.execute_fetchall(
        "SELECT 1 FROM tool_results WHERE session_id = ?",
        (target_state.session_id,),
    )


@pytest.mark.asyncio
async def test_save_updates_existing_session(store: SessionStore):
    state = _make_state()
    first = {"role": "user", "content": "First"}
    await store.save_session(state, [first])
    await store.save_session(
        state,
        [
            first,
            {"role": "assistant", "content": "Reply"},
        ],
    )

    loaded = await store.load_session("test-session")
    assert len(loaded.messages) == 2


@pytest.mark.asyncio
async def test_list_sessions(store: SessionStore):
    for i in range(3):
        state = _make_state(f"session-{i}", name=f"Chat {i}")
        await store.save_session(state, [{"role": "user", "content": f"msg {i}"}])

    sessions = await store.list_sessions(limit=10)
    assert len(sessions) == 3
    assert all("session_id" in s for s in sessions)


@pytest.mark.asyncio
async def test_load_nonexistent_returns_none(store: SessionStore):
    loaded = await store.load_session("does-not-exist")
    assert loaded is None


@pytest.mark.asyncio
async def test_get_latest_id(store: SessionStore):
    await store.save_session(_make_state("old"), [])
    await store.save_session(_make_state("new"), [])

    latest = await store.get_latest_id()
    assert latest == "new"


@pytest.mark.asyncio
async def test_archive_and_restore(store: SessionStore):
    state = _make_state()
    await store.save_session(state, [{"role": "user", "content": "test"}])

    assert await store.archive_session("test-session")

    # Archived sessions don't appear in active list
    active = await store.list_sessions()
    assert not any(s["session_id"] == "test-session" for s in active)

    # But appear in archived list
    archived = await store.list_archived_sessions()
    assert any(s["session_id"] == "test-session" for s in archived)

    # Restore
    assert await store.restore_session("test-session")
    active = await store.list_sessions()
    assert any(s["session_id"] == "test-session" for s in active)


@pytest.mark.asyncio
async def test_save_stamps_created_at(store: SessionStore):
    state = _make_state()
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    await store.save_session(state, messages)

    loaded = await store.load_session("test-session")
    assert loaded is not None
    assert all(m.get("created_at") for m in loaded.messages)


@pytest.mark.asyncio
async def test_update_progress_upserts_for_brand_new_session(store: SessionStore):
    """Regression: a fresh session's first save_progress (called by
    submit_chat_message before the agent starts) used to silently no-op
    because the SQL was UPDATE-only and the row didn't exist yet. The
    user-typed message would then be invisible if the user switched
    sessions and came back before the run's first step finished."""
    state = _make_state("brand-new")
    messages = [
        {"role": "user", "content": "hi", "client_id": "cid-1"},
    ]
    await store.update_progress(state, messages)

    loaded = await store.load_session("brand-new")
    assert loaded is not None
    assert loaded.messages[0]["content"] == "hi"
    assert loaded.messages[0]["client_id"] == "cid-1"
    assert loaded.active_message_count == 1


@pytest.mark.asyncio
async def test_update_progress_keeps_metadata_on_existing_session(store: SessionStore):
    """Mid-run checkpoints must not clobber metadata that the final save
    sets (e.g. last_input_tokens used for compaction)."""
    state = _make_state("with-meta")
    user_message = {"role": "user", "content": "hi"}
    await store.save_session(
        state,
        [user_message],
        metadata={"last_input_tokens": 1234},
    )
    await store.update_progress(
        state,
        [
            user_message,
            {"role": "assistant", "content": "yo"},
        ],
    )
    loaded = await store.load_session("with-meta")
    assert loaded is not None
    assert loaded.last_input_tokens == 1234
    assert len(loaded.messages) == 2
    assert loaded.active_message_count == 2


@pytest.mark.asyncio
async def test_save_preserves_existing_created_at(store: SessionStore):
    state = _make_state()
    fixed = "2024-01-01T00:00:00+00:00"
    messages = [
        {"role": "user", "content": "hello", "created_at": fixed},
        {"role": "assistant", "content": "hi"},
    ]
    await store.save_session(state, messages)
    loaded = await store.load_session("test-session")
    assert loaded is not None
    assert loaded.messages[0]["created_at"] == fixed
    assert loaded.messages[1]["created_at"] != fixed


@pytest.mark.asyncio
async def test_metadata_round_trip(store: SessionStore):
    state = _make_state()
    metadata = {"last_input_tokens": 1234}
    await store.save_session(state, [], metadata=metadata)

    loaded = await store.load_session("test-session")
    assert loaded.last_input_tokens == 1234


@pytest.mark.asyncio
async def test_session_messages_preserve_raw_transcript_across_compaction(store: SessionStore):
    state = _make_state()
    original = [
        {"role": "user", "content": "first", "client_id": "u-1"},
        {"role": "assistant", "content": "reply", "client_id": "a-1"},
        {"role": "user", "content": "second", "client_id": "u-2"},
    ]
    await store.save_session(state, original)

    compacted = [
        {"role": "assistant", "content": "Summary of earlier chat.", "client_id": "summary-1"},
        original[-1],
    ]
    await store.save_session(state, compacted)

    page = await store.list_session_messages("test-session", limit=10)

    assert [row["message"]["content"] for row in page["messages"]] == [
        "first",
        "reply",
        "second",
        "Summary of earlier chat.",
    ]
    assert page["has_more_before"] is False
    assert page["has_more_after"] is False


@pytest.mark.asyncio
async def test_checkpoint_reconstructs_active_context_from_immutable_transcript(store: SessionStore):
    state = _make_state()
    original = [
        {"role": "system", "content": "system", "client_id": "sys"},
        {"role": "user", "content": "first", "client_id": "u-1"},
        {"role": "assistant", "content": "reply", "client_id": "a-1"},
        {"role": "user", "content": "second", "client_id": "u-2"},
        {
            "role": "assistant",
            "content": "reply 2",
            "client_id": "a-2",
            "anthropic_content": [{"type": "text", "text": "reply 2"}],
            "provider_tool_calls": [
                {
                    "id": "tsc-2",
                    "name": "tool_search",
                    "arguments": {"tools": ["wiki_read_page"]},
                    "result": "Matched tools: wiki_read_page",
                    "done": True,
                    "loaded_tool_names": ["wiki_read_page"],
                }
            ],
            "openai_response_items": [
                {"id": "tsc-2", "type": "tool_search_call", "status": "completed"},
                {
                    "type": "tool_search_output",
                    "tools": [{"type": "function", "name": "wiki_read_page"}],
                },
            ],
        },
    ]
    await store.save_session(state, original)
    before_rows = await store.read_conn.execute_fetchall(
        "SELECT message_id, seq, message_json FROM session_messages WHERE session_id = ? ORDER BY seq",
        (state.session_id,),
    )
    before = {row["message_id"]: (row["seq"], row["message_json"]) for row in before_rows}

    compacted = _build_compacted_messages(original, 1, 3, "first summary")
    await store.save_session(state, compacted)

    # A malformed active projection recovers from the durable checkpoint.
    # A valid [] is an explicit user clear and must never resurrect history.
    await store.conn.execute("UPDATE sessions SET messages = '{broken' WHERE session_id = ?", (state.session_id,))
    await store.conn.commit()
    loaded = await store.load_session(state.session_id)
    assert loaded is not None
    assert [message["content"] for message in loaded.messages] == [
        "system",
        "[Session State Handoff]\nfirst summary",
        "second",
        "reply 2",
    ]
    assert loaded.messages[-1]["anthropic_content"] == [{"type": "text", "text": "reply 2"}]
    assert loaded.messages[-1]["provider_tool_calls"][0]["loaded_tool_names"] == ["wiki_read_page"]
    assert [item["type"] for item in loaded.messages[-1]["openai_response_items"]] == [
        "tool_search_call",
        "tool_search_output",
    ]

    after_rows = await store.read_conn.execute_fetchall(
        "SELECT message_id, seq, message_json FROM session_messages WHERE session_id = ? ORDER BY seq",
        (state.session_id,),
    )
    after = {row["message_id"]: (row["seq"], row["message_json"]) for row in after_rows}
    assert {message_id: after[message_id] for message_id in before} == before
    assert len(after_rows) == len(before_rows) + 1

    compactions = await store.list_chat_compactions(state.session_id)
    assert len(compactions) == 1
    assert compactions[0]["compaction_id"] == compacted[1]["message_id"]
    assert compactions[0]["messages_before"] == len(original)
    assert compactions[0]["messages_after"] == len(compacted)


@pytest.mark.asyncio
async def test_repeated_checkpoints_reconstruct_latest_summary_and_ordered_tail(store: SessionStore):
    state = _make_state()
    original = [
        {"role": "system", "content": "system", "client_id": "sys"},
        {"role": "user", "content": "one", "client_id": "u-1"},
        {"role": "assistant", "content": "one reply", "client_id": "a-1"},
        {"role": "user", "content": "two", "client_id": "u-2"},
        {"role": "assistant", "content": "two reply", "client_id": "a-2"},
    ]
    await store.save_session(state, original)
    first = _build_compacted_messages(original, 1, 3, "summary one")
    await store.save_session(state, first)

    third_turn = [
        {"role": "user", "content": "three", "client_id": "u-3"},
        {"role": "assistant", "content": "three reply", "client_id": "a-3"},
    ]
    first_with_tail = [*first, *third_turn]
    await store.save_session(state, first_with_tail)
    second = _build_compacted_messages(first_with_tail, 1, 4, "summary two")
    await store.save_session(state, second)

    loaded = await store.load_session(state.session_id)
    assert loaded is not None
    assert [message["content"] for message in loaded.messages] == [
        "system",
        "[Session State Handoff]\nsummary two",
        "three",
        "three reply",
    ]
    assert [row["compaction_id"] for row in await store.list_chat_compactions(state.session_id)] == [
        first[1]["message_id"],
        second[1]["message_id"],
    ]


@pytest.mark.asyncio
async def test_valid_empty_snapshot_never_resurrects_immutable_transcript(store: SessionStore):
    state = _make_state()
    await store.save_session(
        state,
        [
            {"role": "user", "content": "old request", "client_id": "u-1"},
            {"role": "assistant", "content": "old reply", "client_id": "a-1"},
        ],
    )
    await store.conn.execute(
        "UPDATE sessions SET messages = '[]', active_message_count = 0 WHERE session_id = ?",
        (state.session_id,),
    )
    await store.conn.commit()

    loaded = await store.load_session(state.session_id)
    raw_rows = await store.read_conn.execute_fetchall(
        "SELECT message_id FROM session_messages WHERE session_id = ? ORDER BY seq",
        (state.session_id,),
    )

    assert loaded is not None and loaded.messages == []
    assert [row["message_id"] for row in raw_rows] == ["u-1", "a-1"]


@pytest.mark.asyncio
async def test_repeated_checkpoint_reconstruction_survives_cache_corruption_and_restart(tmp_path: Path):
    db = tmp_path / "checkpoint-restart.db"
    conn = await database.connect(db)
    read_conn = await database.connect(db, readonly=True)
    first_store = SessionStore(conn, read_conn)
    await first_store.init_schema()
    state = _make_state("checkpoint-restart")
    original = [
        {"role": "system", "content": "system", "client_id": "sys"},
        {"role": "user", "content": "one", "client_id": "u-1"},
        {"role": "assistant", "content": "one reply", "client_id": "a-1"},
        {"role": "user", "content": "two", "client_id": "u-2"},
        {"role": "assistant", "content": "two reply", "client_id": "a-2"},
    ]
    await first_store.save_session(state, original)
    first = _build_compacted_messages(original, 1, 3, "summary one")
    await first_store.save_session(state, first)
    first_with_tail = [
        *first,
        {"role": "user", "content": "three", "client_id": "u-3"},
        {"role": "assistant", "content": "three reply", "client_id": "a-3"},
    ]
    await first_store.save_session(state, first_with_tail)
    second = _build_compacted_messages(first_with_tail, 1, 4, "summary two")
    await first_store.save_session(state, second)
    await conn.execute("UPDATE sessions SET messages = '{broken' WHERE session_id = ?", (state.session_id,))
    await conn.commit()
    await read_conn.close()
    await conn.close()

    restarted_conn = await database.connect(db)
    restarted_read = await database.connect(db, readonly=True)
    restarted = SessionStore(restarted_conn, restarted_read)
    try:
        await restarted.init_schema()
        loaded = await restarted.load_session(state.session_id)

        assert loaded is not None
        assert [message["content"] for message in loaded.messages] == [
            "system",
            "[Session State Handoff]\nsummary two",
            "three",
            "three reply",
        ]
        assert loaded.messages[1]["message_id"] == second[1]["message_id"]
    finally:
        await restarted_read.close()
        await restarted_conn.close()


@pytest.mark.asyncio
async def test_legacy_handoff_without_tail_ids_uses_compatibility_projection(store: SessionStore):
    state = _make_state()
    original = [
        {"role": "system", "content": "system", "client_id": "sys"},
        {"role": "user", "content": "old", "client_id": "u-1"},
        {"role": "assistant", "content": "tail", "client_id": "a-1"},
    ]
    await store.save_session(state, original)
    legacy_projection = [
        original[0],
        {
            "role": "assistant",
            "content": "[Session State Handoff]\nlegacy summary",
            "client_id": "legacy-summary",
            "compaction": {"kind": "session_handoff", "message_start": 1, "message_end": 2},
        },
        original[-1],
    ]
    await store.save_session(state, legacy_projection)

    loaded = await store.load_session(state.session_id)
    assert loaded is not None
    assert [message["content"] for message in loaded.messages] == [
        "system",
        "[Session State Handoff]\nlegacy summary",
        "tail",
    ]


@pytest.mark.asyncio
async def test_normal_resave_cannot_rewrite_existing_transcript_row(store: SessionStore):
    state = _make_state()
    message = {"role": "tool", "content": "original result", "client_id": "tool-row"}
    await store.save_session(state, [message])
    await store.save_session(
        state,
        [{"role": "tool", "content": "silently replaced", "client_id": "tool-row"}],
    )

    row = (
        await store.read_conn.execute_fetchall(
            "SELECT message_json FROM session_messages WHERE session_id = ? AND message_id = ?",
            (state.session_id, "tool-row"),
        )
    )[0]
    assert json.loads(row["message_json"])["content"] == "original result"
    loaded = await store.load_session(state.session_id)
    assert loaded is not None
    assert loaded.messages[0]["content"] == "silently replaced"


@pytest.mark.asyncio
async def test_rewind_deletes_exact_active_suffix_and_reconstructs_after_compaction(store: SessionStore):
    state = _make_state()
    original = [
        {"role": "system", "content": "system", "client_id": "sys"},
        {"role": "user", "content": "one", "client_id": "u-1"},
        {"role": "assistant", "content": "one reply", "client_id": "a-1"},
        {"role": "user", "content": "two", "client_id": "u-2"},
        {"role": "assistant", "content": "two reply", "client_id": "a-2"},
    ]
    await store.save_session(state, original)
    compacted = _build_compacted_messages(original, 1, 3, "summary")
    active = [
        *compacted,
        {"role": "user", "content": "three", "client_id": "u-3"},
        {"role": "assistant", "content": "three reply", "client_id": "a-3"},
    ]
    await store.save_session(state, active)
    expected_ids = store._message_id_sequence(active)
    assert expected_ids is not None

    assert await store.rewind_session(
        state,
        active[:2],
        expected_message_ids=expected_ids,
        discard_message_ids=expected_ids[2:],
    )
    assert state.context_generation == 1
    assert state.context_etag is not None

    loaded = await store.load_session(state.session_id)
    assert loaded is not None
    assert [message["content"] for message in loaded.messages] == [
        "system",
        "[Session State Handoff]\nsummary",
    ]
    renewed_handoff = loaded.messages[1]
    assert renewed_handoff["message_id"] != compacted[1]["message_id"]
    assert renewed_handoff["compaction"]["reason"] == "user_rewind"
    assert renewed_handoff["compaction"]["preserved_tail_ids"] == []

    rows = await store.read_conn.execute_fetchall(
        "SELECT message_id FROM session_messages WHERE session_id = ? ORDER BY seq",
        (state.session_id,),
    )
    remaining_ids = [row["message_id"] for row in rows]
    # Inactive pre-compaction rows and the old summary remain audit evidence.
    assert {"sys", "u-1", "a-1", compacted[1]["message_id"], renewed_handoff["message_id"]} <= set(remaining_ids)
    assert not {"u-2", "a-2", "u-3", "a-3"} & set(remaining_ids)

    await store.conn.execute("UPDATE sessions SET messages = '{broken' WHERE session_id = ?", (state.session_id,))
    await store.conn.commit()
    reconstructed = await store.load_session(state.session_id)
    assert reconstructed is not None
    assert [message["message_id"] for message in reconstructed.messages] == [
        "sys",
        renewed_handoff["message_id"],
    ]


@pytest.mark.asyncio
async def test_rewind_rejects_stale_active_snapshot_without_deleting_new_messages(store: SessionStore):
    state = _make_state()
    original = [
        {"role": "user", "content": "one", "client_id": "u-1"},
        {"role": "assistant", "content": "reply", "client_id": "a-1"},
    ]
    await store.save_session(state, original)
    stale_ids = store._message_id_sequence(original)
    assert stale_ids is not None
    current = [*original, {"role": "user", "content": "concurrent", "client_id": "u-2"}]
    await store.save_session(state, current)

    assert not await store.rewind_session(
        state,
        [],
        expected_message_ids=stale_ids,
        discard_message_ids=stale_ids,
    )

    loaded = await store.load_session(state.session_id)
    assert loaded is not None
    assert [message["message_id"] for message in loaded.messages] == ["u-1", "a-1", "u-2"]
    rows = await store.read_conn.execute_fetchall(
        "SELECT message_id FROM session_messages WHERE session_id = ? ORDER BY seq",
        (state.session_id,),
    )
    assert [row["message_id"] for row in rows] == ["u-1", "a-1", "u-2"]


@pytest.mark.asyncio
async def test_rewind_rolls_back_snapshot_when_transcript_delete_fails(store: SessionStore):
    state = _make_state()
    messages = [
        {"role": "user", "content": "one", "client_id": "u-1"},
        {"role": "assistant", "content": "reply", "client_id": "a-1"},
    ]
    await store.save_session(state, messages)
    expected_ids = store._message_id_sequence(messages)
    assert expected_ids is not None
    await store.conn.execute(
        """
        CREATE TRIGGER fail_rewind_delete
        BEFORE DELETE ON session_messages
        WHEN OLD.session_id = 'test-session'
        BEGIN
            SELECT RAISE(ABORT, 'forced rewind failure');
        END
        """
    )
    await store.conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="forced rewind failure"):
        await store.rewind_session(
            state,
            [],
            expected_message_ids=expected_ids,
            discard_message_ids=expected_ids,
        )

    loaded = await store.load_session(state.session_id)
    assert loaded is not None
    assert [message["message_id"] for message in loaded.messages] == expected_ids
    rows = await store.read_conn.execute_fetchall(
        "SELECT message_id FROM session_messages WHERE session_id = ? ORDER BY seq",
        (state.session_id,),
    )
    assert [row["message_id"] for row in rows] == expected_ids


@pytest.mark.asyncio
async def test_context_transactions_do_not_cross_commit_between_sessions(store: SessionStore, monkeypatch):
    first = _make_state("transaction-a")
    second = _make_state("transaction-b")
    await store.save_session(first, [{"role": "user", "content": "old a", "client_id": "a-old"}])
    await store.save_session(second, [{"role": "user", "content": "old b", "client_id": "b-old"}])
    entered = asyncio.Event()
    release = asyncio.Event()
    original_mirror = store._mirror_session_messages

    async def blocking_mirror(session_id, messages, *, connection=None):
        if session_id == first.session_id:
            entered.set()
            await release.wait()
        await original_mirror(session_id, messages, connection=connection)

    monkeypatch.setattr(store, "_mirror_session_messages", blocking_mirror)
    first_save = asyncio.create_task(
        store.save_session(first, [{"role": "user", "content": "new a", "client_id": "a-new"}])
    )
    await entered.wait()
    second_save = asyncio.create_task(
        store.save_session(second, [{"role": "user", "content": "new b", "client_id": "b-new"}])
    )
    try:
        await asyncio.sleep(0.05)
        assert not second_save.done()
        rows = await store.read_conn.execute_fetchall(
            "SELECT session_id, messages FROM sessions WHERE session_id IN (?, ?) ORDER BY session_id",
            (first.session_id, second.session_id),
        )
        assert [json.loads(row["messages"])[0]["content"] for row in rows] == ["old a", "old b"]
    finally:
        release.set()
    await asyncio.gather(first_save, second_save)


@pytest.mark.asyncio
async def test_update_progress_rolls_back_projection_when_transcript_mirror_fails(store: SessionStore):
    state = _make_state("progress-rollback")
    original = [{"role": "user", "content": "kept", "client_id": "u-kept"}]
    await store.save_session(state, original)
    await store.conn.execute(
        """
        CREATE TRIGGER fail_progress_mirror
        BEFORE INSERT ON session_messages
        WHEN NEW.session_id = 'progress-rollback' AND NEW.message_id = 'a-fail'
        BEGIN
            SELECT RAISE(ABORT, 'forced progress mirror failure');
        END
        """
    )
    await store.conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="forced progress mirror failure"):
        await store.update_progress(
            state,
            [*original, {"role": "assistant", "content": "must roll back", "client_id": "a-fail"}],
        )

    loaded = await store.load_session(state.session_id)
    assert loaded is not None
    assert [message["message_id"] for message in loaded.messages] == ["u-kept"]
    rows = await store.read_conn.execute_fetchall(
        "SELECT message_id FROM session_messages WHERE session_id = ? ORDER BY seq",
        (state.session_id,),
    )
    assert [row["message_id"] for row in rows] == ["u-kept"]


@pytest.mark.asyncio
async def test_create_session_if_absent_rolls_back_row_when_transcript_mirror_fails(store: SessionStore):
    await store.conn.execute(
        """
        CREATE TRIGGER fail_create_mirror
        BEFORE INSERT ON session_messages
        WHEN NEW.session_id = 'create-rollback'
        BEGIN
            SELECT RAISE(ABORT, 'forced create mirror failure');
        END
        """
    )
    await store.conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="forced create mirror failure"):
        await store.create_session_if_absent(
            _make_state("create-rollback"),
            [{"role": "user", "content": "must roll back", "client_id": "u-fail"}],
        )

    assert not await store.read_conn.execute_fetchall("SELECT 1 FROM sessions WHERE session_id = 'create-rollback'")


@pytest.mark.asyncio
async def test_provision_if_absent_returns_existing_durable_identity(store: SessionStore):
    service = SessionService(store)
    first, created = await service.provision_if_absent(
        name="first name",
        session_type="channel",
        session_id="stable-channel",
        announce=False,
    )
    assert created is True

    existing, created = await service.provision_if_absent(
        name="retry name",
        session_type="channel",
        session_id="stable-channel",
        announce=False,
    )

    assert created is False
    assert existing == first
    assert existing.name == "first name"
    assert existing.public_ref == first.public_ref


@pytest.mark.asyncio
async def test_session_service_clear_context_atomically_removes_history_and_checkpoints(store: SessionStore):
    state = _make_state()
    original = [
        {"role": "system", "content": "system", "client_id": "sys"},
        {"role": "user", "content": "one", "client_id": "u-1"},
        {"role": "assistant", "content": "reply", "client_id": "a-1"},
        {"role": "user", "content": "two", "client_id": "u-2"},
    ]
    await store.save_session(state, original)
    compacted = _build_compacted_messages(original, 1, 3, "summary")
    await store.save_session(state, compacted)
    service = SessionService(store)
    data = await service.load(state.session_id)
    assert data is not None

    assert await service.clear_context(data)
    assert data.context_generation == 1
    assert data.state.context_generation == 1
    assert data.context_etag == data.state.context_etag

    loaded = await service.load(state.session_id)
    assert loaded is not None and loaded.messages == []
    assert loaded.active_message_count == 0
    for table in ("session_messages", "session_turns", "chat_compactions"):
        rows = await store.read_conn.execute_fetchall(
            f"SELECT COUNT(*) AS count FROM {table} WHERE session_id = ?",
            (state.session_id,),
        )
        assert rows[0]["count"] == 0


@pytest.mark.asyncio
async def test_stale_run_cannot_restore_context_after_clear(store: SessionStore):
    state = _make_state()
    messages = [{"role": "user", "content": "delete me", "client_id": "u-1"}]
    await store.save_session(state, messages)
    stale = await store.load_session(state.session_id)
    current = await store.load_session(state.session_id)
    assert stale is not None and current is not None

    service = SessionService(store)
    assert await service.clear_context(current)
    with pytest.raises(StaleSessionContextError):
        await store.update_progress(
            stale.state,
            [*stale.messages, {"role": "assistant", "content": "late", "client_id": "a-late"}],
        )

    loaded = await store.load_session(state.session_id)
    assert loaded is not None and loaded.messages == []
    assert not (await store.list_session_messages(state.session_id, limit=10))["messages"]


@pytest.mark.asyncio
async def test_stale_run_cannot_restore_context_after_rewind(store: SessionStore):
    state = _make_state()
    messages = [
        {"role": "user", "content": "one", "client_id": "u-1"},
        {"role": "assistant", "content": "one reply", "client_id": "a-1"},
        {"role": "user", "content": "two", "client_id": "u-2"},
        {"role": "assistant", "content": "two reply", "client_id": "a-2"},
    ]
    await store.save_session(state, messages)
    stale = await store.load_session(state.session_id)
    current = await store.load_session(state.session_id)
    assert stale is not None and current is not None

    service = SessionService(store)
    assert await service.revert(state.session_id, turns=1) is not None
    with pytest.raises(StaleSessionContextError):
        await store.save_session(stale.state, stale.messages)

    loaded = await store.load_session(state.session_id)
    assert loaded is not None
    assert [message["message_id"] for message in loaded.messages] == ["u-1", "a-1"]


@pytest.mark.asyncio
async def test_rewind_rejects_same_id_content_change(store: SessionStore):
    state = _make_state()
    messages = [
        {"role": "user", "content": "one", "client_id": "u-1"},
        {"role": "assistant", "content": "one reply", "client_id": "a-1"},
        {"role": "user", "content": "two", "client_id": "u-2"},
    ]
    await store.save_session(state, messages)
    stale = await store.load_session(state.session_id)
    current = await store.load_session(state.session_id)
    assert stale is not None and current is not None
    current.messages[0]["content"] = "newer one"
    await store.save_session(current.state, current.messages)

    expected_ids = store._message_id_sequence(stale.messages)
    assert expected_ids is not None
    assert not await store.rewind_session(
        stale.state,
        stale.messages[:2],
        expected_message_ids=expected_ids,
        discard_message_ids=expected_ids[2:],
        expected_generation=stale.context_generation,
        expected_projection_etag=stale.context_etag,
    )

    loaded = await store.load_session(state.session_id)
    assert loaded is not None
    assert loaded.messages[0]["content"] == "newer one"
    assert [message["message_id"] for message in loaded.messages] == expected_ids


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["clear", "rewind"])
async def test_recovered_malformed_projection_can_be_destructively_updated(
    store: SessionStore,
    operation: str,
):
    state = _make_state()
    messages = [
        {"role": "user", "content": "one", "client_id": "u-1"},
        {"role": "assistant", "content": "one reply", "client_id": "a-1"},
        {"role": "user", "content": "two", "client_id": "u-2"},
        {"role": "assistant", "content": "two reply", "client_id": "a-2"},
    ]
    await store.save_session(state, messages)
    await store.conn.execute(
        "UPDATE sessions SET messages = '{broken' WHERE session_id = ?",
        (state.session_id,),
    )
    await store.conn.commit()
    recovered = await store.load_session(state.session_id)
    assert recovered is not None
    assert [message["message_id"] for message in recovered.messages] == ["u-1", "a-1", "u-2", "a-2"]

    service = SessionService(store)
    if operation == "clear":
        assert await service.clear_context(recovered)
        expected_ids: list[str] = []
    else:
        result = await service.revert(state.session_id, turns=1)
        assert result is not None
        expected_ids = ["u-1", "a-1"]

    loaded = await store.load_session(state.session_id)
    assert loaded is not None
    assert [message["message_id"] for message in loaded.messages] == expected_ids


@pytest.mark.asyncio
async def test_session_message_pagination_before_and_around(store: SessionStore):
    state = _make_state()
    messages = [{"role": "user", "content": f"msg {i}", "client_id": f"m-{i}"} for i in range(5)]
    await store.save_session(state, messages)

    latest = await store.list_session_messages("test-session", limit=2)
    assert [row["message_id"] for row in latest["messages"]] == ["m-3", "m-4"]
    assert latest["has_more_before"] is True
    assert latest["has_more_after"] is False

    older = await store.list_session_messages("test-session", limit=2, before="m-3")
    assert [row["message_id"] for row in older["messages"]] == ["m-1", "m-2"]
    assert older["has_more_before"] is True
    assert older["has_more_after"] is True

    around = await store.list_session_messages("test-session", limit=3, around="m-2")
    assert [row["message_id"] for row in around["messages"]] == ["m-1", "m-2", "m-3"]
    assert around["has_more_before"] is True
    assert around["has_more_after"] is True


@pytest.mark.asyncio
async def test_latest_session_messages_include_visible_user_anchor_for_tool_heavy_tail(store: SessionStore):
    state = _make_state()
    messages = [
        {"role": "user", "content": "implement the goal", "client_id": "u-1"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call-1", "type": "function", "function": {"name": "file_search_text", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "result 1"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-2", "type": "function", "function": {"name": "file_read", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call-2", "content": "result 2"},
    ]
    await store.save_session(state, messages)

    latest = await store.list_session_messages("test-session", limit=3)

    assert [row["message"]["role"] for row in latest["messages"]] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
    ]
    assert latest["messages"][0]["message"]["content"] == "implement the goal"
    assert latest["has_more_before"] is False
    assert latest["has_more_after"] is False


@pytest.mark.asyncio
async def test_latest_session_messages_keep_latest_tail_when_adding_visible_user_anchor(store: SessionStore):
    state = _make_state()
    messages = [{"role": "user", "content": "implement the goal", "client_id": "u-1"}]
    for i in range(130):
        call_id = f"call-{i}"
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "client_id": f"a-{i}",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": "bash", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": call_id, "content": f"result {i}", "client_id": f"t-{i}"},
            ]
        )
    messages.append({"role": "assistant", "content": "final answer", "client_id": "final"})
    await store.save_session(state, messages)

    latest = await store.list_session_messages("test-session", limit=50)

    assert latest["messages"][0]["message_id"] == "u-1"
    assert latest["messages"][-1]["message_id"] == "final"
    assert latest["messages"][-1]["message"]["content"] == "final answer"
    assert latest["messages"][1]["message"]["role"] == "assistant"
    assert len(latest["messages"]) == 262
    assert latest["has_more_before"] is False
    assert latest["has_more_after"] is False


@pytest.mark.asyncio
async def test_prune_session_events_keeps_newest_n(store: SessionStore):
    state = _make_state()
    await store.save_session(state, [])
    for seq in range(1, 26):
        await store.record_session_event(
            StreamRecord(seq=seq, session_id="test-session", event=ThinkingEvent(status=f"s{seq}"))
        )

    deleted = await store.prune_session_events("test-session", keep=10)
    assert deleted == 15

    rows = await store.read_conn.execute_fetchall(
        "SELECT seq FROM session_events WHERE session_id = 'test-session' ORDER BY seq ASC"
    )
    assert [r["seq"] for r in rows] == list(range(16, 26))  # newest 10 retained


@pytest.mark.asyncio
async def test_prune_session_events_noop_when_under_cap(store: SessionStore):
    state = _make_state()
    await store.save_session(state, [])
    for seq in range(1, 6):
        await store.record_session_event(
            StreamRecord(seq=seq, session_id="test-session", event=ThinkingEvent(status=f"s{seq}"))
        )

    deleted = await store.prune_session_events("test-session", keep=10)
    assert deleted == 0
    rows = await store.read_conn.execute_fetchall(
        "SELECT COUNT(*) AS c FROM session_events WHERE session_id = 'test-session'"
    )
    assert rows[0]["c"] == 5


@pytest.mark.asyncio
async def test_event_retention_counter_survives_restart(tmp_path: Path, monkeypatch):
    import arden.context.store as store_module

    monkeypatch.setattr(store_module, "SESSION_EVENT_PRUNE_INTERVAL", 3)
    monkeypatch.setattr(store_module, "SESSION_EVENT_DURABLE_RETENTION", 2)
    db = tmp_path / "retention.db"

    conn = await database.connect(db)
    read_conn = await database.connect(db, readonly=True)
    first = SessionStore(conn, read_conn)
    await first.init_schema()
    for seq in (1, 2):
        await first.record_session_event(
            StreamRecord(seq=seq, session_id="restart-session", event=ThinkingEvent(status=f"s{seq}"))
        )
    await read_conn.close()
    await conn.close()

    conn = await database.connect(db)
    read_conn = await database.connect(db, readonly=True)
    restarted = SessionStore(conn, read_conn)
    await restarted.init_schema()
    await restarted.record_session_event(
        StreamRecord(seq=3, session_id="restart-session", event=ThinkingEvent(status="s3"))
    )

    rows = await read_conn.execute_fetchall(
        "SELECT seq FROM session_events WHERE session_id = 'restart-session' ORDER BY seq"
    )
    assert [row["seq"] for row in rows] == [2, 3]
    state = await read_conn.execute_fetchall(
        "SELECT writes_since_prune FROM session_event_retention_state WHERE session_id = 'restart-session'"
    )
    assert state[0]["writes_since_prune"] == 0

    await read_conn.close()
    await conn.close()


@pytest.mark.asyncio
async def test_latest_session_messages_expand_past_meta_only_turns(store: SessionStore):
    # Channel / automation sessions drive their turns with meta user messages
    # (loop:/bg:/goal:), so a tool-heavy active run leaves the newest window
    # with zero VISIBLE user anchors. History must still reach back to prior
    # turns instead of dead-ending on the active run's tool stream.
    state = _make_state()
    messages = [
        {"role": "user", "content": "loop turn 1", "client_id": "loop:x:1", "is_meta": True},
        {"role": "assistant", "content": "previous answer", "client_id": "a-prev"},
        {"role": "user", "content": "loop turn 2", "client_id": "loop:x:2", "is_meta": True},
    ]
    for i in range(60):
        call_id = f"call-{i}"
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "client_id": f"a-{i}",
                    "tool_calls": [
                        {"id": call_id, "type": "function", "function": {"name": "bash", "arguments": "{}"}}
                    ],
                },
                {"role": "tool", "tool_call_id": call_id, "content": f"result {i}", "client_id": f"t-{i}"},
            ]
        )
    await store.save_session(state, messages)

    latest = await store.list_session_messages("test-session", limit=50)
    ids = [row["message_id"] for row in latest["messages"]]

    # Prior turn content is now included, not dead-ended on the active tail.
    assert "a-prev" in ids
    assert "loop:x:1" in ids
    assert latest["has_more_before"] is False


@pytest.mark.asyncio
async def test_latest_session_messages_include_previous_exchange_for_tool_heavy_latest_turn(store: SessionStore):
    state = _make_state()
    messages = [
        {"role": "user", "content": "previous question", "client_id": "u-previous"},
        {"role": "assistant", "content": "previous answer", "client_id": "a-previous"},
        {"role": "user", "content": "continue pls dude", "client_id": "u-current"},
    ]
    for i in range(60):
        call_id = f"call-{i}"
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "client_id": f"a-{i}",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": "bash", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": call_id, "content": f"result {i}", "client_id": f"t-{i}"},
            ]
        )
    await store.save_session(state, messages)

    latest = await store.list_session_messages("test-session", limit=50)

    assert [row["message_id"] for row in latest["messages"][:3]] == [
        "u-previous",
        "a-previous",
        "u-current",
    ]
    assert latest["messages"][-1]["message_id"] == "t-59"
    assert latest["has_more_before"] is False
    assert latest["has_more_after"] is False


@pytest.mark.asyncio
async def test_latest_session_messages_keep_previous_exchange_when_latest_turn_exceeds_page_cap(store: SessionStore):
    state = _make_state()
    messages = [
        {"role": "user", "content": "previous question", "client_id": "u-previous"},
        {
            "role": "assistant",
            "content": "",
            "client_id": "a-previous-tool",
            "tool_calls": [
                {
                    "id": "previous-call",
                    "type": "function",
                    "function": {"name": "research", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "previous-call",
            "content": "previous result",
            "client_id": "t-previous-tool",
        },
        {"role": "user", "content": "current long research", "client_id": "u-current"},
    ]
    for i in range(130):
        call_id = f"call-{i}"
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "client_id": f"a-{i}",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": "bash", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": call_id, "content": f"result {i}", "client_id": f"t-{i}"},
            ]
        )
    await store.save_session(state, messages)

    latest = await store.list_session_messages("test-session", limit=50)

    assert [row["message_id"] for row in latest["messages"][:3]] == [
        "u-previous",
        "a-previous-tool",
        "t-previous-tool",
    ]
    assert latest["messages"][3]["message_id"] == "u-current"
    assert [row["message_id"] for row in latest["messages"][:4]] == [
        "u-previous",
        "a-previous-tool",
        "t-previous-tool",
        "u-current",
    ]
    assert latest["messages"][-1]["message_id"] == "t-129"
    assert latest["has_more_before"] is False
    assert latest["has_more_after"] is False


@pytest.mark.asyncio
async def test_latest_session_messages_keep_contiguous_tail_when_anchor_range_is_too_large(store: SessionStore):
    state = _make_state()
    messages = [{"role": "user", "content": "very large research", "client_id": "u-1"}]
    for i in range(600):
        call_id = f"call-{i}"
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "client_id": f"a-{i}",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": "bash", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": call_id, "content": f"result {i}", "client_id": f"t-{i}"},
            ]
        )
    await store.save_session(state, messages)

    latest = await store.list_session_messages("test-session", limit=50)

    assert len(latest["messages"]) == 50
    assert latest["messages"][0]["message_id"] == "a-575"
    assert latest["messages"][-1]["message_id"] == "t-599"
    assert all(row["message_id"] != "u-1" for row in latest["messages"])
    assert latest["has_more_before"] is True
    assert latest["has_more_after"] is False


@pytest.mark.asyncio
async def test_rewind_session_trims_uncompacted_active_future(store: SessionStore):
    state = _make_state()
    messages = [{"role": "user", "content": f"msg {i}", "client_id": f"m-{i}"} for i in range(4)]
    await store.save_session(state, messages)
    expected_ids = store._message_id_sequence(messages)
    assert expected_ids is not None

    assert await store.rewind_session(
        state,
        messages[:2],
        expected_message_ids=expected_ids,
        discard_message_ids=expected_ids[2:],
    )

    page = await store.list_session_messages("test-session", limit=10)
    assert [row["message_id"] for row in page["messages"]] == ["m-0", "m-1"]
    loaded = await store.load_session(state.session_id)
    assert loaded is not None and loaded.active_message_count == len(loaded.messages) == 2

    turns = await store.list_session_turns("test-session")
    assert [turn["message_start_id"] for turn in turns] == ["m-0", "m-1"]


@pytest.mark.asyncio
async def test_session_turns_group_durable_transcript_by_user_turn(store: SessionStore):
    state = _make_state()
    messages = [
        {"role": "system", "content": "sys", "client_id": "sys"},
        {"role": "user", "content": "first", "client_id": "u-1", "created_at": "2026-01-01T00:00:00+00:00"},
        {"role": "assistant", "content": "reply", "client_id": "a-1", "created_at": "2026-01-01T00:00:01+00:00"},
        {"role": "tool", "content": "tool", "client_id": "t-1", "created_at": "2026-01-01T00:00:02+00:00"},
        {"role": "user", "content": "second", "client_id": "u-2", "created_at": "2026-01-01T00:00:03+00:00"},
        {"role": "assistant", "content": "reply 2", "client_id": "a-2", "created_at": "2026-01-01T00:00:04+00:00"},
    ]
    await store.save_session(state, messages)

    turns = await store.list_session_turns("test-session")

    assert [(turn["message_start_id"], turn["message_end_id"]) for turn in turns] == [("u-1", "t-1"), ("u-2", "a-2")]
    assert turns[0]["started_at"] == "2026-01-01T00:00:00+00:00"
    assert turns[0]["ended_at"] == "2026-01-01T00:00:02+00:00"


@pytest.mark.asyncio
async def test_channel_session_type_and_origin_roundtrip(store: SessionStore):
    """v5: SessionState carries session_type and origin_automation_id."""
    state = SessionState(
        session_id="chan-1",
        started_at=datetime.now(UTC),
        name="offer-42 channel",
        session_type="channel",
        origin_automation_id="watcher-1",
    )
    await store.save_session(state, [{"role": "assistant", "content": "first post"}])

    loaded = await store.load_session("chan-1")
    assert loaded is not None
    assert loaded.state.session_type == "channel"
    assert loaded.state.origin_automation_id == "watcher-1"


@pytest.mark.asyncio
async def test_agent_session_parent_metadata_roundtrip(store: SessionStore):
    area = await store.create_area(name="arden")
    state = SessionState(
        session_id="parent::agent",
        started_at=datetime.now(UTC),
        name="Research blockers",
        session_type="agent",
        parent_session_id="parent",
        parent_tool_call_id="call-research",
        agent_type="research",
        agent_status="running",
        area_id=area["area_id"],
        chat_model="openai/gpt-5",
    )
    await store.save_session(state, [{"role": "user", "content": "research blockers"}])

    loaded = await store.load_session("parent::agent")
    assert loaded is not None
    assert loaded.state.session_type == "agent"
    assert loaded.state.parent_session_id == "parent"
    assert loaded.state.parent_tool_call_id == "call-research"
    assert loaded.state.agent_type == "research"
    assert loaded.state.agent_status == "running"

    rows = await store.list_sessions(area_id=area["area_id"])
    row = rows[0]
    assert row["session_id"] == "parent::agent"
    assert row["parent_session_id"] == "parent"
    assert row["parent_tool_call_id"] == "call-research"
    assert row["agent_type"] == "research"
    assert row["agent_status"] == "running"


@pytest.mark.asyncio
async def test_legacy_chat_session_defaults_when_unset(store: SessionStore):
    """Sessions created without the new fields default to session_type='chat'."""
    state = _make_state()
    await store.save_session(state, [{"role": "user", "content": "hi"}])

    loaded = await store.load_session("test-session")
    assert loaded is not None
    assert loaded.state.session_type == "chat"
    assert loaded.state.origin_automation_id is None


@pytest.mark.asyncio
async def test_session_turns_preserve_raw_transcript_without_handoff_rows(store: SessionStore):
    state = _make_state()
    original = [
        {"role": "user", "content": "first", "client_id": "u-1"},
        {"role": "assistant", "content": "reply", "client_id": "a-1"},
        {"role": "user", "content": "second", "client_id": "u-2"},
        {"role": "assistant", "content": "reply 2", "client_id": "a-2"},
    ]
    await store.save_session(state, original)
    await store.save_session(
        state,
        [
            {"role": "assistant", "content": "[Session State Handoff]\nsummary", "client_id": "handoff"},
            original[-2],
            original[-1],
        ],
    )

    turns = await store.list_session_turns("test-session")

    assert [(turn["message_start_id"], turn["message_end_id"]) for turn in turns] == [("u-1", "a-1"), ("u-2", "a-2")]


@pytest.mark.asyncio
async def test_session_runtime_run_and_pending_approvals(store: SessionStore):
    await store.record_chat_run_started("run-1", "sess-runtime")
    await store.record_chat_run_status("run-1", "running", last_seq=7)
    await store.record_tool_approval_requested(
        run_id="run-1",
        session_id="sess-runtime",
        tool_call_id="tool-1",
        tool_name="file_write",
        action="write",
        scope="internal",
        preview="preview text",
        diff="diff text",
    )

    latest = await store.get_latest_chat_run_for_session("sess-runtime")
    approvals = await store.list_pending_tool_approvals("sess-runtime", run_id="run-1")

    assert latest is not None
    assert latest["run_id"] == "run-1"
    assert latest["status"] == "running"
    assert latest["last_seq"] == 7
    assert len(approvals) == 1
    assert approvals[0]["tool_call_id"] == "tool-1"
    assert approvals[0]["preview"] == "preview text"


@pytest.mark.asyncio
async def test_cold_archived_session_round_trips_full_transcript(store: SessionStore, tmp_path: Path):
    state = _make_state(session_id="cold-session", name="Cold session")
    messages = [
        {"role": "user", "content": "keep this question", "client_id": "u-cold"},
        {"role": "assistant", "content": "keep this answer", "client_id": "a-cold"},
    ]
    await store.save_session(state, messages)
    await store.record_session_event(
        StreamRecord(seq=1, session_id=state.session_id, event=ThinkingEvent(status="evidence"))
    )
    assert await store.archive_session(state.session_id)

    manifest = await store.cold_convert_session(state.session_id, cold_root=tmp_path / "cold")

    assert manifest is not None
    assert Path(manifest.bundle_path).is_file()
    cold_row = (
        await store.read_conn.execute_fetchall("SELECT * FROM sessions WHERE session_id = ?", (state.session_id,))
    )[0]
    assert cold_row["storage_state"] == "cold"
    assert cold_row["messages"] == "[]"
    assert cold_row["context_generation"] == 1
    assert not await store.read_conn.execute_fetchall(
        "SELECT 1 FROM session_messages WHERE session_id = ?", (state.session_id,)
    )
    assert not await store.read_conn.execute_fetchall(
        "SELECT 1 FROM session_events WHERE session_id = ?", (state.session_id,)
    )

    service = SessionService(
        store,
        cold_root=tmp_path / "cold",
        blob_root=tmp_path / "blobs" / "tool-results",
    )
    assert await service.restore(state.session_id)
    restored = await store.load_session(state.session_id)
    assert restored is not None
    assert [message["content"] for message in restored.messages] == [
        "keep this question",
        "keep this answer",
    ]
    assert not Path(manifest.bundle_path).exists()
    restored_row = (
        await store.read_conn.execute_fetchall("SELECT * FROM sessions WHERE session_id = ?", (state.session_id,))
    )[0]
    assert restored_row["storage_state"] == "hot"
    assert restored_row["archived_at"] is None
    assert restored_row["context_generation"] == 1


@pytest.mark.asyncio
async def test_clear_cold_session_cannot_be_undone_by_restore(store: SessionStore, tmp_path: Path):
    state = _make_state(session_id="cold-clear")
    await store.save_session(
        state,
        [{"role": "user", "content": "delete permanently", "client_id": "u-cold-clear"}],
    )
    assert await store.archive_session(state.session_id)
    manifest = await store.cold_convert_session(state.session_id, cold_root=tmp_path / "cold")
    assert manifest is not None
    service = SessionService(
        store,
        cold_root=tmp_path / "cold",
        blob_root=tmp_path / "blobs" / "tool-results",
    )
    data = await service.load(state.session_id)
    assert data is not None and data.messages == []
    assert data.context_generation == 1

    assert await service.clear_context(data)
    assert data.context_generation == 2
    assert not Path(manifest.bundle_path).exists()
    cleared_row = (
        await store.read_conn.execute_fetchall("SELECT * FROM sessions WHERE session_id = ?", (state.session_id,))
    )[0]
    assert cleared_row["storage_state"] == "hot"
    assert cleared_row["context_generation"] == 2
    assert cleared_row["messages"] == "[]"
    assert not await store.read_conn.execute_fetchall(
        "SELECT 1 FROM session_messages WHERE session_id = ?",
        (state.session_id,),
    )

    assert await service.restore(state.session_id)
    restored = await store.load_session(state.session_id)
    assert restored is not None and restored.messages == []
    assert restored.context_generation == 2


@pytest.mark.asyncio
async def test_permanent_delete_removes_all_session_owned_rows(store: SessionStore):
    state = _make_state(session_id="purge-session")
    await store.save_session(state, [{"role": "user", "content": "delete me", "client_id": "u-purge"}])
    await store.record_session_event(
        StreamRecord(seq=1, session_id=state.session_id, event=ThinkingEvent(status="delete me"))
    )
    assert await store.archive_session(state.session_id)

    assert await store.permanently_delete_session(state.session_id)

    assert not await store.read_conn.execute_fetchall(
        "SELECT 1 FROM sessions WHERE session_id = ?", (state.session_id,)
    )
    for table in await store.storage.session_owned_tables(store.read_conn):
        rows = await store.read_conn.execute_fetchall(
            f'SELECT 1 FROM "{table}" WHERE session_id = ? LIMIT 1',
            (state.session_id,),
        )
        assert not rows, table


@pytest.mark.asyncio
async def test_storage_retention_uses_last_access_when_newer_than_activity(store: SessionStore):
    state = _make_state(session_id="accessed-session")
    state.last_activity = datetime.now(UTC) - timedelta(days=180)
    await store.save_session(state, [{"role": "user", "content": "old but opened"}])
    before = (await store.list_storage_cleanup_candidates())[0]

    assert await store.touch_session_access(state.session_id)
    after = (await store.list_storage_cleanup_candidates())[0]

    assert datetime.fromisoformat(before.last_activity) < datetime.now(UTC) - timedelta(days=100)
    assert datetime.fromisoformat(after.last_activity) > datetime.now(UTC) - timedelta(minutes=1)


@pytest.mark.asyncio
async def test_cold_conversion_incrementally_returns_database_pages(store: SessionStore, tmp_path: Path):
    state = _make_state(session_id="large-cold-session")
    await store.save_session(
        state,
        [{"role": "user", "content": "x" * 2_000_000, "client_id": "large-message"}],
    )
    assert await store.archive_session(state.session_id)
    before = sum(path.stat().st_size for path in tmp_path.glob("sessions.db*"))

    assert await store.cold_convert_session(state.session_id, cold_root=tmp_path / "cold") is not None
    reclaimed = await store.incremental_vacuum(pages=8192)

    assert (await store.database_reclaim_status())["mode"] == "incremental"
    assert reclaimed > 0
    after = sum(path.stat().st_size for path in tmp_path.glob("sessions.db*"))
    assert after < before
