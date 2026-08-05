"""More edge cases — targeting recent changes and risky paths."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from pydantic import BaseModel

import arden.database as database
import arden.tools.core.background_tasks as background_tasks_module
from arden.agent import Agent
from arden.context.models import SessionState
from arden.context.store import SessionStore
from arden.core.public_refs import public_ref
from arden.services.chat import _retain_user_content, _time_gap_note
from arden.tools.core import ToolAction, ToolPolicy, ToolResult, ToolScope, tool
from arden.tools.core.background_tasks import BackgroundTaskRegistry
from arden.tools.core.context import IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.registry import ToolRegistry
from tests.helpers import MockCompletionClient, MockLLMClient, make_executor, make_test_executor, make_text_response

# --- Context block stripping ---


def test_retain_user_content():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "context", "content_type": "page_content", "content": "some page"},
                {"type": "text", "text": "hi"},
                {"type": "image", "media_type": "image/png", "data": "..."},
                {"type": "context", "content_type": "time_since_last_message", "content": "5 minutes"},
            ],
        },
        {"role": "assistant", "content": "hello"},
    ]
    result = _retain_user_content(messages)
    content = result[0]["content"]
    assert isinstance(content, list)
    assert len(content) == 2
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image"
    # original unchanged
    assert len(messages[0]["content"]) == 4


def test_retain_user_content_ignores_string_content():
    messages = [{"role": "user", "content": "just a normal message"}]
    result = _retain_user_content(messages)
    assert result[0]["content"] == "just a normal message"


def test_retain_user_content_ignores_non_user():
    messages = [{"role": "assistant", "content": [{"type": "text", "text": "hi"}]}]
    result = _retain_user_content(messages)
    assert len(result[0]["content"]) == 1


def test_time_gap_note_short_gap():
    recent = datetime.now(UTC)
    assert _time_gap_note(recent) is None


def test_time_gap_note_long_gap():
    from datetime import timedelta

    old = datetime.now(UTC) - timedelta(hours=2)
    result = _time_gap_note(old)
    assert result is not None
    assert result["content_type"] == "time_since_last_message"
    assert "hour" in result["content"]


# --- Read connection sees committed writes ---


@pytest_asyncio.fixture
async def store(tmp_path: Path):
    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    s = SessionStore(conn, read_conn)
    await s.init_schema()
    yield s
    await read_conn.close()
    await conn.close()


@pytest.mark.asyncio
async def test_read_conn_sees_committed_write(store: SessionStore):
    """Read connection should see data after write connection commits."""
    state = SessionState(session_id="vis", started_at=datetime.now(UTC))
    await store.save_session(state, [{"role": "user", "content": "test"}])

    # Read via read_conn
    loaded = await store.load_session("vis")
    assert loaded is not None
    assert loaded.messages[0]["content"] == "test"


@pytest.mark.asyncio
async def test_read_conn_sees_updated_sessions_list(store: SessionStore):
    """list_sessions uses read_conn and should see new sessions."""
    for i in range(3):
        state = SessionState(session_id=f"s-{i}", started_at=datetime.now(UTC))
        await store.save_session(state, [{"role": "user", "content": f"msg {i}"}])

    sessions = await store.list_sessions()
    assert len(sessions) == 3


# --- Agent with text + tool_calls in same response ---


class EchoInput(BaseModel):
    text: str = ""


async def echo(execution: ToolExecution, args: EchoInput) -> ToolResult:
    return ToolResult(content=f"echo: {args.text}", preview="echo")


ECHO_TOOL = tool(
    display_name="Echo",
    description="Echoes",
    input_model=EchoInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL),
    execute=echo,
)


@pytest.mark.asyncio
async def test_agent_text_and_tool_calls():
    """Some models return both content and tool_calls. Agent should handle both."""
    from arden.agent import Choice, CompletionResponse, FunctionCall, Message, ToolCall, Usage

    mixed = CompletionResponse(
        choices=[
            Choice(
                message=Message(
                    role="assistant",
                    content="Let me check that for you.",
                    tool_calls=[
                        ToolCall(
                            id="c1", type="function", function=FunctionCall(name="echo", arguments='{"text":"hi"}')
                        ),
                    ],
                    reasoning_content=None,
                ),
                finish_reason="tool_calls",
            )
        ],
        usage=Usage(),
        model="test-model",
    )

    client = MockCompletionClient([mixed, make_text_response("Here's the result")])
    executor = make_executor({"echo": ECHO_TOOL})
    agent = Agent(
        tools=executor.get_tools(),
        client=MockLLMClient(client),
        executor=make_test_executor(executor),
        model="test-model",
    )

    messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "Check something"}]
    result = await agent.run(messages)

    assert result.text == "Here's the result"
    assistant_msgs = [m for m in messages if m["role"] == "assistant"]
    assert any("Let me check" in (m.get("content") or "") for m in assistant_msgs)


# --- Session with unicode and special chars ---


@pytest.mark.asyncio
async def test_save_unicode_content(store: SessionStore):
    state = SessionState(session_id="unicode", started_at=datetime.now(UTC))
    messages = [
        {"role": "user", "content": "日本語テスト 🎉 émojis and spëcial chars"},
        {"role": "assistant", "content": "Ответ на русском"},
    ]
    await store.save_session(state, messages)
    loaded = await store.load_session("unicode")
    assert "日本語" in loaded.messages[0]["content"]
    assert "🎉" in loaded.messages[0]["content"]
    assert "русском" in loaded.messages[1]["content"]


@pytest.mark.asyncio
async def test_save_message_with_newlines_and_quotes(store: SessionStore):
    state = SessionState(session_id="special", started_at=datetime.now(UTC))
    content = "He said \"hello\"\nand then\n\ttabbed in\nwith 'quotes' and \\backslashes"
    await store.save_session(state, [{"role": "user", "content": content}])
    loaded = await store.load_session("special")
    assert loaded.messages[0]["content"] == content


# --- Background registry: on_result callback ---


@pytest.mark.asyncio
async def test_registry_deliver_result_with_no_callback(tmp_path, monkeypatch):
    """deliver_result with on_result=None should not crash (just warn)."""
    monkeypatch.setattr(background_tasks_module, "RESULT_BASE", tmp_path)
    registry = BackgroundTaskRegistry(session_id="sess-1")
    # No on_result set — should log warning but not crash
    await registry.deliver_result(
        task_id="t1",
        result="test result",
        label="test",
        status="completed",
        emit=None,
        agent_ref=public_ref("agent", "t1", empty_slug="agent"),
    )
    # Verify result file was still written
    result_file = Path(registry._write_result_file("t1_check", "check"))
    assert result_file.exists()


# --- Approval rejection when no UI ---


@pytest.mark.asyncio
async def test_approval_rejected_when_no_ui():
    """Tools requiring approval should be rejected when no UI is connected."""
    from arden.tools.core.context import Rejection

    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1"),
        io=IOBridge(),  # no emit, no pending_approvals
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )
    execution = ToolExecution(tool_id="t1", tool_name="bash", ctx=ctx)

    result = await execution.request_approval("rm -rf something")
    assert isinstance(result, Rejection)
    assert "No UI" in result.feedback


@pytest.mark.asyncio
async def test_parallel_approvals_resolve_independently():
    """Two tools requesting approval at the same time each register
    their own Future. Resolving one's Future doesn't affect the
    other's — the old single-queue model would have raced."""
    import asyncio

    from arden.events.sse import ApprovalNeededEvent

    emitted: list = []

    async def emit(event):
        emitted.append(event)

    pending: dict[str, asyncio.Future] = {}
    io = IOBridge(emit=emit, pending_approvals=pending)
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1"),
        io=io,
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )

    e1 = ToolExecution(tool_id="t1", tool_name="bash", ctx=ctx)
    e2 = ToolExecution(tool_id="t2", tool_name="bash", ctx=ctx)

    task1 = asyncio.create_task(e1.request_approval("first"))
    task2 = asyncio.create_task(e2.request_approval("second"))

    # Wait for both to register their futures.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert set(pending.keys()) == {"t1", "t2"}

    # Resolve t2 first — t1 must still be pending.
    pending["t2"].set_result({"approved": True, "result": "ok"})
    result2 = await task2
    assert result2 is None
    assert not task1.done()

    # Now resolve t1 with rejection.
    pending["t1"].set_result({"approved": False, "result": "no"})
    result1 = await task1
    from arden.tools.core.context import Rejection

    assert isinstance(result1, Rejection)
    assert result1.feedback == "no"

    # Both events were emitted up front, both Futures are gone now.
    assert len([e for e in emitted if isinstance(e, ApprovalNeededEvent)]) == 2
    assert pending == {}
