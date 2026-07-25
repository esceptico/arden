import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from arden.agent.ledger import SharedLedger
from arden.context.models import SessionState
from arden.core.tool_executor import ArdenToolExecutor
from arden.tool_call_metadata import DISPLAY_TITLE_ARG
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.registry import ToolRegistry
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope
from arden.tools.executor import ToolExecutor

READ_INTERNAL_POLICY = ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL)


def _tool_context(registry: ToolRegistry) -> ToolContext:
    return ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=registry,
        run=RunContext(run_id="run"),
        io=IOBridge(),
    )


@pytest.mark.asyncio
async def test_arden_tool_executor_skips_duplicate_successful_read():
    calls = 0
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    class SearchInput(BaseModel):
        query: str

    async def search(execution: ToolExecution, args: SearchInput) -> ToolResult:
        nonlocal calls
        calls += 1
        first_started.set()
        await release_first.wait()
        return ToolResult(content=f"result for {args.query}", preview="result")

    registry = ToolRegistry()
    registry.register(
        "search",
        tool(
            description="Search.",
            input_model=SearchInput,
            execute=search,
            policy=READ_INTERNAL_POLICY,
        ),
    )
    executor = ArdenToolExecutor(
        ToolExecutor().with_registry(registry),
        _tool_context(registry),
        ledger=SharedLedger(),
        skip_duplicate_reads=True,
    )

    first = asyncio.create_task(
        executor.execute(
            "search",
            {"query": "mcp", DISPLAY_TITLE_ARG: "Searching MCP docs"},
            "call-1",
        )
    )
    await first_started.wait()
    second = asyncio.create_task(
        executor.execute(
            "search",
            {"query": "mcp", DISPLAY_TITLE_ARG: "Checking MCP references"},
            "call-2",
        )
    )
    await asyncio.sleep(0)

    assert calls == 1

    release_first.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert calls == 1
    assert first_result.content == "result for mcp"
    assert second_result.content == '[Already read by another agent in this run: search {"query":"mcp"}]'


@pytest.mark.asyncio
async def test_arden_tool_executor_retries_duplicate_read_after_failure():
    calls = 0
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    class SearchInput(BaseModel):
        query: str

    async def search(execution: ToolExecution, args: SearchInput) -> ToolResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            await release_first.wait()
            return ToolResult(content="temporary failure", preview="failed", is_error=True)
        return ToolResult(content=f"result for {args.query}", preview="result")

    registry = ToolRegistry()
    registry.register(
        "search",
        tool(
            description="Search.",
            input_model=SearchInput,
            execute=search,
            policy=READ_INTERNAL_POLICY,
        ),
    )
    executor = ArdenToolExecutor(
        ToolExecutor().with_registry(registry),
        _tool_context(registry),
        ledger=SharedLedger(),
        skip_duplicate_reads=True,
    )

    first = asyncio.create_task(executor.execute("search", {"query": "mcp"}, "call-1"))
    await first_started.wait()
    second = asyncio.create_task(executor.execute("search", {"query": "mcp"}, "call-2"))
    await asyncio.sleep(0)

    assert calls == 1

    release_first.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert calls == 2
    assert first_result.is_error
    assert second_result.content == "result for mcp"


@pytest.mark.asyncio
async def test_arden_tool_executor_allows_duplicate_reads_by_default():
    calls = 0

    class SearchInput(BaseModel):
        query: str

    async def search(execution: ToolExecution, args: SearchInput) -> ToolResult:
        nonlocal calls
        calls += 1
        return ToolResult(content=f"result {calls} for {args.query}", preview="result")

    registry = ToolRegistry()
    registry.register(
        "search",
        tool(
            description="Search.",
            input_model=SearchInput,
            execute=search,
            policy=READ_INTERNAL_POLICY,
        ),
    )
    executor = ArdenToolExecutor(ToolExecutor().with_registry(registry), _tool_context(registry), ledger=SharedLedger())

    first_result = await executor.execute("search", {"query": "mcp"}, "call-1")
    second_result = await executor.execute("search", {"query": "mcp"}, "call-2")

    assert calls == 2
    assert first_result.content == "result 1 for mcp"
    assert second_result.content == "result 2 for mcp"


@pytest.mark.asyncio
async def test_duplicate_read_retries_after_post_processing_failure(monkeypatch):
    calls = 0

    class SearchInput(BaseModel):
        query: str

    async def search(execution: ToolExecution, args: SearchInput) -> ToolResult:
        nonlocal calls
        calls += 1
        return ToolResult(content=f"result for {args.query}", preview="result")

    registry = ToolRegistry()
    registry.register(
        "search",
        tool(
            description="Search.",
            input_model=SearchInput,
            execute=search,
            policy=READ_INTERNAL_POLICY,
        ),
    )
    executor = ArdenToolExecutor(
        ToolExecutor().with_registry(registry),
        _tool_context(registry),
        ledger=SharedLedger(),
        skip_duplicate_reads=True,
    )
    failures = 0

    def flaky_offload(result: ToolResult, tool_call_id: str) -> ToolResult:
        nonlocal failures
        if failures == 0:
            failures += 1
            raise RuntimeError("post-processing failed")
        return result

    monkeypatch.setattr(executor, "_maybe_offload", flaky_offload)

    with pytest.raises(RuntimeError, match="post-processing failed"):
        await executor.execute("search", {"query": "mcp"}, "call-1")

    result = await asyncio.wait_for(executor.execute("search", {"query": "mcp"}, "call-2"), timeout=1)

    assert calls == 2
    assert result.content == "result for mcp"
