from typing import Any, Protocol

from mcp_types import CallToolResult, ToolAnnotations
from mcp_types import Tool as McpTool

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
        selected_policy = policy or _policy_from_annotations(mcp_tool.annotations, trust_annotations) or self.policy
        self.policy = _complete_mutation_risk(selected_policy)

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
        action = ToolAction.READ
        requires_approval = False
    elif annotations.destructive_hint is True:
        action = ToolAction.WRITE
        requires_approval = True
    else:
        action = ToolAction.EXECUTE
        requires_approval = True
    return ToolPolicy(
        action=action,
        scope=ToolScope.EXTERNAL,
        requires_approval=requires_approval,
        permissions=frozenset({"mcp"}),
        destructive=(
            annotations.destructive_hint
            if annotations.destructive_hint is not None or action is ToolAction.READ
            else True
        ),
        open_world=annotations.open_world_hint if annotations.open_world_hint is not None else True,
        idempotent=(
            annotations.idempotent_hint
            if annotations.idempotent_hint is not None or action is ToolAction.READ
            else False
        ),
    )


def _complete_mutation_risk(policy: ToolPolicy) -> ToolPolicy:
    if policy.action is ToolAction.READ:
        return policy
    updates = {
        "destructive": True if policy.destructive is None else policy.destructive,
        "open_world": True if policy.open_world is None else policy.open_world,
        "idempotent": False if policy.idempotent is None else policy.idempotent,
    }
    return policy.model_copy(update=updates)
