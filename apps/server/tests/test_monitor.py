from datetime import UTC, datetime

import pytest

from arden.events.triggers import EventApproaching
from arden.monitor.calendar import CalendarMonitor
from arden.monitor.service import Monitor


def _event(event_id: str = "event-1") -> EventApproaching:
    return EventApproaching(
        event_id=event_id,
        summary="Standup",
        start=datetime.now(UTC),
        minutes_until=10,
        location=None,
        attendees=(),
    )


class _Provider:
    def __init__(self):
        self.emit_event = None

    def start(self, emit_event):
        self.emit_event = emit_event

    async def stop(self) -> None:
        pass


def test_monitor_passes_direct_event_sink_to_provider():
    async def emit_event(_event):
        pass

    provider = _Provider()
    monitor = Monitor(emit_event)
    monitor.register(provider)

    monitor.start()

    assert provider.emit_event is emit_event


@pytest.mark.asyncio
async def test_calendar_monitor_emits_events_directly():
    emitted = []

    async def emit_event(event):
        emitted.append(event)

    monitor = CalendarMonitor(source=object(), state_store=object())
    monitor.start(emit_event)
    await monitor.stop()

    await monitor._emit_events([_event("event-1"), _event("event-2")])

    assert [event.event_id for event in emitted] == ["event-1", "event-2"]


@pytest.mark.asyncio
async def test_calendar_monitor_continues_after_emit_failure():
    emitted = []

    async def emit_event(event):
        emitted.append(event.event_id)
        if event.event_id == "bad":
            raise RuntimeError("boom")

    monitor = CalendarMonitor(source=object(), state_store=object())
    monitor.start(emit_event)
    await monitor.stop()

    await monitor._emit_events([_event("bad"), _event("good")])

    assert emitted == ["bad", "good"]
