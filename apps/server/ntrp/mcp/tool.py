from typing import Any, Protocol

from mcp.types import CallToolResult, ToolAnnotations
from mcp.types import Tool as McpTool

from ntrp.mcp.results import call_tool_result_to_tool_result
from ntrp.tools.core.base import Tool, ToolResult
from ntrp.tools.core.context import ToolExecution
from ntrp.tools.core.types import ToolAction, ToolPolicy, ToolScope


class MCPToolSession(Protocol):
    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> CallToolResult: ...


class MCPTool(Tool):
    policy = ToolPolicy(
        action=ToolAction.EXECUTE,
        scope=ToolScope.EXTERNAL,
        requires_approval=True,
        permissions=frozenset({"mcp"}),
    )
    input_model = None

    def __init__(
        self,
        server_name: str,
        mcp_tool: McpTool,
        session: MCPToolSession,
        *,
        policy: ToolPolicy | None = None,
        trust_annotations: bool = False,
    ):
        self._server_name = server_name
        self._mcp_tool = mcp_tool
        self._session = session
        self.policy = policy or _policy_from_annotations(mcp_tool.annotations, trust_annotations) or self.policy

    @property
    def name(self) -> str:
        return f"mcp_{self._server_name}__{self._mcp_tool.name}"

    @property
    def display_name(self) -> str:
        return f"{self._mcp_tool.name} ({self._server_name})"

    @property
    def description(self) -> str:
        return self._mcp_tool.description or f"MCP tool from {self._server_name}"

    async def execute(self, execution: ToolExecution, **kwargs: Any) -> ToolResult:
        try:
            result = await self._session.call_tool(self._mcp_tool.name, kwargs)
            return call_tool_result_to_tool_result(
                result,
                provider=self._server_name,
                tool_name=self._mcp_tool.name,
            )
        except Exception as e:
            return ToolResult(
                content=f"MCP tool error ({self._server_name}/{self._mcp_tool.name}): {e}",
                preview="MCP error",
                is_error=True,
            )

    def to_dict(self, name: str) -> dict:
        schema: dict = {"name": name, "description": self.description}
        input_schema = self._mcp_tool.inputSchema
        if input_schema:
            schema["parameters"] = {
                "type": "object",
                "properties": input_schema.get("properties", {}),
                "required": input_schema.get("required", []),
            }
        return {"type": "function", "function": schema}


def _policy_from_annotations(annotations: ToolAnnotations | None, trusted: bool) -> ToolPolicy | None:
    if not trusted or annotations is None:
        return None
    if annotations.readOnlyHint is True:
        return ToolPolicy(
            action=ToolAction.READ,
            scope=ToolScope.EXTERNAL,
            requires_approval=False,
            permissions=frozenset({"mcp"}),
        )
    if annotations.destructiveHint is True:
        return ToolPolicy(
            action=ToolAction.WRITE,
            scope=ToolScope.EXTERNAL,
            requires_approval=True,
            permissions=frozenset({"mcp"}),
        )
    return None
