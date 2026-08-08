import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

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


type JsonValue = bool | int | float | str | tuple["JsonValue", ...] | Mapping[str, "JsonValue"] | None


class ProviderToolPayloadError(ValueError):
    """A native provider tool call or its stored envelope is invalid."""


class ProviderResponseError(RuntimeError):
    """A provider response cannot be interpreted as a completed model turn."""


def _freeze_json(value: Any, *, path: str) -> JsonValue:
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProviderToolPayloadError(f"{path} must contain finite JSON numbers")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProviderToolPayloadError(f"{path} keys must be strings")
            frozen[key] = _freeze_json(item, path=f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        return tuple(_freeze_json(item, path=f"{path}[{index}]") for index, item in enumerate(value))
    raise ProviderToolPayloadError(f"{path} contains unsupported value {type(value).__name__}")


def _mutable_json(value: JsonValue) -> Any:
    if isinstance(value, Mapping):
        return {key: _mutable_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_mutable_json(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class ProviderToolCall:
    id: str
    name: str
    arguments: Mapping[str, JsonValue] = field(default_factory=dict)
    result: str = ""
    done: bool = True
    loaded_tool_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ProviderToolPayloadError("provider tool call id must be a non-empty string")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ProviderToolPayloadError("provider tool call name must be a non-empty string")
        if not isinstance(self.result, str):
            raise ProviderToolPayloadError("provider tool call result must be a string")
        if not isinstance(self.done, bool):
            raise ProviderToolPayloadError("provider tool call done must be a boolean")
        frozen_arguments = _freeze_json(self.arguments, path="provider tool call arguments")
        if not isinstance(frozen_arguments, Mapping):
            raise ProviderToolPayloadError("provider tool call arguments must be a JSON object")
        if not isinstance(self.loaded_tool_names, tuple):
            raise ProviderToolPayloadError("loaded tool names must be an immutable tuple")
        names: list[str] = []
        for name in self.loaded_tool_names:
            if not isinstance(name, str) or not name.strip():
                raise ProviderToolPayloadError("loaded tool names must be non-empty strings")
            if name not in names:
                names.append(name)
        object.__setattr__(self, "arguments", frozen_arguments)
        object.__setattr__(self, "loaded_tool_names", tuple(names))

    def arguments_dict(self) -> dict[str, Any]:
        return {key: _mutable_json(value) for key, value in self.arguments.items()}

    def arguments_json(self) -> str:
        return json.dumps(self.arguments_dict(), ensure_ascii=False, separators=(",", ":"))

    def to_history_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "arguments": self.arguments_dict(),
            "result": self.result,
            "done": self.done,
            "loaded_tool_names": list(self.loaded_tool_names),
        }

    @classmethod
    def from_history_dict(cls, value: object) -> "ProviderToolCall":
        if not isinstance(value, Mapping):
            raise ProviderToolPayloadError("stored provider tool call must be an object")
        if not all(isinstance(key, str) for key in value):
            raise ProviderToolPayloadError("stored provider tool call field names must be strings")
        allowed = {"id", "name", "arguments", "result", "done", "loaded_tool_names"}
        unknown = set(value) - allowed
        if unknown:
            raise ProviderToolPayloadError(f"stored provider tool call has unknown fields: {sorted(unknown)}")
        missing = allowed - set(value)
        if missing:
            raise ProviderToolPayloadError(f"stored provider tool call is missing fields: {sorted(missing)}")
        arguments = value["arguments"]
        loaded_tool_names = value["loaded_tool_names"]
        if not isinstance(arguments, Mapping):
            raise ProviderToolPayloadError("stored provider tool call arguments must be an object")
        if not isinstance(loaded_tool_names, list):
            raise ProviderToolPayloadError("stored loaded tool names must be an array")
        return cls(
            id=value["id"],
            name=value["name"],
            arguments=arguments,
            result=value["result"],
            done=value["done"],
            loaded_tool_names=tuple(loaded_tool_names),
        )


def provider_tool_calls_from_history(message: Mapping[str, object]) -> tuple[ProviderToolCall, ...]:
    if "provider_tool_calls" not in message:
        return ()
    values = message["provider_tool_calls"]
    if not isinstance(values, list):
        raise ProviderToolPayloadError("stored provider_tool_calls must be an array")
    return tuple(ProviderToolCall.from_history_dict(value) for value in values)


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
