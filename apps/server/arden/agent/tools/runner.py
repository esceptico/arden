import asyncio
import contextlib
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from arden.agent.tools.executor import AgentToolExecutor
from arden.agent.types.events import ToolCompleted, ToolStarted
from arden.agent.types.tool_call import PendingToolCall
from arden.agent.types.tools import ToolOutcomeStatus, ToolResult, normalize_source_refs
from arden.constants import AGENT_MAX_CONCURRENT_TOOLS
from arden.logging import get_logger

_SENTINEL = object()
_logger = get_logger(__name__)


@dataclass(frozen=True)
class _ResolvedCall:
    call: PendingToolCall
    display_name: str
    kind: str = "tool"
    icon: str | None = None
    noun: str | None = None
    source: str | None = None
    changes_state: bool = False
    concurrency_group: str | None = None
    ends_turn: bool = False


@dataclass(frozen=True)
class _ConcurrentResult:
    resolved: _ResolvedCall
    result: ToolResult
    duration_ms: int


def _ms_now() -> int:
    return int(time.monotonic() * 1000)


class _GroupState:
    def __init__(self) -> None:
        self.condition = asyncio.Condition()
        self.readers = 0
        self.writer = False
        self.waiting_writers = 0

    async def acquire(self, *, write: bool) -> None:
        async with self.condition:
            if write:
                self.waiting_writers += 1
                try:
                    await self.condition.wait_for(lambda: not self.writer and self.readers == 0)
                    self.writer = True
                finally:
                    self.waiting_writers -= 1
            else:
                await self.condition.wait_for(lambda: not self.writer and self.waiting_writers == 0)
                self.readers += 1

    async def release(self, *, write: bool) -> None:
        async with self.condition:
            if write:
                self.writer = False
            else:
                self.readers -= 1
            self.condition.notify_all()


class _ConcurrencyCoordinator:
    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("tool concurrency limit must be positive")
        self._global = asyncio.Semaphore(limit)
        self._groups: dict[str, _GroupState] = {}

    @asynccontextmanager
    async def slot(self, call: _ResolvedCall):
        group = self._groups.setdefault(call.concurrency_group or call.call.name, _GroupState())
        await group.acquire(write=call.changes_state)
        try:
            async with self._global:
                yield
        finally:
            await group.release(write=call.changes_state)


class ToolRunner:
    def __init__(
        self,
        executor: AgentToolExecutor,
        depth: int,
        parent_id: str | None,
        max_concurrency: int = AGENT_MAX_CONCURRENT_TOOLS,
    ):
        self._executor = executor
        self._depth = depth
        self._parent_id = parent_id
        self._concurrency = _ConcurrencyCoordinator(max_concurrency)

    def _resolve(self, call: PendingToolCall) -> _ResolvedCall:
        meta = self._executor.get_meta(call.name)
        return _ResolvedCall(
            call=call,
            display_name=meta.display_name if meta else call.name,
            kind=meta.kind if meta else "tool",
            icon=meta.icon if meta else None,
            noun=meta.noun if meta else None,
            source=meta.source if meta else None,
            changes_state=meta.changes_state if meta else False,
            concurrency_group=meta.concurrency_group if meta else call.name,
            ends_turn=meta.ends_turn if meta else False,
        )

    async def _run_one(self, rc: _ResolvedCall) -> tuple[ToolResult, int]:
        start_ms = _ms_now()
        if error := rc.call.argument_error:
            return (
                ToolResult.failure(
                    code=error.code,
                    message=(
                        f"{error.code}: {error.message} "
                        "Retry the tool call with one valid JSON object matching its schema."
                    ),
                    preview="Invalid tool arguments",
                    recovery_action="Retry with one valid JSON object matching the tool schema.",
                ),
                _ms_now() - start_ms,
            )
        try:
            result = await self._executor.execute(rc.call.name, rc.call.args, rc.call.tool_call.id)
            result = result.with_default_outcome()
            return result, _ms_now() - start_ms
        except Exception:
            diagnostic_ref = f"tool-call:{rc.call.tool_call.id}"
            _logger.exception(
                "Unhandled tool execution failed",
                tool_name=rc.call.name,
                tool_call_id=rc.call.tool_call.id,
                diagnostic_ref=diagnostic_ref,
            )
            return (
                ToolResult.failure(
                    code="internal_error",
                    message=(f"Tool execution failed unexpectedly. Diagnostic reference: {diagnostic_ref}."),
                    preview="Tool execution failed",
                    status=ToolOutcomeStatus.UNCERTAIN,
                    recovery_action=(
                        "Check the target's current state before retrying. If state cannot be verified, "
                        "report the diagnostic reference for server-side investigation."
                    ),
                    diagnostic_ref=diagnostic_ref,
                ),
                _ms_now() - start_ms,
            )

    def _started(self, rc: _ResolvedCall) -> ToolStarted:
        return ToolStarted(
            tool_id=rc.call.tool_call.id,
            name=rc.call.name,
            args=rc.call.args,
            depth=self._depth,
            parent_id=self._parent_id,
            display_name=rc.display_name,
            kind=rc.kind,
            icon=rc.icon,
            noun=rc.noun,
            source=rc.source,
        )

    def _completed(self, rc: _ResolvedCall, result: ToolResult, duration_ms: int) -> ToolCompleted:
        return ToolCompleted(
            tool_id=rc.call.tool_call.id,
            name=rc.call.name,
            result=result.content,
            preview=result.preview,
            depth=self._depth,
            parent_id=self._parent_id,
            duration_ms=duration_ms,
            is_error=result.is_error,
            data=result.data,
            display_name=rc.display_name,
            kind=rc.kind,
            model_content=result.model_content,
            source_refs=normalize_source_refs(result.source_refs),
            outcome=result.outcome,
            ends_turn=rc.ends_turn,
        )

    async def reject_all(
        self,
        calls: list[PendingToolCall],
        result: ToolResult,
    ) -> AsyncGenerator[ToolStarted | ToolCompleted]:
        rejected = result.with_default_outcome()
        for call in calls:
            resolved = self._resolve(call)
            yield self._started(resolved)
            yield self._completed(resolved, rejected, 0)

    async def execute_all(self, calls: list[PendingToolCall]) -> AsyncGenerator[ToolStarted | ToolCompleted]:
        """Run every tool the model emitted in this step in parallel.

        Approvals are routed per `tool_id` via `IOBridge.pending_approvals`,
        so multiple mutating tools can each await their own approval
        Future without racing on a shared queue. Tools that don't need
        approval (or have skip_approvals/auto_approve) just run straight
        through. Either way, results stream back in completion order via
        `queue` while every tool's `started` event fires up front.
        """
        resolved = [self._resolve(c) for c in calls]
        if not resolved:
            return

        queue: asyncio.Queue[_ConcurrentResult | object] = asyncio.Queue()

        for rc in resolved:
            yield self._started(rc)

        async def run_one(rc: _ResolvedCall) -> None:
            async with self._concurrency.slot(rc):
                result, duration_ms = await self._run_one(rc)
            await queue.put(_ConcurrentResult(resolved=rc, result=result, duration_ms=duration_ms))

        async def run_all() -> None:
            try:
                async with asyncio.TaskGroup() as tg:
                    for rc in resolved:
                        tg.create_task(run_one(rc))
            finally:
                await queue.put(_SENTINEL)

        task = asyncio.create_task(run_all())

        try:
            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    break
                yield self._completed(item.resolved, item.result, item.duration_ms)
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
