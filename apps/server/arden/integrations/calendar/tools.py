import re
from datetime import datetime

from pydantic import BaseModel, Field

from arden.agent.types.tools import ToolSourceRef, normalize_source_refs
from arden.integrations.base import IntegrationOperationError
from arden.integrations.calendar.client import MultiCalendarSource
from arden.integrations.mutations import execute_idempotent, mutation_result
from arden.integrations.tool_errors import operation_error_result
from arden.tools.core import ToolResult, tool
from arden.tools.core.collections import format_timestamp
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope

CALENDAR_DESCRIPTION = """Browse or search calendar events.

Without query: lists events by time range. Use days_forward/days_back to control window.
With query: searches events by name, attendee, or description. Use specific keywords.

Returns event times, titles, and IDs. Use the event ID for edit/delete operations."""

CREATE_CALENDAR_EVENT_DESCRIPTION = """Create a new calendar event.

Use this to schedule meetings, reminders, or block time on the calendar.
Requires user approval before creating."""

EDIT_CALENDAR_EVENT_DESCRIPTION = """Edit an existing calendar event.

Use calendar_search() or calendar_search(query) first to find the event ID.
Only provide the fields you want to change - others remain unchanged.
Requires user approval before editing."""

DELETE_CALENDAR_EVENT_DESCRIPTION = """Delete a calendar event by ID.

Use calendar_search() or calendar_search(query) first to find the event ID.
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
            if meta.get("is_all_day"):
                time_str = f"{start} (all day)"
            else:
                dt = _parse_datetime(start)
                time_str = format_timestamp(dt) if dt else start
        else:
            time_str = "No time"

        location = f" @ {meta['location']}" if meta.get("location") else ""
        lines.append(f"• {time_str}: {event.title}{location} `[{event.source_id}]`")

    return "\n".join(lines)


_DEFAULT_DAYS_FORWARD = 7
_DEFAULT_DAYS_BACK = 0
_DEFAULT_CALENDAR_LIMIT = 30


class CalendarSearchInput(BaseModel):
    query: str | None = Field(default=None, description="Search query. Omit to list events by time range.")
    days_forward: int = Field(
        default=_DEFAULT_DAYS_FORWARD,
        ge=0,
        le=3650,
        description=f"Days ahead to look when listing (default: {_DEFAULT_DAYS_FORWARD})",
    )
    days_back: int = Field(
        default=_DEFAULT_DAYS_BACK,
        ge=0,
        le=3650,
        description=f"Days back to look when listing (default: {_DEFAULT_DAYS_BACK})",
    )
    limit: int = Field(
        default=_DEFAULT_CALENDAR_LIMIT,
        ge=1,
        le=100,
        description=f"Maximum results (default: {_DEFAULT_CALENDAR_LIMIT})",
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
        if len(events) == limit:
            content += f"\nShowing {limit} events; more may exist. Narrow the query to continue."
        return ToolResult(
            content=content,
            preview=f"{len(events)} events" + (" (possibly capped)" if len(events) == limit else ""),
            data={"count": len(events), "may_have_more": len(events) == limit},
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
    has_more = len(events) > limit
    content = _format_events(trimmed)
    if has_more:
        content += f"\nShowing {limit} events; more exist. Narrow the time range to continue."
    return ToolResult(
        content=content,
        preview=f"{len(trimmed)} events" + (" (capped)" if has_more else ""),
        data={"count": len(trimmed), "has_more": has_more},
        source_refs=_event_source_refs(trimmed),
    )


async def calendar_search(execution: ToolExecution, args: CalendarSearchInput) -> ToolResult:
    source = execution.ctx.get_client("calendar", MultiCalendarSource)
    if args.query:
        return _calendar_search(source, args.query, args.limit)
    return _calendar_list(source, args.days_forward, args.days_back, args.limit)


class CalendarCreateEventInput(BaseModel):
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
    idempotency_key: str = Field(min_length=8, max_length=200)


async def approve_calendar_create_event(
    execution: ToolExecution, args: CalendarCreateEventInput
) -> ApprovalInfo | None:
    start_dt = _parse_datetime(args.start)
    if not start_dt:
        return None
    time_str = format_timestamp(start_dt)
    end_dt = _parse_datetime(args.end)
    if end_dt:
        time_str += f" - {format_timestamp(end_dt)}"
    return ApprovalInfo(
        description=args.summary,
        preview=f"Time: {time_str}\nLocation: {args.location or 'N/A'}",
        diff=None,
    )


async def calendar_create_event(execution: ToolExecution, args: CalendarCreateEventInput) -> ToolResult:
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

    async def invoke() -> ToolResult:
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
        match = re.search(r"\(id: ([^)]+)\)", result)
        event_ref = match.group(1) if match else None
        if event_ref and args.account and not event_ref.startswith(f"{args.account}:"):
            event_ref = f"{args.account}:{event_ref}"
        return mutation_result(
            content=result,
            preview="Created" if event_ref else "Create unverified",
            operation="create",
            target=args.summary,
            receipt=match.group(1) if match else args.idempotency_key,
            after_ref=event_ref,
            observed=(f"Calendar returned event {event_ref}" if event_ref else None),
            data={"event_ref": event_ref} if event_ref else None,
        )

    return await execute_idempotent(
        execution,
        namespace=f"calendar:create:{args.account or 'default'}",
        idempotency_key=args.idempotency_key,
        payload=args.model_dump(exclude={"idempotency_key"}),
        invoke=invoke,
    )


class CalendarEditEventInput(BaseModel):
    event_id: str = Field(description="The event ID to edit (from calendar() or calendar(query))")
    summary: str | None = Field(default=None, description="New event title (optional)")
    start: str | None = Field(default=None, description="New start time in ISO format (optional)")
    end: str | None = Field(default=None, description="New end time in ISO format (optional)")
    description: str | None = Field(default=None, description="New event description (optional)")
    location: str | None = Field(default=None, description="New event location (optional)")
    attendees: str | None = Field(
        default=None, description="New comma-separated attendee emails (optional, replaces existing)"
    )
    idempotency_key: str = Field(min_length=8, max_length=200)


async def approve_calendar_edit_event(execution: ToolExecution, args: CalendarEditEventInput) -> ApprovalInfo | None:
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


async def calendar_edit_event(execution: ToolExecution, args: CalendarEditEventInput) -> ToolResult:
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

    async def invoke() -> ToolResult:
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
        return mutation_result(
            content=result,
            preview="Updated",
            operation="update",
            target=args.event_id,
            receipt=args.idempotency_key,
            before_ref=args.event_id,
            after_ref=args.event_id,
            observed=f"Calendar acknowledged update of {args.event_id}",
        )

    return await execute_idempotent(
        execution,
        namespace="calendar:update",
        idempotency_key=args.idempotency_key,
        payload=args.model_dump(exclude={"idempotency_key"}),
        invoke=invoke,
    )


class CalendarDeleteEventInput(BaseModel):
    event_id: str = Field(description="The event ID to delete")
    idempotency_key: str = Field(min_length=8, max_length=200)


async def approve_calendar_delete_event(
    execution: ToolExecution, args: CalendarDeleteEventInput
) -> ApprovalInfo | None:
    return ApprovalInfo(
        description="Delete calendar event",
        preview=f"Event ref: {args.event_id}",
        diff=f"- calendar event {args.event_id}",
    )


async def calendar_delete_event(execution: ToolExecution, args: CalendarDeleteEventInput) -> ToolResult:
    async def invoke() -> ToolResult:
        source = execution.ctx.get_client("calendar", MultiCalendarSource)
        try:
            result = source.delete_event(args.event_id)
        except IntegrationOperationError as error:
            return operation_error_result(error, preview="Delete failed")
        return mutation_result(
            content=result,
            preview="Deleted",
            operation="delete",
            target=args.event_id,
            receipt=args.idempotency_key,
            before_ref=args.event_id,
            after_ref="absent",
            observed=f"Calendar acknowledged deletion of {args.event_id}",
        )

    return await execute_idempotent(
        execution,
        namespace="calendar:delete",
        idempotency_key=args.idempotency_key,
        payload=args.model_dump(exclude={"idempotency_key"}),
        invoke=invoke,
    )


calendar_search_tool = tool(
    display_name="Calendar",
    display_description="Browse and search calendar events.",
    description=CALENDAR_DESCRIPTION,
    input_model=CalendarSearchInput,
    policy=ToolPolicy(
        action=ToolAction.READ, scope=ToolScope.EXTERNAL, permissions=frozenset({"calendar"}), deferred=True
    ),
    execute=calendar_search,
)

calendar_create_event_tool = tool(
    display_name="CreateEvent",
    display_description="Create a calendar event after approval.",
    description=CREATE_CALENDAR_EVENT_DESCRIPTION,
    input_model=CalendarCreateEventInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.EXTERNAL,
        requires_approval=True,
        permissions=frozenset({"calendar"}),
        deferred=True,
    ),
    approval=approve_calendar_create_event,
    execute=calendar_create_event,
)

calendar_edit_event_tool = tool(
    display_name="EditEvent",
    display_description="Edit a calendar event after approval.",
    description=EDIT_CALENDAR_EVENT_DESCRIPTION,
    input_model=CalendarEditEventInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.EXTERNAL,
        requires_approval=True,
        permissions=frozenset({"calendar"}),
        deferred=True,
    ),
    approval=approve_calendar_edit_event,
    execute=calendar_edit_event,
)

calendar_delete_event_tool = tool(
    display_name="DeleteEvent",
    display_description="Delete a calendar event after approval.",
    description=DELETE_CALENDAR_EVENT_DESCRIPTION,
    input_model=CalendarDeleteEventInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.EXTERNAL,
        requires_approval=True,
        permissions=frozenset({"calendar"}),
        deferred=True,
    ),
    approval=approve_calendar_delete_event,
    execute=calendar_delete_event,
)
