from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from arden.context.models import SessionState
from arden.integrations.base import IntegrationOperationError, IntegrationProviderError
from arden.integrations.calendar.client import GoogleCalendar, MultiCalendarSource
from arden.integrations.calendar.models import (
    CalendarAccount,
    CalendarCreateDraft,
    CalendarDeleteDraft,
    CalendarEvent,
    CalendarEventPage,
    CalendarMutationResult,
    CalendarPageRef,
    CalendarUpdateDraft,
    qualify_event_ref,
)
from arden.integrations.calendar.tools import (
    CalendarCreateEventInput,
    CalendarDeleteEventInput,
    CalendarEditEventInput,
    CalendarSearchInput,
    approve_calendar_create_event,
    approve_calendar_edit_event,
    calendar_create_event,
    calendar_delete_event,
    calendar_edit_event,
    calendar_search,
)
from arden.integrations.mutations import IDEMPOTENCY_LEDGER_SERVICE, IdempotencyLedger
from arden.tools.core.context import IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.registry import ToolRegistry


def _item(
    _source: str,
    event_ref: str,
    title: str | None,
    *,
    metadata: dict | None = None,
) -> CalendarEvent:
    now = datetime(2026, 7, 10, tzinfo=UTC)
    details = metadata or {}
    account_ref, separator, _provider_ref = event_ref.partition(":")
    assert separator
    start = datetime.fromisoformat(details.get("start", now.isoformat()))
    return CalendarEvent(
        event_ref=event_ref,
        account_ref=account_ref,
        title=title,
        start=start,
        end=start + timedelta(hours=1),
        is_all_day=False,
        calendar_ref=details.get("calendar_id"),
        url=details.get("html_link"),
    )


def _execution(service: object, tool_name: str) -> ToolExecution:
    return ToolExecution(
        tool_id="call-1",
        tool_name=tool_name,
        ctx=ToolContext(
            session_state=SessionState(session_id="session-1", started_at=datetime(2026, 7, 10, tzinfo=UTC)),
            registry=ToolRegistry(),
            run=RunContext(run_id="run-1"),
            io=IOBridge(),
            services={"calendar": service},
        ),
    )


class FakeCalendarSource(MultiCalendarSource):
    def __init__(self, events: list[CalendarEvent]):
        self.events = tuple(events)

    def search(self, query: str, limit: int = 50, account_ref: str | None = None) -> CalendarEventPage:
        assert account_ref is None
        return CalendarEventPage(events=self.events[:limit])

    def list_window(
        self,
        *,
        days_back: int,
        days_forward: int,
        limit: int,
        account_ref: str | None = None,
    ) -> CalendarEventPage:
        assert days_back == 0
        assert days_forward > 0
        assert account_ref is None
        return CalendarEventPage(events=self.events[:limit])


class FailingGoogleService:
    def calendars(self):
        return self

    def events(self):
        return self

    def insert(self, **kwargs):
        return self

    def get(self, **kwargs):
        return self

    def execute(self):
        raise RuntimeError("provider secret")


class ReturningGoogleService:
    def __init__(self, result: dict):
        self.result = result

    def events(self):
        return self

    def calendars(self):
        return self

    def insert(self, **kwargs):
        return self

    def list(self, **kwargs):
        return self

    def get(self, **kwargs):
        return self

    def execute(self):
        return self.result


class StaticRequest:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class RecordingUpdateService:
    def __init__(self, receipt_id: str = "event-1"):
        self.receipt_id = receipt_id
        self.patch_body = None
        self.patch_calls = 0

    def events(self):
        return self

    def get(self, **_kwargs):
        raise AssertionError("calendar update must not read provider state")

    def patch(self, **kwargs):
        self.patch_calls += 1
        self.patch_body = kwargs["body"]
        return StaticRequest({"id": self.receipt_id, "summary": "Review"})


def _google_calendar() -> GoogleCalendar:
    return GoogleCalendar("me@example.test", Path("/unused/calendar.json"))


def _create_draft(**overrides) -> CalendarCreateDraft:
    values = {
        "account_ref": "me@example.test",
        "summary": "Review",
        "start": datetime(2026, 7, 20, 9, tzinfo=UTC),
        "end": datetime(2026, 7, 20, 10, tzinfo=UTC),
        "description": None,
        "location": None,
        "attendees": (),
        "all_day": False,
    }
    values.update(overrides)
    return CalendarCreateDraft(**values)


def test_calendar_create_propagates_unknown_provider_failure(monkeypatch):
    source = _google_calendar()
    monkeypatch.setattr(source, "_get_service", lambda: FailingGoogleService())

    with pytest.raises(RuntimeError, match="provider secret"):
        source.create_event(_create_draft())


def test_calendar_create_sanitizes_known_provider_failure(monkeypatch):
    class OfflineService(FailingGoogleService):
        def execute(self):
            raise OSError("provider secret")

    source = _google_calendar()
    monkeypatch.setattr(source, "_get_service", lambda: OfflineService())
    with pytest.raises(IntegrationProviderError) as exc_info:
        source.create_event(_create_draft())

    assert exc_info.value.code == "provider_error"
    assert "provider secret" not in str(exc_info.value)


def test_calendar_identity_lookup_does_not_rewrap_typed_operation_error(monkeypatch):
    expected = IntegrationOperationError(
        code="rate_limited",
        safe_message="Calendar identity lookup was rate limited.",
        retryable=True,
    )

    class TypedFailureService:
        def calendars(self):
            return self

        def get(self, **_kwargs):
            return self

        def execute(self):
            raise expected

    source = _google_calendar()
    monkeypatch.setattr(source, "_get_service", lambda: TypedFailureService())

    with pytest.raises(IntegrationOperationError) as exc_info:
        source.verify_connection()

    assert exc_info.value is expected


def test_calendar_create_requires_provider_event_reference(monkeypatch):
    source = _google_calendar()
    monkeypatch.setattr(source, "_get_service", lambda: ReturningGoogleService({"summary": "Review"}))

    with pytest.raises(IntegrationOperationError, match="invalid event reference") as exc_info:
        source.create_event(_create_draft())

    assert exc_info.value.code == "invalid_response"


@pytest.mark.parametrize(
    "receipt",
    [
        {"id": "event-1", "summary": "Unsafe\nSummary"},
        {"id": "event-1", "htmlLink": "javascript:alert(1)"},
        {"id": "event-1", "htmlLink": "https://calendar.example:99999/event-1"},
    ],
)
def test_calendar_create_rejects_unsafe_provider_receipt(monkeypatch, receipt):
    source = _google_calendar()
    monkeypatch.setattr(source, "_get_service", lambda: ReturningGoogleService(receipt))

    with pytest.raises(IntegrationOperationError) as exc_info:
        source.create_event(_create_draft())

    assert exc_info.value.code == "invalid_response"


def test_calendar_read_rejects_invalid_provider_time():
    source = _google_calendar()
    source._service = ReturningGoogleService(
        {
            "items": [
                {
                    "id": "event-1",
                    "start": {"dateTime": "not-a-time"},
                    "end": {"dateTime": "2026-07-20T10:00:00+00:00"},
                }
            ]
        }
    )

    with pytest.raises(IntegrationOperationError, match="invalid event start time") as exc_info:
        source.get_upcoming()

    assert exc_info.value.code == "invalid_response"


@pytest.mark.parametrize(
    "response",
    [
        [],
        {"items": {}},
        {"items": ["event"]},
        {
            "items": [
                {
                    "id": "event-1",
                    "start": {"dateTime": "2026-07-20T09:00:00+00:00"},
                    "end": {"dateTime": "2026-07-20T10:00:00+00:00"},
                    "attendees": [{}],
                }
            ]
        },
    ],
)
def test_calendar_read_rejects_malformed_successful_page(response):
    source = _google_calendar()
    source._service = ReturningGoogleService(response)

    with pytest.raises(IntegrationOperationError) as exc_info:
        source.get_upcoming()

    assert exc_info.value.code == "invalid_response"


def test_calendar_read_returns_immutable_typed_page():
    source = _google_calendar()
    source._service = ReturningGoogleService(
        {
            "items": [
                {
                    "id": "event-1",
                    "summary": "Review",
                    "start": {"dateTime": "2026-07-20T09:00:00+00:00"},
                    "end": {"dateTime": "2026-07-20T10:00:00+00:00"},
                }
            ],
            "nextPageToken": "provider-secret-token",
        }
    )

    page = source.get_upcoming()

    assert page.has_more
    assert page.next_page_ref is not None
    assert page.next_page_ref.account_ref == "me@example.test"
    assert page.next_page_ref.provider_token == "provider-secret-token"
    assert page.events[0].event_ref == "me@example.test:event-1"
    with pytest.raises(FrozenInstanceError):
        page.events[0].title = "Changed"


def test_calendar_connection_rejects_malformed_account_identity():
    source = _google_calendar()
    source._service = ReturningGoogleService({"id": 123})

    with pytest.raises(IntegrationOperationError) as exc_info:
        source.verify_connection()

    assert exc_info.value.code == "invalid_response"


@pytest.mark.parametrize(
    "field_value",
    [
        {"summary": "Unsafe\nTitle"},
        {"location": "Unsafe\nLocation"},
        {"htmlLink": "javascript:alert(1)"},
    ],
)
def test_calendar_read_rejects_unsafe_model_strings(field_value):
    source = _google_calendar()
    source._service = ReturningGoogleService(
        {
            "items": [
                {
                    "id": "event-1",
                    "start": {"dateTime": "2026-07-20T09:00:00+00:00"},
                    "end": {"dateTime": "2026-07-20T10:00:00+00:00"},
                    **field_value,
                }
            ]
        }
    )

    with pytest.raises(IntegrationOperationError) as exc_info:
        source.get_upcoming()

    assert exc_info.value.code == "invalid_response"


def test_calendar_missing_summary_stays_optional():
    source = _google_calendar()
    source._service = ReturningGoogleService(
        {
            "items": [
                {
                    "id": "event-1",
                    "start": {"dateTime": "2026-07-20T09:00:00+00:00"},
                    "end": {"dateTime": "2026-07-20T10:00:00+00:00"},
                }
            ]
        }
    )

    assert source.get_upcoming().events[0].title is None


def test_calendar_rejects_repeated_provider_page_token():
    source = _google_calendar()
    source._service = ReturningGoogleService({"items": [], "nextPageToken": "same-page"})

    initial = source.search("review", limit=10)

    assert initial.next_page_ref is not None
    with pytest.raises(IntegrationOperationError, match="current page_ref") as exc_info:
        source.continue_page(initial.next_page_ref)

    assert exc_info.value.code == "invalid_response"


def test_calendar_qualification_rejects_empty_provider_ref():
    with pytest.raises(ValueError, match="non-blank"):
        qualify_event_ref("me@example.test", "me@example.test:")


def test_calendar_update_sends_only_the_typed_patch(monkeypatch):
    source = _google_calendar()
    service = RecordingUpdateService()
    monkeypatch.setattr(source, "_get_service", lambda: service)
    draft = CalendarUpdateDraft(
        event_ref="me@example.test:event-1",
        changed_fields=frozenset({"description", "attendees"}),
        description="Updated notes",
        attendees=(),
    )

    result = source.update_event("event-1", draft)

    assert result.event_ref == "event-1"
    assert service.patch_body == {"description": "Updated notes", "attendees": []}
    assert service.patch_calls == 1


def test_calendar_temporal_update_patches_exact_atomic_triad(monkeypatch):
    source = _google_calendar()
    service = RecordingUpdateService()
    monkeypatch.setattr(source, "_get_service", lambda: service)
    draft = CalendarUpdateDraft(
        event_ref="me@example.test:event-1",
        changed_fields=frozenset({"start", "end", "all_day"}),
        start=datetime(2026, 7, 20, tzinfo=UTC),
        end=datetime(2026, 7, 22, tzinfo=UTC),
        all_day=True,
    )

    source.update_event("event-1", draft)

    assert service.patch_body == {
        "start": {"date": "2026-07-20"},
        "end": {"date": "2026-07-22"},
    }
    assert service.patch_calls == 1


def test_calendar_update_rejects_receipt_for_another_event(monkeypatch):
    source = _google_calendar()
    service = RecordingUpdateService(receipt_id="event-2")
    monkeypatch.setattr(source, "_get_service", lambda: service)
    draft = CalendarUpdateDraft(
        event_ref="me@example.test:event-1",
        changed_fields=frozenset({"summary"}),
        summary="Updated",
    )

    with pytest.raises(IntegrationOperationError, match="different event") as exc_info:
        source.update_event("event-1", draft)

    assert exc_info.value.code == "invalid_response"


def test_calendar_create_rejects_invalid_supplied_end():
    with pytest.raises(ValidationError, match="calendar end must be an ISO-8601 timestamp"):
        CalendarCreateEventInput(
            summary="Review",
            start="2026-07-20T09:00:00+04:00",
            end="not-a-time",
            account="me@example.test",
            idempotency_key="calendar-review-invalid-end",
        )


def test_calendar_edit_rejects_noop():
    with pytest.raises(ValidationError, match="must change at least one field"):
        CalendarEditEventInput(
            event_ref="me@example.test:event-123",
            idempotency_key="calendar-edit-noop",
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"start": "2026-07-20T09:00:00+04:00"},
        {"end": "2026-07-20T10:00:00+04:00"},
        {"all_day": True},
        {
            "start": "2026-07-20T09:00:00+04:00",
            "end": "2026-07-20T10:00:00+04:00",
        },
    ],
)
def test_calendar_edit_requires_complete_temporal_triad(changes):
    with pytest.raises(ValidationError, match="requires start, end, and all_day together"):
        CalendarEditEventInput(
            event_ref="me@example.test:event-123",
            idempotency_key="calendar-edit-temporal",
            **changes,
        )


@pytest.mark.parametrize("query", ["", "   ", " review "])
def test_calendar_search_rejects_inexact_query(query):
    with pytest.raises(ValidationError, match="query must be exact and non-blank"):
        CalendarSearchInput(query=query)


def test_calendar_search_bounds_query():
    with pytest.raises(ValidationError, match="at most 500 characters"):
        CalendarSearchInput(query="x" * 501)


def test_calendar_search_rejects_empty_listing_window():
    with pytest.raises(ValidationError, match="requires a non-zero time range"):
        CalendarSearchInput(days_forward=0, days_back=0)


def test_calendar_search_accepts_only_the_exact_page_ref_for_continuation():
    page_ref = CalendarPageRef(
        account_ref="me@example.test",
        provider_token="page-2",
        query="review",
        start_at=datetime(2026, 7, 20, 9, tzinfo=UTC),
        end_at=datetime(2026, 7, 21, 9, tzinfo=UTC),
        limit=10,
    ).encode()

    assert CalendarSearchInput(page_ref=page_ref).page_ref == page_ref
    with pytest.raises(ValidationError, match="page_ref must be passed alone"):
        CalendarSearchInput(query="review", page_ref=page_ref)
    with pytest.raises(ValidationError, match="exact Calendar page reference"):
        CalendarSearchInput(page_ref="page-2")


@pytest.mark.parametrize("input_type", [CalendarCreateEventInput, CalendarEditEventInput])
def test_calendar_mutation_rejects_duplicate_attendees(input_type):
    common = {
        "attendees": ["Ada@example.test", "ada@example.test"],
        "idempotency_key": "calendar-attendee-duplicate",
    }
    if input_type is CalendarCreateEventInput:
        common.update(
            summary="Review",
            start="2026-07-20T09:00:00+04:00",
            account="me@example.test",
        )
    else:
        common["event_ref"] = "me@example.test:event-123"

    with pytest.raises(ValidationError, match="duplicate calendar attendee"):
        input_type(**common)


def test_calendar_mutation_bounds_attendees():
    with pytest.raises(ValidationError, match="at most 200 items"):
        CalendarCreateEventInput(
            summary="Review",
            start="2026-07-20T09:00:00+04:00",
            account="me@example.test",
            attendees=[f"person-{index}@example.test" for index in range(201)],
            idempotency_key="calendar-attendee-limit",
        )


@pytest.mark.asyncio
async def test_calendar_create_approval_shows_complete_mutation():
    args = CalendarCreateEventInput(
        summary="Review",
        start="2026-07-20T09:00:00+04:00",
        end="2026-07-20T10:00:00+04:00",
        description="Discuss launch",
        location="Room 2",
        attendees=["ada@example.test", "grace@example.test"],
        all_day=False,
        account="me@example.test",
        idempotency_key="calendar-review-approval",
    )

    approval = await approve_calendar_create_event(_execution(FakeCalendarSource([]), "create"), args)

    assert approval is not None
    assert approval.preview == (
        "Account: me@example.test\n"
        "All day: no\n"
        "Start: 2026-07-20T09:00:00+04:00\n"
        "End: 2026-07-20T10:00:00+04:00\n"
        "Description: Discuss launch\n"
        "Location: Room 2\n"
        "Attendees: ada@example.test, grace@example.test"
    )


@pytest.mark.asyncio
async def test_calendar_create_approval_shows_all_day_exclusive_end():
    args = CalendarCreateEventInput(
        summary="Retreat",
        start="2026-07-20T00:00:00+04:00",
        end="2026-07-22T00:00:00+04:00",
        all_day=True,
        account="me@example.test",
        idempotency_key="calendar-retreat-approval",
    )

    approval = await approve_calendar_create_event(_execution(FakeCalendarSource([]), "create"), args)

    assert "All day: yes" in approval.preview
    assert "Start: 2026-07-20" in approval.preview
    assert "End: 2026-07-22 (exclusive)" in approval.preview


@pytest.mark.asyncio
async def test_calendar_edit_approval_shows_explicit_clears():
    args = CalendarEditEventInput(
        event_ref="me@example.test:event-123",
        summary="",
        description="",
        location="",
        attendees=[],
        start="2026-07-20T09:00:00+04:00",
        end="2026-07-20T10:00:00+04:00",
        all_day=False,
        idempotency_key="calendar-edit-clear",
    )

    approval = await approve_calendar_edit_event(_execution(FakeCalendarSource([]), "edit"), args)

    assert approval is not None
    assert approval.preview == (
        "Event: me@example.test:event-123\n"
        "Title: (clear)\n"
        "All day: no\n"
        "Start: 2026-07-20T09:00:00+04:00\n"
        "End: 2026-07-20T10:00:00+04:00\n"
        "Description: (clear)\n"
        "Location: (clear)\n"
        "Attendees: (clear)"
    )


@pytest.mark.asyncio
async def test_calendar_create_maps_provider_failure_to_typed_result(tmp_path):
    source = FakeCalendarSource([])

    def fail_create(_draft):
        raise IntegrationOperationError(
            code="rate_limited",
            safe_message="Calendar provider request failed.",
            retryable=True,
        )

    source.create_event = fail_create
    execution = _execution(source, "calendar_create_event")
    execution.ctx.services[IDEMPOTENCY_LEDGER_SERVICE] = IdempotencyLedger(tmp_path / "idempotency.sqlite3")
    result = await calendar_create_event(
        execution,
        CalendarCreateEventInput(
            summary="Review",
            start="2026-07-20T09:00:00+04:00",
            account="me@example.test",
            idempotency_key="calendar-review-1",
        ),
    )

    assert result.is_error
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "rate_limited"


@pytest.mark.asyncio
async def test_calendar_create_keeps_case_insensitive_qualified_ref_once(tmp_path):
    source = FakeCalendarSource([])
    source.create_event = lambda _draft: CalendarMutationResult(
        event_ref="me@example.test:event-123",
        summary="Review",
        url="https://calendar.example/event-123",
    )
    execution = _execution(source, "calendar_create_event")
    execution.ctx.services[IDEMPOTENCY_LEDGER_SERVICE] = IdempotencyLedger(tmp_path / "idempotency.sqlite3")

    result = await calendar_create_event(
        execution,
        CalendarCreateEventInput(
            summary="Review",
            start="2026-07-20T09:00:00+04:00",
            account="ME@example.test",
            idempotency_key="calendar-review-ref-1",
        ),
    )

    assert result.data == {"event_ref": "me@example.test:event-123"}
    assert result.outcome is not None and result.outcome.effect is not None
    assert result.outcome.effect.after_ref == "me@example.test:event-123"
    assert result.outcome.receipt == "me@example.test:event-123"
    assert result.content == ("Created event: Review `[me@example.test:event-123]`\nhttps://calendar.example/event-123")


@pytest.mark.asyncio
async def test_calendar_update_uses_typed_qualified_receipt(tmp_path):
    source = FakeCalendarSource([])
    source.update_event = lambda _draft: CalendarMutationResult(
        event_ref="me@example.test:event-123",
        summary="Updated review",
    )
    execution = _execution(source, "calendar_edit_event")
    execution.ctx.services[IDEMPOTENCY_LEDGER_SERVICE] = IdempotencyLedger(tmp_path / "idempotency.sqlite3")

    result = await calendar_edit_event(
        execution,
        CalendarEditEventInput(
            event_ref="me@example.test:event-123",
            summary="Updated review",
            idempotency_key="calendar-update-ref-1",
        ),
    )

    assert result.data == {"event_ref": "me@example.test:event-123"}
    assert result.outcome is not None and result.outcome.receipt == "me@example.test:event-123"
    assert result.content == "Updated event: Updated review `[me@example.test:event-123]`"


@pytest.mark.asyncio
async def test_calendar_delete_uses_typed_qualified_receipt(tmp_path):
    source = FakeCalendarSource([])
    source.delete_event = lambda _draft: CalendarMutationResult(event_ref="me@example.test:event-123")
    execution = _execution(source, "calendar_delete_event")
    execution.ctx.services[IDEMPOTENCY_LEDGER_SERVICE] = IdempotencyLedger(tmp_path / "idempotency.sqlite3")

    result = await calendar_delete_event(
        execution,
        CalendarDeleteEventInput(
            event_ref="me@example.test:event-123",
            idempotency_key="calendar-delete-ref-1",
        ),
    )

    assert result.data == {"event_ref": "me@example.test:event-123"}
    assert result.outcome is not None and result.outcome.receipt == "me@example.test:event-123"
    assert result.content == "Deleted event `[me@example.test:event-123]`"


@pytest.mark.asyncio
async def test_calendar_search_uses_event_id_title_and_html_link():
    source = FakeCalendarSource(
        [
            _item(
                "calendar",
                "primary@example.test:event-123",
                "Planning review",
                metadata={
                    "calendar_id": "organizer@example.test",
                    "start": "2026-07-10T09:00:00+00:00",
                    "html_link": "https://calendar.google.com/calendar/event?eid=event-123",
                },
            )
        ]
    )

    result = await calendar_search(
        _execution(source, "calendar_search"),
        CalendarSearchInput(query="Planning review"),
    )

    assert [ref.to_dict() for ref in result.source_refs] == [
        {
            "provider": "calendar",
            "kind": "event",
            "ref": "primary@example.test:event-123",
            "title": "Planning review",
            "url": "https://calendar.google.com/calendar/event?eid=event-123",
        }
    ]
    assert "2026-07-10T09:00:00+00:00 → 2026-07-10T10:00:00+00:00" in result.content


@pytest.mark.asyncio
async def test_calendar_search_makes_the_opaque_continuation_visible_to_the_agent():
    page_ref = CalendarPageRef(
        account_ref="me@example.test",
        provider_token="provider-secret-token",
        query="review",
        start_at=datetime(2026, 7, 20, 9, tzinfo=UTC),
        end_at=datetime(2026, 7, 21, 9, tzinfo=UTC),
        limit=10,
    )

    class PagedCalendarSource(FakeCalendarSource):
        def search(self, query: str, limit: int = 50, account_ref: str | None = None) -> CalendarEventPage:
            assert query == "review"
            assert limit == 10
            assert account_ref == "me@example.test"
            return CalendarEventPage(events=self.events, next_page_ref=page_ref, has_more=True)

    result = await calendar_search(
        _execution(PagedCalendarSource([_item("calendar", "me@example.test:event-1", "Review")]), "calendar_search"),
        CalendarSearchInput(query="review", account="me@example.test", limit=10),
    )

    encoded = page_ref.encode()
    assert f"Continue with page_ref: {encoded}" in result.content
    assert "provider-secret-token" not in result.content
    assert result.data["next_page_ref"] == encoded


@pytest.mark.asyncio
async def test_calendar_search_shows_all_day_exclusive_end():
    source = FakeCalendarSource(
        [
            CalendarEvent(
                event_ref="me@example.test:event-123",
                account_ref="me@example.test",
                title="Retreat",
                start=datetime(2026, 7, 10, tzinfo=UTC),
                end=datetime(2026, 7, 12, tzinfo=UTC),
                is_all_day=True,
            )
        ]
    )

    result = await calendar_search(_execution(source, "calendar_search"), CalendarSearchInput(query="Retreat"))

    assert "2026-07-10 → 2026-07-12 (all day; end exclusive)" in result.content


@pytest.mark.asyncio
async def test_calendar_search_owns_the_untitled_presentation():
    source = FakeCalendarSource([_item("calendar", "me@example.test:event-123", None)])

    result = await calendar_search(_execution(source, "calendar_search"), CalendarSearchInput(query="event"))

    assert "Untitled event" in result.content
    assert result.source_refs[0].title == "Untitled event"


@pytest.mark.asyncio
async def test_calendar_equal_local_ids_from_two_calendars_remain_distinct():
    source = FakeCalendarSource(
        [
            _item(
                "calendar",
                "first@example.test:same-id",
                "First",
                metadata={"calendar_id": "first@example.test", "start": "2026-07-10T09:00:00+00:00"},
            ),
            _item(
                "calendar",
                "second@example.test:same-id",
                "Second",
                metadata={"calendar_id": "second@example.test", "start": "2026-07-10T10:00:00+00:00"},
            ),
        ]
    )

    result = await calendar_search(_execution(source, "calendar_search"), CalendarSearchInput(query="same"))

    assert [ref.ref for ref in result.source_refs] == [
        "first@example.test:same-id",
        "second@example.test:same-id",
    ]


@pytest.mark.asyncio
async def test_multi_calendar_search_refs_round_trip_to_the_right_account():
    calls: list[tuple[str, str, str]] = []
    first_item = _item(
        "calendar",
        "first@example.test:series:instance",
        "First",
        metadata={"calendar_id": "organizer@example.test", "start": "2026-07-10T09:00:00+00:00"},
    )
    second_item = _item(
        "calendar",
        "second@example.test:series:instance",
        "Second",
        metadata={"calendar_id": "organizer@example.test", "start": "2026-07-10T10:00:00+00:00"},
    )

    def account_source(account: str, item: CalendarEvent):
        return SimpleNamespace(
            account=CalendarAccount(account),
            search=lambda _query, limit: CalendarEventPage(events=(item,)[:limit]),
            get_upcoming=lambda days, limit: CalendarEventPage(events=(item,)[:limit]),
            list_window=lambda **_kwargs: CalendarEventPage(events=(item,)),
            update_event=lambda event_id, _draft: (
                calls.append(("update", account, event_id)) or CalendarMutationResult(event_ref=event_id)
            ),
            delete_event=lambda event_id: (
                calls.append(("delete", account, event_id)) or CalendarMutationResult(event_ref=event_id)
            ),
        )

    source = object.__new__(MultiCalendarSource)
    source.sources = [
        account_source("first@example.test", first_item),
        account_source("second@example.test", second_item),
    ]

    result = await calendar_search(
        _execution(source, "calendar_search"),
        CalendarSearchInput(query="same", limit=10),
    )

    assert [ref.ref for ref in result.source_refs] == [
        "first@example.test:series:instance",
        "second@example.test:series:instance",
    ]
    assert "organizer@example.test:" not in result.content
    assert first_item.event_ref == "first@example.test:series:instance"

    listed = await calendar_search(
        _execution(source, "calendar_search"),
        CalendarSearchInput(days_forward=1, limit=10),
    )
    assert [ref.ref for ref in listed.source_refs] == [
        "first@example.test:series:instance",
        "second@example.test:series:instance",
    ]

    source.update_event(
        CalendarUpdateDraft(
            event_ref=result.source_refs[1].ref,
            changed_fields=frozenset({"summary"}),
            summary="Review",
        )
    )
    source.delete_event(CalendarDeleteDraft(event_ref=result.source_refs[0].ref))

    assert calls == [
        ("update", "second@example.test", "series:instance"),
        ("delete", "first@example.test", "series:instance"),
    ]
