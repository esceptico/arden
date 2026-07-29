import pytest
from pydantic import BaseModel

from arden.agent import Agent, AgentHooks, Result, StopReason, ToolCompleted, ToolStarted
from arden.constants import AGENT_MAX_OUTPUT_TOKENS
from arden.tools.core import EmptyInput, Tool, ToolAction, ToolPolicy, ToolResult, ToolScope, tool
from arden.tools.core.context import ToolExecution
from tests.helpers import (
    MockCompletionClient,
    MockLLMClient,
    make_executor,
    make_test_executor,
    make_text_response,
    make_tool_response,
)


class EchoInput(BaseModel):
    text: str = ""


async def echo(execution: ToolExecution, args: EchoInput) -> ToolResult:
    return ToolResult(content=f"echo: {args.text}", preview="echo")


async def fail(execution: ToolExecution, args: EmptyInput) -> ToolResult:
    raise RuntimeError("tool crashed")


READ_INTERNAL_POLICY = ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL)

ECHO_TOOL = tool(
    display_name="Echo",
    description="Echoes input back",
    input_model=EchoInput,
    policy=READ_INTERNAL_POLICY,
    execute=echo,
)
FAIL_TOOL = tool(display_name="Fail", description="Always fails", policy=READ_INTERNAL_POLICY, execute=fail)


def _make_agent(mock_client: MockCompletionClient, tools: dict[str, Tool] | None = None) -> Agent:
    executor = make_executor(tools)
    return Agent(
        tools=executor.get_tools(),
        client=MockLLMClient(mock_client),
        executor=make_test_executor(executor),
        model="test-model",
    )


def _msgs(text: str) -> list[dict]:
    return [{"role": "system", "content": "test"}, {"role": "user", "content": text}]


@pytest.mark.asyncio
async def test_simple_text_response():
    client = MockCompletionClient([make_text_response("Hello!")])
    agent = _make_agent(client)
    result = await agent.run(_msgs("Hi"))
    assert result.text == "Hello!"
    assert result.stop_reason == StopReason.END_TURN
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_agent_passes_reasoning_effort_to_llm_client():
    client = MockCompletionClient([make_text_response("Hello!")])
    executor = make_executor()
    agent = Agent(
        tools=[],
        client=MockLLMClient(client),
        executor=make_test_executor(executor),
        model="test-model",
        reasoning_effort="high",
    )

    await agent.run(_msgs("Hi"))

    assert client.calls[0]["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_tool_call_then_response():
    client = MockCompletionClient(
        [make_tool_response("echo", {"text": "world"}), make_text_response("Got: echo: world")]
    )
    agent = _make_agent(client, {"echo": ECHO_TOOL})
    messages = _msgs("Echo world")
    result = await agent.run(messages)
    assert result.text == "Got: echo: world"
    assert result.steps == 1
    tool_msg = [m for m in messages if m["role"] == "tool"]
    assert len(tool_msg) == 1
    assert "echo: world" in tool_msg[0]["content"]


@pytest.mark.asyncio
async def test_tool_error_handled():
    client = MockCompletionClient([make_tool_response("fail", {}), make_text_response("Tool failed, sorry.")])
    agent = _make_agent(client, {"fail": FAIL_TOOL})
    messages = _msgs("Do something")
    result = await agent.run(messages)
    assert result.text == "Tool failed, sorry."
    tool_msg = [m for m in messages if m["role"] == "tool"]
    assert len(tool_msg) == 1
    assert "tool crashed" not in tool_msg[0]["content"]
    assert "Diagnostic reference:" in tool_msg[0]["content"]


@pytest.mark.asyncio
async def test_unknown_tool_returns_error():
    client = MockCompletionClient(
        [make_tool_response("nonexistent", {"x": 1}), make_text_response("I couldn't find that tool.")]
    )
    agent = _make_agent(client)
    result = await agent.run(_msgs("Call missing tool"))
    assert result.text == "I couldn't find that tool."


@pytest.mark.asyncio
async def test_stream_yields_events_in_order():
    client = MockCompletionClient([make_tool_response("echo", {"text": "hi"}), make_text_response("Done.")])
    agent = _make_agent(client, {"echo": ECHO_TOOL})
    events = []
    async for item in agent.stream(_msgs("test")):
        events.append(item)
    assert any(isinstance(e, ToolStarted) for e in events)
    assert any(isinstance(e, ToolCompleted) for e in events)
    assert isinstance(events[-1], Result)
    assert events[-1].text == "Done."
    assert events[-1].stop_reason == StopReason.END_TURN


@pytest.mark.asyncio
async def test_max_depth_stops_agent():
    client = MockCompletionClient([make_text_response("Should not run")])
    executor = make_executor()
    agent = Agent(
        tools=[],
        client=MockLLMClient(client),
        executor=make_test_executor(executor),
        model="test-model",
        max_depth=2,
        current_depth=2,
    )
    result = await agent.run(_msgs("Hi"))
    assert result.stop_reason == StopReason.MAX_DEPTH
    assert len(client.calls) == 0


@pytest.mark.asyncio
async def test_pending_messages_hook():
    client = MockCompletionClient([make_tool_response("echo", {"text": "first"}), make_text_response("Final answer")])
    pending = [{"role": "user", "content": "injected message"}]

    async def get_pending() -> list[dict]:
        batch = list(pending)
        pending.clear()
        return batch

    executor = make_executor({"echo": ECHO_TOOL})
    agent = Agent(
        tools=executor.get_tools(),
        client=MockLLMClient(client),
        executor=make_test_executor(executor),
        model="test-model",
        hooks=AgentHooks(get_pending_messages=get_pending),
    )
    messages = _msgs("Start")
    result = await agent.run(messages)
    assert result.text == "Final answer"
    contents = [m.get("content") for m in messages]
    assert "injected message" in contents


@pytest.mark.asyncio
async def test_create_agent_returns_agent_with_hooks():
    """Smoke test: create_agent should return an Agent instance with working hooks attribute."""
    from datetime import UTC, datetime

    from arden.context.models import SessionState
    from arden.core.factory import AgentConfig, create_agent
    from arden.tools.executor import ToolExecutor

    executor = ToolExecutor(get_services=dict)
    config = AgentConfig(model="claude-sonnet-4-6", research_model=None, max_depth=3)
    session_state = SessionState(session_id="test", started_at=datetime.now(UTC))

    agent = create_agent(
        executor=executor,
        config=config,
        tools=[],
        session_state=session_state,
        run_id="test-run",
    )

    assert isinstance(agent, Agent)
    assert hasattr(agent, "hooks")
    assert isinstance(agent.hooks, AgentHooks)
    assert agent.model_request_middlewares
    assert agent.prompt_cache_key == "test"


def test_create_agent_wires_run_budget_config():
    from datetime import UTC, datetime

    from arden.context.models import SessionState
    from arden.core.factory import AgentConfig, create_agent
    from arden.core.usage_tracker import UsageTracker
    from arden.tools.executor import ToolExecutor

    tracker = UsageTracker()
    executor = ToolExecutor(get_services=dict)
    config = AgentConfig(
        model="claude-sonnet-4-6",
        research_model=None,
        max_depth=3,
        max_iterations=4,
        max_tool_calls=5,
        max_wall_time_seconds=6.0,
        max_cost=7.0,
    )
    session_state = SessionState(session_id="test", started_at=datetime.now(UTC))

    agent = create_agent(
        executor=executor,
        config=config,
        tools=[],
        session_state=session_state,
        run_id="test-run",
        parent_tracker=tracker,
    )

    assert agent.max_iterations == 4
    assert agent.max_tool_calls == 5
    assert agent.max_wall_time_seconds == 6.0
    assert agent.max_cost == 7.0
    tracker.cost = 8.0
    assert agent.cost_getter is not None
    assert agent.cost_getter() == 8.0


def test_agent_config_uses_finite_default_output_token_budget():
    from arden.config import Config
    from arden.core.factory import AgentConfig

    config = AgentConfig.from_config(Config(chat_model="claude-sonnet-4-6"))

    assert config.max_output_tokens == AGENT_MAX_OUTPUT_TOKENS == 500_000
