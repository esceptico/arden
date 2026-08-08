import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator, model_validator

from arden.agent import ToolOutcomeStatus, ToolResult
from arden.agent.types.tools import ToolEffect, ToolOutcome, ToolSourceRef
from arden.core.raw_tool_results import RAW_TOOL_RESULTS_BASE, read_raw_tool_result
from arden.execution.models import InvocationRecord, InvocationStatus

_STATUS_TO_OUTCOME = {
    InvocationStatus.FAILED: ToolOutcomeStatus.FAILED,
    InvocationStatus.CANCELLED: ToolOutcomeStatus.UNCERTAIN,
    InvocationStatus.UNCERTAIN: ToolOutcomeStatus.UNCERTAIN,
}
_MAX_DEVICE_CONTENT_CHARS = 1_100_000
_MAX_DEVICE_RESULT_BYTES = 2 * 1024 * 1024
_MAX_DEVICE_DATA_DEPTH = 16
_MAX_DEVICE_DATA_ITEMS = 50_000


class InvocationResultPayloadError(ValueError):
    """A device result violated the executor wire contract."""


class _DeviceResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class DeviceSourceRef(_DeviceResultModel):
    provider: str
    kind: str
    ref: str
    title: str
    url: str | None = None

    @model_validator(mode="after")
    def valid_source_ref(self):
        self.to_domain()
        return self

    def to_domain(self) -> ToolSourceRef:
        return ToolSourceRef(
            provider=self.provider,
            kind=self.kind,
            ref=self.ref,
            title=self.title,
            url=self.url,
        )


class DeviceEffect(_DeviceResultModel):
    operation: str = Field(min_length=1, max_length=128)
    target: str = Field(min_length=1, max_length=4_096)

    @field_validator("operation", "target")
    @classmethod
    def exact_text(cls, value: str) -> str:
        if value != value.strip() or any(character in value for character in "\r\n"):
            raise ValueError("device effect fields must be exact single-line text")
        return value

    def to_domain(self) -> ToolEffect:
        return ToolEffect(operation=self.operation, target=self.target)


class DeviceResourceObservation(_DeviceResultModel):
    id: str = Field(min_length=1, max_length=4_096)
    version: str | None = Field(default=None, max_length=1_024)
    content_read: bool

    @field_validator("id", "version")
    @classmethod
    def exact_text(cls, value: str | None) -> str | None:
        if value is not None and (
            not value.strip()
            or value != value.strip()
            or any(character in value for character in "\r\n")
        ):
            raise ValueError("device observation fields must be exact single-line text")
        return value


class DeviceObservedPath(_DeviceResultModel):
    path: str = Field(min_length=1, max_length=4_096)
    kind: Literal["file", "directory", "other"]

    @field_validator("path")
    @classmethod
    def exact_path(cls, value: str) -> str:
        if value != value.strip() or any(character in value for character in "\r\n"):
            raise ValueError("observed paths must be exact single-line text")
        return value


class DeviceFileDiscovery(_DeviceResultModel):
    version: Literal[1]
    observed: list[DeviceObservedPath] = Field(default_factory=list, max_length=10_000)
    discovered_roots: list[str] = Field(default_factory=list, max_length=10_000)
    missed_roots: list[str] = Field(default_factory=list, max_length=10_000)

    @field_validator("discovered_roots", "missed_roots")
    @classmethod
    def exact_roots(cls, values: list[str]) -> list[str]:
        if any(
            not value
            or value != value.strip()
            or len(value) > 4_096
            or any(character in value for character in "\r\n")
            for value in values
        ):
            raise ValueError("file-discovery roots must be exact single-line paths")
        return values


class DeviceResultEnvelope(_DeviceResultModel):
    """Provider-neutral result produced by the desktop executor."""

    content: str = Field(max_length=_MAX_DEVICE_CONTENT_CHARS)
    preview: str = Field(min_length=1, max_length=2_000)
    data: dict[str, JsonValue] | None = None
    source_refs: list[DeviceSourceRef] = Field(default_factory=list, max_length=50)
    effect: DeviceEffect | None = None
    observations: list[DeviceResourceObservation] = Field(default_factory=list, max_length=10_000)
    file_discovery: DeviceFileDiscovery | None = None

    @model_validator(mode="after")
    def unique_source_refs(self):
        identities = [(ref.provider, ref.ref) for ref in self.source_refs]
        if len(identities) != len(set(identities)):
            raise ValueError("device result source_refs must be unique")
        if self.data is not None:
            stack: list[tuple[JsonValue, int]] = [(self.data, 1)]
            item_count = 0
            while stack:
                value, depth = stack.pop()
                if depth > _MAX_DEVICE_DATA_DEPTH:
                    raise ValueError("device result data exceeds the nesting limit")
                if isinstance(value, dict):
                    item_count += len(value)
                    stack.extend((item, depth + 1) for item in value.values())
                elif isinstance(value, list):
                    item_count += len(value)
                    stack.extend((item, depth + 1) for item in value)
                if item_count > _MAX_DEVICE_DATA_ITEMS:
                    raise ValueError("device result data exceeds the item limit")
        if len(self.serialized().encode("utf-8")) > _MAX_DEVICE_RESULT_BYTES:
            raise ValueError("device result exceeds the byte limit")
        return self

    def serialized(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    def to_tool_result(self, record: InvocationRecord) -> ToolResult:
        if record.status == InvocationStatus.SUCCEEDED:
            return ToolResult(
                content=self.content,
                preview=self.preview,
                data=self.data,
                source_refs=tuple(ref.to_domain() for ref in self.source_refs),
                outcome=ToolOutcome(
                    status=ToolOutcomeStatus.SUCCEEDED,
                    effect=self.effect.to_domain() if self.effect is not None else None,
                ),
            )

        return ToolResult.failure(
            code=record.error_code or "tool_error",
            message=self.content,
            preview=self.preview,
            status=_STATUS_TO_OUTCOME[record.status],
            retryable=record.status == InvocationStatus.FAILED,
            recovery_action=(
                "Verify the device-side state before retrying."
                if record.status != InvocationStatus.FAILED
                else "Retry once the device executor is available."
            ),
        )


def _payload_text(record: InvocationRecord) -> str:
    if record.result_payload is not None:
        return record.result_payload
    if record.result_sha256 is None:
        raise InvocationResultPayloadError("terminal device invocation has no result payload")
    blob_path = RAW_TOOL_RESULTS_BASE / record.result_sha256[:2] / f"{record.result_sha256}.txt.gz"
    try:
        return read_raw_tool_result(str(blob_path))
    except OSError as exc:
        raise InvocationResultPayloadError("device result payload is unreadable") from exc


def result_payload(record: InvocationRecord) -> DeviceResultEnvelope:
    try:
        return DeviceResultEnvelope.model_validate_json(_payload_text(record))
    except ValidationError as exc:
        raise InvocationResultPayloadError("device result payload is invalid") from exc


def tool_result_from_payload(record: InvocationRecord, payload: DeviceResultEnvelope) -> ToolResult:
    return payload.to_tool_result(record)


def validate_device_result(
    status: InvocationStatus,
    result: DeviceResultEnvelope,
    error_code: str | None,
) -> None:
    if status not in {
        InvocationStatus.SUCCEEDED,
        InvocationStatus.FAILED,
        InvocationStatus.CANCELLED,
        InvocationStatus.UNCERTAIN,
    }:
        raise ValueError("executor results require a terminal status")
    if status == InvocationStatus.SUCCEEDED:
        if error_code is not None:
            raise ValueError("successful executor results must not include error_code")
    elif not error_code or error_code != error_code.strip() or len(error_code) > 128:
        raise ValueError("non-successful executor results require an exact error_code")
    if status != InvocationStatus.SUCCEEDED and result.effect is not None:
        raise ValueError("non-successful executor results must not include an effect")
