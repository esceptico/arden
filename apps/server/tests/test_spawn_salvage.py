"""Tests for the spawn-failure salvage path: when a sub-agent's LLM call
errors mid-run, we summarize the tool results gathered so far instead of
returning a bare error string."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import arden.database as database
from arden.agent import (
    Choice,
    CompletionResponse,
    FunctionCall,
    Message,
    Result,
    StopReason,
    TextDelta,
    TextEnded,
    TextStarted,
    ToolCall,
    Usage,
)
from arden.agent.types.tools import ToolMeta
from arden.agent.types.tools import ToolResult as AgentToolResult
from arden.context.models import AreaContext, SessionState
from arden.context.store import SessionStore
from arden.core import spawner as spawner_module
from arden.core.isolation import IsolationLevel
from arden.core.spawner import (
    _clamp_for_salvage,
    _deterministic_salvage,
    _salvage_summary,
    create_spawn_fn,
)
from arden.events.sse import (
    BackgroundTaskEvent,
    TaskFinishedEvent,
    TaskProgressEvent,
    TaskStartedEvent,
    TextMessageContentEvent,
    TokenUsageEvent,
)
from arden.server.state import RunRegistry, RunStatus
from arden.services.session import SessionService
from arden.tools.core import ToolAction, ToolPolicy, ToolResult, ToolScope, tool
from arden.tools.core.context import BackgroundTaskRegistry, ChildSession, IOBridge, RunContext, ToolContext
from tests.helpers import make_executor, make_text_response


class ParentTracker:
    def __init__(self, cost: float = 0.0):
        self.cost = cost


@pytest.mark.asyncio
async def test_spawned_agent_cannot_widen_parent_tool_scope(monkeypatch):
    captured = {}

    async def execute(_execution, _args):
        return ToolResult(content="ok", preview="ok")

    policy = ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL)
    executor = make_executor(
        {
            "allowed": tool(description="allowed", policy=policy, execute=execute),
            "forbidden": tool(description="forbidden", policy=policy, execute=execute),
        }
    )

    class FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def stream(self, messages):
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    monkeypatch.setattr(spawner_module, "Agent", FakeAgent)
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3, allowed_tool_names={"allowed"}),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )

    spawn = create_spawn_fn(executor=executor, model="test-model", max_depth=3, current_depth=0)
    await spawn(ctx, "child task", system_prompt="child prompt", isolation=IsolationLevel.SHARED)

    schema_names = {item["function"]["name"] for item in captured["tools"]}
    assert schema_names == {"allowed"}
    assert captured["executor"]._ctx.run.allowed_tool_names == {"allowed"}


@pytest.mark.asyncio
async def test_spawned_agent_prompt_includes_area_context(monkeypatch):
    captured = {}

    class FakeAgent:
        async def stream(self, messages):
            captured["messages"] = messages
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    monkeypatch.setattr(spawner_module, "Agent", lambda **kwargs: FakeAgent())

    executor = make_executor()
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
        area=AreaContext(
            area_id="proj-1",
            name="Arden",
            default_cwd="/Users/me/src/arden",
            instructions="Use the repo conventions.",
            knowledge_scope="area:proj-1",
        ),
    )

    spawn = create_spawn_fn(executor=executor, model="test-model", max_depth=3, current_depth=0)
    result = await spawn(ctx, "research task", system_prompt="child prompt", tools=[])

    assert result.text == "done"
    prompt = captured["messages"][0]["content"]
    assert "## AREA" in prompt
    assert "Name: Arden" in prompt
    assert "Default cwd: /Users/me/src/arden" in prompt
    assert "Instructions:\nUse the repo conventions." in prompt


def test_clamp_for_salvage_leaves_short_messages_alone():
    msg = {"role": "tool", "content": "small"}
    assert _clamp_for_salvage(msg) is msg


def test_clamp_for_salvage_truncates_long_tool_content():
    long_content = "x" * 10_000
    out = _clamp_for_salvage({"role": "tool", "content": long_content, "tool_call_id": "abc"})
    assert len(out["content"]) < 5000
    assert "[clamped for salvage summary]" in out["content"]
    assert out["tool_call_id"] == "abc"


def test_clamp_for_salvage_skips_user_and_system():
    long = "x" * 10_000
    assert _clamp_for_salvage({"role": "user", "content": long})["content"] == long
    assert _clamp_for_salvage({"role": "system", "content": long})["content"] == long


def test_deterministic_salvage_includes_tail_tool_results():
    msgs = [
        {"role": "user", "content": "find stuff"},
        {"role": "assistant", "content": None},
        {"role": "tool", "content": "first finding"},
        {"role": "tool", "content": "second finding"},
    ]
    out = _deterministic_salvage(msgs, "boom")
    assert "[partial — sub-agent errored: boom]" in out
    assert "first finding" in out
    assert "second finding" in out


def test_deterministic_salvage_with_no_tool_results():
    out = _deterministic_salvage([{"role": "user", "content": "x"}], "boom")
    assert "(none)" in out


@pytest.mark.asyncio
async def test_salvage_summary_calls_llm_with_clamped_messages(monkeypatch):
    captured: dict = {}

    class FakeClient:
        async def complete(self, model, messages, **kwargs):
            captured["model"] = model
            captured["messages"] = messages
            return CompletionResponse(
                choices=[
                    Choice(
                        message=Message(
                            role="assistant", content="here is what i found", tool_calls=None, reasoning_content=None
                        ),
                        finish_reason="stop",
                    )
                ],
                usage=Usage(),
                model=model,
            )

    monkeypatch.setattr(spawner_module, "llm_client", FakeClient())

    summary = await _salvage_summary(
        model="m",
        child_messages=[{"role": "tool", "content": "x" * 10_000}],
        error="boom",
        task="research X",
    )
    assert summary == "here is what i found"
    # The salvage adds a final user message asking for the summary.
    assert captured["messages"][-1]["role"] == "user"
    assert "boom" in captured["messages"][-1]["content"]
    assert "research X" in captured["messages"][-1]["content"]
    # The original tool message was clamped.
    assert "[clamped for salvage summary]" in captured["messages"][0]["content"]


@pytest.mark.asyncio
async def test_salvage_summary_returns_empty_when_llm_also_fails(monkeypatch):
    class FakeClient:
        async def complete(self, *_, **__):
            raise RuntimeError("llm dead")

    monkeypatch.setattr(spawner_module, "llm_client", FakeClient())

    summary = await _salvage_summary("m", [{"role": "tool", "content": "x"}], "boom", "task")
    assert summary == ""


@pytest.mark.asyncio
async def test_spawn_emits_foreground_task_lifecycle_on_success(monkeypatch):
    class FakeLLM:
        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None, prompt_cache_key=None):
            yield CompletionResponse(
                choices=[
                    Choice(
                        message=Message(role="assistant", content="done", tool_calls=None, reasoning_content=None),
                        finish_reason="stop",
                    )
                ],
                usage=Usage(),
                model=model,
            )

    monkeypatch.setattr(spawner_module, "llm_client", FakeLLM())

    emitted = []

    async def emit(event):
        emitted.append(event)

    executor = make_executor()
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(emit=emit),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )

    spawn = create_spawn_fn(executor=executor, model="test-model", max_depth=3, current_depth=0)
    result = await spawn(
        ctx,
        "research task",
        system_prompt="sys",
        tools=[],
        parent_id="call-research",
        agent_type="research",
        timeout=1,
    )

    assert result.text == "done"
    task_events = [event for event in emitted if isinstance(event, (TaskStartedEvent, TaskFinishedEvent))]
    assert [event.type.value for event in task_events] == ["task_started", "task_finished"]
    assert task_events[0].run_id == "run-1"
    assert task_events[0].task_id == "call-research"
    assert task_events[0].parent_tool_call_id == "call-research"
    assert task_events[0].depth == 1
    assert task_events[1].status == "completed"
    assert result.child_run_id
    assert result.child_run_id != "call-research"
    assert result.parent_tool_call_id == "call-research"
    assert result.agent_type == "research"
    assert result.wait is True
    assert result.status == "completed"


@pytest.mark.asyncio
async def test_spawn_persists_child_agent_session(monkeypatch, tmp_path: Path):
    class FakeLLM:
        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None, prompt_cache_key=None):
            yield CompletionResponse(
                choices=[
                    Choice(
                        message=Message(role="assistant", content="done", tool_calls=None, reasoning_content=None),
                        finish_reason="stop",
                    )
                ],
                usage=Usage(),
                model=model,
            )

    monkeypatch.setattr(spawner_module, "llm_client", FakeLLM())

    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    store = SessionStore(conn, read_conn)
    await store.init_schema()
    session_service = SessionService(store)
    try:
        emitted = []

        async def emit(event):
            emitted.append(event)

        executor = make_executor()
        area = await store.create_area(name="Area One")
        parent = SessionState(session_id="parent", started_at=datetime.now(UTC), area_id=area["area_id"])
        ctx = ToolContext(
            session_state=parent,
            registry=executor.registry,
            run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
            io=IOBridge(emit=emit),
            services={"session": session_service},
            background_tasks=BackgroundTaskRegistry(session_id="parent"),
        )

        spawn = create_spawn_fn(executor=executor, model="test-model", max_depth=3, current_depth=0)
        result = await spawn(
            ctx,
            "research blockers",
            system_prompt="sys",
            tools=[],
            parent_id="call-research",
            agent_type="research",
            timeout=1,
        )

        assert result.child_session_id
        child = await session_service.load(result.child_session_id)
        assert child is not None
        assert child.state.session_type == "agent"
        assert child.state.parent_session_id == "parent"
        assert child.state.parent_tool_call_id == "call-research"
        assert child.state.agent_type == "research"
        assert child.state.agent_status == "completed"
        assert child.state.area_id == area["area_id"]  # child inherits the container
        assert [message["role"] for message in child.messages] == ["system", "user", "assistant"]
        assert child.messages[-1]["content"] == "done"

        rows = await store.list_sessions()
        row = next(item for item in rows if item["session_id"] == result.child_session_id)
        assert row["parent_session_id"] == "parent"
        assert row["agent_status"] == "completed"

        task_events = [event for event in emitted if isinstance(event, (TaskStartedEvent, TaskFinishedEvent))]
        assert [event.child_session_id for event in task_events] == [result.child_session_id, result.child_session_id]
        assert [event.child_run_id for event in task_events] == [result.child_run_id, result.child_run_id]
    finally:
        await read_conn.close()
        await conn.close()


@pytest.mark.asyncio
async def test_spawn_wait_false_returns_running_child_run(monkeypatch):
    class FakeLLM:
        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None, prompt_cache_key=None):
            yield CompletionResponse(
                choices=[
                    Choice(
                        message=Message(role="assistant", content="done", tool_calls=None, reasoning_content=None),
                        finish_reason="stop",
                    )
                ],
                usage=Usage(),
                model=model,
            )

    monkeypatch.setattr(spawner_module, "llm_client", FakeLLM())

    executor = make_executor()
    bg_registry = BackgroundTaskRegistry(session_id="test")
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(),
        background_tasks=bg_registry,
    )

    spawn = create_spawn_fn(executor=executor, model="test-model", max_depth=3, current_depth=0)
    result = await spawn(
        ctx,
        "background research",
        system_prompt="sys",
        tools=[],
        parent_id="call-background",
        agent_type="background_research",
        wait=False,
        timeout=1,
    )

    assert result.child_run_id
    assert result.parent_tool_call_id == "call-background"
    assert result.agent_type == "background_research"
    assert result.wait is False
    assert result.status == "running"
    assert result.text == "Started a background agent to: background research"
    assert result.child_run_id not in result.text

    task = bg_registry._tasks[result.child_run_id]
    await task


@pytest.mark.asyncio
async def test_background_spawn_empty_final_is_visible_in_child_messages(monkeypatch):
    class FakeLLM:
        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None, prompt_cache_key=None):
            yield CompletionResponse(
                choices=[
                    Choice(
                        message=Message(role="assistant", content="", tool_calls=None, reasoning_content=None),
                        finish_reason="stop",
                    )
                ],
                usage=Usage(),
                model=model,
            )

    monkeypatch.setattr(spawner_module, "llm_client", FakeLLM())

    child_emitted = []

    async def child_emit(event):
        child_emitted.append(event)

    async def factory(params):
        async def _finish(_status):
            return None

        async def _aclose():
            return None

        return ChildSession(io=IOBridge(emit=child_emit), finish=_finish, aclose=_aclose)

    executor = make_executor()
    bg_registry = BackgroundTaskRegistry(session_id="parent")
    run = RunContext(run_id="run-1", current_depth=0, max_depth=3)
    run.child_io_factory = factory
    ctx = ToolContext(
        session_state=SessionState(session_id="parent", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=run,
        io=IOBridge(),
        background_tasks=bg_registry,
        run_registry=RunRegistry(),
    )

    spawn = create_spawn_fn(executor=executor, model="test-model", max_depth=3, current_depth=0)
    result = await spawn(
        ctx,
        "background research",
        system_prompt="sys",
        tools=[],
        parent_id="call-background",
        agent_type="background_research",
        wait=False,
        timeout=1,
    )

    await bg_registry._tasks[result.child_run_id]

    child_text = "".join(e.delta for e in child_emitted if isinstance(e, TextMessageContentEvent))
    assert "[partial — sub-agent returned empty final answer]" in child_text
    assert "No recoverable tool results were produced." in child_text


@pytest.mark.asyncio
async def test_spawn_wait_false_persists_child_session_and_background_snapshot(monkeypatch, tmp_path: Path):
    class FakeLLM:
        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None, prompt_cache_key=None):
            yield CompletionResponse(
                choices=[
                    Choice(
                        message=Message(role="assistant", content="done", tool_calls=None, reasoning_content=None),
                        finish_reason="stop",
                    )
                ],
                usage=Usage(),
                model=model,
            )

    monkeypatch.setattr(spawner_module, "llm_client", FakeLLM())

    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    store = SessionStore(conn, read_conn)
    await store.init_schema()
    session_service = SessionService(store)
    try:

        async def record(**event):
            status = event["status"]
            if status == "started":
                await store.record_background_agent_started(
                    task_id=event["task_id"],
                    session_id=event["session_id"],
                    parent_run_id=event.get("parent_run_id"),
                    parent_tool_call_id=event.get("parent_tool_call_id"),
                    child_session_id=event.get("child_session_id"),
                    agent_type=event.get("agent_type") or "background_research",
                    wait=bool(event.get("wait")),
                    command=event.get("command") or "",
                )
            elif event.get("terminal"):
                await store.record_background_agent_finished(
                    task_id=event["task_id"],
                    session_id=event["session_id"],
                    status=status,
                    result_ref=event.get("result_ref"),
                    result_text=event.get("result_text"),
                )
            else:
                await store.record_background_agent_event(
                    task_id=event["task_id"],
                    session_id=event["session_id"],
                    status=status,
                    detail=event.get("detail"),
                    result_ref=event.get("result_ref"),
                )

        emitted = []

        async def emit(event):
            emitted.append(event)

        executor = make_executor()
        bg_registry = BackgroundTaskRegistry(session_id="parent", record_event=record)
        ctx = ToolContext(
            session_state=SessionState(session_id="parent", started_at=datetime.now(UTC)),
            registry=executor.registry,
            run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
            io=IOBridge(emit=emit),
            services={"session": session_service},
            background_tasks=bg_registry,
        )

        spawn = create_spawn_fn(executor=executor, model="test-model", max_depth=3, current_depth=0)
        result = await spawn(
            ctx,
            "background research",
            system_prompt="sys",
            tools=[],
            parent_id="call-background",
            agent_type="background_research",
            wait=False,
            timeout=1,
        )

        assert result.child_session_id
        await bg_registry._tasks[result.child_run_id]

        child = await session_service.load(result.child_session_id)
        assert child is not None
        assert child.state.session_type == "agent"
        assert child.state.agent_status == "completed"

        runs = await store.list_background_agent_runs("parent")
        assert runs[0]["child_run_id"] == result.child_run_id
        assert runs[0]["child_session_id"] == result.child_session_id

        bg_events = [event for event in emitted if isinstance(event, BackgroundTaskEvent)]
        assert bg_events[0].child_session_id == result.child_session_id
        assert bg_events[0].child_run_id == result.child_run_id
    finally:
        await read_conn.close()
        await conn.close()


@pytest.mark.asyncio
async def test_foreground_subagent_emits_generated_name_while_running(monkeypatch):
    emitted = []

    async def emit(event):
        emitted.append(event)

    async def fake_generate_agent_name(model: str, task: str) -> str:
        await asyncio.sleep(0)
        return "Web Release Scout"

    class FakeAgent:
        async def stream(self, messages):
            await asyncio.sleep(0.01)
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    monkeypatch.setattr(spawner_module, "generate_agent_name", fake_generate_agent_name)
    monkeypatch.setattr(spawner_module, "Agent", lambda **kwargs: FakeAgent())

    executor = make_executor()
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(emit=emit),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )

    spawn = create_spawn_fn(executor=executor, model="test-model", max_depth=3, current_depth=0)
    result = await spawn(
        ctx,
        "do web research",
        system_prompt="sys",
        tools=[],
        parent_id="call-research",
        timeout=1,
    )

    assert result.text == "done"
    task_events = [
        event for event in emitted if isinstance(event, (TaskStartedEvent, TaskProgressEvent, TaskFinishedEvent))
    ]
    assert [event.type.value for event in task_events] == ["task_started", "task_progress", "task_finished"]
    # task_started carries a distinct slug initially (not empty, not yet the
    # generated label); task_progress/finished carry the generated label.
    assert task_events[0].name and task_events[0].name != "Web Release Scout"
    assert task_events[1].name == "Web Release Scout"
    assert task_events[1].parent_tool_call_id == "call-research"
    assert task_events[2].name == "Web Release Scout"


@pytest.mark.asyncio
async def test_foreground_subagent_cancel_returns_partial_summary(monkeypatch):
    emitted = []

    async def emit(event):
        emitted.append(event)

    class SlowAgent:
        hooks = SimpleNamespace(on_response=None)

        async def stream(self, messages):
            messages.append({"role": "assistant", "content": "Found useful evidence."})
            await asyncio.sleep(60)

    monkeypatch.setattr(spawner_module, "Agent", lambda **kwargs: SlowAgent())
    monkeypatch.setattr(spawner_module, "_salvage_summary", AsyncMock(return_value="Partial summary."))

    executor = make_executor()
    registry = RunRegistry()
    parent_run = registry.create_run("test")
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id=parent_run.run_id, current_depth=0, max_depth=3),
        io=IOBridge(emit=emit),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
        run_registry=registry,
    )

    spawn = create_spawn_fn(executor=executor, model="test-model", max_depth=3, current_depth=0)
    task = asyncio.create_task(
        spawn(
            ctx,
            "research trace replay",
            system_prompt="sys",
            tools=[],
            parent_id="call-research",
        )
    )
    await asyncio.sleep(0)

    result = registry.cancel_subagent(parent_run.run_id, "call-research")
    assert result == {"found": True, "cancel_requested": True}

    spawn_result = await task
    assert "partial" in spawn_result.text.lower()
    assert "Partial summary." in spawn_result.text
    assert any(getattr(event, "status", None) == "cancelled" for event in emitted)


@pytest.mark.asyncio
async def test_background_agent_drains_steering_message_mid_run(monkeypatch):
    """Parent→child steering, end to end: a message queued into a running
    background agent's inbox is drained into its loop at the next step."""
    captured: dict = {}
    bg_registry = BackgroundTaskRegistry(session_id="test")

    class SteeringLLM:
        def __init__(self):
            self.calls = 0

        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None, prompt_cache_key=None):
            self.calls += 1
            if self.calls == 1:
                # The agent is live now — steer it. A tool call forces a
                # second step, across whose boundary the inbox is drained.
                task_id = next(iter(bg_registry._tasks))
                assert bg_registry.queue_injection(
                    task_id, {"role": "user", "content": "<steering_message>\nalso check pricing\n</steering_message>"}
                )
                yield CompletionResponse(
                    choices=[
                        Choice(
                            message=Message(
                                role="assistant",
                                content=None,
                                tool_calls=[
                                    ToolCall(
                                        id="c1", type="function", function=FunctionCall(name="finder", arguments="{}")
                                    )
                                ],
                                reasoning_content=None,
                            ),
                            finish_reason="tool_calls",
                        )
                    ],
                    usage=Usage(),
                    model=model,
                )
                return
            captured["messages"] = list(messages)
            yield CompletionResponse(
                choices=[
                    Choice(
                        message=Message(role="assistant", content="done", tool_calls=None, reasoning_content=None),
                        finish_reason="stop",
                    )
                ],
                usage=Usage(),
                model=model,
            )

    monkeypatch.setattr(spawner_module, "llm_client", SteeringLLM())

    class FinderExecutor:
        async def execute(self, name, args, tool_call_id):
            return AgentToolResult(content="ok", preview="ok", is_error=False)

        def get_meta(self, name):
            return ToolMeta(name="finder", display_name="Finder", kind="tool")

    monkeypatch.setattr("arden.core.spawner.ArdenToolExecutor", lambda *_a, **_k: FinderExecutor())

    executor = make_executor()
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(),
        background_tasks=bg_registry,
    )

    spawn = create_spawn_fn(executor=executor, model="test-model", max_depth=3, current_depth=0)
    result = await spawn(
        ctx,
        "background research",
        system_prompt="sys",
        tools=[],
        parent_id="call-bg",
        agent_type="background_research",
        wait=False,
        timeout=5,
    )

    await bg_registry._tasks[result.child_run_id]

    steered = [
        m for m in captured["messages"] if m.get("role") == "user" and "also check pricing" in (m.get("content") or "")
    ]
    assert steered, f"steering message was not drained into the child loop: {captured.get('messages')}"
    # Drained exactly once — the inbox is empty afterward.
    assert bg_registry.drain_injections(result.child_run_id) == []


@pytest.mark.asyncio
async def test_background_spawn_rejected_at_concurrency_cap(monkeypatch):
    from arden.constants import AGENT_MAX_CONCURRENT

    bg_registry = BackgroundTaskRegistry(session_id="test")
    fillers = [asyncio.create_task(asyncio.sleep(3600)) for _ in range(AGENT_MAX_CONCURRENT)]
    for i, task in enumerate(fillers):
        bg_registry.register(f"filler-{i}", task, command="busy")

    class BoomLLM:
        async def stream(self, *args, **kwargs):
            raise AssertionError("must not spawn when at the concurrency cap")
            yield  # pragma: no cover

    monkeypatch.setattr(spawner_module, "llm_client", BoomLLM())

    child_sessions: list[str] = []
    finished: list[tuple[str, str]] = []
    closed: list[str] = []

    async def child_io_factory(params):
        child_sessions.append(params.session_id)

        async def finish(status: str) -> None:
            finished.append((params.session_id, status))

        async def aclose() -> None:
            closed.append(params.session_id)

        return ChildSession(io=IOBridge(), finish=finish, aclose=aclose)

    executor = make_executor()
    run_registry = RunRegistry()
    parent = run_registry.create_run("test", run_id="run-1")
    parent.status = RunStatus.RUNNING
    run = RunContext(run_id=parent.run_id, current_depth=0, max_depth=3)
    run.child_io_factory = child_io_factory
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=run,
        io=IOBridge(),
        background_tasks=bg_registry,
        run_registry=run_registry,
    )

    spawn = create_spawn_fn(executor=executor, model="test-model", max_depth=3, current_depth=0)
    try:
        result = await spawn(ctx, "one too many", system_prompt="sys", tools=[], background=True, timeout=1)
        assert result.status == "failed"
        assert result.child_run_id == ""
        assert "concurrent" in result.text.lower()
        # No new task was registered.
        assert len(bg_registry.list_pending()) == AGENT_MAX_CONCURRENT
        assert len(child_sessions) == 1
        assert finished == [(child_sessions[0], "failed")]
        assert closed == child_sessions
        assert run_registry.get_active_run(child_sessions[0]) is None
    finally:
        for task in fillers:
            task.cancel()
        await asyncio.gather(*fillers, return_exceptions=True)


@pytest.mark.asyncio
async def test_spawn_start_failure_closes_acquired_child_session(monkeypatch):
    monkeypatch.setattr(spawner_module, "llm_client", _single_response_llm())

    async def fail_start(**_event):
        raise RuntimeError("start persistence failed")

    child_sessions: list[str] = []
    finished: list[tuple[str, str]] = []
    closed: list[str] = []

    async def child_io_factory(params):
        child_sessions.append(params.session_id)

        async def finish(status: str) -> None:
            finished.append((params.session_id, status))

        async def aclose() -> None:
            closed.append(params.session_id)

        return ChildSession(io=IOBridge(), finish=finish, aclose=aclose)

    executor = make_executor()
    run_registry = RunRegistry()
    parent = run_registry.create_run("test", run_id="run-1")
    parent.status = RunStatus.RUNNING
    run = RunContext(run_id=parent.run_id, current_depth=0, max_depth=3)
    run.child_io_factory = child_io_factory
    background_tasks = BackgroundTaskRegistry(session_id="test", record_event=fail_start)
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=run,
        io=IOBridge(),
        background_tasks=background_tasks,
        run_registry=run_registry,
    )

    spawn = create_spawn_fn(executor=executor, model="test-model", max_depth=3, current_depth=0)
    with pytest.raises(RuntimeError, match="start persistence failed"):
        await spawn(ctx, "cannot start", system_prompt="sys", tools=[], timeout=1)

    assert len(child_sessions) == 1
    assert finished == [(child_sessions[0], "failed")]
    assert closed == child_sessions
    assert run_registry.get_active_run(child_sessions[0]) is None
    assert background_tasks.list_pending() == []


@pytest.mark.asyncio
async def test_spawn_cost_budget_uses_parent_spend(monkeypatch):
    class FakeLLM:
        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None, prompt_cache_key=None):
            raise AssertionError("child should stop before model call")
            yield  # pragma: no cover

    monkeypatch.setattr(spawner_module, "llm_client", FakeLLM())

    executor = make_executor()
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
        parent_tracker=ParentTracker(cost=1.0),
    )

    spawn = create_spawn_fn(
        executor=executor,
        model="test-model",
        max_depth=3,
        current_depth=0,
        max_cost=1.0,
    )
    result = await spawn(ctx, "research task", system_prompt="sys", tools=[], timeout=1)

    assert result.text == (
        "[partial — sub-agent returned empty final answer]\nNo recoverable tool results were produced."
    )


@pytest.mark.asyncio
async def test_spawn_tool_call_budget_is_shared_with_parent(monkeypatch):
    class FakeLLM:
        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None, prompt_cache_key=None):
            raise AssertionError("child should stop before model call")
            yield  # pragma: no cover

    monkeypatch.setattr(spawner_module, "llm_client", FakeLLM())

    executor = make_executor()
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3, max_tool_calls=1),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )
    assert ctx.run.budget is not None
    ctx.run.budget.tool_calls = 1

    spawn = create_spawn_fn(
        executor=executor,
        model="test-model",
        max_depth=3,
        current_depth=0,
        max_tool_calls=1,
    )
    result = await spawn(ctx, "research task", system_prompt="sys", tools=[], timeout=1)

    assert result.text == (
        "[partial — sub-agent returned empty final answer]\nNo recoverable tool results were produced."
    )


@pytest.mark.asyncio
async def test_spawn_wall_time_budget_uses_parent_start(monkeypatch):
    class FakeLLM:
        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None, prompt_cache_key=None):
            raise AssertionError("child should stop before model call")
            yield  # pragma: no cover

    monkeypatch.setattr(spawner_module, "llm_client", FakeLLM())

    executor = make_executor()
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3, started_at=0.0),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )

    spawn = create_spawn_fn(
        executor=executor,
        model="test-model",
        max_depth=3,
        current_depth=0,
        max_wall_time_seconds=0.0,
    )
    result = await spawn(ctx, "research task", system_prompt="sys", tools=[], timeout=1)

    assert result.text == (
        "[partial — sub-agent returned empty final answer]\nNo recoverable tool results were produced."
    )


@pytest.mark.asyncio
async def test_background_spawn_rolls_cost_into_parent_tracker(monkeypatch):
    class FakeLLM:
        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None, prompt_cache_key=None):
            yield CompletionResponse(
                choices=[
                    Choice(
                        message=Message(role="assistant", content="done", tool_calls=None, reasoning_content=None),
                        finish_reason="stop",
                    )
                ],
                usage=Usage(prompt_tokens=10),
                model=model,
            )

    monkeypatch.setattr(spawner_module, "llm_client", FakeLLM())

    parent_tracker = ParentTracker(cost=0.0)
    executor = make_executor()
    bg_registry = BackgroundTaskRegistry(session_id="test")
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(),
        background_tasks=bg_registry,
        parent_tracker=parent_tracker,
    )

    spawn = create_spawn_fn(executor=executor, model="claude-sonnet-4-6", max_depth=3, current_depth=0)
    result = await spawn(ctx, "task", system_prompt="sys", tools=[], background=True, timeout=1)
    task_id = result.child_run_id
    task = bg_registry._tasks[task_id]

    await task

    assert parent_tracker.cost > 0
    assert task.done()
    assert task_id not in bg_registry._tasks


@pytest.mark.asyncio
async def test_spawn_emits_live_token_usage_for_child_response(monkeypatch):
    class FakeLLM:
        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None, prompt_cache_key=None):
            yield CompletionResponse(
                choices=[
                    Choice(
                        message=Message(role="assistant", content="done", tool_calls=None, reasoning_content=None),
                        finish_reason="stop",
                    )
                ],
                usage=Usage(prompt_tokens=10, completion_tokens=2, cache_read_tokens=3),
                model=model,
            )

    monkeypatch.setattr(spawner_module, "llm_client", FakeLLM())

    async def slow_agent_name(model, task):
        await asyncio.sleep(2)
        return "Slow label"

    monkeypatch.setattr(spawner_module, "generate_agent_name", slow_agent_name)

    emitted = []

    async def emit(event):
        emitted.append(event)

    parent_tracker = ParentTracker(cost=0.0)
    executor = make_executor()
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(emit=emit),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
        parent_tracker=parent_tracker,
    )

    spawn = create_spawn_fn(executor=executor, model="claude-sonnet-4-6", max_depth=3, current_depth=0)

    await spawn(ctx, "task", system_prompt="sys", tools=[], timeout=1)

    usage_events = [event for event in emitted if isinstance(event, TokenUsageEvent)]
    assert len(usage_events) == 1
    assert usage_events[0].run_id == "run-1"
    assert usage_events[0].usage == {"prompt": 10, "completion": 2, "total": 15, "cache_read": 3, "cache_write": 0}
    assert usage_events[0].cost == parent_tracker.cost


@pytest.mark.asyncio
async def test_spawn_returns_salvage_when_inner_agent_fails(monkeypatch):
    """End-to-end: the inner agent's LLM call raises, and spawn returns
    a partial-summary string instead of letting the exception escape."""
    salvage_text = "Found 3 things before the error."

    class FakeLLM:
        def __init__(self):
            self.complete_calls = 0
            self.complete_messages = None

        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None, prompt_cache_key=None):
            # The inner agent's first model call simulates a fatal LLM error.
            raise RuntimeError("oops")
            yield  # pragma: no cover  # keep generator-typed

        async def complete(self, model, messages, **kwargs):
            self.complete_calls += 1
            self.complete_messages = messages
            return CompletionResponse(
                choices=[
                    Choice(
                        message=Message(
                            role="assistant", content=salvage_text, tool_calls=None, reasoning_content=None
                        ),
                        finish_reason="stop",
                    )
                ],
                usage=Usage(),
                model=model,
            )

    fake = FakeLLM()
    monkeypatch.setattr(spawner_module, "llm_client", fake)

    emitted = []

    async def emit(event):
        emitted.append(event)

    executor = make_executor()
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(emit=emit),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )

    spawn = create_spawn_fn(executor=executor, model="test-model", max_depth=3, current_depth=0)
    result = await spawn(
        ctx,
        "research task",
        system_prompt="sys",
        tools=[],
        parent_id="call",
        timeout=1,
    )

    assert "[partial — sub-agent errored:" in result.text
    assert "oops" not in result.text
    assert salvage_text in result.text
    assert fake.complete_calls == 1
    assert "oops" not in str(fake.complete_messages)
    assert "RuntimeError" in str(fake.complete_messages)
    task_events = [event for event in emitted if isinstance(event, (TaskStartedEvent, TaskFinishedEvent))]
    assert [event.type.value for event in task_events] == ["task_started", "task_finished"]
    assert task_events[1].status == "failed"


@pytest.mark.asyncio
async def test_spawn_uses_reasoning_effort_for_model_override(monkeypatch):
    captured: dict = {}

    class FakeLLM:
        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None, prompt_cache_key=None):
            captured["model"] = model
            captured["reasoning_effort"] = reasoning_effort
            yield CompletionResponse(
                choices=[
                    Choice(
                        message=Message(role="assistant", content="done", tool_calls=None, reasoning_content=None),
                        finish_reason="stop",
                    )
                ],
                usage=Usage(),
                model=model,
            )

    monkeypatch.setattr(spawner_module, "llm_client", FakeLLM())

    child_emitted = []

    async def child_emit(event):
        child_emitted.append(event)

    async def factory(params):
        async def _finish(_status):
            return None

        async def _aclose():
            return None

        return ChildSession(io=IOBridge(emit=child_emit), finish=_finish, aclose=_aclose)

    executor = make_executor()
    run = RunContext(run_id="run-1", current_depth=0, max_depth=3)
    run.child_io_factory = factory
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=run,
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
        run_registry=RunRegistry(),
    )

    spawn = create_spawn_fn(
        executor=executor,
        model="chat-model",
        max_depth=3,
        current_depth=0,
        reasoning_effort="low",
        model_reasoning_efforts={"research-model": "max"},
    )
    result = await spawn(
        ctx,
        "research task",
        system_prompt="sys",
        tools=[],
        model_override="research-model",
        parent_id="call",
        timeout=1,
    )

    assert result.text == "done"
    assert captured == {"model": "research-model", "reasoning_effort": "max"}


@pytest.mark.asyncio
async def test_spawn_salvage_preserves_tool_results_after_loop_progress(monkeypatch):
    """The real scenario: sub-agent runs several tool calls successfully,
    then the LLM dies on the next iteration. Salvage must see the tool
    results that were already accumulated — that's the entire point of
    the feature (don't throw away ~200 tool calls of paid work)."""
    captured_salvage_messages: list = []

    class FakeLLM:
        def __init__(self):
            self.stream_calls = 0

        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None, prompt_cache_key=None):
            self.stream_calls += 1
            if self.stream_calls == 1:
                # First call: agent decides to invoke a tool. The agent
                # loop will then dispatch it and append the tool result
                # to the messages list, before looping back for call 2.
                yield CompletionResponse(
                    choices=[
                        Choice(
                            message=Message(
                                role="assistant",
                                content=None,
                                tool_calls=[
                                    ToolCall(
                                        id="call_1",
                                        type="function",
                                        function=FunctionCall(name="finder", arguments="{}"),
                                    )
                                ],
                                reasoning_content=None,
                            ),
                            finish_reason="tool_calls",
                        )
                    ],
                    usage=Usage(),
                    model=model,
                )
                return
            # Second call: simulate a fatal LLM error mid-loop.
            raise RuntimeError("api blew up")

        async def complete(self, model, messages, **kwargs):
            captured_salvage_messages.append(messages)
            return CompletionResponse(
                choices=[
                    Choice(
                        message=Message(
                            role="assistant",
                            content="Recovered: found one thing about apricots.",
                            tool_calls=None,
                            reasoning_content=None,
                        ),
                        finish_reason="stop",
                    )
                ],
                usage=Usage(),
                model=model,
            )

    monkeypatch.setattr(spawner_module, "llm_client", FakeLLM())

    class FinderExecutor:
        async def execute(self, name, args, tool_call_id):
            return AgentToolResult(content="apricots are good", preview="apricots", is_error=False)

        def get_meta(self, name):
            return ToolMeta(name="finder", display_name="Finder", kind="tool")

    monkeypatch.setattr("arden.core.spawner.ArdenToolExecutor", lambda *_args, **_kwargs: FinderExecutor())

    child_emitted = []

    async def child_emit(event):
        child_emitted.append(event)

    async def factory(params):
        async def _finish(_status):
            return None

        async def _aclose():
            return None

        return ChildSession(io=IOBridge(emit=child_emit), finish=_finish, aclose=_aclose)

    executor = make_executor()
    run = RunContext(run_id="run-1", current_depth=0, max_depth=3)
    run.child_io_factory = factory
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=run,
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
        run_registry=RunRegistry(),
    )

    spawn = create_spawn_fn(executor=executor, model="test-model", max_depth=3, current_depth=0)
    result = await spawn(
        ctx,
        "find things about apricots",
        system_prompt="sys",
        tools=[],
        parent_id="call",
        timeout=5,
    )

    assert "[partial — sub-agent errored:" in result.text
    assert "Recovered: found one thing about apricots." in result.text
    # Verify the salvage call actually saw the tool result — that's the
    # whole point. Find the tool message in what we sent to `complete`.
    salvage_msgs = captured_salvage_messages[0]
    tool_msgs = [m for m in salvage_msgs if m.get("role") == "tool"]
    assert tool_msgs, f"no tool messages in salvage payload: {salvage_msgs}"
    assert any("apricots are good" in (m.get("content") or "") for m in tool_msgs)


@pytest.mark.asyncio
async def test_spawn_salvages_when_inner_agent_returns_empty_final(monkeypatch):
    captured_salvage_messages: list = []

    class FakeLLM:
        def __init__(self):
            self.stream_calls = 0

        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None, prompt_cache_key=None):
            self.stream_calls += 1
            if self.stream_calls == 1:
                yield CompletionResponse(
                    choices=[
                        Choice(
                            message=Message(
                                role="assistant",
                                content=None,
                                tool_calls=[
                                    ToolCall(
                                        id="call_1",
                                        type="function",
                                        function=FunctionCall(name="finder", arguments="{}"),
                                    )
                                ],
                                reasoning_content=None,
                            ),
                            finish_reason="tool_calls",
                        )
                    ],
                    usage=Usage(),
                    model=model,
                )
                return
            yield CompletionResponse(
                choices=[
                    Choice(
                        message=Message(role="assistant", content="", tool_calls=None, reasoning_content=None),
                        finish_reason="stop",
                    )
                ],
                usage=Usage(),
                model=model,
            )

        async def complete(self, model, messages, **kwargs):
            captured_salvage_messages.append(messages)
            return CompletionResponse(
                choices=[
                    Choice(
                        message=Message(
                            role="assistant",
                            content="Recovered from tool results: apricots are good.",
                            tool_calls=None,
                            reasoning_content=None,
                        ),
                        finish_reason="stop",
                    )
                ],
                usage=Usage(),
                model=model,
            )

    monkeypatch.setattr(spawner_module, "llm_client", FakeLLM())

    class FinderExecutor:
        async def execute(self, name, args, tool_call_id):
            return AgentToolResult(content="apricots are good", preview="apricots", is_error=False)

        def get_meta(self, name):
            return ToolMeta(name="finder", display_name="Finder", kind="tool")

    monkeypatch.setattr("arden.core.spawner.ArdenToolExecutor", lambda *_args, **_kwargs: FinderExecutor())

    child_emitted = []

    async def child_emit(event):
        child_emitted.append(event)

    async def factory(params):
        async def _finish(_status):
            return None

        async def _aclose():
            return None

        return ChildSession(io=IOBridge(emit=child_emit), finish=_finish, aclose=_aclose)

    executor = make_executor()
    run = RunContext(run_id="run-1", current_depth=0, max_depth=3)
    run.child_io_factory = factory
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=run,
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
        run_registry=RunRegistry(),
    )

    spawn = create_spawn_fn(executor=executor, model="test-model", max_depth=3, current_depth=0)
    result = await spawn(
        ctx,
        "find things about apricots",
        system_prompt="sys",
        tools=[],
        parent_id="call",
        timeout=5,
    )

    assert "[partial — sub-agent returned empty final answer]" in result.text
    assert "Recovered from tool results: apricots are good." in result.text
    tool_msgs = [m for m in captured_salvage_messages[0] if m.get("role") == "tool"]
    assert any("apricots are good" in (m.get("content") or "") for m in tool_msgs)
    child_text = "".join(e.delta for e in child_emitted if isinstance(e, TextMessageContentEvent))
    assert "Recovered from tool results: apricots are good." in child_text


@pytest.mark.asyncio
async def test_spawn_salvages_on_foreground_timeout(monkeypatch):
    """If the foreground sub-agent hits the wait_for timeout, we still
    return a salvage summary instead of a bare timeout error."""
    import asyncio as _aio

    class FakeLLM:
        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None, prompt_cache_key=None):
            # Sleep longer than the spawn timeout to force a TimeoutError.
            await _aio.sleep(10)
            yield  # pragma: no cover

        async def complete(self, model, messages, **kwargs):
            return CompletionResponse(
                choices=[
                    Choice(
                        message=Message(
                            role="assistant",
                            content="best-effort summary",
                            tool_calls=None,
                            reasoning_content=None,
                        ),
                        finish_reason="stop",
                    )
                ],
                usage=Usage(),
                model=model,
            )

    monkeypatch.setattr(spawner_module, "llm_client", FakeLLM())

    executor = make_executor()
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )

    spawn = create_spawn_fn(executor=executor, model="test-model", max_depth=3, current_depth=0)
    result = await spawn(
        ctx,
        "task",
        system_prompt="sys",
        tools=[],
        parent_id="call",
        timeout=0.1,
    )

    assert "[partial — sub-agent timed out" in result.text
    assert "best-effort summary" in result.text


def test_clamp_for_salvage_handles_list_typed_content():
    """List-typed content (multi-part blocks from vision/tool results)
    must also be clamped — otherwise an oversized blob bypasses the
    safety net and can re-trigger the same context-length failure."""
    blocks = [{"type": "text", "text": "a" * 8000}, {"type": "text", "text": "b" * 8000}]
    out = _clamp_for_salvage({"role": "tool", "content": blocks, "tool_call_id": "x"})
    assert isinstance(out["content"], str)
    assert len(out["content"]) < 5000
    assert "[clamped for salvage summary]" in out["content"]


def _single_response_llm():
    class FakeLLM:
        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None, prompt_cache_key=None):
            yield CompletionResponse(
                choices=[
                    Choice(
                        message=Message(role="assistant", content="done", tool_calls=None, reasoning_content=None),
                        finish_reason="stop",
                    )
                ],
                usage=Usage(),
                model=model,
            )

    return FakeLLM()


@pytest.mark.asyncio
async def test_full_isolation_advertises_child_session_id_without_persistence(monkeypatch):
    """FULL isolation always advertises child_session_id, decoupled from whether
    the session row persisted. With no session service at all (as here) the child
    is never written to disk — but the id is still a valid route, so it must reach
    the UI or the agent card can't be opened/cleared."""
    monkeypatch.setattr(spawner_module, "llm_client", _single_response_llm())

    emitted = []

    async def emit(event):
        emitted.append(event)

    executor = make_executor()
    ctx = ToolContext(
        session_state=SessionState(session_id="parent", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(emit=emit),
        background_tasks=BackgroundTaskRegistry(session_id="parent"),
    )

    spawn = create_spawn_fn(executor=executor, model="test-model", max_depth=3, current_depth=0)
    result = await spawn(
        ctx, "research", system_prompt="sys", tools=[], parent_id="call-x", agent_type="research", timeout=1
    )

    assert result.child_session_id
    assert result.child_session_id.startswith("parent::")
    task_events = [e for e in emitted if isinstance(e, (TaskStartedEvent, TaskFinishedEvent))]
    assert task_events
    assert all(e.child_session_id == result.child_session_id for e in task_events)


@pytest.mark.asyncio
async def test_shared_isolation_advertises_no_child_session(monkeypatch):
    """SHARED isolation reuses the parent session — there is no distinct child
    session to open, so child_session_id must be None everywhere."""
    monkeypatch.setattr(spawner_module, "llm_client", _single_response_llm())

    emitted = []

    async def emit(event):
        emitted.append(event)

    executor = make_executor()
    ctx = ToolContext(
        session_state=SessionState(session_id="parent", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(emit=emit),
        background_tasks=BackgroundTaskRegistry(session_id="parent"),
    )

    spawn = create_spawn_fn(executor=executor, model="test-model", max_depth=3, current_depth=0)
    result = await spawn(
        ctx,
        "inline subtask",
        system_prompt="sys",
        tools=[],
        parent_id="call-y",
        agent_type="research",
        isolation=IsolationLevel.SHARED,
        timeout=1,
    )

    assert result.child_session_id is None
    task_events = [e for e in emitted if isinstance(e, (TaskStartedEvent, TaskFinishedEvent))]
    assert task_events
    assert all(e.child_session_id is None for e in task_events)


@pytest.mark.asyncio
async def test_full_subagent_streams_to_own_child_bus(monkeypatch):
    """A FULL subagent streams its OWN events to its CHILD session bus at depth 0
    (un-nested), while the PARENT bus gets only the lifecycle events — so the
    child session behaves exactly like a normal run instead of being static."""

    class FakeAgent:
        # No `hooks` attr → the spawner skips hook wiring; this agent just
        # streams a small text turn so we can watch where its events land.
        async def stream(self, messages):
            yield TextStarted(depth=1, parent_id="call-research", message_id="m1")
            yield TextDelta(depth=1, parent_id="call-research", message_id="m1", content="child answer")
            yield TextEnded(depth=1, parent_id="call-research", message_id="m1", content="child answer")
            yield Result(text="child answer", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    monkeypatch.setattr(spawner_module, "Agent", lambda **kwargs: FakeAgent())

    parent_emitted: list = []
    child_emitted: list = []
    closed: list = []

    async def parent_emit(event):
        parent_emitted.append(event)

    async def child_emit(event):
        child_emitted.append(event)

    async def factory(params):
        async def _aclose():
            closed.append(params.session_id)

        async def _finish(_status):
            return None

        return ChildSession(io=IOBridge(emit=child_emit), finish=_finish, aclose=_aclose)

    executor = make_executor()
    run = RunContext(run_id="run-1", current_depth=0, max_depth=3)
    run.child_io_factory = factory
    ctx = ToolContext(
        session_state=SessionState(session_id="parent", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=run,
        io=IOBridge(emit=parent_emit),
        background_tasks=BackgroundTaskRegistry(session_id="parent"),
        run_registry=RunRegistry(),
    )

    spawn = create_spawn_fn(executor=executor, model="test-model", max_depth=3, current_depth=0)
    result = await spawn(
        ctx, "research task", system_prompt="sys", tools=[], parent_id="call-research", agent_type="research", timeout=1
    )

    assert result.text == "child answer"
    # Parent stream = lifecycle only (the parent renders the agent as a leaf).
    assert [e.type.value for e in parent_emitted] == ["task_started", "task_finished"]
    # Child bus = the agent's own stream, re-based to its session's frame
    # (depth 0, no parent), and carrying NO lifecycle events.
    assert child_emitted, "child session bus received no events"
    assert all(getattr(e, "depth", 0) == 0 for e in child_emitted)
    assert all(getattr(e, "parent_id", None) is None for e in child_emitted)
    assert not any(isinstance(e, (TaskStartedEvent, TaskProgressEvent, TaskFinishedEvent)) for e in child_emitted)
    # The child bus is drained/evicted once the run ends.
    assert closed == [result.child_session_id]


@pytest.mark.asyncio
async def test_shared_subagent_does_not_use_child_bus(monkeypatch):
    """SHARED isolation has no distinct child session, so the child_io_factory is
    never called and the subagent keeps nesting into the parent's io."""

    class FakeLLM:
        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None, prompt_cache_key=None):
            yield make_text_response("inline", model=model)

    monkeypatch.setattr(spawner_module, "llm_client", FakeLLM())

    factory_calls: list = []

    async def factory(params):
        factory_calls.append(params.session_id)

        async def _aclose():
            return None

        async def _finish(_status):
            return None

        return ChildSession(io=IOBridge(), finish=_finish, aclose=_aclose)

    executor = make_executor()
    run = RunContext(run_id="run-1", current_depth=0, max_depth=3)
    run.child_io_factory = factory
    ctx = ToolContext(
        session_state=SessionState(session_id="parent", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=run,
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="parent"),
        run_registry=RunRegistry(),
    )

    spawn = create_spawn_fn(executor=executor, model="test-model", max_depth=3, current_depth=0)
    result = await spawn(
        ctx,
        "inline subtask",
        system_prompt="sys",
        tools=[],
        parent_id="call-y",
        agent_type="research",
        isolation=IsolationLevel.SHARED,
        timeout=1,
    )

    assert result.child_session_id is None
    assert factory_calls == []


@pytest.mark.asyncio
async def test_awaited_spawn_records_durable_row_and_terminal_status(monkeypatch):
    """An awaited spawn writes the same durable roster row a detached one does:
    started with wait=True and the frozen SpawnSpec, then the terminal outcome
    with the result text — without any deliver_result injection."""
    monkeypatch.setattr(spawner_module, "llm_client", _single_response_llm())

    events: list[dict] = []

    async def record(**event):
        events.append(event)

    executor = make_executor()
    bg_registry = BackgroundTaskRegistry(session_id="test", record_event=record)
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(),
        background_tasks=bg_registry,
    )

    spawn = create_spawn_fn(executor=executor, model="test-model", max_depth=3, current_depth=0)
    result = await spawn(
        ctx,
        "research task",
        system_prompt="sys",
        tools=[],
        parent_id="call-research",
        agent_type="research",
        timeout=1,
    )

    assert result.status == "completed"
    started = next(e for e in events if e["status"] == "started")
    assert started["task_id"] == result.child_run_id
    assert started["wait"] is True
    assert started["parent_run_id"] == "run-1"
    spec = json.loads(started["spawn_spec"])
    assert spec["agent_type"] == "research"
    terminal = [e for e in events if e.get("terminal")]
    assert [(e["status"], e["result_text"]) for e in terminal] == [("completed", "done")]


@pytest.mark.asyncio
async def test_awaited_spawn_visible_in_registry_while_running(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    class GatedAgent:
        async def stream(self, messages):
            started.set()
            await release.wait()
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    monkeypatch.setattr(spawner_module, "Agent", lambda **kwargs: GatedAgent())

    executor = make_executor()
    bg_registry = BackgroundTaskRegistry(session_id="test")
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(),
        background_tasks=bg_registry,
    )

    spawn = create_spawn_fn(executor=executor, model="test-model", max_depth=3, current_depth=0)
    task = asyncio.create_task(
        spawn(ctx, "slow research", system_prompt="sys", tools=[], parent_id="call-slow", timeout=5)
    )
    await started.wait()

    pending = bg_registry.list_pending()
    assert len(pending) == 1
    task_id = pending[0][0]
    assert bg_registry.parent_run(task_id) == "run-1"

    release.set()
    result = await task
    assert result.status == "completed"
    assert result.child_run_id == task_id
    assert bg_registry.list_pending() == []


@pytest.mark.asyncio
async def test_awaited_spawns_reserve_uncapped(monkeypatch):
    """Awaited spawns hold roster slots but never hit (or count against) the
    detached-agent cap — workflow fan-out legitimately exceeds it."""
    from arden.constants import AGENT_MAX_CONCURRENT

    streaming = 0
    gate = asyncio.Event()

    class GatedAgent:
        async def stream(self, messages):
            nonlocal streaming
            streaming += 1
            await gate.wait()
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    monkeypatch.setattr(spawner_module, "Agent", lambda **kwargs: GatedAgent())

    executor = make_executor()
    bg_registry = BackgroundTaskRegistry(session_id="test")
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(),
        background_tasks=bg_registry,
    )

    spawn = create_spawn_fn(executor=executor, model="test-model", max_depth=3, current_depth=0)
    count = AGENT_MAX_CONCURRENT + 1
    tasks = [
        asyncio.create_task(spawn(ctx, f"task {i}", system_prompt="sys", tools=[], parent_id=f"call-{i}", timeout=5))
        for i in range(count)
    ]
    while streaming < count:
        await asyncio.sleep(0)

    assert len(bg_registry.list_pending()) == count
    assert bg_registry.pending_count == 0
    # A detached reserve still has its full cap available beside them.
    assert bg_registry.reserve("bg-probe", command="probe", limit=AGENT_MAX_CONCURRENT)
    bg_registry.release("bg-probe")

    gate.set()
    results = await asyncio.gather(*tasks)
    assert all(r.status == "completed" for r in results)
    assert bg_registry.list_pending() == []


@pytest.mark.asyncio
async def test_run_cancel_cascades_to_awaited_child(monkeypatch):
    """cancel_run's registry sweep now sees awaited children: the durable
    cancel mirror gets the pair and the parent's await raises CancelledError."""

    class SlowAgent:
        async def stream(self, messages):
            await asyncio.sleep(60)
            yield Result(text="never", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    monkeypatch.setattr(spawner_module, "Agent", lambda **kwargs: SlowAgent())

    executor = make_executor()
    registry = RunRegistry()
    parent_run = registry.create_run("test")
    bg_registry = registry.get_background_registry("test")
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id=parent_run.run_id, current_depth=0, max_depth=3),
        io=IOBridge(),
        background_tasks=bg_registry,
        run_registry=registry,
    )

    spawn = create_spawn_fn(executor=executor, model="test-model", max_depth=3, current_depth=0)
    task = asyncio.create_task(spawn(ctx, "long research", system_prompt="sys", tools=[], parent_id="call-research"))
    while not bg_registry._tasks:
        await asyncio.sleep(0)
    child_task_id = next(iter(bg_registry._tasks))

    outcome = registry.cancel_run(parent_run.run_id)
    assert outcome["cancel_requested"]
    assert ("test", child_task_id) in outcome["cancelled_children"]
    with pytest.raises(asyncio.CancelledError):
        await task
