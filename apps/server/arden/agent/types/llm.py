from dataclasses import dataclass
from enum import StrEnum

from arden.agent.types.tool_call import ToolCall
from arden.agent.types.usage import Usage


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class FinishReason(StrEnum):
    STOP = "stop"
    TOOL_CALLS = "tool_calls"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"


@dataclass(frozen=True)
class ProviderToolCall:
    id: str
    name: str
    arguments: str = "{}"
    result: str = ""
    done: bool = True
    provider_item: dict | None = None


@dataclass(frozen=True)
class Message:
    role: Role
    content: str | None
    tool_calls: list[ToolCall] | None
    reasoning_content: str | None
    reasoning_encrypted_content: str | None = None
    anthropic_content: list[dict] | None = None
    provider_tool_calls: list[ProviderToolCall] | None = None
    openai_response_items: list[dict] | None = None


@dataclass(frozen=True)
class Choice:
    message: Message
    finish_reason: FinishReason | None


@dataclass(frozen=True)
class CompletionResponse:
    choices: list[Choice]
    usage: Usage
    model: str


@dataclass(frozen=True)
class ReasoningContentDelta:
    content: str


@dataclass(frozen=True)
class ToolCallStreamDelta:
    """Incremental tool-call data emitted by a streaming LLM provider."""

    index: int
    tool_id: str | None = None
    name: str | None = None
    arguments_delta: str | None = None
    done: bool = False
