from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httplib2
from google.auth.exceptions import GoogleAuthError, RefreshError
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from requests import RequestException

from arden.integrations.base import IntegrationConnectionError, IntegrationOperationError, IntegrationProviderError
from arden.integrations.calendar.models import (
    CalendarAccount,
    CalendarCreateDraft,
    CalendarDeleteDraft,
    CalendarEventPage,
    CalendarMutationResult,
    CalendarPageRef,
    CalendarUpdateDraft,
    qualify_event_ref,
)
from arden.integrations.calendar.payloads import (
    decode_calendar_account,
    decode_calendar_event_page,
    decode_calendar_mutation,
)
from arden.integrations.google_auth.accounts import validate_google_account_email
from arden.integrations.google_auth.auth import SCOPES_CALENDAR, get_google_credentials
from arden.logging import get_logger

_logger = get_logger(__name__)
_KNOWN_PROVIDER_ERRORS = (HttpError, GoogleAuthError, RequestException, OSError, httplib2.ServerNotFoundError)


def _time_payload(value: datetime, *, all_day: bool) -> dict[str, str]:
    if all_day:
        return {"date": value.strftime("%Y-%m-%d")}
    return {"dateTime": value.isoformat()}


class GoogleCalendar:
    name = "calendar"

    def __init__(
        self,
        account_ref: str,
        token_path: Path,
        days_back: int = 7,
        days_ahead: int = 30,
    ):
        self.account = CalendarAccount(account_ref)
        self.token_path = token_path
        self.days_back = days_back
        self.days_ahead = days_ahead
        self._service = None
        self._creds = None

    def _get_credentials(self):
        if self._creds is None or not self._creds.valid:
            self._creds = get_google_credentials(
                self.token_path,
                require_scopes=SCOPES_CALENDAR,
                integration_id="calendar",
            )
        return self._creds

    def _get_service(self):
        if self._service is None:
            self._service = build("calendar", "v3", credentials=self._get_credentials())
        return self._service

    def _execute(self, request: Any, operation: str) -> object:
        try:
            return request.execute()
        except (IntegrationConnectionError, IntegrationOperationError):
            raise
        except RefreshError as exc:
            raise IntegrationConnectionError(
                integration_id="calendar",
                reason="auth_required",
                detail=f"Calendar authentication failed for {self.account.account_ref}. Reconnect that account.",
                retry_safe=True,
                account_ref=self.account.account_ref,
            ) from exc
        except _KNOWN_PROVIDER_ERRORS as exc:
            _logger.warning("Calendar %s request failed for %s", operation, self.account.account_ref)
            raise IntegrationProviderError(integration_label="Calendar", cause=exc) from exc

    def verify_connection(self) -> None:
        raw = self._execute(
            self._get_service().calendars().get(calendarId="primary"),
            "identity",
        )
        provider_account = decode_calendar_account(raw)
        if provider_account.account_ref.casefold() != self.account.account_ref.casefold():
            raise IntegrationConnectionError(
                integration_id="calendar",
                reason="auth_required",
                detail=f"Calendar authorization does not belong to {self.account.account_ref}. Reconnect that account.",
                retry_safe=True,
                account_ref=self.account.account_ref,
            )

    def _list_events(
        self,
        *,
        query: str | None,
        start_at: datetime,
        end_at: datetime,
        limit: int,
        page_ref: CalendarPageRef | None = None,
    ) -> CalendarEventPage:
        if page_ref is not None:
            if page_ref.account_ref.casefold() != self.account.account_ref.casefold():
                raise IntegrationOperationError(
                    code="invalid_ref",
                    safe_message="Calendar page_ref belongs to a different account.",
                )
            if (
                page_ref.query != query
                or page_ref.start_at != start_at
                or page_ref.end_at != end_at
                or page_ref.limit != limit
            ):
                raise IntegrationOperationError(
                    code="invalid_ref",
                    safe_message="Calendar page_ref does not match this exact search window.",
                )
        request = self._get_service().events().list(
            calendarId="primary",
            timeMin=start_at.isoformat(),
            timeMax=end_at.isoformat(),
            maxResults=limit,
            singleEvents=True,
            orderBy="startTime",
            **({"q": query} if query is not None else {}),
            **({"pageToken": page_ref.provider_token} if page_ref is not None else {}),
        )
        page = decode_calendar_event_page(
            self._execute(request, "event search"),
            self.account.account_ref,
            query=query,
            start_at=start_at,
            end_at=end_at,
            limit=limit,
        )
        if page_ref is not None and page.next_page_ref is not None:
            if page.next_page_ref.provider_token == page_ref.provider_token:
                raise IntegrationOperationError(
                    code="invalid_response",
                    safe_message="Calendar returned the current page_ref again.",
                )
        return page

    def get_upcoming(
        self, days: int = 7, limit: int = 20, page_ref: CalendarPageRef | None = None
    ) -> CalendarEventPage:
        now = datetime.now(tz=UTC)
        return self._list_events(
            query=None,
            start_at=now,
            end_at=now + timedelta(days=days),
            limit=limit,
            page_ref=page_ref,
        )

    def get_past(
        self, days: int = 7, limit: int = 20, page_ref: CalendarPageRef | None = None
    ) -> CalendarEventPage:
        now = datetime.now(tz=UTC)
        return self._list_events(
            query=None,
            start_at=now - timedelta(days=days),
            end_at=now,
            limit=limit,
            page_ref=page_ref,
        )

    def search(
        self, query: str, limit: int = 20, page_ref: CalendarPageRef | None = None
    ) -> CalendarEventPage:
        now = datetime.now(tz=UTC)
        return self._list_events(
            query=query,
            start_at=now - timedelta(days=self.days_back),
            end_at=now + timedelta(days=self.days_ahead),
            limit=limit,
            page_ref=page_ref,
        )

    def list_window(
        self,
        *,
        days_back: int,
        days_forward: int,
        limit: int,
        page_ref: CalendarPageRef | None = None,
    ) -> CalendarEventPage:
        now = datetime.now(tz=UTC)
        return self._list_events(
            query=None,
            start_at=now - timedelta(days=days_back),
            end_at=now + timedelta(days=days_forward),
            limit=limit,
            page_ref=page_ref,
        )

    def continue_page(self, page_ref: CalendarPageRef) -> CalendarEventPage:
        return self._list_events(
            query=page_ref.query,
            start_at=page_ref.start_at,
            end_at=page_ref.end_at,
            limit=page_ref.limit,
            page_ref=page_ref,
        )

    def create_event(self, draft: CalendarCreateDraft) -> CalendarMutationResult:
        event_body: dict[str, Any] = {
            "summary": draft.summary,
            "start": _time_payload(draft.start, all_day=draft.all_day),
            "end": _time_payload(draft.end, all_day=draft.all_day),
        }
        if draft.description is not None:
            event_body["description"] = draft.description
        if draft.location is not None:
            event_body["location"] = draft.location
        if draft.attendees:
            event_body["attendees"] = [{"email": email} for email in draft.attendees]

        request = self._get_service().events().insert(calendarId="primary", body=event_body)
        return decode_calendar_mutation(self._execute(request, "event creation"))

    def delete_event(self, event_id: str) -> CalendarMutationResult:
        request = self._get_service().events().delete(calendarId="primary", eventId=event_id)
        self._execute(request, "event deletion")
        return CalendarMutationResult(event_ref=event_id)

    def update_event(
        self,
        event_id: str,
        draft: CalendarUpdateDraft,
    ) -> CalendarMutationResult:
        service = self._get_service()
        event_body: dict[str, Any] = {}

        if "summary" in draft.changed_fields:
            event_body["summary"] = draft.summary
        if "description" in draft.changed_fields:
            event_body["description"] = draft.description
        if "location" in draft.changed_fields:
            event_body["location"] = draft.location
        if "attendees" in draft.changed_fields:
            event_body["attendees"] = [{"email": email} for email in draft.attendees]
        if {"start", "end", "all_day"} <= draft.changed_fields:
            start = cast("datetime", draft.start)
            end = cast("datetime", draft.end)
            all_day = cast("bool", draft.all_day)
            event_body["start"] = _time_payload(start, all_day=all_day)
            event_body["end"] = _time_payload(end, all_day=all_day)

        request = service.events().patch(calendarId="primary", eventId=event_id, body=event_body)
        result = decode_calendar_mutation(self._execute(request, "event update"))
        if result.event_ref != event_id:
            raise IntegrationOperationError(
                code="invalid_response",
                safe_message="Calendar provider returned a receipt for a different event.",
            )
        return result


class MultiCalendarSource:
    name = "calendar"

    def __init__(
        self,
        account_sources: list[tuple[str, Path]],
        days_back: int,
        days_ahead: int,
    ):
        account_keys = [account_ref.casefold() for account_ref, _token_path in account_sources]
        if len(account_keys) != len(set(account_keys)):
            raise IntegrationOperationError(
                code="configuration_error",
                safe_message="Calendar account configuration contains a duplicate account.",
            )

        sources = tuple(
            GoogleCalendar(
                account_ref=account_ref,
                token_path=token_path,
                days_back=days_back,
                days_ahead=days_ahead,
            )
            for account_ref, token_path in account_sources
        )
        for source in sources:
            source._get_credentials()
        self.sources = sources

    def _require_sources(self) -> None:
        if not self.sources:
            raise IntegrationConnectionError(
                integration_id="calendar",
                reason="not_configured",
                detail="No Google Calendar account is connected.",
            )

    def verify_connection(self) -> None:
        self._require_sources()
        for source in self.sources:
            source.verify_connection()

    def verify_account(self, account_ref: str) -> None:
        self._source_for_account(account_ref).verify_connection()

    def list_accounts(self) -> tuple[CalendarAccount, ...]:
        return tuple(source.account for source in self.sources)

    def _collect(
        self,
        fetch: Callable[[GoogleCalendar], CalendarEventPage],
        limit: int,
    ) -> CalendarEventPage:
        self._require_sources()
        if len(self.sources) == 1:
            return fetch(self.sources[0])
        events = []
        has_more = False
        for source in self.sources:
            page = fetch(source)
            events.extend(page.events)
            has_more = has_more or page.has_more
        events.sort(key=lambda event: event.start)
        if len(events) > limit:
            has_more = True
        return CalendarEventPage(
            events=tuple(events[:limit]),
            has_more=has_more,
        )

    def get_upcoming(
        self, days: int = 7, limit: int = 20, account_ref: str | None = None
    ) -> CalendarEventPage:
        self._require_sources()
        if account_ref is not None:
            return self._source_for_account(account_ref).get_upcoming(days=days, limit=limit)
        per_account = max(limit // len(self.sources), 5)
        return self._collect(lambda source: source.get_upcoming(days=days, limit=per_account), limit)

    def get_past(self, days: int = 7, limit: int = 20, account_ref: str | None = None) -> CalendarEventPage:
        self._require_sources()
        if account_ref is not None:
            return self._source_for_account(account_ref).get_past(days=days, limit=limit)
        per_account = max(limit // len(self.sources), 5)
        return self._collect(lambda source: source.get_past(days=days, limit=per_account), limit)

    def search(
        self, query: str, limit: int = 20, account_ref: str | None = None
    ) -> CalendarEventPage:
        self._require_sources()
        if account_ref is not None:
            return self._source_for_account(account_ref).search(query, limit=limit)
        per_account = max(limit // len(self.sources), 5)
        return self._collect(lambda source: source.search(query, limit=per_account), limit)

    def list_window(
        self,
        *,
        days_back: int,
        days_forward: int,
        limit: int,
        account_ref: str | None = None,
    ) -> CalendarEventPage:
        self._require_sources()
        if account_ref is not None:
            return self._source_for_account(account_ref).list_window(
                days_back=days_back,
                days_forward=days_forward,
                limit=limit,
            )
        per_account = max(limit // len(self.sources), 5)
        return self._collect(
            lambda source: source.list_window(
                days_back=days_back, days_forward=days_forward, limit=per_account
            ),
            limit,
        )

    def continue_page(self, page_ref: CalendarPageRef) -> CalendarEventPage:
        self._require_sources()
        return self._source_for_account(page_ref.account_ref).continue_page(page_ref)

    def create_event(self, draft: CalendarCreateDraft) -> CalendarMutationResult:
        source = self._source_for_account(draft.account_ref)
        result = source.create_event(draft)
        return replace(result, event_ref=qualify_event_ref(source.account.account_ref, result.event_ref))

    def delete_event(self, draft: CalendarDeleteDraft) -> CalendarMutationResult:
        source, provider_id = self._resolve_event_ref(draft.event_ref)
        result = source.delete_event(provider_id)
        return replace(result, event_ref=qualify_event_ref(source.account.account_ref, result.event_ref))

    def update_event(self, draft: CalendarUpdateDraft) -> CalendarMutationResult:
        source, provider_id = self._resolve_event_ref(draft.event_ref)
        result = source.update_event(provider_id, draft)
        return replace(result, event_ref=qualify_event_ref(source.account.account_ref, result.event_ref))

    def _source_for_account(self, account_ref: str) -> GoogleCalendar:
        if not account_ref:
            raise IntegrationOperationError(
                code="invalid_ref",
                safe_message="Calendar create requires an exact account email.",
            )
        for source in self.sources:
            if source.account.account_ref.casefold() == account_ref.casefold():
                return source
        available = ", ".join(account.account_ref for account in self.list_accounts())
        detail = f" Available: {available}" if available else ""
        raise IntegrationOperationError(
            code="invalid_ref",
            safe_message=f"Calendar account not found: {account_ref}.{detail}",
        )

    def _resolve_event_ref(self, event_ref: str) -> tuple[GoogleCalendar, str]:
        account_ref, separator, provider_ref = event_ref.partition(":")
        if not separator or not provider_ref:
            raise IntegrationOperationError(
                code="invalid_ref",
                safe_message="Calendar event references must be account-qualified.",
            )
        try:
            validate_google_account_email(account_ref)
        except ValueError as exc:
            raise IntegrationOperationError(
                code="invalid_ref",
                safe_message="Calendar event references must be account-qualified.",
            ) from exc
        return self._source_for_account(account_ref), provider_ref
