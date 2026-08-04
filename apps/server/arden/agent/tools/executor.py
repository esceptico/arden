from typing import Protocol

from arden.agent.types.tools import ToolMeta, ToolResult


class AgentToolExecutor(Protocol):
    async def execute(self, name: str, args: dict, tool_call_id: str) -> ToolResult: ...

    def get_meta(self, name: str) -> ToolMeta | None: ...

    def mark_provider_loaded_tools(self, names: set[str]) -> None: ...
