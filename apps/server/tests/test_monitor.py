from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from arden.events.triggers import EventApproaching
from arden.integrations.calendar.client import MultiCalendarSource
from arden.monitor.calendar import CalendarMonitor
from arden.monitor.service import Monitor
from arden.search.types import RawItem


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


def test_calendar_monitor_distinguishes_same_event_id_across_accounts():
    start = datetime.now(UTC) + timedelta(minutes=1)

    def account_source(account: str):
        item = RawItem(
            source="calendar",
            source_id="same-id",
            title="Review",
            content="",
            created_at=start,
            updated_at=start,
            metadata={"start": start.isoformat()},
        )
        return SimpleNamespace(
            get_email_address=lambda: account,
            get_upcoming=lambda days, limit: [item][:limit],
        )

    source = object.__new__(MultiCalendarSource)
    source.sources = [account_source("first@example.test"), account_source("second@example.test")]

    events = CalendarMonitor(source=source, state_store=object())._poll()

    assert [event.event_id for event in events] == [
        "first@example.test:same-id",
        "second@example.test:same-id",
    ]
