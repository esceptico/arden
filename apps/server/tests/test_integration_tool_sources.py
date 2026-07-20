from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from ntrp.context.models import SessionState
from ntrp.integrations.base import IntegrationOperationError
from ntrp.integrations.calendar.client import GoogleCalendar, MultiCalendarSource
from ntrp.integrations.calendar.tools import CalendarInput, CreateCalendarEventInput, calendar, create_calendar_event
from ntrp.integrations.gmail.client import GmailSource, MultiGmailSource
from ntrp.integrations.gmail.tools import (
    EmailsInput,
    ReadEmailInput,
    SendEmailInput,
    approve_send_email,
    emails,
    read_email,
    send_email,
)
from ntrp.integrations.mutations import IDEMPOTENCY_LEDGER_SERVICE, IdempotencyLedger
from ntrp.integrations.slack.client import SlackClient, SlackThreadResult
from ntrp.integrations.slack.tools import (
    SlackPostBlocksInput,
    SlackSearchInput,
    SlackThreadInput,
    approve_slack_post_blocks,
    slack_search,
    slack_thread,
)
from ntrp.search.types import RawItem
from ntrp.tools.core.context import IOBridge, RunContext, ToolContext, ToolExecution
from ntrp.tools.core.registry import ToolRegistry


def _item(
    source: str,
    source_id: str,
    title: str,
    *,
    metadata: dict | None = None,
) -> RawItem:
    now = datetime(2026, 7, 10, tzinfo=UTC)
    return RawItem(
        source=source,
        source_id=source_id,
        title=title,
        content="result content",
        created_at=now,
        updated_at=now,
        metadata=metadata or {},
    )


def _execution(service_name: str, service: object, tool_name: str) -> ToolExecution:
    return ToolExecution(
        tool_id="call-1",
        tool_name=tool_name,
        ctx=ToolContext(
            session_state=SessionState(session_id="session-1", started_at=datetime(2026, 7, 10, tzinfo=UTC)),
            registry=ToolRegistry(),
            run=RunContext(run_id="run-1"),
            io=IOBridge(),
            services={service_name: service},
        ),
    )


class FakeSlackSource(SlackClient):
    def __init__(self, messages: list[RawItem]):
        self.messages = messages

    async def search_messages(self, *args, **kwargs) -> list[RawItem]:
        return self.messages

    async def read_thread(self, source_id: str) -> SlackThreadResult | None:
        return SlackThreadResult(text="thread body")


class FakeGmailSource(MultiGmailSource):
    def __init__(self, search_results: list[RawItem] | None = None):
        self.search_results = search_results or []

    def search(self, query: str, limit: int = 50) -> list[RawItem]:
        return self.search_results[:limit]

    def list_accounts(self) -> list[str]:
        return ["me@example.test"]

    def list_recent(self, days: int = 7, limit: int = 50) -> list[object]:
        return [
            SimpleNamespace(
                identity="message-recent",
                title="Recent message",
                source="gmail",
                account="me@example.test",
                preview=None,
            )
        ]

    def read(self, source_id: str) -> object | None:
        return SimpleNamespace(
            content="From: sender@example.test\nSubject: Read message\n\nBody",
            account="me@example.test",
        )


class FakeCalendarSource(MultiCalendarSource):
    def __init__(self, events: list[RawItem]):
        self.events = events

    def search(self, query: str, limit: int = 50) -> list[RawItem]:
        return self.events[:limit]


class FailingGoogleService:
    def users(self):
        return self

    def messages(self):
        return self

    def events(self):
        return self

    def send(self, **kwargs):
        return self

    def insert(self, **kwargs):
        return self

    def execute(self):
        raise RuntimeError("provider secret")


def test_gmail_send_raises_sanitized_provider_failure(monkeypatch):
    source = GmailSource()
    monkeypatch.setattr(source, "has_send_scope", lambda: True)
    monkeypatch.setattr(source, "_get_service", lambda: FailingGoogleService())

    with pytest.raises(RuntimeError, match="Gmail provider request failed") as exc_info:
        source.send(to="you@example.test", subject="Status", body="Ready")

    assert "provider secret" not in str(exc_info.value)


def test_calendar_create_raises_sanitized_provider_failure(monkeypatch):
    source = GoogleCalendar()
    monkeypatch.setattr(source, "_get_service", lambda: FailingGoogleService())

    with pytest.raises(RuntimeError, match="Calendar provider request failed") as exc_info:
        source.create_event(
            summary="Review",
            start=datetime(2026, 7, 20, 9, tzinfo=UTC),
        )

    assert "provider secret" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_send_email_maps_provider_failure_to_typed_result(tmp_path):
    source = FakeGmailSource()

    def fail_send(**kwargs):
        raise IntegrationOperationError(
            code="provider_error",
            safe_message="Gmail provider request failed.",
            retryable=True,
        )

    source.send_email = fail_send
    execution = _execution("gmail", source, "send_email")
    execution.ctx.services[IDEMPOTENCY_LEDGER_SERVICE] = IdempotencyLedger(tmp_path / "idempotency.sqlite3")
    result = await send_email(
        execution,
        SendEmailInput(
            account="me@example.test",
            to="you@example.test",
            subject="Status",
            body="Ready",
            idempotency_key="send-status-1",
        ),
    )

    assert result.is_error
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "provider_error"
    assert result.outcome.error.retryable


@pytest.mark.asyncio
async def test_calendar_create_maps_provider_failure_to_typed_result(tmp_path):
    source = FakeCalendarSource([])

    def fail_create(**kwargs):
        raise IntegrationOperationError(
            code="rate_limited",
            safe_message="Calendar provider request failed.",
            retryable=True,
        )

    source.create_event = fail_create
    execution = _execution("calendar", source, "create_calendar_event")
    execution.ctx.services[IDEMPOTENCY_LEDGER_SERVICE] = IdempotencyLedger(tmp_path / "idempotency.sqlite3")
    result = await create_calendar_event(
        execution,
        CreateCalendarEventInput(
            summary="Review",
            start="2026-07-20T09:00:00+04:00",
            idempotency_key="calendar-review-1",
        ),
    )

    assert result.is_error
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "rate_limited"


@pytest.mark.asyncio
async def test_slack_search_uses_message_id_title_and_existing_permalink():
    source = FakeSlackSource(
        [
            _item(
                "slack",
                "C123:1710000000.000100",
                "#product — Ada",
                metadata={"permalink": "https://workspace.slack.com/archives/C123/p1710000000000100"},
            )
        ]
    )

    result = await slack_search(_execution("slack", source, "slack_search"), SlackSearchInput(query="roadmap"))

    assert [ref.to_dict() for ref in result.source_refs] == [
        {
            "provider": "slack",
            "kind": "message",
            "ref": "C123:1710000000.000100",
            "title": "#product — Ada",
            "url": "https://workspace.slack.com/archives/C123/p1710000000000100",
        }
    ]


@pytest.mark.asyncio
async def test_slack_thread_uses_input_message_id_without_permalink_lookup():
    source = FakeSlackSource([])

    result = await slack_thread(
        _execution("slack", source, "slack_thread"),
        SlackThreadInput(message_id="C123:1710000000.000100"),
    )

    assert [ref.to_dict() for ref in result.source_refs] == [
        {
            "provider": "slack",
            "kind": "message",
            "ref": "C123:1710000000.000100",
            "title": "Slack message C123:1710000000.000100",
        }
    ]


@pytest.mark.asyncio
async def test_slack_thread_missing_message_is_typed_and_recoverable():
    source = FakeSlackSource([])

    async def missing_thread(_source_id: str) -> SlackThreadResult | None:
        return None

    source.read_thread = missing_thread  # type: ignore[method-assign]
    result = await slack_thread(
        _execution("slack", source, "slack_thread"),
        SlackThreadInput(message_id="C404:0"),
    )

    assert result.is_error
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "not_found"
    assert result.outcome.error.recovery_action == "Call slack_search first to obtain a current message reference."


@pytest.mark.asyncio
async def test_gmail_search_uses_returned_message_id_without_inventing_a_url():
    source = FakeGmailSource(
        [
            _item(
                "gmail",
                "message-123",
                "Quarterly plan",
                metadata={
                    "account": "me@example.test",
                    "thread_id": "thread-456",
                    "subject": "Quarterly plan",
                    "from": "ada@example.test",
                },
            )
        ]
    )

    result = await emails(
        _execution("gmail", source, "emails"),
        EmailsInput(query="quarterly plan"),
    )

    assert [ref.to_dict() for ref in result.source_refs] == [
        {
            "provider": "gmail",
            "kind": "message",
            "ref": "me@example.test:message-123",
            "title": "Quarterly plan",
        }
    ]


@pytest.mark.asyncio
async def test_gmail_search_drops_empty_ids_and_falls_back_only_for_blank_titles():
    source = FakeGmailSource(
        [
            _item(
                "gmail",
                "",
                "Missing identity",
                metadata={"account": "me@example.test", "subject": "Missing identity"},
            ),
            _item(
                "gmail",
                "message-123",
                "   ",
                metadata={"account": "me@example.test", "subject": "", "from": "ada@example.test"},
            ),
        ]
    )

    result = await emails(
        _execution("gmail", source, "emails"),
        EmailsInput(query="quarterly plan"),
    )

    assert [ref.to_dict() for ref in result.source_refs] == [
        {
            "provider": "gmail",
            "kind": "message",
            "ref": "me@example.test:message-123",
            "title": "Gmail message message-123",
        }
    ]


@pytest.mark.asyncio
async def test_gmail_read_uses_requested_message_id_with_stable_fallback_title():
    source = FakeGmailSource()

    result = await read_email(
        _execution("gmail", source, "read_email"),
        ReadEmailInput(email_id="message-123"),
    )

    assert [ref.to_dict() for ref in result.source_refs] == [
        {
            "provider": "gmail",
            "kind": "message",
            "ref": "me@example.test:message-123",
            "title": "Gmail message message-123",
        }
    ]


def test_multi_gmail_read_returns_the_matching_account_identity():
    source = object.__new__(MultiGmailSource)
    source.sources = [
        SimpleNamespace(read=lambda _source_id: None, get_email_address=lambda: "first@example.test"),
        SimpleNamespace(read=lambda _source_id: "message body", get_email_address=lambda: "second@example.test"),
    ]

    result = source.read("second@example.test:message-123")

    assert result is not None
    assert result.content == "message body"
    assert result.account == "second@example.test"


@pytest.mark.asyncio
async def test_send_email_approval_includes_the_body():
    info = await approve_send_email(
        _execution("gmail", FakeGmailSource(), "send_email"),
        SendEmailInput(
            account="me@example.test",
            to="you@example.test",
            subject="Release status",
            body="The release is ready for final review.",
            idempotency_key="release-status-1",
        ),
    )

    assert info is not None
    assert info.description == "you@example.test"
    assert "Subject: Release status" in (info.preview or "")
    assert "Body:\nThe release is ready for final review." in (info.preview or "")


@pytest.mark.asyncio
async def test_slack_blocks_approval_includes_the_actual_blocks():
    info = await approve_slack_post_blocks(
        _execution("slack", FakeSlackSource([]), "slack_post_blocks"),
        SlackPostBlocksInput(
            channel="#releases",
            text="Release status",
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "*Release is green*"},
                }
            ],
            idempotency_key="slack-release-1",
        ),
    )

    assert info is not None
    assert '"text":"*Release is green*"' in (info.preview or "")


@pytest.mark.asyncio
async def test_calendar_search_uses_event_id_title_and_html_link():
    source = FakeCalendarSource(
        [
            _item(
                "calendar",
                "event-123",
                "Planning review",
                metadata={
                    "calendar_id": "primary@example.test",
                    "start": "2026-07-10T09:00:00+00:00",
                    "html_link": "https://calendar.google.com/calendar/event?eid=event-123",
                },
            )
        ]
    )

    result = await calendar(
        _execution("calendar", source, "calendar"),
        CalendarInput(query="Planning review"),
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


@pytest.mark.asyncio
async def test_gmail_equal_local_ids_from_two_accounts_remain_distinct():
    source = FakeGmailSource(
        [
            _item("gmail", "same-id", "First", metadata={"account": "first@example.test"}),
            _item("gmail", "same-id", "Second", metadata={"account": "second@example.test"}),
        ]
    )

    result = await emails(_execution("gmail", source, "emails"), EmailsInput(query="same"))

    assert [ref.ref for ref in result.source_refs] == [
        "first@example.test:same-id",
        "second@example.test:same-id",
    ]


@pytest.mark.asyncio
async def test_gmail_list_equal_local_ids_from_two_accounts_remain_distinct():
    source = FakeGmailSource()
    source.list_recent = lambda **_kwargs: [
        SimpleNamespace(
            identity="same-id",
            title="First",
            source="gmail",
            account="first@example.test",
            preview=None,
        ),
        SimpleNamespace(
            identity="same-id",
            title="Second",
            source="gmail",
            account="second@example.test",
            preview=None,
        ),
    ]

    result = await emails(_execution("gmail", source, "emails"), EmailsInput())

    assert [ref.ref for ref in result.source_refs] == [
        "first@example.test:same-id",
        "second@example.test:same-id",
    ]


@pytest.mark.asyncio
async def test_calendar_equal_local_ids_from_two_calendars_remain_distinct():
    source = FakeCalendarSource(
        [
            _item(
                "calendar",
                "same-id",
                "First",
                metadata={"calendar_id": "first@example.test", "start": "2026-07-10T09:00:00+00:00"},
            ),
            _item(
                "calendar",
                "same-id",
                "Second",
                metadata={"calendar_id": "second@example.test", "start": "2026-07-10T10:00:00+00:00"},
            ),
        ]
    )

    result = await calendar(_execution("calendar", source, "calendar"), CalendarInput(query="same"))

    assert [ref.ref for ref in result.source_refs] == [
        "first@example.test:same-id",
        "second@example.test:same-id",
    ]
