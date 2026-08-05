from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from arden.agent import Usage
from arden.agent.types.tools import decode_source_refs
from arden.events.internal import RunCompleted, RunFailed

OUTBOX_RUN_COMPLETED = "run.completed"
OUTBOX_RUN_FAILED = "run.failed"
OUTBOX_AUTOMATION_SETTLED = "automation.settled"
OUTBOX_WIKI_PROJECTION_REQUESTED = "wiki.projection.requested"
OUTBOX_AGENT_RUN_REQUESTED = "agent.run.requested"


class OutboxPayloadError(ValueError):
    """A durable outbox row does not match its current event contract."""


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _UsagePayload(_WireModel):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    cache_read_tokens: int = Field(ge=0)
    cache_write_tokens: int = Field(ge=0)


class _RunCompletedPayload(_WireModel):
    run_id: str = Field(min_length=1, max_length=512)
    session_id: str = Field(min_length=1, max_length=512)
    messages: list[dict[str, Any]]
    usage: _UsagePayload
    result: str | None
    source_refs: list[dict[str, object]]
    automation_task_id: str | None = Field(max_length=512)


class _RunFailedPayload(_WireModel):
    run_id: str = Field(min_length=1, max_length=512)
    session_id: str = Field(min_length=1, max_length=512)
    error: str = Field(min_length=1)
    automation_task_id: str | None = Field(max_length=512)


class _AutomationSettledPayload(_WireModel):
    automation_run_id: int = Field(ge=1)
    task_id: str = Field(min_length=1, max_length=512)
    success: bool


class _AgentRunRequestedPayload(_WireModel):
    session_id: str = Field(min_length=1, max_length=512)
    task_id: str = Field(min_length=1, max_length=512)


def _decode[Payload: _WireModel](model: type[Payload], payload: object, event_type: str) -> Payload:
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise OutboxPayloadError(f"invalid {event_type} outbox payload") from error


@dataclass(frozen=True)
class AutomationSettled:
    automation_run_id: int
    task_id: str
    success: bool


@dataclass(frozen=True)
class AgentRunRequested:
    session_id: str
    task_id: str


def run_completed_payload(event: RunCompleted) -> dict:
    return _RunCompletedPayload(
        run_id=event.run_id,
        session_id=event.session_id,
        messages=list(event.messages),
        usage=_UsagePayload(
            prompt_tokens=event.usage.prompt_tokens,
            completion_tokens=event.usage.completion_tokens,
            cache_read_tokens=event.usage.cache_read_tokens,
            cache_write_tokens=event.usage.cache_write_tokens,
        ),
        result=event.result,
        source_refs=[ref.to_dict() for ref in event.source_refs],
        automation_task_id=event.automation_task_id,
    ).model_dump(mode="python")


def run_completed_from_payload(payload: object) -> RunCompleted:
    decoded = _decode(_RunCompletedPayload, payload, OUTBOX_RUN_COMPLETED)
    return RunCompleted(
        run_id=decoded.run_id,
        session_id=decoded.session_id,
        messages=tuple(decoded.messages),
        usage=Usage(
            prompt_tokens=decoded.usage.prompt_tokens,
            completion_tokens=decoded.usage.completion_tokens,
            cache_read_tokens=decoded.usage.cache_read_tokens,
            cache_write_tokens=decoded.usage.cache_write_tokens,
        ),
        result=decoded.result,
        source_refs=decode_source_refs(decoded.source_refs, max_items=None),
        automation_task_id=decoded.automation_task_id,
    )


def run_failed_payload(event: RunFailed) -> dict:
    return _RunFailedPayload(
        run_id=event.run_id,
        session_id=event.session_id,
        error=event.error,
        automation_task_id=event.automation_task_id,
    ).model_dump(mode="python")


def run_failed_from_payload(payload: object) -> RunFailed:
    decoded = _decode(_RunFailedPayload, payload, OUTBOX_RUN_FAILED)
    return RunFailed(
        run_id=decoded.run_id,
        session_id=decoded.session_id,
        error=decoded.error,
        automation_task_id=decoded.automation_task_id,
    )


def automation_settled_payload(event: AutomationSettled) -> dict:
    return _AutomationSettledPayload(
        automation_run_id=event.automation_run_id,
        task_id=event.task_id,
        success=event.success,
    ).model_dump(mode="python")


def automation_settled_from_payload(payload: object) -> AutomationSettled:
    decoded = _decode(_AutomationSettledPayload, payload, OUTBOX_AUTOMATION_SETTLED)
    return AutomationSettled(
        automation_run_id=decoded.automation_run_id,
        task_id=decoded.task_id,
        success=decoded.success,
    )


def agent_run_requested_payload(event: AgentRunRequested) -> dict:
    return _AgentRunRequestedPayload(
        session_id=event.session_id,
        task_id=event.task_id,
    ).model_dump(mode="python")


def agent_run_requested_from_payload(payload: object) -> AgentRunRequested:
    decoded = _decode(_AgentRunRequestedPayload, payload, OUTBOX_AGENT_RUN_REQUESTED)
    return AgentRunRequested(
        session_id=decoded.session_id,
        task_id=decoded.task_id,
    )
