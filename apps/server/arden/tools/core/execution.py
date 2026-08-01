from dataclasses import dataclass
from typing import Any, Protocol

from arden.tools.core.base import ToolResult
from arden.tools.core.context import ToolExecution
from arden.tools.core.registry import ToolRegistry


@dataclass(frozen=True)
class ToolInvocation:
    """One logical execution of a tool call, idempotent by invocation_id."""

    invocation_id: str
    tool_name: str
    arguments: dict[str, Any]


class ExecutionBackend(Protocol):
    async def execute(self, invocation: ToolInvocation, execution: ToolExecution) -> ToolResult: ...


class InProcessExecutionBackend:
    def __init__(self, registry: ToolRegistry):
        self._registry = registry

    async def execute(self, invocation: ToolInvocation, execution: ToolExecution) -> ToolResult:
        return await self._registry.execute(invocation.tool_name, execution, invocation.arguments)


class ExecutionRouter:
    """Routes an invocation to the backend that executes it.

    Every tool routes in-process today; client-executor routing slots in here
    without touching tool code or callers.
    """

    def __init__(self, registry: ToolRegistry):
        self._in_process = InProcessExecutionBackend(registry)

    def backend_for(self, tool_name: str) -> ExecutionBackend:
        return self._in_process

    async def execute(self, invocation: ToolInvocation, execution: ToolExecution) -> ToolResult:
        backend = self.backend_for(invocation.tool_name)
        return await backend.execute(invocation, execution)
