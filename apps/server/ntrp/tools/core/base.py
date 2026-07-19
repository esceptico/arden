from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from ntrp.agent import ToolResult
from ntrp.tool_call_metadata import DISPLAY_TITLE_ARG, RESERVED_TOOL_ARGUMENTS
from ntrp.tools.core.context import ToolExecution
from ntrp.tools.core.types import ApprovalInfo, ToolPolicy


def _inline_refs(schema: dict) -> dict:
    """Resolve $ref pointers by inlining definitions from $defs."""
    defs = schema.get("$defs", {})
    if not defs:
        return schema

    def _resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                ref_path = node["$ref"]
                ref_name = ref_path.rsplit("/", 1)[-1]
                if ref_name in defs:
                    return _resolve(defs[ref_name])
                return node
            return {k: _resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [_resolve(item) for item in node]
        return node

    return _resolve(schema)


__all__ = ["Tool", "ToolResult", "ApprovalInfo", "TITLE_ARG", "RESERVED_ARG_KEYS"]

# The model emits a short UI action title as a namespaced pseudo-arg on every tool call
# (the letta / Claude-Code "inline field" pattern — free, same completion). It
# is stripped from the args before execute() so tools never see it; it only
# feeds the desktop trace's per-step label.
TITLE_ARG = DISPLAY_TITLE_ARG
RESERVED_ARG_KEYS = RESERVED_TOOL_ARGUMENTS
_TITLE_PROP = {
    "type": "string",
    "description": (
        "Short UI action title — a 3-6 word present-continuous phrase naming what "
        'this call does for the user (e.g. "Searching email for the invoice", '
        '"Reading the design doc", "Checking your calendar"). A display label only, '
        "not part of the tool's work; optional."
    ),
}


class Tool(ABC):
    display_name: str | None = None
    description: str
    policy: ToolPolicy
    input_model: type[BaseModel] | None = None
    # Semantic kind used by the UI to pick a rendering surface. "agent"
    # marks tools that internally spawn a sub-agent (research, etc.) so
    # the chat can render them as agent cards instead of plain rows.
    kind: str = "tool"

    async def approval_info(self, execution: ToolExecution, **kwargs: Any) -> ApprovalInfo | None:
        return None

    @abstractmethod
    async def execute(self, execution: ToolExecution, **kwargs: Any) -> ToolResult: ...

    def to_dict(self, name: str) -> dict:
        properties: dict = {}
        required: list = []
        if self.input_model is not None:
            json_schema = _inline_refs(self.input_model.model_json_schema())
            properties = dict(json_schema.get("properties", {}))
            required = list(json_schema.get("required", []))
        collisions = RESERVED_ARG_KEYS.intersection(properties)
        if collisions:
            names = ", ".join(sorted(collisions))
            raise ValueError(f"Tool {name!r} schema uses reserved tool argument(s): {names}")
        # Inject the optional action-title hint first so it streams early. It is
        # stripped before execute() (see registry.execute), so the title never
        # reaches the tool — it only labels the call in the UI.
        properties = {TITLE_ARG: _TITLE_PROP, **properties}
        schema: dict = {
            "name": name,
            "description": self.description,
            "parameters": {"type": "object", "properties": properties, "required": required},
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
            "description": self.description,
            "kind": self.kind,
            "policy": policy,
        }
