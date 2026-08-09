"""Spawn lifecycle, failure, cancellation, and child-session tests."""

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import arden.database as database
from arden.agent import (
    AgentHooks,
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
    ToolStarted,
    Usage,
)
from arden.agent.types.tools import ToolMeta
from arden.agent.types.tools import ToolResult as AgentToolResult
from arden.context.models import AreaContext, SessionState
from arden.context.store import SessionStore
from arden.core import spawner as spawner_module
from arden.core.isolation import IsolationLevel
from arden.core.public_refs import public_ref
from arden.core.spawn_lifecycle import (
    ChildSessionLifecycle,
    cancel_and_join,
    collect_failure,
    run_operations,
)
from arden.core.spawner import create_spawn_fn, get_response_cost
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
from arden.tools.core.background_tasks import BackgroundTaskRegistry
from arden.tools.core.context import ChildSession, IOBridge, RunContext, ToolContext
from tests.helpers import make_executor, make_text_response


class ParentTracker:
    def __init__(self, cost: float = 0.0):
        self.cost = cost


def test_response_cost_rejects_unknown_model():
    response = CompletionResponse(choices=[], usage=Usage(), model="missing/model")

    with pytest.raises(ValueError, match="Unknown model: missing/model"):
        get_response_cost(response)


def _persisted_context(**kwargs) -> ToolContext:
    kwargs.setdefault(
        "services",
        {"session": SimpleNamespace(provision_state=AsyncMock(), save=AsyncMock())},
    )
    return ToolContext(**kwargs)


@pytest.mark.asyncio
async def test_cancel_and_join_preserves_new_caller_cancellation():
    started = asyncio.Event()
    child_cancelled = asyncio.Event()

    async def slow_child() -> None:
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            child_cancelled.set()
            await asyncio.sleep(60)

    async def caller() -> None:
        child = asyncio.create_task(slow_child())
        await started.wait()
        caller_task = asyncio.current_task()

        async def cancel_caller() -> None:
            await child_cancelled.wait()
            caller_task.cancel()

        asyncio.create_task(cancel_caller())
        await cancel_and_join(child)

    with pytest.raises(asyncio.CancelledError):
        await asyncio.create_task(caller())


@pytest.mark.asyncio
async def test_collect_failure_does_not_convert_system_exit():
    async def exit_process() -> None:
        raise SystemExit(7)

    errors: list[Exception] = []
    with pytest.raises(SystemExit) as caught:
        await collect_failure(errors, exit_process())

    assert caught.value.code == 7
    assert errors == []


@pytest.mark.asyncio
async def test_run_operations_attempts_every_operation_before_cancellation():
    calls: list[str] = []

    async def cancel() -> None:
        calls.append("cancel")
        raise asyncio.CancelledError

    async def fail() -> None:
        calls.append("fail")
        raise RuntimeError("cleanup failed")

    async def finish() -> None:
        calls.append("finish")

    with pytest.raises(asyncio.CancelledError) as caught:
        await run_operations("terminal cleanup failed", cancel, fail, finish)

    assert calls == ["cancel", "fail", "finish"]
    assert isinstance(caught.value.__cause__, RuntimeError)


async def _provisioned_child_lifecycle(*, save, child_io_factory=None):
    executor = make_executor()
    service = SimpleNamespace(provision_state=AsyncMock(), save=save)
    run = RunContext(run_id="run-1", child_io_factory=child_io_factory)
    parent = ToolContext(
        session_state=SessionState(session_id="parent", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=run,
        io=IOBridge(),
        services={"session": service},
        background_tasks=BackgroundTaskRegistry(session_id="parent"),
    )
    child_state = SessionState(session_id="child", started_at=datetime.now(UTC), session_type="agent")
    child = ToolContext(
        session_state=child_state,
        registry=executor.registry,
        run=run,
        io=IOBridge(),
        services=parent.services,
        background_tasks=parent.background_tasks,
    )
    lifecycle = ChildSessionLifecycle(
        parent=parent,
        child=child,
        state=child_state,
        messages=[],
        task_id="agent~111111",
        has_child_session=True,
    )
    await lifecycle.provision()
    await lifecycle.acquire_io()
    return lifecycle, service


@pytest.mark.asyncio
async def test_child_settlement_closes_io_after_terminal_record_cancellation():
    finished: list[str] = []
    closed = False

    async def child_io_factory(_params):
        async def finish(status: str) -> None:
            finished.append(status)

        async def close() -> None:
            nonlocal closed
            closed = True

        return ChildSession(io=IOBridge(), finish=finish, aclose=close)

    lifecycle, service = await _provisioned_child_lifecycle(
        save=AsyncMock(),
        child_io_factory=child_io_factory,
    )

    async def cancel_record(_status: str, _error: Exception | None) -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await lifecycle.settle("completed", cancel_record)

    service.save.assert_awaited_once()
    assert finished == ["completed"]
    assert closed


@pytest.mark.asyncio
async def test_child_settlement_persists_failed_status_after_save_cancellation():
    persisted: list[str | None] = []
    recorded: list[tuple[str, Exception | None]] = []

    async def save(state, _messages) -> None:
        persisted.append(state.agent_status)
        if len(persisted) == 1:
            raise asyncio.CancelledError

    lifecycle, _service = await _provisioned_child_lifecycle(save=save)

    async def record(status: str, error: Exception | None) -> None:
        recorded.append((status, error))

    with pytest.raises(asyncio.CancelledError):
        await lifecycle.settle("completed", record)

    assert persisted == ["completed", "failed"]
    assert recorded and recorded[0][0] == "failed"
    assert isinstance(recorded[0][1], RuntimeError)


def _tool_event_agent(*, gate: asyncio.Event | None = None):
    class ToolEventAgent:
        def __init__(self) -> None:
            self.hooks = SimpleNamespace(on_response=None, on_step_finish=None, get_pending_messages=None)

        async def stream(self, _messages):
            if gate is not None:
                await gate.wait()
            yield ToolStarted(tool_id="tool-1", name="inspect", args={}, display_name="Inspect")
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    return ToolEventAgent()


@pytest.mark.asyncio
async def test_parent_stream_failure_propagates_after_one_failed_settlement(monkeypatch):
    monkeypatch.setattr(spawner_module, "Agent", lambda **_kwargs: _tool_event_agent())
    emitted = 0
    statuses: list[str] = []

    async def emit(_event):
        nonlocal emitted
        emitted += 1
        if emitted == 2:
            raise RuntimeError("parent stream unavailable")

    async def record_event(**event):
        statuses.append(event["status"])

    executor = make_executor()
    ctx = _persisted_context(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(emit=emit),
        background_tasks=BackgroundTaskRegistry(session_id="test", record_event=record_event),
    )

    with pytest.raises(RuntimeError, match="parent stream unavailable"):
        await create_spawn_fn(executor, "gpt-5-mini", 3, 0)(
            ctx,
            "inspect",
            system_prompt="sys",
            isolation=IsolationLevel.SHARED,
        )

    assert statuses == ["started", "failed"]


@pytest.mark.asyncio
async def test_child_stream_failure_propagates_after_one_failed_settlement(monkeypatch):
    monkeypatch.setattr(spawner_module, "Agent", lambda **_kwargs: _tool_event_agent())
    child_emits = 0
    finished: list[str] = []
    statuses: list[str] = []

    async def child_emit(_event):
        nonlocal child_emits
        child_emits += 1
        if child_emits == 1:
            raise RuntimeError("child stream unavailable")

    async def child_io_factory(_params):
        async def finish(status: str) -> None:
            finished.append(status)

        async def close() -> None:
            return None

        return ChildSession(io=IOBridge(emit=child_emit), finish=finish, aclose=close)

    async def record_event(**event):
        statuses.append(event["status"])

    executor = make_executor()
    ctx = _persisted_context(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(
            run_id="run-1",
            current_depth=0,
            max_depth=3,
            child_io_factory=child_io_factory,
        ),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="test", record_event=record_event),
    )

    with pytest.raises(RuntimeError, match="child stream unavailable"):
        await create_spawn_fn(executor, "gpt-5-mini", 3, 0)(ctx, "inspect", system_prompt="sys")

    assert statuses == ["started", "failed"]
    assert finished == ["failed"]


@pytest.mark.asyncio
async def test_activity_store_failure_settles_detached_child_then_propagates(monkeypatch):
    gate = asyncio.Event()
    monkeypatch.setattr(spawner_module, "Agent", lambda **_kwargs: _tool_event_agent(gate=gate))
    statuses: list[str] = []

    async def record_event(**event):
        if event["status"] == "activity":
            raise RuntimeError("activity store unavailable")
        statuses.append(event["status"])

    async def emit(_event):
        return None

    executor = make_executor()
    registry = BackgroundTaskRegistry(session_id="test", record_event=record_event)
    ctx = _persisted_context(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(emit=emit),
        background_tasks=registry,
    )
    result = await create_spawn_fn(executor, "gpt-5-mini", 3, 0)(
        ctx,
        "inspect",
        system_prompt="sys",
        wait=False,
    )
    task = registry.task(result.child_run_id)
    assert task is not None

    gate.set()
    with pytest.raises(RuntimeError, match="activity store unavailable"):
        await task

    assert statuses == ["started", "failed"]


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
            self.hooks = AgentHooks()
            captured.update(kwargs)

        async def stream(self, messages):
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    monkeypatch.setattr(spawner_module, "Agent", FakeAgent)
    ctx = _persisted_context(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3, allowed_tool_names={"allowed"}),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )

    spawn = create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)
    await spawn(ctx, "child task", system_prompt="child prompt", isolation=IsolationLevel.SHARED)

    schema_names = {item["function"]["name"] for item in captured["tools"]}
    assert schema_names == {"allowed"}
    assert captured["executor"]._ctx.run.allowed_tool_names == {"allowed"}


@pytest.mark.asyncio
async def test_restart_spawn_reuses_existing_child_session(monkeypatch):
    class FakeAgent:
        def __init__(self):
            self.hooks = AgentHooks()

        async def stream(self, _messages):
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    monkeypatch.setattr(spawner_module, "Agent", lambda **_kwargs: FakeAgent())
    service = SimpleNamespace(provision_state=AsyncMock(), save=AsyncMock())
    executor = make_executor()
    ctx = _persisted_context(
        session_state=SessionState(session_id="parent", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(),
        services={"session": service},
        background_tasks=BackgroundTaskRegistry(session_id="parent"),
    )

    result = await create_spawn_fn(executor, "gpt-5-mini", 3, 0)(
        ctx,
        "resume research",
        system_prompt="sys",
        task_id="agent-resume",
        agent_ref=public_ref("resume research", "agent-resume", empty_slug="agent"),
        _resume_child_session_id="parent::existing",
    )

    assert result.child_session_id == "parent::existing"
    provisioned_state = service.provision_state.await_args.args[0]
    assert provisioned_state.session_id == "parent::existing"


@pytest.mark.asyncio
async def test_spawned_agent_prompt_includes_area_context(monkeypatch):
    captured = {}

    class FakeAgent:
        def __init__(self):
            self.hooks = AgentHooks()

        async def stream(self, messages):
            captured["messages"] = messages
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    monkeypatch.setattr(spawner_module, "Agent", lambda **kwargs: FakeAgent())

    executor = make_executor()
    ctx = _persisted_context(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
        area=AreaContext(
            area_id="proj-1",
            area_ref="arden~123456",
            name="Arden",
            default_cwd="/Users/me/src/arden",
            instructions="Use the repo conventions.",
        ),
    )

    spawn = create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)
    result = await spawn(ctx, "research task", system_prompt="child prompt", tools=[])

    assert result.text == "done"
    prompt = captured["messages"][0]["content"]
    assert "## AREA" in prompt
    assert "Name: Arden" in prompt
    assert "Reference: arden~123456" in prompt
    assert "proj-1" not in prompt
    assert "Default cwd: /Users/me/src/arden" in prompt
    assert "Instructions:\nUse the repo conventions." in prompt


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
    ctx = _persisted_context(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(emit=emit),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )

    spawn = create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)
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
    store = SessionStore(conn, read_conn, event_conn=conn)
    await store.init_schema()
    session_service = SessionService(store)
    try:
        emitted = []

        async def emit(event):
            emitted.append(event)

        executor = make_executor()
        area = await store.create_area(name="Area One")
        parent = SessionState(session_id="parent", started_at=datetime.now(UTC), area_id=area["area_id"])
        ctx = _persisted_context(
            session_state=parent,
            registry=executor.registry,
            run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
            io=IOBridge(emit=emit),
            services={"session": session_service},
            background_tasks=BackgroundTaskRegistry(session_id="parent"),
        )

        spawn = create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)
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
    ctx = _persisted_context(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(),
        background_tasks=bg_registry,
    )

    spawn = create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)
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
    ctx = _persisted_context(
        session_state=SessionState(session_id="parent", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=run,
        io=IOBridge(),
        background_tasks=bg_registry,
        run_registry=RunRegistry(),
    )

    spawn = create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)
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
    assert child_text == "Sub-agent returned no final answer."


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
    store = SessionStore(conn, read_conn, event_conn=conn)
    await store.init_schema()
    session_service = SessionService(store)
    try:

        async def record(**event):
            status = event["status"]
            if status == "started":
                await store.record_background_agent_started(
                    task_id=event["task_id"],
                    agent_ref=event["agent_ref"],
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
        ctx = _persisted_context(
            session_state=SessionState(session_id="parent", started_at=datetime.now(UTC)),
            registry=executor.registry,
            run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
            io=IOBridge(emit=emit),
            services={"session": session_service},
            background_tasks=bg_registry,
        )

        spawn = create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)
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
async def test_foreground_subagent_cancel_returns_explicit_failure(monkeypatch):
    emitted = []

    async def emit(event):
        emitted.append(event)

    class SlowAgent:
        def __init__(self):
            self.hooks = AgentHooks()

        async def stream(self, messages):
            messages.append({"role": "assistant", "content": "Found useful evidence."})
            await asyncio.sleep(60)

    monkeypatch.setattr(spawner_module, "Agent", lambda **kwargs: SlowAgent())

    executor = make_executor()
    registry = RunRegistry()
    parent_run = registry.create_run("test")
    ctx = _persisted_context(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id=parent_run.run_id, current_depth=0, max_depth=3),
        io=IOBridge(emit=emit),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
        run_registry=registry,
    )

    spawn = create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)
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
    assert spawn_result.text == "Sub-agent cancelled by user."
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
    ctx = _persisted_context(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(),
        background_tasks=bg_registry,
    )

    spawn = create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)
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
    ctx = _persisted_context(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=run,
        io=IOBridge(),
        background_tasks=bg_registry,
        run_registry=run_registry,
    )

    spawn = create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)
    try:
        result = await spawn(ctx, "one too many", system_prompt="sys", tools=[], background=True, timeout=1)
        assert result.status == "failed"
        assert result.child_run_id == ""
        assert "concurrent" in result.text.lower()
        # No new task was registered.
        assert len(bg_registry.list_pending()) == AGENT_MAX_CONCURRENT
        assert child_sessions == []
        assert finished == []
        assert closed == []
    finally:
        for task in fillers:
            task.cancel()
        await asyncio.gather(*fillers, return_exceptions=True)


@pytest.mark.asyncio
async def test_spawn_start_failure_closes_acquired_child_session(monkeypatch):
    monkeypatch.setattr(spawner_module, "llm_client", _single_response_llm())

    start_events: list[str] = []

    async def fail_start(**event):
        start_events.append(event["status"])
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
    session_service = SimpleNamespace(provision_state=AsyncMock(), save=AsyncMock())
    ctx = _persisted_context(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=run,
        io=IOBridge(),
        services={"session": session_service},
        background_tasks=background_tasks,
        run_registry=run_registry,
    )

    spawn = create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)
    with pytest.raises(RuntimeError, match="start persistence failed"):
        await spawn(ctx, "cannot start", system_prompt="sys", tools=[], timeout=1)

    assert start_events == ["started"]
    assert len(child_sessions) == 1
    assert finished == [(child_sessions[0], "failed")]
    assert closed == child_sessions
    assert run_registry.get_active_run(child_sessions[0]) is None
    assert background_tasks.list_pending() == []
    session_service.provision_state.assert_awaited_once()
    assert session_service.save.await_args.args[0].agent_status == "failed"


@pytest.mark.asyncio
async def test_spawn_provision_failure_stops_before_llm_and_settles_resources(monkeypatch):
    class BoomLLM:
        async def stream(self, *args, **kwargs):
            raise AssertionError("LLM must not run before child session provisioning")
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

    session_service = SimpleNamespace(
        provision_state=AsyncMock(side_effect=RuntimeError("provision failed")),
        save=AsyncMock(),
    )
    executor = make_executor()
    run_registry = RunRegistry()
    parent = run_registry.create_run("test", run_id="run-1")
    parent.status = RunStatus.RUNNING
    run = RunContext(run_id=parent.run_id, current_depth=0, max_depth=3)
    run.child_io_factory = child_io_factory
    background_tasks = BackgroundTaskRegistry(session_id="test")
    ctx = _persisted_context(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=run,
        io=IOBridge(),
        services={"session": session_service},
        background_tasks=background_tasks,
        run_registry=run_registry,
    )

    spawn = create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)
    with pytest.raises(RuntimeError, match="provision failed"):
        await spawn(ctx, "cannot provision", system_prompt="sys", tools=[], timeout=1)

    session_service.provision_state.assert_awaited_once()
    session_service.save.assert_not_awaited()
    assert child_sessions == []
    assert finished == []
    assert closed == []
    assert background_tasks.list_pending() == []


@pytest.mark.asyncio
async def test_spawn_start_failure_chains_cleanup_failures(monkeypatch):
    monkeypatch.setattr(spawner_module, "llm_client", _single_response_llm())

    async def fail_start(**_event):
        raise RuntimeError("start persistence failed")

    async def child_io_factory(_params):
        async def finish(_status: str) -> None:
            raise RuntimeError("finish failed")

        async def aclose() -> None:
            raise RuntimeError("close failed")

        return ChildSession(io=IOBridge(), finish=finish, aclose=aclose)

    session_service = SimpleNamespace(
        provision_state=AsyncMock(),
        save=AsyncMock(side_effect=RuntimeError("terminal save failed")),
    )
    executor = make_executor()
    run_registry = RunRegistry()
    parent = run_registry.create_run("test", run_id="run-1")
    parent.status = RunStatus.RUNNING
    run = RunContext(run_id=parent.run_id, current_depth=0, max_depth=3)
    run.child_io_factory = child_io_factory
    background_tasks = BackgroundTaskRegistry(session_id="test", record_event=fail_start)
    ctx = _persisted_context(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=run,
        io=IOBridge(),
        services={"session": session_service},
        background_tasks=background_tasks,
        run_registry=run_registry,
    )

    spawn = create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)
    with pytest.raises(BaseExceptionGroup) as caught:
        await spawn(ctx, "cannot settle", system_prompt="sys", tools=[], timeout=1)

    assert [str(error) for error in caught.value.exceptions] == [
        "start persistence failed",
        "terminal save failed",
        "finish failed",
        "close failed",
    ]
    assert background_tasks.list_pending() == []
    assert run_registry.get_active_run(session_service.provision_state.await_args.args[0].session_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("wait", [True, False])
async def test_terminal_record_failure_does_not_rewrite_outcome(monkeypatch, wait):
    monkeypatch.setattr(spawner_module, "llm_client", _single_response_llm())
    durable_statuses: list[str] = []
    finished: list[str] = []

    async def record_event(**event):
        durable_statuses.append(event["status"])
        if event["status"] == "completed":
            raise RuntimeError("terminal record failed")

    async def child_io_factory(_params):
        async def finish(status: str) -> None:
            finished.append(status)

        return ChildSession(io=IOBridge(), finish=finish, aclose=AsyncMock())

    executor = make_executor()
    run = RunContext(run_id="run-1", current_depth=0, max_depth=3)
    run.child_io_factory = child_io_factory
    background_tasks = BackgroundTaskRegistry(session_id="test", record_event=record_event)
    ctx = _persisted_context(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=run,
        io=IOBridge(),
        background_tasks=background_tasks,
    )

    spawn = create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)
    if wait:
        with pytest.raises(RuntimeError, match="terminal record failed"):
            await spawn(ctx, "finish once", system_prompt="sys", tools=[], wait=True, timeout=1)
    else:
        result = await spawn(ctx, "finish once", system_prompt="sys", tools=[], wait=False, timeout=1)
        task = background_tasks.task(result.child_run_id)
        assert task is not None
        with pytest.raises(RuntimeError, match="terminal record failed"):
            await task

    assert durable_statuses == ["started", "completed"]
    assert finished == ["completed"]


@pytest.mark.asyncio
async def test_spawn_final_persistence_failure_cannot_return_success(monkeypatch):
    monkeypatch.setattr(spawner_module, "llm_client", _single_response_llm())

    parent_events = []
    durable_statuses: list[str] = []
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

    async def emit(event) -> None:
        parent_events.append(event)

    async def record_event(**event):
        durable_statuses.append(event["status"])

    async def save_child(state, _messages):
        if state.agent_status != "running":
            raise RuntimeError("final persistence failed")

    session_service = SimpleNamespace(provision_state=AsyncMock(), save=AsyncMock(side_effect=save_child))
    executor = make_executor()
    run_registry = RunRegistry()
    parent = run_registry.create_run("test", run_id="run-1")
    parent.status = RunStatus.RUNNING
    run = RunContext(run_id=parent.run_id, current_depth=0, max_depth=3)
    run.child_io_factory = child_io_factory
    background_tasks = BackgroundTaskRegistry(session_id="test", record_event=record_event)
    ctx = _persisted_context(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=run,
        io=IOBridge(emit=emit),
        services={"session": session_service},
        background_tasks=background_tasks,
        run_registry=run_registry,
    )

    spawn = create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)
    with pytest.raises(RuntimeError, match="final persistence failed"):
        await spawn(ctx, "complete then fail", system_prompt="sys", tools=[], timeout=1)

    session_service.save.assert_awaited()
    assert finished == [(child_sessions[0], "failed")]
    assert closed == child_sessions
    assert run_registry.get_active_run(child_sessions[0]) is None
    assert background_tasks.list_pending() == []
    assert durable_statuses == ["started", "failed"]
    assert [event.status for event in parent_events if isinstance(event, TaskFinishedEvent)] == ["failed"]


@pytest.mark.asyncio
async def test_background_final_persistence_failure_is_not_delivered_as_success(monkeypatch):
    monkeypatch.setattr(spawner_module, "llm_client", _single_response_llm())

    async def save_child(state, _messages):
        if state.agent_status != "running":
            raise RuntimeError("background final persistence failed")

    session_service = SimpleNamespace(provision_state=AsyncMock(), save=AsyncMock(side_effect=save_child))
    deliver = AsyncMock()
    durable_statuses: list[str] = []

    async def record_event(**event):
        durable_statuses.append(event["status"])

    background_tasks = BackgroundTaskRegistry(session_id="test", on_result=deliver, record_event=record_event)
    executor = make_executor()
    ctx = _persisted_context(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(),
        services={"session": session_service},
        background_tasks=background_tasks,
    )

    spawn = create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)
    result = await spawn(ctx, "complete in background", system_prompt="sys", tools=[], wait=False, timeout=1)
    task = background_tasks.task(result.child_run_id)
    assert task is not None
    with pytest.raises(RuntimeError, match="background final persistence failed"):
        await task

    deliver.assert_awaited_once()
    delivered_messages = deliver.await_args.args[0]
    assert delivered_messages[0]["background_status"] == "failed"
    assert durable_statuses == ["started", "failed"]


@pytest.mark.asyncio
async def test_background_registration_failure_never_runs_child(monkeypatch):
    class BoomLLM:
        async def stream(self, *args, **kwargs):
            raise AssertionError("child must remain gated until registration succeeds")
            yield  # pragma: no cover

    class FailingRegisterRegistry(BackgroundTaskRegistry):
        def register(self, task_id, task, command, *, observe_failure=False):
            raise RuntimeError("task registration failed")

    monkeypatch.setattr(spawner_module, "llm_client", BoomLLM())
    durable_statuses: list[str] = []
    finished: list[str] = []
    closed = False

    async def record_event(**event):
        durable_statuses.append(event["status"])

    async def child_io_factory(_params):
        async def finish(status: str) -> None:
            finished.append(status)

        async def aclose() -> None:
            nonlocal closed
            closed = True

        return ChildSession(io=IOBridge(), finish=finish, aclose=aclose)

    session_service = SimpleNamespace(provision_state=AsyncMock(), save=AsyncMock())
    registry = FailingRegisterRegistry(session_id="test", record_event=record_event)
    executor = make_executor()
    run = RunContext(run_id="run-1", current_depth=0, max_depth=3)
    run.child_io_factory = child_io_factory
    ctx = _persisted_context(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=run,
        io=IOBridge(),
        services={"session": session_service},
        background_tasks=registry,
    )

    spawn = create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)
    with pytest.raises(RuntimeError, match="task registration failed"):
        await spawn(ctx, "never run", system_prompt="sys", tools=[], wait=False, timeout=1)

    assert durable_statuses == ["started", "failed"]
    assert finished == ["failed"]
    assert closed
    assert registry.list_pending() == []


@pytest.mark.asyncio
async def test_detached_registry_observes_task_failure(caplog):
    async def fail() -> None:
        raise RuntimeError("detached failure")

    caplog.set_level(logging.ERROR)
    registry = BackgroundTaskRegistry(session_id="test")
    task = asyncio.create_task(fail())
    registry.register("agent-failed", task, command="Agent", observe_failure=True)

    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)

    assert "Detached background task agent-failed failed" in caplog.text


@pytest.mark.asyncio
async def test_background_started_emit_failure_never_runs_child(monkeypatch):
    class BoomLLM:
        async def stream(self, *args, **kwargs):
            raise AssertionError("child must remain gated until started emission succeeds")
            yield  # pragma: no cover

    monkeypatch.setattr(spawner_module, "llm_client", BoomLLM())
    durable_statuses: list[str] = []
    emitted_statuses: list[str] = []

    async def record_event(**event):
        durable_statuses.append(event["status"])

    async def emit(event) -> None:
        emitted_statuses.append(event.status)
        if event.status == "started":
            raise RuntimeError("started emission failed")

    session_service = SimpleNamespace(provision_state=AsyncMock(), save=AsyncMock())
    registry = BackgroundTaskRegistry(session_id="test", record_event=record_event)
    executor = make_executor()
    ctx = _persisted_context(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(emit=emit),
        services={"session": session_service},
        background_tasks=registry,
    )

    spawn = create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)
    with pytest.raises(RuntimeError, match="started emission failed"):
        await spawn(ctx, "never run", system_prompt="sys", tools=[], wait=False, timeout=1)

    assert durable_statuses == ["started", "failed"]
    assert emitted_statuses == ["started"]
    assert registry.list_pending() == []


@pytest.mark.asyncio
async def test_spawn_cost_budget_uses_parent_spend(monkeypatch):
    class FakeLLM:
        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None, prompt_cache_key=None):
            raise AssertionError("child should stop before model call")
            yield  # pragma: no cover

    monkeypatch.setattr(spawner_module, "llm_client", FakeLLM())

    executor = make_executor()
    ctx = _persisted_context(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
        parent_tracker=ParentTracker(cost=1.0),
    )

    spawn = create_spawn_fn(
        executor=executor,
        model="gpt-5-mini",
        max_depth=3,
        current_depth=0,
        max_cost=1.0,
    )
    result = await spawn(ctx, "research task", system_prompt="sys", tools=[], timeout=1)

    assert result.text == "Sub-agent returned no final answer."


@pytest.mark.asyncio
async def test_spawn_tool_call_budget_is_shared_with_parent(monkeypatch):
    class FakeLLM:
        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None, prompt_cache_key=None):
            raise AssertionError("child should stop before model call")
            yield  # pragma: no cover

    monkeypatch.setattr(spawner_module, "llm_client", FakeLLM())

    executor = make_executor()
    ctx = _persisted_context(
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
        model="gpt-5-mini",
        max_depth=3,
        current_depth=0,
        max_tool_calls=1,
    )
    result = await spawn(ctx, "research task", system_prompt="sys", tools=[], timeout=1)

    assert result.text == "Sub-agent returned no final answer."


@pytest.mark.asyncio
async def test_spawn_wall_time_budget_uses_parent_start(monkeypatch):
    class FakeLLM:
        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None, prompt_cache_key=None):
            raise AssertionError("child should stop before model call")
            yield  # pragma: no cover

    monkeypatch.setattr(spawner_module, "llm_client", FakeLLM())

    executor = make_executor()
    ctx = _persisted_context(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3, started_at=0.0),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )

    spawn = create_spawn_fn(
        executor=executor,
        model="gpt-5-mini",
        max_depth=3,
        current_depth=0,
        max_wall_time_seconds=0.0,
    )
    result = await spawn(ctx, "research task", system_prompt="sys", tools=[], timeout=1)

    assert result.text == "Sub-agent returned no final answer."


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
    ctx = _persisted_context(
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

    emitted = []

    async def emit(event):
        emitted.append(event)

    parent_tracker = ParentTracker(cost=0.0)
    executor = make_executor()
    ctx = _persisted_context(
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
async def test_spawn_returns_explicit_failure_when_inner_agent_fails(monkeypatch):
    class FakeLLM:
        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None, prompt_cache_key=None):
            raise RuntimeError("oops")
            yield  # pragma: no cover

    monkeypatch.setattr(spawner_module, "llm_client", FakeLLM())

    emitted = []

    async def emit(event):
        emitted.append(event)

    executor = make_executor()
    ctx = _persisted_context(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(emit=emit),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )

    spawn = create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)
    result = await spawn(
        ctx,
        "research task",
        system_prompt="sys",
        tools=[],
        parent_id="call",
        timeout=1,
    )

    assert result.text == "Sub-agent failed: RuntimeError."
    assert result.status == "failed"
    assert "oops" not in result.text
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
    ctx = _persisted_context(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=run,
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
        run_registry=RunRegistry(),
    )

    spawn = create_spawn_fn(
        executor=executor,
        model="gpt-5-mini",
        max_depth=3,
        current_depth=0,
        reasoning_effort="low",
        model_reasoning_efforts={"gpt-5.6-sol": "max"},
    )
    result = await spawn(
        ctx,
        "research task",
        system_prompt="sys",
        tools=[],
        model_override="gpt-5.6-sol",
        parent_id="call",
        timeout=1,
    )

    assert result.text == "done"
    assert captured == {"model": "gpt-5.6-sol", "reasoning_effort": "max"}


@pytest.mark.asyncio
async def test_spawn_returns_explicit_failure_for_empty_answer(monkeypatch):
    class EmptyLLM:
        async def stream(self, messages, model, tools, **kwargs):
            yield make_text_response("")

    monkeypatch.setattr(spawner_module, "llm_client", EmptyLLM())
    executor = make_executor()
    ctx = _persisted_context(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )

    result = await create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)(
        ctx, "task", system_prompt="sys", tools=[]
    )

    assert result.status == "failed"
    assert result.text == "Sub-agent returned no final answer."


@pytest.mark.asyncio
async def test_spawn_returns_explicit_timeout(monkeypatch):
    class SlowLLM:
        async def stream(self, messages, model, tools, **kwargs):
            await asyncio.sleep(60)
            yield  # pragma: no cover

    monkeypatch.setattr(spawner_module, "llm_client", SlowLLM())
    executor = make_executor()
    ctx = _persisted_context(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )

    result = await create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)(
        ctx, "task", system_prompt="sys", tools=[], timeout=0.01
    )

    assert result.status == "failed"
    assert result.text == "Sub-agent timed out after 0.01s."


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
async def test_full_isolation_requires_session_persistence_before_execution(monkeypatch):
    class BoomLLM:
        async def stream(self, *args, **kwargs):
            raise AssertionError("LLM must not run without child session persistence")
            yield  # pragma: no cover

    monkeypatch.setattr(spawner_module, "llm_client", BoomLLM())

    emitted = []

    async def emit(event):
        emitted.append(event)

    executor = make_executor()
    ctx = ToolContext(
        session_state=SessionState(session_id="parent", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(emit=emit),
        services={},
        background_tasks=BackgroundTaskRegistry(session_id="parent"),
    )

    spawn = create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)
    with pytest.raises(RuntimeError, match="require the session service"):
        await spawn(
            ctx, "research", system_prompt="sys", tools=[], parent_id="call-x", agent_type="research", timeout=1
        )

    assert emitted == []
    assert ctx.background_tasks.list_pending() == []


@pytest.mark.asyncio
async def test_shared_isolation_advertises_no_child_session(monkeypatch):
    """SHARED isolation reuses the parent session — there is no distinct child
    session to open, so child_session_id must be None everywhere."""
    monkeypatch.setattr(spawner_module, "llm_client", _single_response_llm())

    emitted = []

    async def emit(event):
        emitted.append(event)

    executor = make_executor()
    ctx = _persisted_context(
        session_state=SessionState(session_id="parent", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(emit=emit),
        background_tasks=BackgroundTaskRegistry(session_id="parent"),
    )

    spawn = create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)
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
        def __init__(self):
            self.hooks = AgentHooks()

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
    ctx = _persisted_context(
        session_state=SessionState(session_id="parent", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=run,
        io=IOBridge(emit=parent_emit),
        background_tasks=BackgroundTaskRegistry(session_id="parent"),
        run_registry=RunRegistry(),
    )

    spawn = create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)
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
    ctx = _persisted_context(
        session_state=SessionState(session_id="parent", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=run,
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="parent"),
        run_registry=RunRegistry(),
    )

    spawn = create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)
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

    async def no_suspension(**_kwargs):
        return None

    async def write_suspension(**_kwargs):
        return None

    executor = make_executor()
    bg_registry = BackgroundTaskRegistry(session_id="test", record_event=record)
    ctx = _persisted_context(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(
            get_suspension=no_suspension,
            consume_suspension=write_suspension,
            record_suspension=write_suspension,
            resolve_suspension=write_suspension,
        ),
        background_tasks=bg_registry,
    )

    spawn = create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)
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
        def __init__(self):
            self.hooks = AgentHooks()

        async def stream(self, messages):
            started.set()
            await release.wait()
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    monkeypatch.setattr(spawner_module, "Agent", lambda **kwargs: GatedAgent())

    executor = make_executor()
    bg_registry = BackgroundTaskRegistry(session_id="test")
    ctx = _persisted_context(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(),
        background_tasks=bg_registry,
    )

    spawn = create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)
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
        def __init__(self):
            self.hooks = AgentHooks()

        async def stream(self, messages):
            nonlocal streaming
            streaming += 1
            await gate.wait()
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    monkeypatch.setattr(spawner_module, "Agent", lambda **kwargs: GatedAgent())

    executor = make_executor()
    bg_registry = BackgroundTaskRegistry(session_id="test")
    ctx = _persisted_context(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(),
        background_tasks=bg_registry,
    )

    spawn = create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)
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
    assert bg_registry.reserve(
        "bg-probe",
        command="probe",
        limit=AGENT_MAX_CONCURRENT,
        agent_ref=public_ref("probe", "bg-probe", empty_slug="agent"),
    )
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
        def __init__(self):
            self.hooks = AgentHooks()

        async def stream(self, messages):
            await asyncio.sleep(60)
            yield Result(text="never", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    monkeypatch.setattr(spawner_module, "Agent", lambda **kwargs: SlowAgent())

    executor = make_executor()
    registry = RunRegistry()
    parent_run = registry.create_run("test")
    bg_registry = registry.get_background_registry("test")
    ctx = _persisted_context(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id=parent_run.run_id, current_depth=0, max_depth=3),
        io=IOBridge(),
        background_tasks=bg_registry,
        run_registry=registry,
    )

    spawn = create_spawn_fn(executor=executor, model="gpt-5-mini", max_depth=3, current_depth=0)
    task = asyncio.create_task(spawn(ctx, "long research", system_prompt="sys", tools=[], parent_id="call-research"))
    while not bg_registry._tasks:
        await asyncio.sleep(0)
    child_task_id = next(iter(bg_registry._tasks))

    outcome = registry.cancel_run(parent_run.run_id)
    assert outcome["cancel_requested"]
    assert ("test", child_task_id) in outcome["cancelled_children"]
    with pytest.raises(asyncio.CancelledError):
        await task
