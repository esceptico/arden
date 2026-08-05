from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from arden.agent import ToolResult
from arden.tools.core.context import ToolExecution
from arden.tools.core.schema import tool_parameters
from arden.tools.core.types import ApprovalInfo, ApprovalWaived, ToolPolicy

__all__ = ["Tool", "ToolResult", "ApprovalInfo"]


class Tool(ABC):
    display_name: str | None = None
    display_description: str | None = None
    description: str
    policy: ToolPolicy
    input_model: type[BaseModel] | None = None
    # Semantic kind used by the UI to pick a rendering surface. "agent"
    # marks tools that internally spawn a sub-agent (research, etc.) so
    # the chat can render them as agent cards instead of plain rows.
    kind: str = "tool"
    # A successful call is itself the run's final answer. Failed calls remain
    # in the loop so the model can recover.
    ends_turn: bool = False

    async def approval_info(self, execution: ToolExecution, **kwargs: Any) -> ApprovalInfo | ApprovalWaived | None:
        return None

    async def preflight(self, execution: ToolExecution, **kwargs: Any) -> ToolResult | None:
        """Return a failure to stop before approval, or None to continue."""
        return None

    @abstractmethod
    async def execute(self, execution: ToolExecution, **kwargs: Any) -> ToolResult: ...

    def to_dict(self, name: str) -> dict:
        input_schema = self.input_model.model_json_schema() if self.input_model is not None else {"type": "object"}
        schema: dict = {
            "name": name,
            "description": self.description,
            "parameters": tool_parameters(input_schema, tool_name=name),
        }
        return {
            "type": "function",
            "function": schema,
        }

    def get_metadata(self, name: str) -> dict:
        policy = self.policy.model_dump(mode="json")
        policy["permissions"] = sorted(policy["permissions"])
        return {
            "name": name,
            "display_name": self.display_name or name.replace("_", " ").title(),
            "description": self.display_description or self.description,
            "kind": self.kind,
            "policy": policy,
        }
