"""Session store tests — real SQLite, round-trip persistence."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio

import arden.database as database
from arden.constants import RAW_TOOL_RESULT_INLINE_MAX_BYTES
from arden.context.models import SessionState
from arden.context.store import SessionStore
from arden.core.raw_tool_results import RAW_TOOL_RESULT_DATA_KEY, persist_raw_tool_result
from arden.events.sse import ThinkingEvent, ToolCallResultEvent
from arden.server.bus import StreamRecord
from arden.services.session import SessionService


@pytest_asyncio.fixture
async def store(tmp_path: Path):
    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    s = SessionStore(conn, read_conn)
    await s.init_schema()
    yield s
    await read_conn.close()
    await conn.close()


def _make_state(session_id: str = "test-session", name: str | None = None) -> SessionState:
    return SessionState(
        session_id=session_id,
        started_at=datetime.now(UTC),
        name=name,
    )


@pytest.mark.asyncio
async def test_area_round_trip_and_session_scoping(store: SessionStore):
    area = await store.create_area(
        name="arden",
        default_cwd=" /Users/me/src/arden ",
        instructions="Prefer small focused changes.",
    )

    assert area["area_id"].startswith("area_")
    assert area["name"] == "arden"
    assert area["default_cwd"] == "/Users/me/src/arden"
    assert area["instructions"] == "Prefer small focused changes."
    assert area["knowledge_scope"] == f"area:{area['area_id']}"

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
async def test_area_schema_migrates_existing_sessions_table(tmp_path: Path):
    conn = await database.connect(tmp_path / "legacy-areas.db")
    read_conn = await database.connect(tmp_path / "legacy-areas.db", readonly=True)
    await conn.executescript(
        """
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            last_activity TEXT NOT NULL,
            messages TEXT,
            metadata TEXT,
            name TEXT,
            archived_at TEXT,
            session_type TEXT NOT NULL DEFAULT 'chat',
            origin_automation_id TEXT
        );
        """
    )
    await conn.commit()

    store = SessionStore(conn, read_conn)
    await store.init_schema()

    columns = await conn.execute_fetchall("PRAGMA table_info(sessions)")
    indexes = await conn.execute_fetchall("PRAGMA index_list(sessions)")
    column_names = {row["name"] for row in columns}
    assert "area_id" in column_names
    assert "parent_session_id" in column_names
    assert "parent_tool_call_id" in column_names
    assert "agent_type" in column_names
    assert "agent_status" in column_names
    index_names = {row["name"] for row in indexes}
    assert "idx_sessions_area_activity" in index_names
    assert "idx_sessions_parent_activity" in index_names

    await read_conn.close()
    await conn.close()


@pytest.mark.asyncio
async def test_tool_results_schema_migrates_legacy_table(tmp_path: Path):
    conn = await database.connect(tmp_path / "legacy-tool-results.db")
    read_conn = await database.connect(tmp_path / "legacy-tool-results.db", readonly=True)
    await conn.executescript(
        """
        CREATE TABLE tool_results (
            content_hash TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            byte_len INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        INSERT INTO tool_results (content_hash, content, byte_len, created_at)
        VALUES ('abc', 'legacy raw', 10, '2026-01-01T00:00:00+00:00');
        """
    )
    await conn.commit()

    store = SessionStore(conn, read_conn)
    await store.init_schema()

    columns = await conn.execute_fetchall("PRAGMA table_info(tool_results)")
    column_names = {row["name"] for row in columns}
    assert "tool_result_id" in column_names
    assert "content_bytes" in column_names
    assert "blob_path" in column_names

    legacy_rows = await conn.execute_fetchall("SELECT content_hash, content FROM tool_results_legacy")
    assert [(row["content_hash"], row["content"]) for row in legacy_rows] == [("abc", "legacy raw")]

    await read_conn.close()
    await conn.close()


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
async def test_chat_run_and_queued_message_ledger(store: SessionStore):
    await store.record_chat_run_started("run-1", "sess-1")
    await store.record_chat_queued_message(
        client_id="cid-1",
        session_id="sess-1",
        run_id="run-1",
        message={"role": "user", "content": "follow-up", "client_id": "cid-1"},
    )

    queued = await store.list_chat_queued_messages("sess-1")
    assert [row["client_id"] for row in queued] == ["cid-1"]
    assert queued[0]["status"] == "queued"
    assert queued[0]["message"]["content"] == "follow-up"

    await store.mark_chat_queued_message_ingested("cid-1", ingested_seq=42)
    await store.record_chat_run_status("run-1", "completed", last_seq=99)

    completed = await store.get_chat_run("run-1")
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["last_seq"] == 99
    assert completed["ended_at"] is not None

    queued = await store.list_chat_queued_messages("sess-1")
    assert queued[0]["status"] == "ingested"
    assert queued[0]["ingested_seq"] == 42
    assert queued[0]["ingested_at"] is not None


@pytest.mark.asyncio
async def test_ingesting_queued_message_advances_its_idempotency_receipt(store: SessionStore):
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
    await store.record_chat_queued_message(
        client_id="cid-ingested",
        session_id="sess-1",
        run_id="run-1",
        message={"role": "user", "content": "follow-up", "client_id": "cid-ingested"},
    )

    await store.mark_chat_queued_message_ingested("cid-ingested", ingested_seq=9)

    receipt = await store.get_chat_idempotency_key("sess-1", "cid-ingested")
    assert receipt is not None
    assert receipt["status"] == "ingested"
    assert receipt["run_id"] == "run-1"
    assert receipt["expires_at"] is not None


@pytest.mark.asyncio
async def test_cancelled_queue_message_cannot_be_reopened_by_late_enqueue(store: SessionStore):
    await store.claim_chat_idempotency_key(
        session_id="sess-1",
        client_id="cid-cancelled",
        request_hash="hash-cancelled",
    )
    await store.update_chat_idempotency_key(
        session_id="sess-1",
        client_id="cid-cancelled",
        status="queued",
        run_id="run-1",
    )
    await store.record_chat_queued_message(
        client_id="cid-cancelled",
        session_id="sess-1",
        run_id="run-1",
        message={"role": "user", "content": "first", "client_id": "cid-cancelled"},
    )

    assert await store.cancel_chat_queued_message(
        session_id="sess-1",
        client_id="cid-cancelled",
        run_id="run-1",
    ) == "cancelled"
    assert await store.record_chat_queued_message(
        client_id="cid-cancelled",
        session_id="sess-1",
        run_id="run-1",
        message={"role": "user", "content": "late", "client_id": "cid-cancelled"},
    ) == "cancelled"

    queued = await store.list_chat_queued_messages("sess-1")
    receipt = await store.get_chat_idempotency_key("sess-1", "cid-cancelled")
    assert queued[0]["status"] == "cancelled"
    assert queued[0]["message"]["content"] == "first"
    assert receipt is not None
    assert receipt["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancelling_absent_queue_message_creates_terminal_tombstone(store: SessionStore):
    assert await store.cancel_chat_queued_message(
        session_id="sess-1",
        client_id="cid-before-post",
    ) == "cancelled"

    claimed, receipt = await store.claim_chat_idempotency_key(
        session_id="sess-1",
        client_id="cid-before-post",
        request_hash="hash-post",
    )
    assert claimed is False
    assert receipt["status"] == "cancelled"
    assert receipt["run_id"] is None
    assert await store.record_chat_queued_message(
        client_id="cid-before-post",
        session_id="sess-1",
        run_id="run-late",
        message={"role": "user", "content": "late", "client_id": "cid-before-post"},
    ) == "cancelled"
    assert await store.list_chat_queued_messages("sess-1", status="queued") == []


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
async def test_interrupted_chat_queued_messages_become_retryable(store: SessionStore):
    await store.record_chat_run_started("run-1", "sess-1")
    await store.record_chat_queued_message(
        client_id="cid-queued",
        session_id="sess-1",
        run_id="run-1",
        message={"role": "user", "content": "queued", "client_id": "cid-queued"},
    )
    await store.mark_interrupted_chat_runs()
    changed = await store.mark_interrupted_chat_queued_messages_retryable()

    assert changed == 1
    queued = await store.list_chat_queued_messages("sess-1")
    assert queued[0]["status"] == "failed_retryable"


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
        tool_name="write_file",
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
        tool_name="send_email",
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
        tool_name="send_email",
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
async def test_run_sidecars_resolve_exact_initiating_queued_and_meta_turns(store: SessionStore):
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
    await store.record_chat_queued_message(
        client_id="queued-1",
        session_id="s-1",
        run_id="run-1",
        message={"role": "user", "content": "follow up", "client_id": "queued-1"},
    )
    await store.mark_chat_queued_message_ingested("queued-1")

    initiating = await store.get_run_sidecars_for_turn(session_id="s-1", turn_id="turn-1")
    queued = await store.get_run_sidecars_for_turn(session_id="s-1", turn_id="queued-1")
    meta = await store.get_run_sidecars_for_turn(session_id="s-1", turn_id="meta-user-run-2")

    assert initiating and initiating["run_id"] == "run-1"
    assert queued and queued["run_id"] == "run-1"
    assert meta and meta["run_id"] == "run-2"
    assert await store.get_run_sidecars_for_turn(session_id="other", turn_id="turn-1") is None
    assert await store.get_run_sidecars_for_turn(session_id="s-1", turn_id="missing") is None


@pytest.mark.asyncio
async def test_run_sidecars_ignore_queued_turn_until_ingested(store: SessionStore):
    await store.record_chat_run_started("run-1", "s-1", metadata={"client_id": "turn-1"})
    await store.record_run_context_manifest(run_id="run-1", session_id="s-1", manifest=[])
    await store.record_chat_queued_message(
        client_id="queued-1",
        session_id="s-1",
        run_id="run-1",
        message={"role": "user", "content": "pending", "client_id": "queued-1"},
    )

    assert await store.get_run_sidecars_for_turn(session_id="s-1", turn_id="queued-1") is None


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
            tool_name="write_file",
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
async def test_tool_call_schema_migrates_single_column_primary_key(tmp_path):
    conn = await database.connect(tmp_path / "legacy_sessions.db")
    await conn.executescript(
        """
        CREATE TABLE tool_calls (
            run_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            tool_call_id TEXT PRIMARY KEY,
            tool_name TEXT NOT NULL,
            action TEXT NOT NULL,
            scope TEXT NOT NULL,
            args_hash TEXT,
            status TEXT NOT NULL,
            result_preview TEXT,
            started_at TEXT NOT NULL,
            ended_at TEXT
        );
        INSERT INTO tool_calls (
            run_id, session_id, tool_call_id, tool_name, action, scope,
            args_hash, status, result_preview, started_at, ended_at
        )
        VALUES (
            'run-old', 's-old', 'call-1', 'read_state', 'read', 'internal',
            'oldhash', 'success', 'ok', '2026-05-16T00:00:00+00:00', NULL
        );
        """
    )
    await conn.commit()
    store = SessionStore(conn)

    try:
        await store.init_schema()
        await store.record_tool_call_started(
            run_id="run-new",
            session_id="s-new",
            tool_call_id="call-1",
            tool_name="read_state",
            action="read",
            scope="internal",
            args_hash="newhash",
        )

        old_rows = await store.list_tool_calls(run_id="run-old")
        new_rows = await store.list_tool_calls(run_id="run-new")
        assert old_rows[0]["args_hash"] == "oldhash"
        assert new_rows[0]["args_hash"] == "newhash"
    finally:
        await conn.close()


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
    await store.record_background_agent_started(
        task_id="bg-1",
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
    await store.record_background_agent_started(
        task_id="bg-1",
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
        session_id="sess-1",
        parent_run_id="run-1",
        command="first",
    )
    await store.record_background_agent_started(
        task_id="bg-1",
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
        session_id="sess-1",
        parent_run_id="run-1",
        command="first",
    )
    await store.record_background_agent_started(
        task_id="bg-1",
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
async def test_background_agent_schema_migrates_old_task_id_primary_key(tmp_path: Path):
    conn = await database.connect(tmp_path / "old-sessions.db")
    read_conn = await database.connect(tmp_path / "old-sessions.db", readonly=True)
    await conn.execute(
        """
        CREATE TABLE background_agent_runs (
            task_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            parent_run_id TEXT,
            status TEXT NOT NULL,
            command TEXT NOT NULL,
            detail TEXT,
            result_ref TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            updated_at TEXT NOT NULL,
            ended_at TEXT,
            cancel_requested_at TEXT,
            notified_at TEXT
        )
        """
    )
    await conn.execute(
        """
        INSERT INTO background_agent_runs (
            task_id, session_id, parent_run_id, status, command,
            created_at, started_at, updated_at
        )
        VALUES ('bg-1', 'sess-1', 'run-1', 'running', 'old', 'now', 'now', 'now')
        """
    )
    await conn.commit()

    s = SessionStore(conn, read_conn)
    await s.init_schema()
    await s.record_background_agent_started(
        task_id="bg-1",
        session_id="sess-2",
        parent_run_id="run-2",
        command="new",
    )
    await s.record_background_agent_finished(
        task_id="bg-1",
        session_id="sess-2",
        status="completed",
        result_text="result",
    )

    assert (await s.list_background_agent_runs("sess-1"))[0]["command"] == "old"
    assert (await s.list_background_agent_runs("sess-2"))[0]["command"] == "new"
    migrated = (await s.list_background_agent_runs("sess-1"))[0]
    assert migrated["child_run_id"] == "bg-1"
    assert migrated["parent_tool_call_id"] is None
    assert migrated["agent_type"] == "background_research"
    assert migrated["wait"] is False
    assert await s.get_background_agent_result("sess-2", "bg-1") == "result"

    await read_conn.close()
    await conn.close()


@pytest.mark.asyncio
async def test_background_agent_event_schema_adds_delivery_columns_before_index(tmp_path: Path):
    conn = await database.connect(tmp_path / "old-events.db")
    await conn.execute(
        """
        CREATE TABLE background_agent_events (
            session_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            task_id TEXT NOT NULL,
            status TEXT NOT NULL,
            detail TEXT,
            result_ref TEXT,
            terminal INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            PRIMARY KEY (session_id, seq)
        )
        """
    )
    await conn.commit()

    store = SessionStore(conn)
    try:
        await store.init_schema()
        columns = {row["name"] for row in await conn.execute_fetchall("PRAGMA table_info(background_agent_events)")}
        indexes = {row["name"] for row in await conn.execute_fetchall("PRAGMA index_list(background_agent_events)")}

        assert {"event_id", "delivered_at"}.issubset(columns)
        assert "idx_background_agent_events_event_id" in indexes
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_marks_running_background_agents_interrupted_on_startup(store: SessionStore):
    await store.record_background_agent_started(
        task_id="bg-1",
        session_id="sess-1",
        parent_run_id="run-1",
        command="research task",
    )

    changed = await store.mark_interrupted_background_agent_runs()
    runs = await store.list_background_agent_runs("sess-1")

    assert changed == 1
    assert runs[0]["status"] == "interrupted"


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
    assert payload["content_bytes"] == len(raw.encode("utf-8"))
    assert len(rows[0]["event_json"]) < RAW_TOOL_RESULT_INLINE_MAX_BYTES

    manifest = await store.get_tool_result(payload["raw_ref"])
    assert manifest is not None
    assert manifest["session_id"] == "sess-raw"
    assert manifest["tool_call_id"] == "call-raw"
    assert manifest["content"] == raw

    tool_calls = await store.list_tool_calls(run_id="run-1")
    assert tool_calls[0]["result_ref"] == payload["raw_ref"]

    events = await store.list_session_events("sess-raw", after_seq=0)
    assert events[0].event.tool_call_id == "call-raw"
    assert events[0].event.raw_ref == payload["raw_ref"]
    assert events[0].event.content != raw


@pytest.mark.asyncio
async def test_small_tool_result_event_stays_inline(store: SessionStore):
    await store.record_session_event(
        StreamRecord(
            seq=13,
            session_id="sess-small",
            event=ToolCallResultEvent(tool_call_id="call-small", name="read_file", content="small raw result"),
        )
    )

    rows = await store.read_conn.execute_fetchall(
        "SELECT event_json FROM session_events WHERE session_id = 'sess-small'"
    )
    payload = json.loads(rows[0]["event_json"])
    assert payload["content"] == "small raw result"
    assert "raw_ref" not in payload
    assert await store.get_tool_result("missing") is None


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
    assert RAW_TOOL_RESULT_DATA_KEY not in payload.get("data", {})

    manifest = await store.get_tool_result(payload["raw_ref"])
    assert manifest is not None
    assert manifest["content"] == raw


@pytest.mark.asyncio
async def test_compaction_boundary_round_trip(store: SessionStore):
    rehydration_state = {"active_plan_ref": "plan:abc", "pending_approval_ids": ["call-1"]}
    await store.record_chat_compaction(
        compaction_id="compact-1",
        session_id="sess-1",
        boundary_seq=12,
        messages_before=20,
        messages_after=5,
        rehydration_state=rehydration_state,
    )

    compactions = await store.list_chat_compactions("sess-1")

    assert len(compactions) == 1
    assert compactions[0]["compaction_id"] == "compact-1"
    assert compactions[0]["boundary_seq"] == 12
    assert compactions[0]["messages_before"] == 20
    assert compactions[0]["messages_after"] == 5
    assert compactions[0]["rehydration_state"] == rehydration_state


@pytest.mark.asyncio
async def test_save_updates_existing_session(store: SessionStore):
    state = _make_state()
    await store.save_session(state, [{"role": "user", "content": "First"}])
    await store.save_session(
        state,
        [
            {"role": "user", "content": "First"},
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


@pytest.mark.asyncio
async def test_update_progress_keeps_metadata_on_existing_session(store: SessionStore):
    """Mid-run checkpoints must not clobber metadata that the final save
    sets (e.g. last_input_tokens used for compaction)."""
    state = _make_state("with-meta")
    await store.save_session(
        state,
        [{"role": "user", "content": "hi"}],
        metadata={"last_input_tokens": 1234},
    )
    await store.update_progress(
        state,
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "yo"},
        ],
    )
    loaded = await store.load_session("with-meta")
    assert loaded is not None
    assert loaded.last_input_tokens == 1234
    assert len(loaded.messages) == 2


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
                {"id": "call-1", "type": "function", "function": {"name": "search_text", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "result 1"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-2", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}],
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
async def test_delete_session_messages_from_trims_reverted_future(store: SessionStore):
    state = _make_state()
    messages = [{"role": "user", "content": f"msg {i}", "client_id": f"m-{i}"} for i in range(4)]
    await store.save_session(state, messages)

    assert await store.delete_session_messages_from("test-session", message_id="m-2")

    page = await store.list_session_messages("test-session", limit=10)
    assert [row["message_id"] for row in page["messages"]] == ["m-0", "m-1"]

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
        tool_name="write_file",
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
