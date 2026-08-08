import base64
import binascii
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from pydantic import ValidationError

from arden.agent.types.llm import Role
from arden.agent.types.tool_call import FunctionCall, ToolCall
from arden.agent.types.tools import ToolOutcome
from arden.core.content import (
    AudioContent,
    ContentBlock,
    ContentBlockError,
    ContextContent,
    ImageContent,
    TextContent,
    blocks_to_text,
    parse_block,
)

type HistoryContent = str | tuple[ContentBlock, ...]
type HistoryMessage = SystemHistoryMessage | UserHistoryMessage | AssistantHistoryMessage | ToolHistoryMessage

_CONTENT_BLOCK_TYPES = (TextContent, ImageContent, AudioContent, ContextContent)


class HistoryDecodeError(ValueError):
    """A persisted model-history entry does not match the canonical contract."""


class HistoryProjectionError(ValueError):
    """Canonical history cannot be represented by the selected provider."""


class _HistoryFieldError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ReplayProvider(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI_RESPONSES = "openai_responses"


@dataclass(frozen=True, slots=True)
class OpaqueReplay:
    """Exact provider continuation state with explicit provider affinity."""

    provider: ReplayProvider
    _items: object
    _message_index: int

    def materialize(self) -> list[dict[str, Any]]:
        if (
            not isinstance(self._items, Sequence)
            or isinstance(self._items, (str, bytes))
            or not all(isinstance(item, Mapping) for item in self._items)
        ):
            raise HistoryDecodeError(
                f"Invalid model-history message at index {self._message_index} (provider.invalid_replay)"
            ) from None
        return deepcopy([dict(item) for item in self._items])


@dataclass(frozen=True, slots=True)
class SystemHistoryMessage:
    content: HistoryContent
    # Stable-prefix boundaries. Adapters without explicit cache controls ignore them.
    cache_breakpoints: frozenset[int] = frozenset()
    role: Literal[Role.SYSTEM] = field(default=Role.SYSTEM, init=False)


@dataclass(frozen=True, slots=True)
class UserHistoryMessage:
    content: HistoryContent
    role: Literal[Role.USER] = field(default=Role.USER, init=False)


@dataclass(frozen=True, slots=True)
class AssistantHistoryMessage:
    content: HistoryContent
    tool_calls: tuple[ToolCall, ...] = ()
    reasoning_encrypted_content: str | None = None
    replays: tuple[OpaqueReplay, ...] = ()
    role: Literal[Role.ASSISTANT] = field(default=Role.ASSISTANT, init=False)

    def replay_for(self, provider: ReplayProvider) -> OpaqueReplay | None:
        return next((replay for replay in self.replays if replay.provider == provider), None)


@dataclass(frozen=True, slots=True)
class ToolHistoryMessage:
    tool_call_id: str
    content: HistoryContent
    outcome: ToolOutcome | None = None
    tool_references: tuple[str, ...] = ()
    role: Literal[Role.TOOL] = field(default=Role.TOOL, init=False)

    @property
    def content_text(self) -> str:
        return history_content_to_text(self.content)


@dataclass(frozen=True, slots=True)
class ModelHistory:
    messages: tuple[HistoryMessage, ...]

    @classmethod
    def from_raw(cls, value: Sequence[Mapping[str, object]]) -> "ModelHistory":
        messages: list[HistoryMessage] = []
        for index, raw in enumerate(value):
            try:
                messages.append(_decode_message(raw, index=index))
            except _HistoryFieldError as exc:
                raise HistoryDecodeError(f"Invalid model-history message at index {index} ({exc.code})") from None
            except (KeyError, TypeError, ValueError, ValidationError):
                raise HistoryDecodeError(f"Invalid model-history message at index {index} (invalid_shape)") from None
        history = cls(tuple(messages))
        history._validate_sequence()
        return history

    def _validate_sequence(self) -> None:
        known_call_ids: set[str] = set()
        for index, message in enumerate(self.messages):
            if isinstance(message, SystemHistoryMessage) and index != 0:
                raise HistoryDecodeError(f"Invalid model-history message at index {index} (system.not_first)") from None
            if isinstance(message, AssistantHistoryMessage):
                known_call_ids.update(tool_call.id for tool_call in message.tool_calls)
            elif isinstance(message, ToolHistoryMessage) and message.tool_call_id not in known_call_ids:
                raise HistoryDecodeError(
                    f"Invalid model-history message at index {index} (tool.orphan_result)"
                ) from None

    def tool_names_by_call_id(self) -> dict[str, str]:
        return {
            tool_call.id: tool_call.function.name
            for message in self.messages
            if isinstance(message, AssistantHistoryMessage)
            for tool_call in message.tool_calls
        }


def history_content_to_text(content: HistoryContent) -> str:
    if isinstance(content, str):
        return content
    for block in content:
        if isinstance(block, (ImageContent, AudioContent)):
            raise unsupported_content_block("text history", block)
    return blocks_to_text(list(content))


def unsupported_content_block(provider: str, block: ContentBlock) -> HistoryProjectionError:
    return HistoryProjectionError(f"{provider} does not support history content block type {block.type!r}")


def _decode_message(raw: Mapping[str, object], *, index: int) -> HistoryMessage:
    try:
        role = Role(str(raw["role"]))
    except (KeyError, ValueError):
        raise _HistoryFieldError("role.invalid") from None
    cache_breakpoints = _decode_cache_breakpoints(raw.get("content")) if role == Role.SYSTEM else frozenset()
    content = _decode_content(raw.get("content"), allow_cache_control=role == Role.SYSTEM)

    match role:
        case Role.SYSTEM:
            return SystemHistoryMessage(
                content=content,
                cache_breakpoints=cache_breakpoints,
            )
        case Role.USER:
            return UserHistoryMessage(content=content)
        case Role.ASSISTANT:
            return AssistantHistoryMessage(
                content=content,
                tool_calls=_decode_tool_calls(raw.get("tool_calls")),
                reasoning_encrypted_content=_optional_string(raw.get("reasoning_encrypted_content")),
                replays=_decode_replays(raw, message_index=index),
            )
        case Role.TOOL:
            tool_call_id = raw.get("tool_call_id")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                raise _HistoryFieldError("tool.tool_call_id_missing")
            return ToolHistoryMessage(
                tool_call_id=tool_call_id,
                content=content,
                outcome=_decode_outcome(raw.get("outcome")),
                tool_references=_decode_tool_references(raw.get("data")),
            )


def _decode_tool_references(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise _HistoryFieldError("tool.data_invalid")
    raw = value.get("tool_references")
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise _HistoryFieldError("tool.references_invalid")
    names = tuple(raw)
    if not names or any(not isinstance(name, str) or not name.strip() for name in names):
        raise _HistoryFieldError("tool.references_invalid")
    if len(set(names)) != len(names):
        raise _HistoryFieldError("tool.references_duplicate")
    return names


def _decode_content(value: object, *, allow_cache_control: bool = False) -> HistoryContent:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if not isinstance(value, Sequence) or isinstance(value, bytes):
        raise _HistoryFieldError("content.invalid_type")

    blocks: list[ContentBlock] = []
    for block in value:
        if isinstance(block, _CONTENT_BLOCK_TYPES):
            blocks.append(block.model_copy(deep=True))
        elif isinstance(block, Mapping):
            if block.get("type") not in {"text", "image", "audio", "context"}:
                raise _HistoryFieldError("content.unsupported_block")
            try:
                block_payload = dict(block)
                if allow_cache_control:
                    block_payload.pop("cache_control", None)
                blocks.append(parse_block(block_payload))
            except (ContentBlockError, ValidationError):
                raise _HistoryFieldError("content.invalid_block") from None
        else:
            raise _HistoryFieldError("content.block_not_object")
    return tuple(blocks)


def _decode_tool_calls(value: object) -> tuple[ToolCall, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise _HistoryFieldError("tool_calls.invalid_type")

    calls: list[ToolCall] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            raise _HistoryFieldError("tool_call.not_object")
        function = raw.get("function")
        if not isinstance(function, Mapping):
            raise _HistoryFieldError("tool_call.function_missing")
        if raw.get("type", "function") != "function":
            raise _HistoryFieldError("tool_call.type_unsupported")
        tool_id = raw.get("id")
        name = function.get("name")
        arguments = function.get("arguments", "{}")
        if not isinstance(tool_id, str) or not tool_id:
            raise _HistoryFieldError("tool_call.id_missing")
        if not isinstance(name, str) or not name:
            raise _HistoryFieldError("tool_call.name_missing")
        if not isinstance(arguments, str):
            raise _HistoryFieldError("tool_call.arguments_not_text")
        calls.append(
            ToolCall(
                id=tool_id,
                type="function",
                function=FunctionCall(name=name, arguments=arguments),
                thought_signature=(_decode_signature(raw["thought_signature"]) if "thought_signature" in raw else None),
            )
        )
    return tuple(calls)


def _decode_signature(value: object) -> bytes | None:
    if not isinstance(value, str) or not value:
        raise _HistoryFieldError("tool_call.thought_signature_invalid")
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise _HistoryFieldError("tool_call.thought_signature_invalid") from None


def _decode_replays(raw: Mapping[str, object], *, message_index: int) -> tuple[OpaqueReplay, ...]:
    fields = (
        (ReplayProvider.ANTHROPIC, "anthropic_content"),
        (ReplayProvider.OPENAI_RESPONSES, "openai_response_items"),
    )
    replays: list[OpaqueReplay] = []
    for provider, field_name in fields:
        if field_name not in raw or raw[field_name] is None:
            continue
        value = raw[field_name]
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and not value:
            continue
        replays.append(OpaqueReplay(provider=provider, _items=value, _message_index=message_index))
    return tuple(replays)


def _decode_cache_breakpoints(value: object) -> frozenset[int]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return frozenset()
    breakpoints: set[int] = set()
    for index, block in enumerate(value):
        if not isinstance(block, Mapping) or "cache_control" not in block:
            continue
        cache_control = block["cache_control"]
        if not isinstance(cache_control, Mapping) or dict(cache_control) != {"type": "ephemeral"}:
            raise _HistoryFieldError("system.cache_control_unsupported")
        breakpoints.add(index)
    return frozenset(breakpoints)


def _decode_outcome(value: object) -> ToolOutcome | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise _HistoryFieldError("tool.outcome_invalid")
    try:
        return ToolOutcome.decode(value)
    except (TypeError, ValueError):
        raise _HistoryFieldError("tool.outcome_invalid") from None


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _HistoryFieldError("assistant.reasoning_state_not_text")
    return value or None
