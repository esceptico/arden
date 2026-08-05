from typing import Any, Protocol

from mcp_types import CallToolResult, ToolAnnotations
from mcp_types import Tool as McpTool

from arden.mcp.models import validate_mcp_mutation_policy
from arden.mcp.results import call_tool_result_to_tool_result, mcp_exception_result
from arden.tools.core.base import Tool, ToolResult
from arden.tools.core.context import ToolExecution
from arden.tools.core.schema import tool_parameters
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope


class MCPToolSession(Protocol):
    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> CallToolResult: ...


class MCPTool(Tool):
    policy = ToolPolicy(
        action=ToolAction.EXECUTE,
        scope=ToolScope.EXTERNAL,
        requires_approval=True,
        permissions=frozenset({"mcp"}),
        destructive=True,
        open_world=True,
        idempotent=False,
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
        if policy is not None:
            selected_policy = policy
            source = f"MCP tool {server_name!r}/{mcp_tool.name!r} policy"
        else:
            annotation_policy = _policy_from_annotations(mcp_tool.annotations, trust_annotations)
            selected_policy = annotation_policy or self.policy
            source = f"MCP tool {server_name!r}/{mcp_tool.name!r} trusted annotations"
        self.policy = validate_mcp_mutation_policy(selected_policy, source=source)

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
        except Exception as error:
            return mcp_exception_result(error, provider=self._server_name, tool_name=self._mcp_tool.name)

    def to_dict(self, name: str) -> dict:
        input_schema = self._mcp_tool.input_schema or {"type": "object"}
        schema: dict = {
            "name": name,
            "description": self.description,
            "parameters": tool_parameters(input_schema, tool_name=name),
        }
        return {"type": "function", "function": schema}


def _policy_from_annotations(annotations: ToolAnnotations | None, trusted: bool) -> ToolPolicy | None:
    if not trusted or annotations is None:
        return None
    if annotations.read_only_hint is True and annotations.destructive_hint is True:
        raise ValueError("Trusted MCP annotations cannot mark a tool as both read-only and destructive")
    if annotations.read_only_hint is True:
        return ToolPolicy(
            action=ToolAction.READ,
            scope=ToolScope.EXTERNAL,
            requires_approval=False,
            permissions=frozenset({"mcp"}),
            destructive=False if annotations.destructive_hint is None else annotations.destructive_hint,
            open_world=True if annotations.open_world_hint is None else annotations.open_world_hint,
            idempotent=True if annotations.idempotent_hint is None else annotations.idempotent_hint,
        )

    if annotations.read_only_hint is not False:
        raise ValueError("Trusted non-read MCP annotations must explicitly set readOnlyHint=false")

    hints = {
        "destructiveHint": annotations.destructive_hint,
        "openWorldHint": annotations.open_world_hint,
        "idempotentHint": annotations.idempotent_hint,
    }
    missing = [name for name, value in hints.items() if value is None]
    if missing:
        raise ValueError(f"Trusted non-read MCP annotations must explicitly set {', '.join(missing)}")

    return ToolPolicy(
        action=ToolAction.WRITE if annotations.destructive_hint else ToolAction.EXECUTE,
        scope=ToolScope.EXTERNAL,
        requires_approval=True,
        permissions=frozenset({"mcp"}),
        destructive=annotations.destructive_hint,
        open_world=annotations.open_world_hint,
        idempotent=annotations.idempotent_hint,
    )
