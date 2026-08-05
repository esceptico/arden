"""Strict server-to-device executor command contract."""

import json
from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

COMMAND_ENVELOPE_VERSION = 1
_MAX_COMMAND_BYTES = 2 * 1024 * 1024
_MAX_ARGUMENT_BYTES = 1 * 1024 * 1024
_MAX_JSON_DEPTH = 16
_MAX_JSON_ITEMS = 50_000
_MAX_CONTEXT_OBSERVATIONS = 10_000
_MAX_TEXT_CHARS = 4_096


class ExecutorCommandPayloadError(ValueError):
    """A stored or outbound executor command violated the wire contract."""


class _CommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


def _exact_text(value: str, *, field_name: str, max_length: int = _MAX_TEXT_CHARS) -> str:
    if not value or value != value.strip() or len(value) > max_length or any(char in value for char in "\r\n"):
        raise ValueError(f"{field_name} must be exact single-line text")
    return value


class ExecutorResourceObservation(_CommandModel):
    version: str | None = Field(default=None, max_length=1_024)
    content_read: bool

    @field_validator("version")
    @classmethod
    def exact_version(cls, value: str | None) -> str | None:
        if value is not None:
            return _exact_text(value, field_name="resource observation version", max_length=1_024)
        return value


class ExecutorFileDiscoveryState(_CommandModel):
    observed_paths: dict[str, Literal["file", "directory", "other"]] = Field(max_length=_MAX_CONTEXT_OBSERVATIONS)
    miss_counts: dict[str, int] = Field(max_length=_MAX_CONTEXT_OBSERVATIONS)

    @field_validator("observed_paths", "miss_counts")
    @classmethod
    def exact_paths(cls, values: dict[str, object]) -> dict[str, object]:
        for path in values:
            _exact_text(path, field_name="file-discovery path")
        return values

    @field_validator("miss_counts")
    @classmethod
    def positive_miss_counts(cls, values: dict[str, int]) -> dict[str, int]:
        if any(count < 1 for count in values.values()):
            raise ValueError("file-discovery miss counts must be positive")
        return values


class ExecutorToolContext(_CommandModel):
    """Provider-neutral state the desktop tool handlers may consume."""

    default_cwd: str | None = Field(default=None, max_length=_MAX_TEXT_CHARS)
    offload_dir: str = Field(min_length=1, max_length=_MAX_TEXT_CHARS)
    offload_threshold: int = Field(ge=1, le=16 * 1024 * 1024)
    resource_observations: dict[str, ExecutorResourceObservation] = Field(max_length=_MAX_CONTEXT_OBSERVATIONS)
    file_discovery: ExecutorFileDiscoveryState

    @field_validator("default_cwd", "offload_dir")
    @classmethod
    def exact_paths(cls, value: str | None) -> str | None:
        if value is not None:
            return _exact_text(value, field_name="executor context path")
        return value

    @field_validator("resource_observations")
    @classmethod
    def exact_resource_ids(cls, values: dict[str, ExecutorResourceObservation]) -> dict[str, ExecutorResourceObservation]:
        for resource_id in values:
            _exact_text(resource_id, field_name="resource observation id")
        return values


class _BaseExecutorCommand(_CommandModel):
    version: Literal[COMMAND_ENVELOPE_VERSION]
    invocation_id: str = Field(min_length=1, max_length=512)

    @field_validator("invocation_id")
    @classmethod
    def exact_invocation_id(cls, value: str) -> str:
        return _exact_text(value, field_name="invocation id", max_length=512)


class ExecuteToolCommand(_BaseExecutorCommand):
    type: Literal["execute_tool"]
    tool_call_id: str = Field(min_length=1, max_length=512)
    tool_name: str = Field(min_length=1, max_length=512)
    arguments: dict[str, JsonValue] = Field(max_length=10_000)
    context: ExecutorToolContext
    run_id: str = Field(min_length=1, max_length=512)
    session_id: str = Field(min_length=1, max_length=512)
    deadline_at: str | None

    @field_validator("tool_call_id", "tool_name", "run_id", "session_id")
    @classmethod
    def exact_identifiers(cls, value: str) -> str:
        return _exact_text(value, field_name="executor command identifier", max_length=512)

    @field_validator("deadline_at")
    @classmethod
    def exact_deadline(cls, value: str | None) -> str | None:
        if value is None:
            return None
        _exact_text(value, field_name="executor command deadline", max_length=128)
        try:
            datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("executor command deadline must be ISO-8601") from exc
        return value

    @field_validator("arguments")
    @classmethod
    def exact_argument_keys(cls, values: dict[str, JsonValue]) -> dict[str, JsonValue]:
        for key in values:
            _exact_text(key, field_name="tool argument key", max_length=512)
        return values

    @model_validator(mode="after")
    def bounded_arguments_and_command(self):
        argument_bytes = len(_canonical_json(self.arguments).encode("utf-8"))
        if argument_bytes > _MAX_ARGUMENT_BYTES:
            raise ValueError("executor tool arguments exceed the byte limit")
        _validate_json_shape(self.arguments, label="executor tool arguments")
        if len(self.serialized().encode("utf-8")) > _MAX_COMMAND_BYTES:
            raise ValueError("executor command exceeds the byte limit")
        return self

    def serialized(self) -> str:
        return _canonical_json(self.model_dump(mode="json"))


class CancelToolCommand(_BaseExecutorCommand):
    type: Literal["cancel_tool"]


ExecutorCommandEnvelope = Annotated[ExecuteToolCommand | CancelToolCommand, Field(discriminator="type")]
_COMMAND_ADAPTER = TypeAdapter(ExecutorCommandEnvelope)


def parse_executor_command(value: object) -> ExecuteToolCommand | CancelToolCommand:
    try:
        return _COMMAND_ADAPTER.validate_python(value)
    except ValidationError as exc:
        raise ExecutorCommandPayloadError("executor command payload is invalid") from exc


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _validate_json_shape(value: JsonValue, *, label: str) -> None:
    stack: list[tuple[JsonValue, int]] = [(value, 1)]
    item_count = 0
    while stack:
        item, depth = stack.pop()
        if depth > _MAX_JSON_DEPTH:
            raise ValueError(f"{label} exceed the nesting limit")
        if isinstance(item, dict):
            item_count += len(item)
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            item_count += len(item)
            stack.extend((child, depth + 1) for child in item)
        if item_count > _MAX_JSON_ITEMS:
            raise ValueError(f"{label} exceed the item limit")
