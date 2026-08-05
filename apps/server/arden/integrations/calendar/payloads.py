from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from arden.integrations.base import IntegrationOperationError
from arden.integrations.calendar.models import (
    CalendarAccount,
    CalendarEvent,
    CalendarEventPage,
    CalendarMutationResult,
    CalendarPageRef,
    qualify_event_ref,
)


def _invalid(detail: str) -> IntegrationOperationError:
    return IntegrationOperationError(
        code="invalid_response",
        safe_message=f"Calendar provider returned {detail}.",
    )


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _invalid(f"an invalid {field}")
    return value


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise _invalid(f"an invalid {field}")
    return value


def _optional_text(payload: Mapping[str, Any], field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    return _required_text(value, field)


def _event_datetime(value: object, field: str) -> tuple[datetime, bool]:
    payload = _mapping(value, f"event {field} time")
    has_date_time = "dateTime" in payload
    has_date = "date" in payload
    if has_date_time == has_date:
        raise _invalid(f"an invalid event {field} time")
    raw = payload["dateTime"] if has_date_time else payload["date"]
    if not isinstance(raw, str):
        raise _invalid(f"an invalid event {field} time")
    try:
        parsed = (
            datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if has_date_time
            else datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=UTC)
        )
    except ValueError as exc:
        raise _invalid(f"an invalid event {field} time") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _invalid(f"an event {field} time without a timezone")
    return parsed, has_date


def decode_calendar_account(raw: object) -> CalendarAccount:
    payload = _mapping(raw, "account identity")
    try:
        return CalendarAccount(account_ref=_required_text(payload.get("id"), "account identity"))
    except ValueError as exc:
        raise _invalid("an invalid account identity") from exc


def decode_calendar_event(raw: object, account_ref: str) -> CalendarEvent:
    payload = _mapping(raw, "event")
    provider_ref = _required_text(payload.get("id"), "event reference")
    start, start_all_day = _event_datetime(payload.get("start"), "start")
    end, end_all_day = _event_datetime(payload.get("end"), "end")
    if start_all_day != end_all_day:
        raise _invalid("inconsistent all-day event times")

    summary = payload.get("summary")
    title = None if summary is None else _required_text(summary, "event summary")

    attendees_raw = payload.get("attendees")
    attendees: tuple[str, ...] = ()
    if attendees_raw is not None:
        if not isinstance(attendees_raw, list):
            raise _invalid("an invalid event attendee list")
        attendees = tuple(
            _required_text(_mapping(attendee, "event attendee").get("email"), "event attendee email")
            for attendee in attendees_raw
        )

    organizer_raw = payload.get("organizer")
    calendar_ref = None
    if organizer_raw is not None:
        calendar_ref = _optional_text(_mapping(organizer_raw, "event organizer"), "email")

    try:
        return CalendarEvent(
            event_ref=qualify_event_ref(account_ref, provider_ref),
            account_ref=account_ref,
            title=title,
            start=start,
            end=end,
            is_all_day=start_all_day,
            description=_optional_text(payload, "description"),
            location=_optional_text(payload, "location"),
            calendar_ref=calendar_ref,
            status=_optional_text(payload, "status"),
            attendees=attendees,
            url=_optional_text(payload, "htmlLink"),
        )
    except ValueError as exc:
        raise _invalid("an invalid event") from exc


def _next_page_token(payload: Mapping[str, Any]) -> str | None:
    value = payload.get("nextPageToken")
    if value is None:
        return None
    token = _required_text(value, "next page token")
    if len(token) > 4_096 or any(not 0x21 <= ord(character) <= 0x7E for character in token):
        raise _invalid("an invalid next page token")
    return token


def decode_calendar_event_page(
    raw: object,
    account_ref: str,
    *,
    query: str | None,
    start_at: datetime,
    end_at: datetime,
    limit: int,
) -> CalendarEventPage:
    payload = _mapping(raw, "event page")
    items = payload.get("items")
    if items is None:
        events: tuple[CalendarEvent, ...] = ()
    elif isinstance(items, list):
        events = tuple(decode_calendar_event(item, account_ref) for item in items)
    else:
        raise _invalid("an invalid event list")
    next_page_token = _next_page_token(payload)
    try:
        next_page_ref = (
            CalendarPageRef(
                account_ref=account_ref,
                provider_token=next_page_token,
                query=query,
                start_at=start_at,
                end_at=end_at,
                limit=limit,
            )
            if next_page_token is not None
            else None
        )
        return CalendarEventPage(
            events=events,
            next_page_ref=next_page_ref,
            has_more=next_page_ref is not None,
        )
    except ValueError as exc:
        raise _invalid("an invalid event page continuation") from exc


def decode_calendar_mutation(raw: object) -> CalendarMutationResult:
    payload = _mapping(raw, "mutation receipt")
    try:
        return CalendarMutationResult(
            event_ref=_required_text(payload.get("id"), "event reference"),
            summary=_optional_text(payload, "summary"),
            url=_optional_text(payload, "htmlLink"),
        )
    except ValueError as exc:
        raise _invalid("an invalid mutation receipt") from exc
