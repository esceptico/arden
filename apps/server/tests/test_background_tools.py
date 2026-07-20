import asyncio
import contextlib
from datetime import UTC, datetime

import pytest

import ntrp.tools.background as background_module
from ntrp.context.models import SessionState
from ntrp.core.spawner import SpawnResult
from ntrp.tools.core.context import BackgroundTaskRegistry, IOBridge, RunContext, ToolContext, ToolExecution
from ntrp.tools.core.registry import ToolRegistry
from ntrp.tools.core.types import ToolAction


def _ctx(registry: BackgroundTaskRegistry) -> ToolContext:
    return ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(),
        background_tasks=registry,
    )


async def _register_live(registry: BackgroundTaskRegistry, task_id: str, command: str) -> asyncio.Task:
    task = asyncio.create_task(asyncio.sleep(3600))
    registry.register(task_id, task, command=command)
    return task


async def _cancel(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_background_tool_spawns_detached_background_research_agent():
    captured = {}

    async def spawn_fn(ctx, task, **kwargs):
        captured["task"] = task
        captured.update(kwargs)
        return SpawnResult(
            text="Started a background agent to: scan docs",
            child_run_id="agent-1",
            parent_tool_call_id="background-1",
            agent_type="background_research",
            wait=False,
            status="running",
        )

    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )
    ctx.spawn_fn = spawn_fn
    execution = ToolExecution(tool_id="background-1", tool_name="background", ctx=ctx)

    result = await background_module.background(
        execution,
        background_module.BackgroundInput(task="scan docs"),
    )

    assert captured["task"] == "scan docs"
    assert captured["parent_id"] == "background-1"
    assert captured["agent_type"] == "background_research"
    assert captured["wait"] is False
    assert "background" not in captured
    assert result.content == "Started a background agent to: scan docs"
    assert result.data == {
        "child_agent": {
            "child_run_id": "agent-1",
            "parent_tool_call_id": "background-1",
            "agent_type": "background_research",
            "wait": False,
            "status": "running",
        }
    }


def test_background_tool_is_agent_kind():
    assert background_module.background_tool.kind == "agent"
    assert background_module.background_tool.policy.action == ToolAction.EXECUTE


def test_background_registry_reservations_count_toward_cap():
    registry = BackgroundTaskRegistry(session_id="test")

    assert registry.reserve("task-1", command="Agent", limit=1)
    assert registry.pending_count == 1
    assert not registry.reserve("task-2", command="Agent", limit=1)

    registry.release("task-1")
    assert registry.pending_count == 0


@pytest.mark.asyncio
async def test_send_to_agent_delivers_to_running_agent():
    registry = BackgroundTaskRegistry(session_id="test")
    task = await _register_live(registry, "agent-1", "scan docs")
    try:
        execution = ToolExecution(tool_id="t", tool_name="send_to_agent", ctx=_ctx(registry))
        result = await background_module.send_to_agent(
            execution,
            background_module.SendToAgentInput(task_id="agent-1", message="also check pricing"),
        )
        assert not result.is_error
        assert "agent-1" in result.content

        drained = registry.drain_injections("agent-1")
        assert len(drained) == 1
        assert drained[0]["role"] == "user"
        assert "also check pricing" in drained[0]["content"]
        # drained exactly once
        assert registry.drain_injections("agent-1") == []
    finally:
        await _cancel(task)


@pytest.mark.asyncio
async def test_send_to_agent_unknown_id_lists_running_agents():
    registry = BackgroundTaskRegistry(session_id="test")
    task = await _register_live(registry, "agent-live", "scan docs")
    try:
        execution = ToolExecution(tool_id="t", tool_name="send_to_agent", ctx=_ctx(registry))
        result = await background_module.send_to_agent(
            execution,
            background_module.SendToAgentInput(task_id="agent-missing", message="hi"),
        )
        assert result.is_error
        assert "agent-live" in result.content
    finally:
        await _cancel(task)


@pytest.mark.asyncio
async def test_list_background_tasks_is_sorted_by_task_id():
    registry = BackgroundTaskRegistry(session_id="test")
    later = await _register_live(registry, "task-z", "later")
    earlier = await _register_live(registry, "task-a", "earlier")
    try:
        result = await background_module.list_background_tasks(
            ToolExecution(tool_id="t", tool_name="list_background_tasks", ctx=_ctx(registry)),
            background_module.EmptyInput(),
        )
        assert result.content.index("task-a") < result.content.index("task-z")
    finally:
        await _cancel(later)
        await _cancel(earlier)


@pytest.mark.asyncio
async def test_cancel_subtree_cancels_descendant_background_agents():
    from ntrp.server.state import RunRegistry

    reg = RunRegistry()
    # Agent A (session "P") spawned B, which runs in A's child session "P::a"
    # and itself spawned C (running in "P::a::b").
    rb = reg.get_background_registry("P::a")
    task_b = await _register_live(rb, "agent-B", "b")
    await rb.record_started(task_id="agent-B", command="b", child_session_id="P::a::b")
    rc = reg.get_background_registry("P::a::b")
    task_c = await _register_live(rc, "agent-C", "c")
    await rc.record_started(task_id="agent-C", command="c", child_session_id="P::a::b::c")

    try:
        cancelled = reg.cancel_subtree("P::a")
        assert set(cancelled) == {("P::a", "agent-B"), ("P::a::b", "agent-C")}
        with pytest.raises(asyncio.CancelledError):
            await task_b
        with pytest.raises(asyncio.CancelledError):
            await task_c
    finally:
        await _cancel(task_b)
        await _cancel(task_c)


@pytest.mark.asyncio
async def test_queue_injection_skips_finished_agent():
    registry = BackgroundTaskRegistry(session_id="test")
    done = asyncio.create_task(asyncio.sleep(0))
    await done
    registry.register("agent-done", done, command="x")
    assert registry.queue_injection("agent-done", {"role": "user", "content": "x"}) is False
    assert registry.queue_injection("agent-never-existed", {"role": "user", "content": "x"}) is False
