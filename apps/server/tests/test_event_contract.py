import json
import re
from pathlib import Path

from arden.events.sse import (
    EventType,
    NavigationRequestedEvent,
    RunBackgroundedEvent,
    RunCancelledEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    SessionCreatedEvent,
    ThinkingEvent,
)
from arden.server.bus import StreamRecord, stream_record_to_sse_string


def _payload(event):
    record = StreamRecord(session_id="sess-1", seq=1, event=event)
    frame = stream_record_to_sse_string("sess-1", record)
    return json.loads(frame.split("data: ", 1)[1].strip())


def test_terminal_events_identify_run_session_and_sequence():
    events = [
        RunStartedEvent(session_id="sess-1", run_id="run-1"),
        RunFinishedEvent(run_id="run-1"),
        RunCancelledEvent(run_id="run-1"),
        RunBackgroundedEvent(session_id="sess-1", run_id="run-1"),
        RunErrorEvent(run_id="run-1", message="failed"),
    ]

    for event in events:
        payload = _payload(event)
        assert payload["session_id"] == "sess-1"
        assert payload["seq"] == 1
        assert payload["run_id"] == "run-1"


def test_thinking_event_identifies_run_session_and_sequence():
    payload = _payload(ThinkingEvent(session_id="sess-1", run_id="run-1", status="processing..."))

    assert payload["type"] == "thinking"
    assert payload["session_id"] == "sess-1"
    assert payload["seq"] == 1
    assert payload["run_id"] == "run-1"
    assert payload["status"] == "processing..."


def test_stream_record_uses_sse_id_as_cursor():
    record = StreamRecord(session_id="sess-1", seq=44, event=RunFinishedEvent(run_id="run-1"))
    frame = stream_record_to_sse_string("sess-1", record)

    assert frame.startswith("id: 44\n")
    assert "event: RUN_FINISHED\n" in frame


def test_session_created_nests_session_under_bus_session_id():
    # SESSION_CREATED rides the global automation bus, whose key overwrites
    # the top-level `session_id`. The new session's identity must survive
    # nested under `session` so the client can render the row.
    bus_key = "automation:events"
    event = SessionCreatedEvent(session={"session_id": "20260530_120000_001", "name": "scan offers"})
    record = StreamRecord(session_id=bus_key, seq=7, event=event)
    payload = json.loads(stream_record_to_sse_string(bus_key, record).split("data: ", 1)[1].strip())

    assert payload["type"] == "session_created"
    assert payload["session_id"] == "automation:events"  # bus key, not the new session
    assert payload["session"]["session_id"] == "20260530_120000_001"
    assert payload["session"]["name"] == "scan offers"
    assert payload["seq"] == 7


def test_navigation_requested_travels_with_its_origin_session():
    # Same bus-clobber trap as SESSION_CREATED: the requesting session cannot
    # ride in `session_id`, so it travels as `origin_session_id`.
    bus_key = "automation:events"
    event = NavigationRequestedEvent(
        origin_session_id="20260530_120000_001",
        destination={"kind": "area", "area_id": "ops"},
        label="Open the Ops area",
    )
    record = StreamRecord(session_id=bus_key, seq=812, event=event)
    payload = json.loads(stream_record_to_sse_string(bus_key, record).split("data: ", 1)[1].strip())

    assert payload["type"] == "navigation_requested"
    assert payload["session_id"] == "automation:events"
    assert payload["origin_session_id"] == "20260530_120000_001"
    assert payload["destination"] == {"kind": "area", "area_id": "ops"}
    assert payload["label"] == "Open the Ops area"
    assert payload["seq"] == 812


def test_desktop_event_unions_cover_backend_event_types():
    repo_root = Path(__file__).resolve().parents[3]
    desktop_sources = [
        repo_root / "apps/desktop/src/api/events.ts",
        repo_root / "apps/desktop/src/features/automations/hooks/useAutomationEvents.ts",
    ]
    desktop_literals = set()
    for source in desktop_sources:
        desktop_literals.update(re.findall(r'type: "([^"]+)"', source.read_text()))

    backend_literals = {event_type.value for event_type in EventType}
    assert backend_literals <= desktop_literals
