"""Tests for the unified parallel tool runner.

Mutating + non-mutating calls now run in one TaskGroup instead of being
split into sequential mutating + parallel non-mutating phases. Approvals
are routed per tool_id via Future, so multiple mutating tools can each
await their own approval without racing on a shared queue.
"""

import asyncio

import pytest

from arden.agent.tools.dispatch import _append_results
from arden.agent.tools.runner import ToolRunner
from arden.agent.types.events import ToolCompleted, ToolStarted
from arden.agent.types.tool_call import FunctionCall, PendingToolCall, ToolArgumentError, ToolCall
from arden.agent.types.tools import ToolMeta, ToolOutcomeStatus, ToolResult


class _FakeExecutor:
    """Records execute() arrival order and lets each tool be released
    independently so we can prove "all started before any completed"."""

    def __init__(self, metas: dict[str, ToolMeta], release: dict[str, asyncio.Event]):
        self._metas = metas
        self._release = release
        self.arrived: list[str] = []

    def get_meta(self, name: str) -> ToolMeta | None:
        return self._metas.get(name)

    async def execute(self, name: str, args: dict, tool_call_id: str) -> ToolResult:
        self.arrived.append(tool_call_id)
        await self._release[tool_call_id].wait()
        return ToolResult(content=f"ok-{tool_call_id}", preview=name)


def _make_call(call_id: str, name: str) -> PendingToolCall:
    return PendingToolCall(
        tool_call=ToolCall(id=call_id, type="function", function=FunctionCall(name=name, arguments="{}")),
        name=name,
        args={},
    )


@pytest.mark.asyncio
async def test_runner_executes_mutating_and_non_mutating_in_one_batch():
    """All tools should start in parallel — no second phase for mutating."""
    metas = {
        "read_file": ToolMeta(name="read_file", display_name="Read"),
        "write_file": ToolMeta(name="write_file", display_name="Write"),
        "bash": ToolMeta(name="bash", display_name="Bash"),
    }
    release = {cid: asyncio.Event() for cid in ("c1", "c2", "c3")}
    executor = _FakeExecutor(metas, release)
    runner = ToolRunner(executor=executor, depth=0, parent_id=None)

    calls = [
        _make_call("c1", "read_file"),
        _make_call("c2", "write_file"),
        _make_call("c3", "bash"),
    ]

    started: list[str] = []
    completed: list[str] = []

    async def consume() -> None:
        async for event in runner.execute_all(calls):
            if isinstance(event, ToolStarted):
                started.append(event.tool_id)
            elif isinstance(event, ToolCompleted):
                completed.append(event.tool_id)

    consumer = asyncio.create_task(consume())

    # Yield until every tool has reached executor.execute() — proves
    # they're all dispatched concurrently before any has returned.
    for _ in range(20):
        if len(executor.arrived) == 3:
            break
        await asyncio.sleep(0)
    assert set(executor.arrived) == {"c1", "c2", "c3"}
    assert started == ["c1", "c2", "c3"]
    assert completed == []  # nobody finished yet — gating on release events

    # Release in reverse order; results should arrive in completion order.
    release["c3"].set()
    release["c1"].set()
    release["c2"].set()
    await consumer
    assert set(completed) == {"c1", "c2", "c3"}


@pytest.mark.asyncio
async def test_runner_adds_succeeded_outcome_to_legacy_tool_result():
    release = {"c1": asyncio.Event()}
    release["c1"].set()
    runner = ToolRunner(
        executor=_FakeExecutor({"read": ToolMeta(name="read", display_name="Read")}, release),
        depth=0,
        parent_id=None,
    )

    events = [event async for event in runner.execute_all([_make_call("c1", "read")])]

    completed = next(event for event in events if isinstance(event, ToolCompleted))
    assert completed.outcome is not None
    assert completed.outcome.status == ToolOutcomeStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_runner_handles_empty_call_list():
    metas: dict[str, ToolMeta] = {}
    executor = _FakeExecutor(metas, {})
    runner = ToolRunner(executor=executor, depth=0, parent_id=None)
    events = [event async for event in runner.execute_all([])]
    assert events == []


@pytest.mark.asyncio
async def test_runner_projects_invalid_arguments_as_typed_failure_without_execution():
    executor = _FakeExecutor(
        {"send": ToolMeta(name="send", display_name="Send")},
        {"c1": asyncio.Event()},
    )
    runner = ToolRunner(executor=executor, depth=0, parent_id=None)
    call = PendingToolCall(
        tool_call=ToolCall(id="c1", type="function", function=FunctionCall(name="send", arguments="{")),
        name="send",
        args={},
        argument_error=ToolArgumentError(
            code="invalid_tool_arguments",
            message="Malformed JSON",
        ),
    )

    events = [event async for event in runner.execute_all([call])]

    completed = next(event for event in events if isinstance(event, ToolCompleted))
    assert executor.arrived == []
    assert completed.outcome is not None
    assert completed.outcome.status == ToolOutcomeStatus.FAILED
    assert completed.outcome.error is not None
    assert completed.outcome.error.code == "invalid_tool_arguments"


def test_append_results_uses_normal_missing_fallback():
    messages: list[dict] = []
    tool_calls = [
        ToolCall(id="c1", type="function", function=FunctionCall(name="done", arguments="{}")),
        ToolCall(id="c2", type="function", function=FunctionCall(name="missing", arguments="{}")),
    ]

    _append_results(
        messages,
        tool_calls,
        {"c1": "ok"},
        missing_content="Tool call result missing.",
    )

    assert [m["content"] for m in messages] == ["ok", "Tool call result missing."]
