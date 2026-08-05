import asyncio
from datetime import UTC, datetime, timedelta

from arden.constants import (
    MONITOR_CALENDAR_DAYS,
    MONITOR_CALENDAR_LIMIT,
    MONITOR_EVENT_APPROACHING_HORIZON_MINUTES,
    MONITOR_POLL_INTERVAL,
)
from arden.events.triggers import EventApproaching, TriggerEvent
from arden.integrations.calendar.client import MultiCalendarSource
from arden.logging import get_logger
from arden.monitor.service import MonitorEventSink
from arden.monitor.store import MonitorStateStore

_logger = get_logger(__name__)


class CalendarMonitor:
    """Polls calendar data and publishes EventApproaching trigger events."""

    def __init__(self, source: MultiCalendarSource, state_store: MonitorStateStore):
        self._source = source
        self._state_store = state_store
        self._emit_event: MonitorEventSink | None = None
        self._task: asyncio.Task | None = None

    def start(self, emit_event: MonitorEventSink) -> None:
        if self._task is not None and not self._task.done():
            return
        self._emit_event = emit_event
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def _run(self) -> None:
        await self._loop()

    async def _loop(self) -> None:
        while True:
            events = await asyncio.to_thread(self._poll)
            await self._emit_events(events)
            await asyncio.sleep(MONITOR_POLL_INTERVAL)

    async def _emit_events(self, events: list[TriggerEvent]) -> None:
        if not self._emit_event:
            return
        for event in events:
            try:
                await self._emit_event(event)
            except Exception:
                _logger.exception("Failed to emit monitor event")

    def _poll(self) -> list[EventApproaching]:
        now = datetime.now(UTC)
        horizon = now + timedelta(minutes=MONITOR_EVENT_APPROACHING_HORIZON_MINUTES)
        events: list[EventApproaching] = []

        page = self._source.get_upcoming(days=MONITOR_CALENDAR_DAYS, limit=MONITOR_CALENDAR_LIMIT)

        for item in page.events:
            start = item.start.astimezone(UTC)
            if start < now or start > horizon:
                continue

            minutes_until = max(0, int((start - now).total_seconds() / 60))
            title = item.title or "Untitled event"
            event = EventApproaching(
                event_id=item.event_ref,
                summary=title,
                start=start,
                minutes_until=minutes_until,
                location=item.location,
                attendees=item.attendees,
            )
            events.append(event)
            _logger.info("Event approaching: %s in %d min", title, minutes_until)

        return events
