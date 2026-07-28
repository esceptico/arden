from enum import StrEnum

from arden.events.types import EventType


class WorkflowState(StrEnum):
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    WAITING_FOR_INPUT = "waiting_for_input"
    WAITING_FOR_AUTH = "waiting_for_auth"
    WAITING_FOR_SUBAGENT = "waiting_for_subagent"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def state_for_event_type(event_type: EventType | str) -> WorkflowState | None:
    raw = event_type.value if isinstance(event_type, EventType) else event_type
    if raw in {EventType.RUN_STARTED.value, EventType.TASK_STARTED.value, EventType.WORKFLOW_STARTED.value}:
        return WorkflowState.RUNNING
    if raw == EventType.APPROVAL_NEEDED.value:
        return WorkflowState.WAITING_FOR_APPROVAL
    if raw == EventType.INPUT_NEEDED.value:
        return WorkflowState.WAITING_FOR_INPUT
    if raw == EventType.RUN_BACKGROUNDED.value or raw == EventType.BACKGROUND_TASK.value:
        return WorkflowState.WAITING_FOR_SUBAGENT
    if raw in {EventType.RUN_FINISHED.value, EventType.TASK_FINISHED.value, EventType.WORKFLOW_FINISHED.value}:
        return WorkflowState.COMPLETED
    if raw == EventType.RUN_ERROR.value:
        return WorkflowState.FAILED
    if raw == EventType.RUN_CANCELLED.value:
        return WorkflowState.CANCELLED
    return None
