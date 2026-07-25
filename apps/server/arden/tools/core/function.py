from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict

from arden.agent import ToolResult
from arden.tools.core.base import Tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ApprovalInfo, ToolPolicy

ToolSet = dict[str, Tool]


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


ToolHandler = Callable[[ToolExecution, BaseModel], Awaitable[ToolResult]]
ApprovalHandler = Callable[[ToolExecution, BaseModel], Awaitable[ApprovalInfo | None]]


class _FunctionTool(Tool):
    """Tool adapter for plain Python callables."""

    def __init__(
        self,
        *,
        description: str,
        execute: ToolHandler,
        policy: ToolPolicy,
        input_model: type[BaseModel] = EmptyInput,
        display_name: str | None = None,
        display_description: str | None = None,
        approval: ApprovalHandler | None = None,
        kind: str = "tool",
    ):
        self.display_name = display_name
        self.display_description = display_description
        self.description = description
        self.input_model = input_model
        self.policy = policy
        if policy.requires_approval and approval is None:
            raise ValueError("approval-gated tools require an approval preview callback")
        self._execute = execute
        self._approval = approval
        self.kind = kind

    async def approval_info(self, execution: ToolExecution, **kwargs: Any) -> ApprovalInfo | None:
        if self._approval is None:
            return None
        args = self.input_model(**kwargs)
        return await self._approval(execution, args)

    async def execute(self, execution: ToolExecution, **kwargs: Any) -> ToolResult:
        args = self.input_model(**kwargs)
        result = await self._execute(execution, args)
        if not isinstance(result, ToolResult):
            raise TypeError("function tool handlers must return ToolResult")
        return result


def tool(
    *,
    description: str,
    execute: ToolHandler,
    policy: ToolPolicy,
    input_model: type[BaseModel] = EmptyInput,
    display_name: str | None = None,
    display_description: str | None = None,
    approval: ApprovalHandler | None = None,
    kind: str = "tool",
) -> Tool:
    return _FunctionTool(
        description=description,
        execute=execute,
        policy=policy,
        input_model=input_model,
        display_name=display_name,
        display_description=display_description,
        approval=approval,
        kind=kind,
    )
