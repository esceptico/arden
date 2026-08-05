import asyncio
import contextlib
from datetime import UTC, datetime

import pytest

import arden.tools.background as background_module
from arden.constants import OFFLOAD_THRESHOLD
from arden.context.models import SessionState
from arden.server.state import RunRegistry
from arden.tools.core.context import BackgroundTaskRegistry, IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.registry import ToolRegistry


def _ctx(registry: BackgroundTaskRegistry, run_registry: RunRegistry | None = None) -> ToolContext:
    return ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(),
        background_tasks=registry,
        run_registry=run_registry,
    )


@pytest.mark.asyncio
async def test_agent_result_read_pages_exact_unicode_content():
    full = 'αβ\n"quoted"\n終わり'

    async def read_result(task_id: str) -> str | None:
        return full if task_id == "agent-1" else None

    registry = BackgroundTaskRegistry(session_id="test", read_result=read_result)
    execution = ToolExecution(tool_id="t", tool_name="agent_result_read", ctx=_ctx(registry))

    first = await background_module.agent_result_read(
        execution,
        background_module.AgentResultReadInput(task_id="agent-1", offset=0, limit=6),
    )
    second = await background_module.agent_result_read(
        execution,
        background_module.AgentResultReadInput(
            task_id="agent-1",
            offset=first.data["next_offset"],
        ),
    )

    assert full[:6] in first.content
    assert full[6:] in second.content
    assert first.data == {
        "task_id": "agent-1",
        "offset": 0,
        "end_offset": 6,
        "next_offset": 6,
        "total_chars": len(full),
        "has_more": True,
    }
    assert 'agent_result_read(task_id="agent-1", offset=6, limit=6)' in first.content
    assert second.data["next_offset"] is None
    assert second.data["has_more"] is False
    assert "End of background agent result." in second.content
    assert "Continue with agent_result_read" not in second.content


@pytest.mark.asyncio
async def test_agent_result_read_worst_case_page_stays_direct_and_bounded():
    full = ('\x00"\\💥' * 2_000) + "tail"

    async def read_result(_task_id: str) -> str:
        return full

    registry = BackgroundTaskRegistry(session_id="test", read_result=read_result)
    result = await background_module.agent_result_read(
        ToolExecution(tool_id="t", tool_name="agent_result_read", ctx=_ctx(registry)),
        background_module.AgentResultReadInput(task_id="x" * 200),
    )
    assert full[: background_module.BACKGROUND_RESULT_READ_MAX_CHARS] in result.content
    assert result.data["next_offset"] == background_module.BACKGROUND_RESULT_READ_MAX_CHARS
    assert not result.content.lstrip().startswith("{")
    # The executor's unconditional final payload bound must not replace this
    # page with a generic file pointer, even at maximum escaping expansion.
    assert len(result.serialized_payload().encode("utf-8")) <= OFFLOAD_THRESHOLD


@pytest.mark.asyncio
async def test_agent_result_read_rejects_missing_result_and_past_end_offset():
    async def read_result(task_id: str) -> str | None:
        return "done" if task_id == "agent-1" else None

    registry = BackgroundTaskRegistry(session_id="test", read_result=read_result)
    execution = ToolExecution(tool_id="t", tool_name="agent_result_read", ctx=_ctx(registry))

    missing = await background_module.agent_result_read(
        execution,
        background_module.AgentResultReadInput(task_id="missing"),
    )
    past_end = await background_module.agent_result_read(
        execution,
        background_module.AgentResultReadInput(task_id="agent-1", offset=5),
    )

    assert missing.is_error
    assert missing.outcome.error.code == "not_found"
    assert past_end.is_error
    assert past_end.outcome.error.code == "invalid_arguments"


@pytest.mark.asyncio
async def test_agent_result_read_empty_terminal_page_and_quoted_task_locator():
    task_id = 'agent-"quoted"\nnext'

    async def read_result(candidate: str) -> str | None:
        return "done" if candidate == task_id else None

    registry = BackgroundTaskRegistry(session_id="test", read_result=read_result)
    result = await background_module.agent_result_read(
        ToolExecution(tool_id="t", tool_name="agent_result_read", ctx=_ctx(registry)),
        background_module.AgentResultReadInput(task_id=task_id, offset=4, limit=3),
    )

    assert result.data == {
        "task_id": task_id,
        "offset": 4,
        "end_offset": 4,
        "next_offset": None,
        "total_chars": 4,
        "has_more": False,
    }
    assert 'Task: "agent-\\"quoted\\"\\nnext"' in result.content
    assert "End of background agent result." in result.content


async def _register_live(registry: BackgroundTaskRegistry, task_id: str, command: str) -> asyncio.Task:
    task = asyncio.create_task(asyncio.sleep(3600))
    registry.register(task_id, task, command=command)
    return task


async def _cancel(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def _live_agent(registry: BackgroundTaskRegistry, task_id: str, child_session_id: str) -> asyncio.Task:
    task = await _register_live(registry, task_id, "scan docs")
    await registry.record_started(task_id=task_id, command="scan docs", child_session_id=child_session_id)
    return task


def test_background_registry_reservations_count_toward_cap():
    registry = BackgroundTaskRegistry(session_id="test")

    assert registry.reserve("task-1", command="Agent", limit=1)
    assert registry.pending_count == 1
    assert not registry.reserve("task-2", command="Agent", limit=1)

    registry.release("task-1")
    assert registry.pending_count == 0


@pytest.mark.asyncio
async def test_task_for_session_ignores_finished_agents():
    registry = BackgroundTaskRegistry(session_id="test")
    live = await _live_agent(registry, "agent-live", "test::live")
    done = asyncio.create_task(asyncio.sleep(0))
    await done
    registry.register("agent-done", done, command="x")
    await registry.record_started(task_id="agent-done", command="x", child_session_id="test::done")

    try:
        assert registry.task_for_session("test::live") == "agent-live"
        assert registry.task_for_session("test::done") is None
        assert registry.task_for_session("test::never") is None
    finally:
        await _cancel(live)


@pytest.mark.asyncio
async def test_live_child_sessions_lists_only_running():
    registry = BackgroundTaskRegistry(session_id="test")
    first = await _live_agent(registry, "agent-b", "test::b")
    second = await _live_agent(registry, "agent-a", "test::a")
    # Reserved-but-unregistered spawns have not started, so they are not steerable.
    registry.reserve("agent-r", command="Agent", limit=9, child_session_id="test::r")

    try:
        assert registry.live_child_sessions() == ["test::a", "test::b"]
    finally:
        await _cancel(first)
        await _cancel(second)


@pytest.mark.asyncio
async def test_cancel_agent_resolves_the_task_from_the_session_id():
    registry = BackgroundTaskRegistry(session_id="test")
    task = await _live_agent(registry, "agent-1", "P::a")

    try:
        result = await background_module.agent_cancel(
            ToolExecution(tool_id="t", tool_name="agent_cancel", ctx=_ctx(registry)),
            background_module.AgentCancelInput(session_id="P::a"),
        )

        assert not result.is_error
        assert "P::a" in result.content
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        await _cancel(task)


@pytest.mark.asyncio
async def test_cancel_agent_unknown_session_lists_live_agent_sessions():
    registry = BackgroundTaskRegistry(session_id="test")
    task = await _live_agent(registry, "agent-1", "P::a")

    try:
        result = await background_module.agent_cancel(
            ToolExecution(tool_id="t", tool_name="agent_cancel", ctx=_ctx(registry)),
            background_module.AgentCancelInput(session_id="P::ghost"),
        )

        assert result.is_error
        assert result.outcome.error.code == "not_found"
        assert "P::a" in result.content
    finally:
        await _cancel(task)


@pytest.mark.asyncio
async def test_cancel_agent_cascades_to_grandchildren():
    reg = RunRegistry()
    # This session spawned B (running in "P::a"), which itself spawned C in "P::a::b".
    own = reg.get_background_registry("test")
    task_b = await _live_agent(own, "agent-B", "P::a")
    rb = reg.get_background_registry("P::a")
    task_c = await _register_live(rb, "agent-C", "c")
    await rb.record_started(task_id="agent-C", command="c", child_session_id="P::a::b")

    try:
        result = await background_module.agent_cancel(
            ToolExecution(tool_id="t", tool_name="agent_cancel", ctx=_ctx(own, run_registry=reg)),
            background_module.AgentCancelInput(session_id="P::a"),
        )

        assert not result.is_error
        assert "Also stopped 1 agent(s)" in result.content
        with pytest.raises(asyncio.CancelledError):
            await task_b
        with pytest.raises(asyncio.CancelledError):
            await task_c
    finally:
        await _cancel(task_b)
        await _cancel(task_c)


@pytest.mark.asyncio
async def test_cancel_agent_approval_preview_names_the_session():
    approval = await background_module.agent_cancel_tool.approval_info(
        ToolExecution(
            tool_id="t",
            tool_name="agent_cancel",
            ctx=_ctx(BackgroundTaskRegistry(session_id="test")),
        ),
        session_id="P::a1",
    )

    assert approval.preview == "Agent session: P::a1"


@pytest.mark.asyncio
async def test_cancel_subtree_cancels_descendant_background_agents():
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


@pytest.mark.asyncio
async def test_close_inbox_refuses_late_steering_and_returns_leftovers():
    registry = BackgroundTaskRegistry(session_id="test")
    task = await _register_live(registry, "agent-A", "a")
    try:
        assert registry.queue_steering("agent-A", "before close") is True

        leftover = registry.close_inbox("agent-A")

        assert len(leftover) == 1
        assert "before close" in leftover[0]["content"]
        # The task is still not done (post-loop cleanup window) — a closed
        # inbox must refuse anyway, so the sender gets a conflict, not a
        # false "queued".
        assert not task.done()
        assert registry.queue_steering("agent-A", "after close") is False
    finally:
        await _cancel(task)


@pytest.mark.asyncio
async def test_deliver_result_folds_undelivered_steering_into_notification():
    captured: list[dict] = []

    async def on_result(messages: list[dict]) -> None:
        captured.extend(messages)

    registry = BackgroundTaskRegistry(session_id="test", on_result=on_result)

    await registry.deliver_result(
        task_id="agent-A",
        result="done",
        label="Agent",
        status="completed",
        emit=None,
        undelivered_steering=["<steering_message>\ntoo late\n</steering_message>"],
    )

    assert captured
    assert "<undelivered_steering>" in captured[0]["content"]
    assert "too late" in captured[0]["content"]
