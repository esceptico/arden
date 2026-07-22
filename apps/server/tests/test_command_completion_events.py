import json
from datetime import UTC, datetime

from ntrp.context.models import SessionState
from ntrp.events.sse import CommandCompletedEvent, event_from_payload
from ntrp.server.state import RunState
from ntrp.services.chat import _command_completion_event


def test_command_completed_wire_shape():
    event = CommandCompletedEvent(
        run_id="run-1",
        outcome={"status": "completed", "summary": "Done"},
    )

    payload = json.loads(event.to_sse()["data"])

    assert payload["type"] == "command_completed"
    assert payload["run_id"] == "run-1"
    assert payload["outcome"]["status"] == "completed"
    replayed = event_from_payload(payload)
    assert isinstance(replayed, CommandCompletedEvent)
    assert replayed.outcome == event.outcome


def test_command_completion_event_only_for_command_sessions():
    run = RunState(run_id="run-1", session_id="session-1")
    run.structured_output = {"status": "completed", "summary": "Done"}
    ordinary = SessionState(session_id="session-1", started_at=datetime.now(UTC))
    command = SessionState(
        session_id="session-1",
        started_at=datetime.now(UTC),
        session_type="agent",
        agent_type="command_sidecar",
    )

    assert _command_completion_event(ordinary, run) is None
    event = _command_completion_event(command, run)
    assert event is not None
    assert event.outcome == {"status": "completed", "summary": "Done", "choices": []}


def test_command_completion_event_fails_closed_on_invalid_output():
    run = RunState(run_id="run-1", session_id="session-1")
    run.structured_output = {"status": "completed", "summary": "Done", "destination": {"kind": "url"}}
    command = SessionState(
        session_id="session-1",
        started_at=datetime.now(UTC),
        session_type="agent",
        agent_type="command_sidecar",
    )

    event = _command_completion_event(command, run)

    assert event is not None
    assert event.outcome == {
        "status": "failed",
        "summary": "The command did not return a valid result.",
        "choices": [],
    }
