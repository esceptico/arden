from dataclasses import dataclass
from typing import Any, Protocol

from arden.tools.core.base import ToolResult
from arden.tools.core.context import ToolExecution
from arden.tools.core.registry import ToolRegistry
from arden.tools.core.types import ToolPlacement


@dataclass(frozen=True)
class ToolInvocation:
    """One logical execution of a tool call, idempotent by invocation_id."""

    invocation_id: str
    tool_name: str
    arguments: dict[str, Any]
    # The caller's effective timeout, so out-of-process backends can record a
    # durable deadline for the invocation. None = no timeout.
    timeout_seconds: float | None = None


class ExecutionBackend(Protocol):
    async def execute(self, invocation: ToolInvocation, execution: ToolExecution) -> ToolResult: ...


class ClientBackend(ExecutionBackend, Protocol):
    def available(self) -> bool: ...


class InProcessExecutionBackend:
    def __init__(self, registry: ToolRegistry):
        self._registry = registry

    async def execute(self, invocation: ToolInvocation, execution: ToolExecution) -> ToolResult:
        return await self._registry.execute(invocation.tool_name, execution, invocation.arguments)


class ExecutionRouter:
    """Routes an invocation to the backend that executes it.

    Server-placed tools run in-process. Client-placed tools route to the
    client execution backend when one is wired; with none wired they fall
    through to in-process, where the tool's own handler refuses — a
    client-placed tool never silently executes server work.
    """

    def __init__(self, registry: ToolRegistry, client_backend: "ClientBackend | None" = None):
        self._registry = registry
        self._in_process = InProcessExecutionBackend(registry)
        self.client_backend = client_backend

    def set_client_backend(self, backend: "ClientBackend") -> None:
        self.client_backend = backend

    def client_tools_available(self) -> bool:
        """Client-placed tools are advertised to a run only while a device
        executor is connected."""
        return self.client_backend is not None and self.client_backend.available()

    def backend_for(self, tool_name: str) -> ExecutionBackend:
        tool = self._registry.get(tool_name)
        if tool is not None and tool.policy.placement == ToolPlacement.CLIENT and self.client_backend is not None:
            return self.client_backend
        return self._in_process

    async def execute(self, invocation: ToolInvocation, execution: ToolExecution) -> ToolResult:
        backend = self.backend_for(invocation.tool_name)
        return await backend.execute(invocation, execution)
