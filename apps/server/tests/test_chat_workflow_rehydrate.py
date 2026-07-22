from arden.events.sse import (
    TaskFinishedEvent,
    TaskStartedEvent,
    ThinkingEvent,
    TokenUsageEvent,
    WorkflowFinishedEvent,
    WorkflowStartedEvent,
)
from arden.server.bus import StreamRecord
from arden.server.routers.chat import reconstruct_workflow_events


def _rec(seq: int, event) -> StreamRecord:
    return StreamRecord(seq=seq, session_id="sess-1", event=event)


def test_reconstruct_keeps_workflow_domain_events_and_drops_the_rest():
    records = [
        _rec(1, ThinkingEvent(status="ignore me")),  # unrelated → dropped
        _rec(2, WorkflowStartedEvent(session_id="sess-1", run_id="r1", workflow_id="wf1", name="WF")),
        _rec(3, TaskStartedEvent(session_id="sess-1", run_id="r1", task_id="t1", workflow_id="wf1", phase="A")),
        _rec(4, TaskStartedEvent(session_id="sess-1", run_id="r1", task_id="bg")),  # no workflow_id → dropped
        _rec(5, TokenUsageEvent(workflow_id="wf1", child_run_id="t1")),
        _rec(6, TokenUsageEvent()),  # run-level usage, no workflow_id → dropped
        _rec(7, TaskFinishedEvent(session_id="sess-1", task_id="t1", workflow_id="wf1", status="completed")),
        _rec(8, WorkflowFinishedEvent(session_id="sess-1", workflow_id="wf1", status="completed")),
    ]

    events = reconstruct_workflow_events(records, "sess-1")
    types = [(e["type"], e["seq"]) for e in events]

    assert types == [
        ("workflow_started", 2),
        ("task_started", 3),
        ("token_usage", 5),
        ("task_finished", 7),
        ("workflow_finished", 8),
    ]
    # Payloads carry the fields the client routing needs + a stamped session_id.
    started = next(e for e in events if e["type"] == "workflow_started")
    assert started["workflow_id"] == "wf1"
    assert started["session_id"] == "sess-1"


def test_reconstruct_stamps_session_id_when_event_omits_it():
    # A workflow event whose payload has an empty session_id falls back to the
    # path session_id so the client can route it.
    records = [_rec(1, WorkflowStartedEvent(run_id="r1", workflow_id="wf9"))]
    events = reconstruct_workflow_events(records, "sess-xyz")
    assert events[0]["session_id"] == "sess-xyz"


def test_reconstruct_drops_events_beyond_checkpoint():
    # Events past the transcript checkpoint belong to an in-flight workflow and
    # arrive via the live SSE tail; including them here would double-apply (and
    # double-count token_usage, which accumulates).
    records = [
        _rec(2, WorkflowStartedEvent(session_id="sess-1", run_id="r1", workflow_id="wf1")),
        _rec(5, TaskStartedEvent(session_id="sess-1", task_id="t1", workflow_id="wf1")),
        _rec(9, TaskFinishedEvent(session_id="sess-1", task_id="t1", workflow_id="wf1", status="completed")),
    ]
    events = reconstruct_workflow_events(records, "sess-1", max_seq=5)
    assert [e["seq"] for e in events] == [2, 5]


def test_reconstruct_empty_for_no_records():
    assert reconstruct_workflow_events([], "sess-1") == []
