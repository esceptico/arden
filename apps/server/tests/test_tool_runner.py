"""Tests for bounded, conflict-aware parallel tool execution."""

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

    def mark_provider_loaded_tools(self, names: set[str]) -> None:
        del names

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
async def test_runner_serializes_mutation_against_reads_in_same_group():
    metas = {
        "file_read": ToolMeta(name="file_read", display_name="Read", concurrency_group="files"),
        "file_write": ToolMeta(
            name="file_write",
            display_name="Write",
            changes_state=True,
            concurrency_group="files",
        ),
        "file_find": ToolMeta(name="file_find", display_name="Find", concurrency_group="files"),
    }
    release = {cid: asyncio.Event() for cid in ("c1", "c2", "c3")}
    executor = _FakeExecutor(metas, release)
    runner = ToolRunner(executor=executor, depth=0, parent_id=None)

    calls = [
        _make_call("c1", "file_read"),
        _make_call("c2", "file_write"),
        _make_call("c3", "file_find"),
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

    # The first read starts. The queued writer blocks the later read so it
    # cannot starve behind an unbounded stream of new readers.
    for _ in range(20):
        if executor.arrived:
            break
        await asyncio.sleep(0)
    assert executor.arrived == ["c1"]
    assert started == ["c1", "c2", "c3"]
    assert completed == []

    release["c1"].set()
    for _ in range(20):
        if executor.arrived == ["c1", "c2"]:
            break
        await asyncio.sleep(0)
    assert executor.arrived == ["c1", "c2"]
    release["c2"].set()
    for _ in range(20):
        if len(executor.arrived) == 3:
            break
        await asyncio.sleep(0)
    assert executor.arrived == ["c1", "c2", "c3"]
    release["c3"].set()
    await consumer
    assert completed == ["c1", "c2", "c3"]


@pytest.mark.asyncio
async def test_runner_keeps_unrelated_resource_groups_parallel():
    metas = {
        "file_write": ToolMeta(
            name="file_write",
            display_name="Write",
            changes_state=True,
            concurrency_group="filesystem",
        ),
        "current_time": ToolMeta(
            name="current_time",
            display_name="Time",
            concurrency_group="current_time",
        ),
    }
    release = {cid: asyncio.Event() for cid in ("c1", "c2")}
    executor = _FakeExecutor(metas, release)
    runner = ToolRunner(executor=executor, depth=0, parent_id=None)
    consumer = asyncio.create_task(
        _consume(runner.execute_all([_make_call("c1", "file_write"), _make_call("c2", "current_time")]))
    )

    for _ in range(20):
        if len(executor.arrived) == 2:
            break
        await asyncio.sleep(0)
    assert set(executor.arrived) == {"c1", "c2"}
    release["c1"].set()
    release["c2"].set()
    await consumer


@pytest.mark.asyncio
async def test_runner_caps_active_tool_executions():
    class _CountingExecutor:
        def __init__(self) -> None:
            self.active = 0
            self.peak = 0
            self.release = asyncio.Event()

        def get_meta(self, name: str) -> ToolMeta:
            return ToolMeta(name=name, display_name=name, concurrency_group=name)

        def mark_provider_loaded_tools(self, names: set[str]) -> None:
            del names

        async def execute(self, name: str, args: dict, tool_call_id: str) -> ToolResult:
            del name, args, tool_call_id
            self.active += 1
            self.peak = max(self.peak, self.active)
            await self.release.wait()
            self.active -= 1
            return ToolResult(content="ok", preview="ok")

    executor = _CountingExecutor()
    runner = ToolRunner(executor=executor, depth=0, parent_id=None, max_concurrency=6)
    calls = [_make_call(f"c{i}", f"tool_{i}") for i in range(12)]

    stream = runner.execute_all(calls)
    consume_task = asyncio.create_task(_consume(stream))
    for _ in range(50):
        if executor.peak == 6:
            break
        await asyncio.sleep(0)
    assert executor.peak == 6
    executor.release.set()
    await consume_task
    assert executor.peak == 6


async def _consume(stream) -> None:
    async for _ in stream:
        pass


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


@pytest.mark.asyncio
async def test_runner_sanitizes_uncaught_tool_exception_and_returns_diagnostic_ref():
    class _ExplodingExecutor:
        def get_meta(self, name: str) -> ToolMeta | None:
            return ToolMeta(name=name, display_name="Exploding tool")

        def mark_provider_loaded_tools(self, names: set[str]) -> None:
            del names

        async def execute(self, name: str, args: dict, tool_call_id: str) -> ToolResult:
            del name, args, tool_call_id
            raise RuntimeError("provider secret")

    events = [
        event
        async for event in ToolRunner(_ExplodingExecutor(), depth=0, parent_id=None).execute_all(
            [_make_call("call-secret", "explode")]
        )
    ]

    completed = next(event for event in events if isinstance(event, ToolCompleted))
    assert completed.is_error
    assert "provider secret" not in completed.result
    assert completed.outcome is not None and completed.outcome.error is not None
    assert completed.outcome.status == ToolOutcomeStatus.UNCERTAIN
    assert completed.outcome.error.code == "internal_error"
    assert completed.outcome.error.diagnostic_ref == "tool-call:call-secret"
    assert "before retrying" in completed.outcome.error.recovery_action


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
