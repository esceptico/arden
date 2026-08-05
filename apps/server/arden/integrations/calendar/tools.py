from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from arden.agent.types.tools import ToolSourceRef, normalize_source_refs
from arden.integrations.base import IntegrationOperationError
from arden.integrations.calendar.client import MultiCalendarSource
from arden.integrations.calendar.models import (
    CALENDAR_ATTENDEE_LIMIT,
    CalendarCreateDraft,
    CalendarDeleteDraft,
    CalendarEvent,
    CalendarEventPage,
    CalendarMutationResult,
    CalendarPageRef,
    CalendarUpdateDraft,
)
from arden.integrations.google_auth.accounts import validate_google_account_email
from arden.integrations.mutations import execute_idempotent, mutation_result
from arden.integrations.tool_errors import operation_error_result
from arden.tools.core import ToolResult, tool
from arden.tools.core.collections import format_timestamp
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope

CALENDAR_DESCRIPTION = """Browse or search calendar events.

Without query: lists events by time range. Use days_forward/days_back to control window.
With query: searches events by name, attendee, or description. Use specific keywords.

Returns event times, titles, and account-qualified references. Copy the full
`account:event_ref` value into edit/delete operations. To continue one page, pass
only the returned page_ref; it keeps the exact account, query, and time window."""

CREATE_CALENDAR_EVENT_DESCRIPTION = """Create a new calendar event.

Use this to schedule meetings, reminders, or block time on the calendar.
Requires user approval before creating."""

EDIT_CALENDAR_EVENT_DESCRIPTION = """Edit an existing calendar event.

Use calendar_search() or calendar_search(query) first, then copy the full account-qualified event reference.
Only provide the fields you want to change - others remain unchanged.
Requires user approval before editing."""

DELETE_CALENDAR_EVENT_DESCRIPTION = """Delete a calendar event.

Use calendar_search() or calendar_search(query) first, then copy the full account-qualified event reference.
Requires user approval before deleting."""


def _event_source_refs(events: Sequence[CalendarEvent]) -> tuple[ToolSourceRef, ...]:
    return normalize_source_refs(
        ToolSourceRef(
            provider="calendar",
            kind="event",
            ref=event.event_ref,
            title=event.title or "Untitled event",
            url=event.url,
        )
        for event in events
    )


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None and dt.utcoffset() is not None else None


def _required_datetime(value: str, field: str) -> datetime:
    parsed = _parse_datetime(value)
    if parsed is None:
        raise ValueError(f"calendar {field} must be an ISO-8601 timestamp with a UTC offset")
    return parsed


def _approval_text(value: str | None) -> str:
    if value is None:
        return "(none)"
    if value == "":
        return "(empty)"
    return value.replace("\r", "\\r").replace("\n", "\\n")


def _approval_attendees(attendees: tuple[str, ...]) -> str:
    return ", ".join(attendees) if attendees else "(none)"


def _update_approval_lines(draft: CalendarUpdateDraft) -> list[str]:
    lines = [f"Event: {draft.event_ref}"]
    if "summary" in draft.changed_fields:
        lines.append(f"Title: {_approval_text(draft.summary) if draft.summary else '(clear)'}")
    if {"start", "end", "all_day"} <= draft.changed_fields:
        start = cast("datetime", draft.start)
        end = cast("datetime", draft.end)
        all_day = cast("bool", draft.all_day)
        start_display = start.date().isoformat() if all_day else format_timestamp(start)
        end_display = end.date().isoformat() if all_day else format_timestamp(end)
        lines.extend(
            (
                f"All day: {'yes' if all_day else 'no'}",
                f"Start: {start_display}",
                f"End: {end_display}{' (exclusive)' if all_day else ''}",
            )
        )
    if "description" in draft.changed_fields:
        lines.append(f"Description: {_approval_text(draft.description) if draft.description else '(clear)'}")
    if "location" in draft.changed_fields:
        lines.append(f"Location: {_approval_text(draft.location) if draft.location else '(clear)'}")
    if "attendees" in draft.changed_fields:
        attendees = draft.attendees or ()
        lines.append(f"Attendees: {_approval_attendees(attendees) if attendees else '(clear)'}")
    return lines


def _format_events(events: Sequence[CalendarEvent]) -> str:
    lines = []
    for event in events:
        if event.is_all_day:
            time_str = (
                f"{event.start.date().isoformat()} → {event.end.date().isoformat()} "
                "(all day; end exclusive)"
            )
        else:
            time_str = f"{format_timestamp(event.start)} → {format_timestamp(event.end)}"

        title = event.title or "Untitled event"
        location = f" @ {event.location}" if event.location else ""
        lines.append(f"• {time_str}: {title}{location} `[{event.event_ref}]`")

    return "\n".join(lines)


def _format_mutation(action: str, result: CalendarMutationResult) -> str:
    subject = f": {result.summary}" if result.summary else ""
    lines = [f"{action} event{subject} `[{result.event_ref}]`"]
    if result.url:
        lines.append(result.url)
    return "\n".join(lines)


_DEFAULT_DAYS_FORWARD = 7
_DEFAULT_DAYS_BACK = 0
_DEFAULT_CALENDAR_LIMIT = 30


class CalendarSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    query: str | None = Field(
        default=None,
        max_length=500,
        description="Exact non-blank search query. Omit to list events by time range.",
    )
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
        le=50,
        description=f"Maximum results (default: {_DEFAULT_CALENDAR_LIMIT})",
    )
    account: str | None = Field(
        default=None,
        min_length=3,
        max_length=320,
        description="Optional exact Calendar account email. Select one account for a chainable page_ref.",
    )
    page_ref: str | None = Field(
        default=None,
        max_length=12_000,
        description="Exact continuation returned by calendar_search. Pass it alone, without other fields.",
    )

    @model_validator(mode="after")
    def validate_search(self):
        if self.account is not None:
            validate_google_account_email(self.account)
        if self.page_ref is not None:
            CalendarPageRef.decode(self.page_ref)
            if self.model_fields_set != {"page_ref"}:
                raise ValueError("page_ref must be passed alone")
            return self
        if self.query is not None and (not self.query or self.query != self.query.strip()):
            raise ValueError("calendar query must be exact and non-blank")
        if self.query is None and self.days_forward == 0 and self.days_back == 0:
            raise ValueError("calendar listing requires a non-zero time range")
        return self


def _continuation(page: CalendarEventPage) -> str:
    if page.next_page_ref is None:
        return ""
    return f"\nContinue with page_ref: {page.next_page_ref.encode()}"


def _page_data(page: CalendarEventPage) -> dict[str, object]:
    return {
        "count": len(page.events),
        "has_more": page.has_more,
        "next_page_ref": page.next_page_ref.encode() if page.next_page_ref is not None else None,
    }


def _calendar_page(page: CalendarEventPage) -> ToolResult:
    events = page.events
    content = _format_events(events) if events else "No calendar events in this exact search window"
    content += _continuation(page)
    if page.has_more and page.next_page_ref is None:
        content += "\nMore events may exist across multiple accounts. Select one account to continue."
    return ToolResult(
        content=content,
        preview=f"{len(events)} events" + (" (more available)" if page.has_more else ""),
        data=_page_data(page),
        source_refs=_event_source_refs(events),
    )


def _calendar_search(
    source: MultiCalendarSource, query: str, limit: int, account: str | None
) -> ToolResult:
    page = source.search(query, limit=limit, account_ref=account)
    events = page.events

    if not events:
        result = _calendar_page(page)
        return ToolResult(
            content=(
                f"No events found matching '{query}'. Try different keywords or omit query to list upcoming."
                + _continuation(page)
            ),
            preview=result.preview,
            data=result.data,
        )

    return _calendar_page(page)


def _calendar_list(
    source: MultiCalendarSource, days_forward: int, days_back: int, limit: int, account: str | None
) -> ToolResult:
    return _calendar_page(
        source.list_window(
            days_back=days_back,
            days_forward=days_forward,
            limit=limit,
            account_ref=account,
        )
    )


async def calendar_search(execution: ToolExecution, args: CalendarSearchInput) -> ToolResult:
    source = execution.ctx.get_client("calendar", MultiCalendarSource)
    try:
        if args.page_ref is not None:
            return _calendar_page(source.continue_page(CalendarPageRef.decode(args.page_ref)))
        if args.query is not None:
            return _calendar_search(source, args.query, args.limit, args.account)
        return _calendar_list(source, args.days_forward, args.days_back, args.limit, args.account)
    except IntegrationOperationError as error:
        return operation_error_result(error, preview="Calendar read failed")


class CalendarCreateEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    summary: str = Field(description="Event title/summary")
    start: str = Field(description="Start time in ISO format with UTC offset (e.g., '2024-01-15T14:00:00+00:00')")
    end: str | None = Field(
        default=None,
        description="End time in ISO format (defaults to 1 hour after start, or the next date for all-day events)",
    )
    description: str | None = Field(default=None, description="Event description (optional)")
    location: str | None = Field(default=None, description="Event location (optional)")
    attendees: list[str] | None = Field(
        default=None,
        max_length=CALENDAR_ATTENDEE_LIMIT,
        description=f"Exact attendee email addresses (optional; maximum {CALENDAR_ATTENDEE_LIMIT})",
    )
    all_day: bool = Field(default=False, description="Whether this is an all-day event (optional)")
    account: str = Field(min_length=3, description="Exact Calendar account email")
    idempotency_key: str = Field(min_length=8, max_length=200)

    def to_draft(self) -> CalendarCreateDraft:
        start = _required_datetime(self.start, "start")
        if self.end is None:
            end = start + (timedelta(days=1) if self.all_day else timedelta(hours=1))
        else:
            end = _required_datetime(self.end, "end")
        if self.all_day and end.date() <= start.date():
            raise ValueError("all-day event end must be a later date")
        return CalendarCreateDraft(
            account_ref=self.account,
            summary=self.summary,
            start=start,
            end=end,
            description=self.description,
            location=self.location,
            attendees=tuple(self.attendees or ()),
            all_day=self.all_day,
        )

    @model_validator(mode="after")
    def validate_draft(self):
        self.to_draft()
        return self


async def approve_calendar_create_event(
    execution: ToolExecution, args: CalendarCreateEventInput
) -> ApprovalInfo:
    draft = args.to_draft()
    start_display = draft.start.date().isoformat() if draft.all_day else format_timestamp(draft.start)
    end_display = draft.end.date().isoformat() if draft.all_day else format_timestamp(draft.end)
    return ApprovalInfo(
        description=draft.summary,
        preview="\n".join(
            (
                f"Account: {draft.account_ref}",
                f"All day: {'yes' if draft.all_day else 'no'}",
                f"Start: {start_display}",
                f"End: {end_display}{' (exclusive)' if draft.all_day else ''}",
                f"Description: {_approval_text(draft.description)}",
                f"Location: {_approval_text(draft.location)}",
                f"Attendees: {_approval_attendees(draft.attendees)}",
            )
        ),
        diff=None,
    )


async def calendar_create_event(execution: ToolExecution, args: CalendarCreateEventInput) -> ToolResult:
    draft = args.to_draft()

    async def invoke() -> ToolResult:
        source = execution.ctx.get_client("calendar", MultiCalendarSource)
        try:
            result = source.create_event(draft)
        except IntegrationOperationError as error:
            return operation_error_result(error, preview="Create failed")
        return mutation_result(
            content=_format_mutation("Created", result),
            preview="Created",
            operation="create",
            target=draft.summary,
            receipt=result.event_ref,
            after_ref=result.event_ref,
            observed=f"Calendar returned event {result.event_ref}",
            data={"event_ref": result.event_ref},
        )

    return await execute_idempotent(
        execution,
        namespace=f"calendar:create:{draft.account_ref.casefold()}",
        idempotency_key=args.idempotency_key,
        payload=args.model_dump(exclude={"idempotency_key"}),
        invoke=invoke,
    )


class CalendarEditEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    event_ref: str = Field(description="Full account-qualified reference returned by calendar_search")
    summary: str | None = Field(default=None, description="New event title; pass an empty string to clear it")
    start: str | None = Field(default=None, description="New start time in ISO format (optional)")
    end: str | None = Field(default=None, description="New end time in ISO format (optional)")
    description: str | None = Field(default=None, description="New description; pass an empty string to clear it")
    location: str | None = Field(default=None, description="New location; pass an empty string to clear it")
    attendees: list[str] | None = Field(
        default=None,
        max_length=CALENDAR_ATTENDEE_LIMIT,
        description=(
            f"New exact attendee emails (maximum {CALENDAR_ATTENDEE_LIMIT}); pass an empty list to clear all"
        ),
    )
    all_day: bool | None = Field(
        default=None,
        description="New all-day state; any temporal edit requires start, end, and all_day together",
    )
    idempotency_key: str = Field(min_length=8, max_length=200)

    def to_draft(self) -> CalendarUpdateDraft:
        mutable_fields = {"summary", "start", "end", "description", "location", "attendees", "all_day"}
        changed_fields = frozenset(self.model_fields_set & mutable_fields)
        for field in changed_fields:
            if getattr(self, field) is None:
                raise ValueError(f"calendar update {field} cannot be null; omit it to leave it unchanged")
        return CalendarUpdateDraft(
            event_ref=self.event_ref,
            changed_fields=changed_fields,
            summary=self.summary,
            start=_required_datetime(self.start, "start") if "start" in changed_fields else None,
            end=_required_datetime(self.end, "end") if "end" in changed_fields else None,
            description=self.description,
            location=self.location,
            attendees=tuple(self.attendees) if self.attendees is not None else None,
            all_day=self.all_day,
        )

    @model_validator(mode="after")
    def validate_draft(self):
        self.to_draft()
        return self


async def approve_calendar_edit_event(execution: ToolExecution, args: CalendarEditEventInput) -> ApprovalInfo:
    draft = args.to_draft()
    changes = _update_approval_lines(draft)
    return ApprovalInfo(
        description=draft.event_ref,
        preview="\n".join(changes),
        diff=None,
    )


async def calendar_edit_event(execution: ToolExecution, args: CalendarEditEventInput) -> ToolResult:
    draft = args.to_draft()

    async def invoke() -> ToolResult:
        source = execution.ctx.get_client("calendar", MultiCalendarSource)
        try:
            result = source.update_event(draft)
        except IntegrationOperationError as error:
            return operation_error_result(error, preview="Update failed")
        return mutation_result(
            content=_format_mutation("Updated", result),
            preview="Updated",
            operation="update",
            target=result.event_ref,
            receipt=result.event_ref,
            before_ref=draft.event_ref,
            after_ref=result.event_ref,
            observed=f"Calendar acknowledged update of {result.event_ref}",
            data={"event_ref": result.event_ref},
        )

    return await execute_idempotent(
        execution,
        namespace="calendar:update",
        idempotency_key=args.idempotency_key,
        payload=args.model_dump(exclude={"idempotency_key"}),
        invoke=invoke,
    )


class CalendarDeleteEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    event_ref: str = Field(description="Full account-qualified reference returned by calendar_search")
    idempotency_key: str = Field(min_length=8, max_length=200)

    def to_draft(self) -> CalendarDeleteDraft:
        return CalendarDeleteDraft(event_ref=self.event_ref)

    @model_validator(mode="after")
    def validate_draft(self):
        self.to_draft()
        return self


async def approve_calendar_delete_event(
    execution: ToolExecution, args: CalendarDeleteEventInput
) -> ApprovalInfo:
    draft = args.to_draft()
    return ApprovalInfo(
        description="Delete calendar event",
        preview=f"Event ref: {draft.event_ref}",
        diff=f"- calendar event {draft.event_ref}",
    )


async def calendar_delete_event(execution: ToolExecution, args: CalendarDeleteEventInput) -> ToolResult:
    draft = args.to_draft()

    async def invoke() -> ToolResult:
        source = execution.ctx.get_client("calendar", MultiCalendarSource)
        try:
            result = source.delete_event(draft)
        except IntegrationOperationError as error:
            return operation_error_result(error, preview="Delete failed")
        return mutation_result(
            content=_format_mutation("Deleted", result),
            preview="Deleted",
            operation="delete",
            target=result.event_ref,
            receipt=result.event_ref,
            before_ref=result.event_ref,
            after_ref="absent",
            observed=f"Calendar acknowledged deletion of {result.event_ref}",
            data={"event_ref": result.event_ref},
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
        destructive=False,
        open_world=True,
        idempotent=True,
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
        destructive=True,
        open_world=True,
        idempotent=True,
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
        destructive=True,
        open_world=True,
        idempotent=True,
    ),
    approval=approve_calendar_delete_event,
    execute=calendar_delete_event,
)
