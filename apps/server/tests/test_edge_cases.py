"""Edge case tests — probing for real bugs."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio

import arden.database as database
from arden.agent import Agent
from arden.context.models import SessionState
from arden.context.store import SessionStore
from arden.core.public_refs import public_ref
from arden.tools.core import EmptyInput, Tool, ToolAction, ToolPolicy, ToolResult, ToolScope, tool
from arden.tools.core.background_tasks import BackgroundTaskRegistry
from arden.tools.core.context import ToolExecution
from tests.helpers import (
    MockCompletionClient,
    MockLLMClient,
    make_executor,
    make_test_executor,
    make_text_response,
    make_tool_response,
)

# --- Helpers ---


async def slow(execution: ToolExecution, args: EmptyInput) -> ToolResult:
    await asyncio.sleep(0.1)
    return ToolResult(content="done", preview="done")


SLOW_TOOL = tool(
    display_name="Slow",
    description="Takes a while",
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL),
    execute=slow,
)


def _make_agent(client, tools: dict[str, Tool] | None = None) -> Agent:
    executor = make_executor(tools)
    return Agent(
        tools=executor.get_tools(),
        client=MockLLMClient(client),
        executor=make_test_executor(executor),
        model="test-model",
    )


# --- Agent edge cases ---


@pytest.mark.asyncio
async def test_agent_multiple_tool_calls_in_one_turn():
    """LLM returns multiple tool calls in a single response."""

    from arden.agent import Choice, CompletionResponse, FunctionCall, Message, ToolCall, Usage

    multi_tool = CompletionResponse(
        choices=[
            Choice(
                message=Message(
                    role="assistant",
                    content=None,
                    tool_calls=[
                        ToolCall(id="call_1", type="function", function=FunctionCall(name="slow", arguments="{}")),
                        ToolCall(id="call_2", type="function", function=FunctionCall(name="slow", arguments="{}")),
                    ],
                    reasoning_content=None,
                ),
                finish_reason="tool_calls",
            )
        ],
        usage=Usage(),
        model="test-model",
    )

    client = MockCompletionClient([multi_tool, make_text_response("Both done")])
    agent = _make_agent(client, {"slow": SLOW_TOOL})
    messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "Do two things"}]

    result = await agent.run(messages)

    assert result.text == "Both done"
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    assert len(tool_msgs) == 2


@pytest.mark.asyncio
async def test_agent_empty_text_response():
    """LLM returns empty content."""
    client = MockCompletionClient([make_text_response("")])
    agent = _make_agent(client)

    result = await agent.run([{"role": "system", "content": "test"}, {"role": "user", "content": "Hi"}])

    assert result.text == ""


@pytest.mark.asyncio
async def test_agent_cancellation():
    """Agent stream can be cancelled mid-execution."""
    client = MockCompletionClient(
        [
            make_tool_response("slow", {}),
            make_text_response("Never reached"),
        ]
    )
    agent = _make_agent(client, {"slow": SLOW_TOOL})
    messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "Start"}]

    gen = agent.stream(messages)
    first = await gen.__anext__()
    assert first is not None
    await gen.aclose()

    assert messages  # has at least the system + user message


# --- Session edge cases ---


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
async def test_save_empty_messages(store: SessionStore):
    state = SessionState(session_id="empty", started_at=datetime.now(UTC))
    await store.save_session(state, [])
    loaded = await store.load_session("empty")
    assert loaded is not None
    assert loaded.messages == []


@pytest.mark.asyncio
async def test_save_large_message(store: SessionStore):
    state = SessionState(session_id="large", started_at=datetime.now(UTC))
    big_content = "x" * 100_000
    await store.save_session(state, [{"role": "user", "content": big_content}])
    loaded = await store.load_session("large")
    assert len(loaded.messages[0]["content"]) == 100_000


@pytest.mark.asyncio
async def test_save_message_with_tool_calls(store: SessionStore):
    """Messages with tool_calls (complex structure) survive round-trip."""
    state = SessionState(session_id="tools", started_at=datetime.now(UTC))
    messages = [
        {"role": "user", "content": "test"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "bash", "arguments": '{"cmd": "ls"}'}}
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "file.txt"},
        {"role": "assistant", "content": "Found file.txt"},
    ]
    await store.save_session(state, messages)
    loaded = await store.load_session("tools")
    assert len(loaded.messages) == 4
    assert loaded.messages[1]["tool_calls"] is not None


@pytest.mark.asyncio
async def test_concurrent_saves(store: SessionStore):
    """Multiple concurrent saves don't corrupt data."""
    state = SessionState(session_id="concurrent", started_at=datetime.now(UTC))

    async def save_n(n):
        msgs = [{"role": "user", "content": f"msg-{i}"} for i in range(n)]
        await store.save_session(state, msgs)

    # Run multiple saves concurrently
    await asyncio.gather(save_n(5), save_n(10), save_n(15))

    loaded = await store.load_session("concurrent")
    assert loaded is not None
    # Should have one of the saves' data (last writer wins)
    assert len(loaded.messages) in (5, 10, 15)


# --- Background task registry ---


@pytest.mark.asyncio
async def test_registry_cancel_nonexistent():
    registry = BackgroundTaskRegistry(session_id="test")
    result = registry.cancel("does-not-exist")
    assert result is None


@pytest.mark.asyncio
async def test_registry_cancel_completed_task():
    registry = BackgroundTaskRegistry(session_id="test")
    task = asyncio.create_task(asyncio.sleep(0))
    await task  # let it complete
    registry._tasks["t1"] = task
    registry._commands["t1"] = "cmd"
    result = registry.cancel("t1")
    assert result is None  # already done


@pytest.mark.asyncio
async def test_registry_list_pending():
    registry = BackgroundTaskRegistry(session_id="test")
    # Create a task that won't complete immediately
    event = asyncio.Event()
    task = asyncio.create_task(event.wait())
    registry.register("t1", task, command="test cmd")

    pending = registry.list_pending()
    assert len(pending) == 1
    assert pending[0] == ("t1", "test cmd")

    event.set()
    await task


@pytest.mark.asyncio
async def test_registry_deliver_result_emits_hidden_model_visible_terminal_event():
    registry = BackgroundTaskRegistry(session_id="sess-1")
    emitted = []

    async def emit(event):
        emitted.append(event)

    await registry.deliver_result(
        task_id="bg-1",
        result="done",
        label="research",
        status="completed",
        emit=emit,
        agent_ref=public_ref("agent", "bg-1", empty_slug="agent"),
    )

    assert emitted[0].event_id == "bg:bg-1:completed"
    assert emitted[0].model_visible is True
    assert emitted[0].ui_visible is False
