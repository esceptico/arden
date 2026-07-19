from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

import ntrp.database as database
from ntrp.context.models import SessionState
from ntrp.context.store import SessionStore
from ntrp.core.model_context_budget import HISTORY_TOOL_RESULT_PREVIEW_CHARS
from ntrp.events.sse import ThinkingEvent
from ntrp.integrations.base import IntegrationConnectionDescriptor
from ntrp.server.bus import BusRegistry
from ntrp.server.routers import session as session_router
from ntrp.server.routers.session import _history_tool_calls, get_session_history, list_sessions
from ntrp.server.state import RunRegistry, RunStatus
from ntrp.services.session import SessionService


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
async def test_history_includes_runtime_snapshot_for_active_session(session_service: SessionService):
    state = _state("sess-runtime")
    await session_service.save(state, [{"role": "user", "content": "hi", "client_id": "msg-1"}])
    await session_service.store.record_chat_run_started("run-1", "sess-runtime")
    await session_service.store.record_chat_run_status("run-1", "running", last_seq=12)
    await session_service.store.record_tool_approval_requested(
        run_id="run-1",
        session_id="sess-runtime",
        tool_call_id="tool-1",
        tool_name="write_file",
        action="write",
        scope="internal",
        diff="diff",
    )
    await session_service.store.record_chat_queued_message(
        client_id="queued-1",
        session_id="sess-runtime",
        run_id="run-1",
        message={"role": "user", "content": "follow up", "client_id": "queued-1"},
        enqueued_seq=13,
    )

    runtime = SimpleNamespace(run_registry=RunRegistry(), executor=None)
    result = await get_session_history(
        session_service, runtime, BusRegistry(), "sess-runtime", limit=100, around_seq=None
    )

    assert result["active_run_id"] == "run-1"
    assert result["runtime"]["checkpoint_seq"] == 12
    assert result["runtime"]["active_run"]["status"] == "running"
    assert result["runtime"]["pending_approvals"][0]["tool_id"] == "tool-1"
    assert result["runtime"]["queued_messages"][0]["client_id"] == "queued-1"


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

    runtime = SimpleNamespace(run_registry=RunRegistry(), executor=None)
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

    runtime = SimpleNamespace(run_registry=registry, executor=None)
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
        message={"role": "user", "content": "old queued", "client_id": "queued-old"},
        enqueued_seq=5,
    )
    await session_service.store.record_chat_run_status("run-old", "interrupted", last_seq=6)

    await session_service.store.record_chat_run_started("run-new", "sess-stale-queue")
    await session_service.store.record_chat_run_status("run-new", "running", last_seq=7)
    await session_service.store.record_chat_queued_message(
        client_id="queued-new",
        session_id="sess-stale-queue",
        run_id="run-new",
        message={"role": "user", "content": "new queued", "client_id": "queued-new"},
        enqueued_seq=8,
    )

    runtime = SimpleNamespace(run_registry=RunRegistry(), executor=None)
    result = await get_session_history(
        session_service, runtime, BusRegistry(), "sess-stale-queue", limit=100, around_seq=None
    )

    assert result["runtime"]["active_run"]["run_id"] == "run-new"
    assert [message["client_id"] for message in result["runtime"]["queued_messages"]] == ["queued-new"]


@pytest.mark.asyncio
async def test_history_runtime_snapshot_omits_retryable_queue_after_terminal_run(session_service: SessionService):
    state = _state("sess-terminal-queue")
    await session_service.save(state, [{"role": "user", "content": "hi", "client_id": "msg-1"}])
    await session_service.store.record_chat_run_started("run-1", "sess-terminal-queue")
    await session_service.store.record_chat_queued_message(
        client_id="queued-1",
        session_id="sess-terminal-queue",
        run_id="run-1",
        message={"role": "user", "content": "retryable", "client_id": "queued-1"},
        enqueued_seq=9,
    )
    await session_service.store.record_chat_run_status("run-1", "interrupted", last_seq=10)
    await session_service.store.mark_interrupted_chat_queued_messages_retryable()

    runtime = SimpleNamespace(run_registry=RunRegistry(), executor=None)
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
                        "function": {"name": "search_text", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": huge_result},
        ],
    )

    runtime = SimpleNamespace(run_registry=RunRegistry(), executor=None)
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

    runtime = SimpleNamespace(run_registry=RunRegistry(), executor=None)
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
                    {"id": "call-2", "function": {"name": "read_file", "arguments": '{"path":"a"}'}},
                ],
            },
        ],
    )

    runtime = SimpleNamespace(run_registry=RunRegistry(), executor=None)
    result = await get_session_history(
        session_service, runtime, BusRegistry(), "sess-malformed-tool-calls", limit=100, around_seq=None
    )

    assistant = result["messages"][-1]
    assert assistant["tool_calls"] == [
        {"id": "call-1", "name": "bash", "arguments": "{}", "kind": "tool"},
        {"id": "call-2", "name": "read_file", "arguments": '{"path":"a"}', "kind": "tool"},
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
                            "name": "search_text",
                            "arguments": '{"query":"ToolCallArgsEvent","path":"."}',
                        },
                    },
                ],
            },
        ],
    )

    class Registry:
        def get(self, name: str):
            if name == "search_text":
                return SimpleNamespace(kind="tool", display_name="SearchText")
            return None

    runtime = SimpleNamespace(run_registry=RunRegistry(), executor=SimpleNamespace(registry=Registry()))
    result = await get_session_history(
        session_service, runtime, BusRegistry(), "sess-tool-display-name", limit=100, around_seq=None
    )

    assert result["messages"][-1]["tool_calls"] == [
        {
            "id": "call-1",
            "name": "search_text",
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

    runtime = SimpleNamespace(run_registry=RunRegistry(), executor=None)
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
                        "function": {"name": "write_file", "arguments": '{"path":"a.txt"}'},
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
        tool_name="write_file",
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
        SimpleNamespace(run_registry=RunRegistry(), executor=None),
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
                        "function": {"name": "search_text", "arguments": '{"query":"x"}'},
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

    runtime = SimpleNamespace(run_registry=RunRegistry(), executor=None)
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

    runtime = SimpleNamespace(run_registry=RunRegistry(), executor=None)
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

    runtime = SimpleNamespace(run_registry=RunRegistry(), executor=None)
    result = await list_sessions(session_service, runtime, BusRegistry())

    session = result["sessions"][0]
    assert session["session_id"] == "sess-interrupted"
    assert session["active_run_id"] == "run-interrupted"
    assert session["run_status"] == "interrupted"
    assert session["checkpoint_seq"] == 22
    assert session["is_active"] is False
    assert session["run_error_code"] == "run_interrupted"
