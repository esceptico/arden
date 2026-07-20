from datetime import datetime

from pydantic import BaseModel, Field

from ntrp.agent.types.tools import ToolSourceRef, normalize_source_refs
from ntrp.integrations.base import IntegrationOperationError
from ntrp.integrations.calendar.client import MultiCalendarSource
from ntrp.integrations.tool_errors import operation_error_result
from ntrp.tools.core import ToolResult, tool
from ntrp.tools.core.context import ToolExecution
from ntrp.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope

CALENDAR_DESCRIPTION = """Browse or search calendar events.

Without query: lists events by time range. Use days_forward/days_back to control window.
With query: searches events by name, attendee, or description. Use specific keywords.

Returns event times, titles, and IDs. Use the event ID for edit/delete operations."""

CREATE_CALENDAR_EVENT_DESCRIPTION = """Create a new calendar event.

Use this to schedule meetings, reminders, or block time on the calendar.
Requires user approval before creating."""

EDIT_CALENDAR_EVENT_DESCRIPTION = """Edit an existing calendar event.

Use calendar() or calendar(query) first to find the event ID.
Only provide the fields you want to change - others remain unchanged.
Requires user approval before editing."""

DELETE_CALENDAR_EVENT_DESCRIPTION = """Delete a calendar event by ID.

Use calendar() or calendar(query) first to find the event ID.
Requires user approval before deleting."""


def _event_source_refs(events: list) -> tuple[ToolSourceRef, ...]:
    return normalize_source_refs(
        ToolSourceRef(
            provider="calendar",
            kind="event",
            ref=(
                f"{calendar_id}:{event.source_id}"
                if (calendar_id := str(event.metadata.get("calendar_id") or "").strip()) and event.source_id
                else ""
            ),
            title=(event.title or "").strip() or f"Calendar event {event.source_id}",
            url=event.metadata.get("html_link"),
        )
        for event in events
    )


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.astimezone()  # naive → local timezone
        return dt
    except Exception:
        return None


def _format_events(events: list) -> str:
    lines = []
    for event in events:
        meta = event.metadata
        start = meta.get("start", "")

        if start:
            dt = datetime.fromisoformat(start)
            if dt.tzinfo is not None:
                dt = dt.astimezone()
            if meta.get("is_all_day"):
                time_str = dt.strftime("%a %b %d") + " (all day)"
            else:
                time_str = dt.strftime("%a %b %d, %H:%M")
        else:
            time_str = "No time"

        location = f" @ {meta['location']}" if meta.get("location") else ""
        lines.append(f"• {time_str}: {event.title}{location} `[{event.source_id}]`")

    return "\n".join(lines)


_DEFAULT_DAYS_FORWARD = 7
_DEFAULT_DAYS_BACK = 0
_DEFAULT_CALENDAR_LIMIT = 30


class CalendarInput(BaseModel):
    query: str | None = Field(default=None, description="Search query. Omit to list events by time range.")
    days_forward: int = Field(
        default=_DEFAULT_DAYS_FORWARD, description=f"Days ahead to look when listing (default: {_DEFAULT_DAYS_FORWARD})"
    )
    days_back: int = Field(
        default=_DEFAULT_DAYS_BACK, description=f"Days back to look when listing (default: {_DEFAULT_DAYS_BACK})"
    )
    limit: int = Field(
        default=_DEFAULT_CALENDAR_LIMIT, description=f"Maximum results (default: {_DEFAULT_CALENDAR_LIMIT})"
    )


def _calendar_search(source: MultiCalendarSource, query: str, limit: int) -> ToolResult:
    try:
        events = source.search(query, limit=limit)

        if not events:
            return ToolResult(
                content=f"No events found matching '{query}'. Try different keywords or omit query to list upcoming.",
                preview="0 events",
            )

        content = _format_events(events)
        return ToolResult(
            content=content,
            preview=f"{len(events)} events",
            source_refs=_event_source_refs(events),
        )
    except IntegrationOperationError as error:
        return operation_error_result(error, preview="Search failed")


def _calendar_list(source: MultiCalendarSource, days_forward: int, days_back: int, limit: int) -> ToolResult:
    events = []

    if days_back > 0:
        past = source.get_past(days=days_back, limit=limit)
        events.extend(past)

    if days_forward > 0:
        upcoming = source.get_upcoming(days=days_forward, limit=limit)
        events.extend(upcoming)

    if not events:
        return ToolResult(content="No calendar events in the specified range", preview="0 events")

    events.sort(key=lambda e: e.metadata.get("start", ""))
    trimmed = events[:limit]

    content = _format_events(trimmed)
    return ToolResult(
        content=content,
        preview=f"{len(events)} events",
        source_refs=_event_source_refs(trimmed),
    )


async def calendar(execution: ToolExecution, args: CalendarInput) -> ToolResult:
    source = execution.ctx.get_client("calendar", MultiCalendarSource)
    if args.query:
        return _calendar_search(source, args.query, args.limit)
    return _calendar_list(source, args.days_forward, args.days_back, args.limit)


class CreateCalendarEventInput(BaseModel):
    summary: str = Field(description="Event title/summary")
    start: str = Field(description="Start time in ISO format (e.g., '2024-01-15T14:00:00')")
    end: str | None = Field(
        default=None, description="End time in ISO format (optional, defaults to 1 hour after start)"
    )
    description: str | None = Field(default=None, description="Event description (optional)")
    location: str | None = Field(default=None, description="Event location (optional)")
    attendees: str | None = Field(default=None, description="Comma-separated email addresses of attendees (optional)")
    all_day: bool = Field(default=False, description="Whether this is an all-day event (optional)")
    account: str | None = Field(default=None, description="Calendar account email (optional if only one account)")


async def approve_create_calendar_event(
    execution: ToolExecution, args: CreateCalendarEventInput
) -> ApprovalInfo | None:
    start_dt = _parse_datetime(args.start)
    if not start_dt:
        return None
    time_str = start_dt.strftime("%Y-%m-%d %H:%M")
    end_dt = _parse_datetime(args.end)
    if end_dt:
        time_str += f" - {end_dt.strftime('%H:%M')}"
    return ApprovalInfo(
        description=args.summary,
        preview=f"Time: {time_str}\nLocation: {args.location or 'N/A'}",
        diff=None,
    )


async def create_calendar_event(execution: ToolExecution, args: CreateCalendarEventInput) -> ToolResult:
    start_dt = _parse_datetime(args.start)
    if not start_dt:
        return ToolResult.failure(
            code="invalid_ref",
            message=f"Invalid start time: {args.start}. Use ISO format: 2024-01-15T14:00:00",
            preview="Invalid start",
            recovery_action="Retry with an ISO-8601 timestamp including a UTC offset.",
        )

    end_dt = _parse_datetime(args.end) if args.end else None
    attendee_list = [e.strip() for e in args.attendees.split(",") if e.strip()] if args.attendees else None

    source = execution.ctx.get_client("calendar", MultiCalendarSource)
    try:
        result = source.create_event(
            account=args.account or "",
            summary=args.summary,
            start=start_dt,
            end=end_dt,
            description=args.description or "",
            location=args.location or "",
            attendees=attendee_list,
            all_day=args.all_day,
        )
    except IntegrationOperationError as error:
        return operation_error_result(error, preview="Create failed")
    return ToolResult(content=result, preview="Created")


class EditCalendarEventInput(BaseModel):
    event_id: str = Field(description="The event ID to edit (from calendar() or calendar(query))")
    summary: str | None = Field(default=None, description="New event title (optional)")
    start: str | None = Field(default=None, description="New start time in ISO format (optional)")
    end: str | None = Field(default=None, description="New end time in ISO format (optional)")
    description: str | None = Field(default=None, description="New event description (optional)")
    location: str | None = Field(default=None, description="New event location (optional)")
    attendees: str | None = Field(
        default=None, description="New comma-separated attendee emails (optional, replaces existing)"
    )


async def approve_edit_calendar_event(execution: ToolExecution, args: EditCalendarEventInput) -> ApprovalInfo | None:
    changes = []
    if args.summary:
        changes.append(f"Title: {args.summary}")
    if args.start:
        changes.append(f"Start: {args.start}")
    if args.end:
        changes.append(f"End: {args.end}")
    if args.location:
        changes.append(f"Location: {args.location}")
    return ApprovalInfo(
        description=args.event_id,
        preview="\n".join(changes) if changes else "No changes",
        diff=None,
    )


async def edit_calendar_event(execution: ToolExecution, args: EditCalendarEventInput) -> ToolResult:
    start_dt = _parse_datetime(args.start) if args.start else None
    if args.start and not start_dt:
        return ToolResult.failure(
            code="invalid_ref",
            message=f"Invalid start time: {args.start}. Use ISO format: 2024-01-15T14:00:00",
            preview="Invalid start",
            recovery_action="Retry with an ISO-8601 timestamp including a UTC offset.",
        )

    end_dt = _parse_datetime(args.end) if args.end else None
    if args.end and not end_dt:
        return ToolResult.failure(
            code="invalid_ref",
            message=f"Invalid end time: {args.end}. Use ISO format: 2024-01-15T15:00:00",
            preview="Invalid end",
            recovery_action="Retry with an ISO-8601 timestamp including a UTC offset.",
        )

    attendee_list = [e.strip() for e in args.attendees.split(",") if e.strip()] if args.attendees else None

    source = execution.ctx.get_client("calendar", MultiCalendarSource)
    try:
        result = source.update_event(
            event_id=args.event_id,
            summary=args.summary,
            start=start_dt,
            end=end_dt,
            description=args.description,
            location=args.location,
            attendees=attendee_list,
        )
    except IntegrationOperationError as error:
        return operation_error_result(error, preview="Update failed")
    return ToolResult(content=result, preview="Updated")


class DeleteCalendarEventInput(BaseModel):
    event_id: str = Field(description="The event ID to delete")


async def approve_delete_calendar_event(
    execution: ToolExecution, args: DeleteCalendarEventInput
) -> ApprovalInfo | None:
    return ApprovalInfo(description=args.event_id, preview=None, diff=None)


async def delete_calendar_event(execution: ToolExecution, args: DeleteCalendarEventInput) -> ToolResult:
    source = execution.ctx.get_client("calendar", MultiCalendarSource)
    try:
        result = source.delete_event(args.event_id)
    except IntegrationOperationError as error:
        return operation_error_result(error, preview="Delete failed")
    return ToolResult(content=result, preview="Deleted")


calendar_tool = tool(
    display_name="Calendar",
    description=CALENDAR_DESCRIPTION,
    input_model=CalendarInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.EXTERNAL, permissions=frozenset({"calendar"})),
    execute=calendar,
)

create_calendar_event_tool = tool(
    display_name="CreateEvent",
    description=CREATE_CALENDAR_EVENT_DESCRIPTION,
    input_model=CreateCalendarEventInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.EXTERNAL,
        requires_approval=True,
        permissions=frozenset({"calendar"}),
    ),
    approval=approve_create_calendar_event,
    execute=create_calendar_event,
)

edit_calendar_event_tool = tool(
    display_name="EditEvent",
    description=EDIT_CALENDAR_EVENT_DESCRIPTION,
    input_model=EditCalendarEventInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.EXTERNAL,
        requires_approval=True,
        permissions=frozenset({"calendar"}),
    ),
    approval=approve_edit_calendar_event,
    execute=edit_calendar_event,
)

delete_calendar_event_tool = tool(
    display_name="DeleteEvent",
    description=DELETE_CALENDAR_EVENT_DESCRIPTION,
    input_model=DeleteCalendarEventInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.EXTERNAL,
        requires_approval=True,
        permissions=frozenset({"calendar"}),
    ),
    approval=approve_delete_calendar_event,
    execute=delete_calendar_event,
)
