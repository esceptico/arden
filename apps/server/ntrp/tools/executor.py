from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Self

from ntrp.integrations import ALL_INTEGRATIONS
from ntrp.logging import get_logger
from ntrp.tools.core.base import Tool, ToolResult
from ntrp.tools.core.context import ToolExecution
from ntrp.tools.core.middleware import ToolMiddleware
from ntrp.tools.core.registry import ToolRegistry
from ntrp.tools.core.types import ToolAction, ToolOverrideDecision
from ntrp.tools.discover import discover_user_tools

_logger = get_logger(__name__)


def _tools_from_named_tools(named_tools: Iterable[Tool]) -> dict[str, Tool]:
    tools: dict[str, Tool] = {}
    for tool in named_tools:
        if tool.name in tools:
            _logger.warning("MCP tool %r skipped — duplicate MCP tool", tool.name)
            continue
        tools[tool.name] = tool
    return tools


class ToolExecutor:
    def __init__(
        self,
        mcp_tools: list[Tool] | None = None,
        get_services: Callable[[], dict[str, Any]] = dict,
        tool_middlewares: Sequence[ToolMiddleware] | None = None,
        tool_overrides: Mapping[str, ToolOverrideDecision | str] | None = None,
    ):
        self._get_services = get_services
        self.registry = (
            ToolRegistry(tool_overrides=tool_overrides)
            if tool_middlewares is None
            else ToolRegistry(middlewares=tool_middlewares, tool_overrides=tool_overrides)
        )
        for integration in ALL_INTEGRATIONS:
            self._register_tools(
                integration.tools,
                source=integration.id,
                conflict="error",
                command_tool_names=integration.command_tool_names,
            )

        self._register_tools(discover_user_tools(), source="user", conflict="skip")

        if mcp_tools:
            self._register_tools(_tools_from_named_tools(mcp_tools), source="mcp", conflict="skip")

        capabilities = frozenset(self._get_services())
        hidden = [
            (name, tool)
            for name, tool in self.registry.tools.items()
            if tool.policy.permissions and not tool.policy.permissions.issubset(capabilities)
        ]
        if hidden:
            by_req = {}
            for name, tool in hidden:
                key = ", ".join(sorted(tool.policy.permissions))
                by_req.setdefault(key, []).append(name)
            for req, names in by_req.items():
                _logger.info("Tools hidden (missing %s): %s", req, ", ".join(names))

    @property
    def tool_services(self) -> dict[str, Any]:
        return self._get_services()

    def _register_tools(
        self,
        tools: Mapping[str, Tool],
        *,
        source: str,
        conflict: str,
        command_tool_names: frozenset[str] = frozenset(),
    ) -> None:
        for name, tool in tools.items():
            if name in self.registry:
                if conflict == "skip":
                    _logger.warning("%s tool %r skipped — conflicts with existing tool", source, name)
                    continue
                raise ValueError(f"duplicate tool name from {source}: {name}")
            self.registry.register(
                name,
                tool,
                source=source,
                command_eligible=name in command_tool_names,
            )
            if not source.startswith("_"):
                _logger.info("Loaded %s tool: %s", source, name)

    def with_registry(self, registry: ToolRegistry) -> Self:
        clone = ToolExecutor.__new__(ToolExecutor)
        clone._get_services = self._get_services
        clone.registry = registry
        return clone

    async def execute(self, tool_name: str, arguments: dict, execution: ToolExecution) -> ToolResult:
        tool = self.registry.get(tool_name)
        if not tool:
            return ToolResult.failure(
                code="unknown_tool",
                message=f"Unknown tool: {tool_name}. Check available tools in the system prompt.",
                preview="Unknown tool",
                recovery_action="Use a tool exposed in the current system prompt.",
            )

        return await self.registry.execute(tool_name, execution, arguments)

    def get_tools(
        self,
        read_only: bool | None = None,
        actions: frozenset[ToolAction] | None = None,
        extra_names: frozenset[str] = frozenset(),
        scope: tuple[str, ...] | None = None,
        command_eligible: bool | None = None,
    ) -> list[dict]:
        return self.registry.get_schemas(
            capabilities=frozenset(self._get_services()),
            read_only=read_only,
            actions=actions,
            extra_names=extra_names,
            scope=scope,
            command_eligible=command_eligible,
        )

    def get_tool_metadata(self) -> list[dict]:
        return self.registry.get_metadata()
