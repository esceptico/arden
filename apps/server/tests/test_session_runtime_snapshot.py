from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException

import arden.database as database
from arden.context.models import SessionState
from arden.context.store import SessionDataCorruptionError, SessionStore
from arden.core.compactor import token_compaction_budget
from arden.core.model_context_budget import HISTORY_TOOL_RESULT_PREVIEW_CHARS
from arden.events.sse import ThinkingEvent
from arden.integrations.base import IntegrationConnectionDescriptor
from arden.server.bus import BusRegistry
from arden.server.routers import session as session_router
from arden.server.routers.session import (
    _history_tool_calls,
    get_session_history,
    list_sessions,
    update_session_model,
)
from arden.server.schemas import UpdateSessionModelRequest
from arden.server.state import RunRegistry, RunStatus
from arden.services.session import SessionService


@pytest_asyncio.fixture
async def session_service(tmp_path: Path):
    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    store = SessionStore(conn, read_conn)
    await store.init_schema()
    yield SessionService(store)
    await read_conn.close()
    await conn.close()


def _state(session_id: str) -> SessionState:
    return SessionState(session_id=session_id, started_at=datetime.now(UTC), name="runtime test")


def _runtime(*, run_registry: RunRegistry | None = None, executor=None):
    return SimpleNamespace(
        run_registry=run_registry or RunRegistry(),
        executor=executor,
        config=SimpleNamespace(
            chat_model="openai-codex/gpt-5.6-sol",
            compression_threshold=0.8,
            max_messages=120,
        ),
    )


def test_history_tool_calls_orders_provider_search_before_loaded_tools():
    msg = {
        "provider_tool_calls": [
            {
                "id": "tsc_1",
                "name": "tool_search",
                "arguments": '{"tools":["slack_search"]}',
                "result": "Matched tools: slack_search",
            }
        ],
        "tool_calls": [
            {
                "id": "call_1",
                "function": {"name": "slack_search", "arguments": '{"query":"after:2026-06-28"}'},
            }
        ],
    }

    calls = _history_tool_calls(msg, lambda name: ("tool", None))

    assert [call["name"] for call in calls] == ["tool_search", "slack_search"]
    assert calls[0]["result"] == "Matched tools: slack_search"


@pytest.mark.asyncio
async def test_explicit_missing_history_returns_not_found(session_service: SessionService):
    with pytest.raises(HTTPException) as exc_info:
        await get_session_history(
            session_service,
            _runtime(),
            BusRegistry(),
            "missing-session",
            limit=100,
            around_seq=None,
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_empty_history_returns_the_complete_default_context_budget(session_service: SessionService):
    runtime = _runtime()

    result = await get_session_history(
        session_service,
        runtime,
        BusRegistry(),
        session_id=None,
        limit=100,
        around_seq=None,
    )

    expected = token_compaction_budget(runtime.config.chat_model, threshold=runtime.config.compression_threshold)
    assert result["context_budget"] == {
        "model": runtime.config.chat_model,
        "hard_limit": expected.hard_limit,
        "compaction_trigger": expected.compaction_trigger,
        "message_limit": runtime.config.max_messages,
        "input_tokens": 0,
        "message_count": 0,
    }


@pytest.mark.asyncio
async def test_history_header_storage_errors_are_not_treated_as_missing(
    session_service: SessionService,
    monkeypatch: pytest.MonkeyPatch,
):
    async def fail_load(_session_id: str):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(session_service.store, "load_session_history_header", fail_load)

    with pytest.raises(RuntimeError, match="database unavailable"):
        await session_service.load_history_header("sess-broken")


@pytest.mark.asyncio
async def test_history_includes_runtime_snapshot_for_active_session(session_service: SessionService):
    state = _state("sess-runtime")
    await session_service.save(state, [{"role": "user", "content": "hi", "client_id": "msg-1"}])
    await session_service.store.record_chat_run_started("run-1", "sess-runtime")
    await session_service.store.record_chat_run_status("run-1", "running", last_seq=12)
    await session_service.store.record_tool_approval_requested(
        run_id="run-1",
        session_id="sess-runtime",
        tool_call_id="tool-1",
        tool_name="file_write",
        action="write",
        scope="internal",
        diff="diff",
    )
    await session_service.store.record_chat_queued_message(
        client_id="queued-1",
        session_id="sess-runtime",
        run_id="run-1",
        message={
            "role": "user",
            "content": "follow up",
            "client_id": "queued-1",
        },
        enqueued_seq=13,
    )

    runtime = _runtime()
    result = await get_session_history(
        session_service, runtime, BusRegistry(), "sess-runtime", limit=100, around_seq=None
    )

    assert result["active_run_id"] == "run-1"
    assert result["runtime"]["checkpoint_seq"] == 12
    assert result["runtime"]["active_run"]["status"] == "running"
    assert result["runtime"]["pending_approvals"][0]["tool_id"] == "tool-1"
    assert result["runtime"]["queued_messages"] == []


@pytest.mark.asyncio
async def test_history_context_budget_uses_session_model_and_exact_compaction_trigger(
    session_service: SessionService,
):
    state = _state("sess-budget")
    state.chat_model = "gpt-5.2"
    messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    await session_service.save(
        state,
        messages,
        metadata={"last_input_tokens": 12_345},
    )
    runtime = SimpleNamespace(
        run_registry=RunRegistry(),
        executor=None,
        config=SimpleNamespace(
            chat_model="openai-codex/gpt-5.6-sol",
            compression_threshold=0.8,
            max_messages=99,
        ),
    )

    result = await get_session_history(
        session_service, runtime, BusRegistry(), "sess-budget", limit=100, around_seq=None
    )

    expected = token_compaction_budget("gpt-5.2", threshold=0.8)
    assert result["context_budget"] == {
        "model": "gpt-5.2",
        "hard_limit": expected.hard_limit,
        "compaction_trigger": expected.compaction_trigger,
        "message_limit": 99,
        "input_tokens": 12_345,
        "message_count": 2,
    }


@pytest.mark.asyncio
async def test_history_context_budget_rejects_unknown_model(
    session_service: SessionService,
):
    state = _state("sess-unknown-budget")
    state.chat_model = "custom/missing-model"
    await session_service.save(
        state,
        [{"role": "user", "content": "hi"}],
        metadata={"last_input_tokens": 321},
    )
    runtime = _runtime()

    with pytest.raises(HTTPException) as exc_info:
        await get_session_history(
            session_service,
            runtime,
            BusRegistry(),
            "sess-unknown-budget",
            limit=100,
            around_seq=None,
        )
    assert exc_info.value.status_code == 409
    assert "Select another model" in exc_info.value.detail


@pytest.mark.asyncio
async def test_history_pages_large_transcript_without_loading_full_session(
    session_service: SessionService,
    monkeypatch: pytest.MonkeyPatch,
):
    state = _state("sess-large-history")
    messages = [{"role": "user", "content": f"message {index}"} for index in range(1_001)]
    await session_service.save(
        state,
        messages,
        metadata={"last_input_tokens": 45_678},
    )

    async def fail_full_load(*_args, **_kwargs):
        raise AssertionError("paged history must not load the full session")

    monkeypatch.setattr(session_service, "load", fail_full_load)
    monkeypatch.setattr(session_service.store, "load_session", fail_full_load)
    result = await get_session_history(
        session_service,
        _runtime(),
        BusRegistry(),
        state.session_id,
        limit=3,
        around_seq=None,
    )

    assert [message["content"] for message in result["messages"]] == [
        "message 998",
        "message 999",
        "message 1000",
    ]
    assert result["page"]["has_more_before"] is True
    assert "usage" not in result
    assert result["context_budget"]["message_count"] == 1_001


@pytest.mark.asyncio
async def test_history_budget_uses_active_projection_count(session_service: SessionService):
    state = _state("sess-history-count-fallback")
    messages = [{"role": "user", "content": f"message {index}"} for index in range(7)]
    await session_service.save(state, messages, metadata={"last_input_tokens": 123})

    result = await get_session_history(
        session_service,
        _runtime(),
        BusRegistry(),
        state.session_id,
        limit=3,
        around_seq=None,
    )

    assert len(result["messages"]) == 3
    assert "usage" not in result
    assert result["context_budget"]["message_count"] == 7


@pytest.mark.asyncio
async def test_history_does_not_use_immutable_count_for_compacted_context(session_service: SessionService):
    state = _state("sess-compacted-history-count")
    original = [
        {"role": "user", "content": "first", "client_id": "u-1"},
        {"role": "assistant", "content": "reply", "client_id": "a-1"},
        {"role": "user", "content": "second", "client_id": "u-2"},
    ]
    await session_service.save(state, original)
    await session_service.save(
        state,
        [
            {"role": "assistant", "content": "Summary of earlier chat.", "client_id": "summary-1"},
            original[-1],
        ],
    )

    result = await get_session_history(
        session_service,
        _runtime(),
        BusRegistry(),
        state.session_id,
        limit=10,
        around_seq=None,
    )

    assert len(result["messages"]) == 4
    assert "usage" not in result
    assert result["context_budget"]["message_count"] == 2


@pytest.mark.asyncio
async def test_history_does_not_repair_missing_transcript_at_read_time(session_service: SessionService):
    state = _state("sess-legacy-history")
    messages = [{"role": "user", "content": f"legacy {index}"} for index in range(5)]
    await session_service.save(state, messages)
    await session_service.store.conn.execute(
        "DELETE FROM session_messages WHERE session_id = ?",
        (state.session_id,),
    )
    await session_service.store.conn.commit()

    result = await get_session_history(
        session_service,
        _runtime(),
        BusRegistry(),
        state.session_id,
        limit=2,
        around_seq=None,
    )
    transcript_rows = await session_service.store.read_conn.execute_fetchall(
        "SELECT COUNT(*) AS count FROM session_messages WHERE session_id = ?",
        (state.session_id,),
    )

    assert result["messages"] == []
    assert "usage" not in result
    assert transcript_rows[0]["count"] == 0


@pytest.mark.asyncio
async def test_history_malformed_metadata_surfaces_corruption(session_service: SessionService):
    state = _state("sess-malformed-history-metadata")
    await session_service.save(
        state,
        [{"role": "user", "content": "one"}, {"role": "assistant", "content": "two"}],
    )
    await session_service.store.conn.execute(
        "UPDATE sessions SET metadata = ? WHERE session_id = ?",
        ("{", state.session_id),
    )
    await session_service.store.conn.commit()

    with pytest.raises(SessionDataCorruptionError, match="metadata is invalid JSON"):
        await get_session_history(
            session_service,
            _runtime(),
            BusRegistry(),
            state.session_id,
            limit=1,
            around_seq=None,
        )


@pytest.mark.asyncio
async def test_model_update_returns_the_new_session_budget(session_service: SessionService):
    state = _state("sess-model-update")
    await session_service.save(
        state,
        [{"role": "user", "content": "hi"}],
        metadata={"last_input_tokens": 456},
    )
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            chat_model="openai-codex/gpt-5.6-sol",
            compression_threshold=0.8,
            max_messages=88,
        )
    )

    result = await update_session_model(
        "sess-model-update",
        UpdateSessionModelRequest(chat_model="gpt-5.2"),
        session_service,
        RunRegistry(),
        runtime,
    )

    expected = token_compaction_budget("gpt-5.2", threshold=0.8)
    assert result["context_budget"] == {
        "model": "gpt-5.2",
        "hard_limit": expected.hard_limit,
        "compaction_trigger": expected.compaction_trigger,
        "message_limit": 88,
        "input_tokens": 456,
        "message_count": 1,
    }


@pytest.mark.asyncio
async def test_model_update_rejects_unknown_model_before_persisting(session_service: SessionService):
    state = _state("sess-invalid-model-update")
    await session_service.save(state, [{"role": "user", "content": "hi"}])

    with pytest.raises(HTTPException) as exc_info:
        await update_session_model(
            state.session_id,
            UpdateSessionModelRequest(chat_model="custom/missing-model"),
            session_service,
            RunRegistry(),
            _runtime(),
        )
    assert exc_info.value.status_code == 422
    assert "Unknown model: custom/missing-model" in exc_info.value.detail

    loaded = await session_service.load(state.session_id)
    assert loaded is not None
    assert loaded.state.chat_model is None


@pytest.mark.asyncio
async def test_history_runtime_snapshot_includes_pending_connection(session_service: SessionService):
    state = _state("sess-connection")
    await session_service.save(state, [{"role": "user", "content": "check email"}])
    await session_service.store.record_chat_run_started("run-connection", "sess-connection")
    await session_service.store.record_chat_run_status("run-connection", "running", last_seq=4)
    await session_service.store.record_integration_connection_requested(
        run_id="run-connection",
        session_id="sess-connection",
        tool_call_id="call-connection",
        descriptor=IntegrationConnectionDescriptor(
            integration_id="gmail",
            connection_id="google",
            label="Gmail",
            capability="Search, read, and send email",
            action="oauth",
            settings_tab="integrations",
            state="auth_required",
            detail="Reconnect Gmail",
            required_scopes=("gmail.readonly",),
            tool_names=("emails",),
        ),
        source="recovery",
        detail="Reconnect Gmail",
    )

    runtime = _runtime()
    result = await get_session_history(
        session_service, runtime, BusRegistry(), "sess-connection", limit=100, around_seq=None
    )

    assert result["runtime"]["pending_connections"] == [
        {
            "tool_id": "call-connection",
            "integration_id": "gmail",
            "connection_id": "google",
            "label": "Gmail",
            "reason": "auth_required",
            "detail": "Reconnect Gmail",
            "capability": "Search, read, and send email",
            "action": "oauth",
            "settings_tab": "integrations",
            "required_scopes": ["gmail.readonly"],
            "source": "recovery",
            "status": "pending",
            "requested_at": result["runtime"]["pending_connections"][0]["requested_at"],
            "run_id": "run-connection",
        }
    ]


@pytest.mark.asyncio
async def test_history_runtime_snapshot_reports_live_backgrounded_run(session_service: SessionService):
    state = _state("sess-live-backgrounded")
    await session_service.save(state, [{"role": "user", "content": "hi", "client_id": "msg-1"}])
    registry = RunRegistry()
    run = registry.create_run("sess-live-backgrounded")
    run.status = RunStatus.RUNNING
    run.backgrounded = True

    runtime = _runtime(run_registry=registry)
    result = await get_session_history(
        session_service, runtime, BusRegistry(), "sess-live-backgrounded", limit=100, around_seq=None
    )

    assert result["runtime"]["active_run"]["run_id"] == run.run_id
    assert result["runtime"]["active_run"]["status"] == "backgrounded"


@pytest.mark.asyncio
async def test_history_runtime_snapshot_hides_stale_queue_rows(session_service: SessionService):
    state = _state("sess-stale-queue")
    await session_service.save(state, [{"role": "user", "content": "hi", "client_id": "msg-1"}])
    await session_service.store.record_chat_run_started("run-old", "sess-stale-queue")
    await session_service.store.record_chat_queued_message(
        client_id="queued-old",
        session_id="sess-stale-queue",
        run_id="run-old",
        message={
            "role": "user",
            "content": "old queued",
            "client_id": "queued-old",
        },
        enqueued_seq=5,
    )
    await session_service.store.record_chat_run_status("run-old", "interrupted", last_seq=6)

    await session_service.store.record_chat_run_started("run-new", "sess-stale-queue")
    await session_service.store.record_chat_run_status("run-new", "running", last_seq=7)
    await session_service.store.record_chat_queued_message(
        client_id="queued-new",
        session_id="sess-stale-queue",
        run_id="run-new",
        message={
            "role": "user",
            "content": "new queued",
            "client_id": "queued-new",
        },
        enqueued_seq=8,
    )

    runtime = _runtime()
    result = await get_session_history(
        session_service, runtime, BusRegistry(), "sess-stale-queue", limit=100, around_seq=None
    )

    assert result["runtime"]["active_run"]["run_id"] == "run-new"
    assert result["runtime"]["queued_messages"] == []


@pytest.mark.asyncio
async def test_history_runtime_snapshot_omits_retryable_queue_after_terminal_run(
    session_service: SessionService,
):
    state = _state("sess-terminal-queue")
    await session_service.save(state, [{"role": "user", "content": "hi", "client_id": "msg-1"}])
    await session_service.store.record_chat_run_started("run-1", "sess-terminal-queue")
    await session_service.store.record_chat_queued_message(
        client_id="queued-1",
        session_id="sess-terminal-queue",
        run_id="run-1",
        message={
            "role": "user",
            "content": "retryable",
            "client_id": "queued-1",
        },
        enqueued_seq=9,
    )
    await session_service.store.record_chat_run_status("run-1", "interrupted", last_seq=10)
    await session_service.store.mark_interrupted_chat_queued_messages_retryable()

    runtime = _runtime()
    result = await get_session_history(
        session_service, runtime, BusRegistry(), "sess-terminal-queue", limit=100, around_seq=None
    )

    assert result["runtime"]["active_run"]["status"] == "interrupted"
    assert result["runtime"]["queued_messages"] == []


@pytest.mark.asyncio
async def test_history_clamps_huge_persisted_tool_content(session_service: SessionService):
    state = _state("sess-huge-tool")
    huge_result = "x" * (HISTORY_TOOL_RESULT_PREVIEW_CHARS + 10_000)
    await session_service.save(
        state,
        [
            {"role": "user", "content": "search"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "file_search_text", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": huge_result},
        ],
    )

    runtime = _runtime()
    result = await get_session_history(
        session_service, runtime, BusRegistry(), "sess-huge-tool", limit=100, around_seq=None
    )

    tool_message = result["messages"][-1]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call-1"
    assert len(tool_message["content"]) < len(huge_result)
    assert "Tool result compacted for history display" in tool_message["content"]
    assert huge_result not in tool_message["content"]


@pytest.mark.asyncio
async def test_history_ignores_legacy_null_tool_calls(session_service: SessionService):
    state = _state("sess-null-tool-calls")
    await session_service.save(
        state,
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello", "tool_calls": None},
        ],
    )

    runtime = _runtime()
    result = await get_session_history(
        session_service, runtime, BusRegistry(), "sess-null-tool-calls", limit=100, around_seq=None
    )

    assistant = result["messages"][-1]
    assert assistant["role"] == "assistant"
    assert assistant["content"] == "hello"
    assert "tool_calls" not in assistant


@pytest.mark.asyncio
async def test_history_skips_malformed_tool_calls_but_keeps_valid_calls(session_service: SessionService):
    state = _state("sess-malformed-tool-calls")
    await session_service.save(
        state,
        [
            {"role": "user", "content": "run tools"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    None,
                    {"id": "missing-function"},
                    {"id": 123, "function": {"name": "bash", "arguments": "{}"}},
                    {"id": "missing-name", "function": {"arguments": "{}"}},
                    {"id": "call-1", "function": {"name": "bash", "arguments": {"cmd": "date"}}},
                    {"id": "call-2", "function": {"name": "file_read", "arguments": '{"path":"a"}'}},
                ],
            },
        ],
    )

    runtime = _runtime()
    result = await get_session_history(
        session_service, runtime, BusRegistry(), "sess-malformed-tool-calls", limit=100, around_seq=None
    )

    assistant = result["messages"][-1]
    assert assistant["tool_calls"] == [
        {"id": "call-1", "name": "bash", "arguments": "{}", "kind": "tool"},
        {"id": "call-2", "name": "file_read", "arguments": '{"path":"a"}', "kind": "tool"},
    ]


@pytest.mark.asyncio
async def test_history_includes_tool_display_name_when_executor_knows_tool(session_service: SessionService):
    state = _state("sess-tool-display-name")
    await session_service.save(
        state,
        [
            {"role": "user", "content": "search"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {
                            "name": "file_search_text",
                            "arguments": '{"query":"ToolCallArgsEvent","path":"."}',
                        },
                    },
                ],
            },
        ],
    )

    class Registry:
        def get(self, name: str):
            if name == "file_search_text":
                return SimpleNamespace(kind="tool", display_name="SearchText")
            return None

    runtime = _runtime(executor=SimpleNamespace(registry=Registry()))
    result = await get_session_history(
        session_service, runtime, BusRegistry(), "sess-tool-display-name", limit=100, around_seq=None
    )

    assert result["messages"][-1]["tool_calls"] == [
        {
            "id": "call-1",
            "name": "file_search_text",
            "arguments": '{"query":"ToolCallArgsEvent","path":"."}',
            "kind": "tool",
            "display_name": "SearchText",
        }
    ]


@pytest.mark.asyncio
async def test_history_includes_tool_result_data(session_service: SessionService):
    state = _state("sess-tool-result-data")
    data = {
        "child_agent": {
            "child_run_id": "child-run-123456",
            "parent_tool_call_id": "call-1",
            "agent_type": "background_research",
            "wait": False,
            "status": "running",
        }
    }
    await session_service.save(
        state,
        [
            {"role": "user", "content": "research"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {"name": "background", "arguments": '{"task":"research"}'},
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "content": "Started background agent.",
                "data": data,
            },
        ],
    )

    runtime = _runtime()
    result = await get_session_history(
        session_service, runtime, BusRegistry(), "sess-tool-result-data", limit=100, around_seq=None
    )

    tool_message = result["messages"][-1]
    assert tool_message["role"] == "tool"
    assert tool_message["data"] == data


@pytest.mark.asyncio
async def test_history_hydrates_tool_outcomes_from_durable_calls(session_service: SessionService):
    state = _state("sess-tool-outcome")
    await session_service.save(
        state,
        [
            {"role": "user", "content": "write"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {"name": "file_write", "arguments": '{"path":"a.txt"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "done"},
        ],
    )
    await session_service.store.record_tool_call_started(
        run_id="run-1",
        session_id="sess-tool-outcome",
        tool_call_id="call-1",
        tool_name="file_write",
        action="write",
        scope="internal",
    )
    await session_service.store.record_tool_call_finished(
        run_id="run-1",
        tool_call_id="call-1",
        status="success",
        result_preview="done",
        outcome={
            "status": "succeeded",
            "effect": {"operation": "write", "target": "a.txt"},
            "receipt": "sha256:abc",
        },
    )

    result = await get_session_history(
        session_service,
        _runtime(),
        BusRegistry(),
        "sess-tool-outcome",
        limit=100,
        around_seq=None,
    )

    assistant = next(message for message in result["messages"] if message.get("tool_calls"))
    assert assistant["tool_calls"][0]["outcome"] == {
        "status": "succeeded",
        "effect": {"operation": "write", "target": "a.txt"},
        "receipt": "sha256:abc",
    }


@pytest.mark.asyncio
async def test_turn_inspector_returns_exact_nullable_sidecar(session_service: SessionService):
    await session_service.store.record_chat_run_started(
        "run-1",
        "sess-inspector",
        metadata={"client_id": "turn-1"},
    )
    await session_service.store.record_run_context_manifest(
        run_id="run-1",
        session_id="sess-inspector",
        manifest=[
            {
                "context_id": "ctx-1",
                "content_type": "memory",
                "source": "memory",
                "ref": "record:1",
                "freshness": "current",
                "selection_reason": "relevant",
                "size_bytes": 42,
            }
        ],
    )

    result = await session_router.get_turn_inspector("sess-inspector", "turn-1", session_service)

    assert result and result["run_id"] == "run-1"
    assert result["context_manifest"][0]["context_id"] == "ctx-1"
    assert await session_router.get_turn_inspector("sess-inspector", "missing", session_service) is None
    assert await session_router.get_turn_inspector("other-session", "turn-1", session_service) is None


@pytest.mark.asyncio
async def test_history_omits_non_durable_tool_result_data(session_service: SessionService):
    state = _state("sess-tool-large-data")
    await session_service.save(
        state,
        [
            {"role": "user", "content": "search"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {"name": "file_search_text", "arguments": '{"query":"x"}'},
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "content": "many matches",
                "data": {"matches": [{"path": "a.py", "text": "x" * 1000}]},
            },
        ],
    )

    runtime = _runtime()
    result = await get_session_history(
        session_service, runtime, BusRegistry(), "sess-tool-large-data", limit=100, around_seq=None
    )

    tool_message = result["messages"][-1]
    assert tool_message["role"] == "tool"
    assert "data" not in tool_message


@pytest.mark.asyncio
async def test_history_runtime_snapshot_keeps_live_tail_after_checkpoint(session_service: SessionService):
    state = _state("sess-live-tail")
    await session_service.save(state, [{"role": "user", "content": "hi", "client_id": "msg-1"}])
    await session_service.store.record_chat_run_started("run-1", "sess-live-tail")
    await session_service.store.record_chat_run_status("run-1", "running", last_seq=1)

    buses = BusRegistry()
    bus = buses.get_or_create("sess-live-tail")
    await bus.emit(ThinkingEvent(status="checkpointed"))
    bus.mark_checkpoint()
    await bus.emit(ThinkingEvent(status="live-tail"))

    runtime = _runtime()
    result = await get_session_history(session_service, runtime, buses, "sess-live-tail", limit=100, around_seq=None)

    assert result["runtime"]["checkpoint_seq"] == 1
    assert result["runtime"]["latest_event_seq"] == 2
    assert result["runtime"]["active_run"]["checkpoint_seq"] == 1
    assert result["runtime"]["active_run"]["latest_event_seq"] == 2


@pytest.mark.asyncio
async def test_sessions_list_surfaces_interrupted_runtime_state(session_service: SessionService):
    state = _state("sess-interrupted")
    await session_service.save(state, [])
    await session_service.store.record_chat_run_started("run-interrupted", "sess-interrupted")
    await session_service.store.record_chat_run_status(
        "run-interrupted",
        "interrupted",
        stop_reason="server_restart",
        last_seq=22,
        error_code="run_interrupted",
    )

    runtime = _runtime()
    result = await list_sessions(session_service, runtime, BusRegistry())

    session = result["sessions"][0]
    assert session["session_id"] == "sess-interrupted"
    assert session["active_run_id"] == "run-interrupted"
    assert session["run_status"] == "interrupted"
    assert session["checkpoint_seq"] == 22
    assert session["is_active"] is False
    assert session["run_error_code"] == "run_interrupted"
