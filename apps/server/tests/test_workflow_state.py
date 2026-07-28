import json

from arden.events.sse import (
    ApprovalNeededEvent,
    BackgroundTaskEvent,
    EventType,
    RunFinishedEvent,
    event_from_payload,
    state_for_event,
)
from arden.workflow.models import WorkflowState, state_for_event_type
from arden.workflow.store import WorkflowStateStore


def test_event_types_map_to_normalized_workflow_states():
    assert state_for_event_type(EventType.RUN_STARTED) == WorkflowState.RUNNING
    assert state_for_event_type(EventType.APPROVAL_NEEDED) == WorkflowState.WAITING_FOR_APPROVAL
    assert state_for_event_type(EventType.INPUT_NEEDED) == WorkflowState.WAITING_FOR_INPUT
    assert state_for_event_type(EventType.RUN_FINISHED) == WorkflowState.COMPLETED
    assert state_for_event_type(EventType.RUN_ERROR) == WorkflowState.FAILED
    assert state_for_event_type(EventType.RUN_CANCELLED) == WorkflowState.CANCELLED


def test_sse_payload_includes_additive_workflow_state():
    payload = json.loads(ApprovalNeededEvent(tool_id="tool-1", name="write_file").to_sse()["data"])

    assert payload["type"] == "approval_needed"
    assert payload["workflow_state"] == "waiting_for_approval"


def test_workflow_state_uses_event_payload_not_only_type():
    assert state_for_event(BackgroundTaskEvent(status="started")) == WorkflowState.WAITING_FOR_SUBAGENT
    assert state_for_event(BackgroundTaskEvent(status="completed")) == WorkflowState.COMPLETED
    assert state_for_event(BackgroundTaskEvent(status="failed")) == WorkflowState.FAILED


def test_event_from_payload_ignores_workflow_state_for_compatibility():
    event = event_from_payload(
        {
            "type": "RUN_FINISHED",
            "run_id": "run-1",
            "workflow_state": "completed",
            "timestamp": 1,
        }
    )

    assert isinstance(event, RunFinishedEvent)
    assert event.run_id == "run-1"


def test_workflow_state_store_persists_latest_state(tmp_path):
    path = tmp_path / "workflow-states.json"
    store = WorkflowStateStore(path)
    store.set_state("sess-1", "run-1", WorkflowState.WAITING_FOR_INPUT, reason="input_needed")

    reloaded = WorkflowStateStore(path)

    assert reloaded.get_state("sess-1", "run-1").state == WorkflowState.WAITING_FOR_INPUT
    assert reloaded.get_state("sess-1", "run-1").reason == "input_needed"
