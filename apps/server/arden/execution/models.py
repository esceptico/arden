from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class InvocationStatus(StrEnum):
    REQUESTED = "requested"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNCERTAIN = "uncertain"


TERMINAL_STATUSES = frozenset(
    {
        InvocationStatus.SUCCEEDED,
        InvocationStatus.FAILED,
        InvocationStatus.CANCELLED,
        InvocationStatus.UNCERTAIN,
    }
)


class InvocationConflictError(Exception):
    """A terminal outcome conflicts with the one already recorded."""

    def __init__(self, invocation_id: str, recorded_status: InvocationStatus, submitted_status: InvocationStatus):
        self.invocation_id = invocation_id
        self.recorded_status = recorded_status
        self.submitted_status = submitted_status
        super().__init__(
            f"invocation {invocation_id} already completed as {recorded_status}; conflicting {submitted_status} rejected"
        )


@dataclass(frozen=True)
class InvocationRecord:
    """Durable source of truth for one logical tool execution.

    Delivery events are a projection of this record; the record survives
    reconnects, restarts, and duplicate deliveries.
    """

    invocation_id: str
    run_id: str
    session_id: str
    tool_call_id: str
    tool_name: str
    placement: str
    arguments_json: str
    status: InvocationStatus
    attempts: int
    cancel_requested: bool
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    deadline_at: datetime | None = None
    result_payload: str | None = None
    result_sha256: str | None = None
    result_blob_ref: str | None = None
    error_code: str | None = None
