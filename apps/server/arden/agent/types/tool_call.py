from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class FunctionCall:
    name: str
    arguments: str


@dataclass(frozen=True)
class ToolCall:
    id: str
    type: Literal["function"]
    function: FunctionCall
    thought_signature: bytes | None = None


@dataclass(frozen=True)
class ToolArgumentError:
    code: Literal["invalid_tool_arguments"]
    message: str


@dataclass(frozen=True)
class PendingToolCall:
    """Parsed tool call — name and args extracted from the raw ToolCall."""

    tool_call: ToolCall
    name: str
    args: dict
    argument_error: ToolArgumentError | None = None
