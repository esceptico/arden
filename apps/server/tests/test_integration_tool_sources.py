from datetime import UTC, datetime

import pytest

from ntrp.context.models import SessionState
from ntrp.integrations.calendar.client import MultiCalendarSource
from ntrp.integrations.calendar.tools import CalendarInput, calendar
from ntrp.integrations.gmail.client import MultiGmailSource, SourceItem
from ntrp.integrations.gmail.tools import EmailsInput, ReadEmailInput, emails, read_email
from ntrp.integrations.slack.client import SlackClient, SlackThreadResult
from ntrp.integrations.slack.tools import SlackSearchInput, SlackThreadInput, slack_search, slack_thread
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

    def list_recent(self, days: int = 7, limit: int = 50) -> list[SourceItem]:
        return [SourceItem(identity="message-recent", title="Recent message", source="gmail")]

    def read(self, source_id: str) -> str | None:
        return "From: sender@example.test\nSubject: Read message\n\nBody"


class FakeCalendarSource(MultiCalendarSource):
    def __init__(self, events: list[RawItem]):
        self.events = events

    def search(self, query: str, limit: int = 50) -> list[RawItem]:
        return self.events[:limit]


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
async def test_gmail_search_uses_returned_message_id_without_inventing_a_url():
    source = FakeGmailSource(
        [
            _item(
                "gmail",
                "message-123",
                "Quarterly plan",
                metadata={"thread_id": "thread-456", "subject": "Quarterly plan", "from": "ada@example.test"},
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
            "ref": "message-123",
            "title": "Quarterly plan",
        }
    ]


@pytest.mark.asyncio
async def test_gmail_search_drops_empty_ids_and_falls_back_only_for_blank_titles():
    source = FakeGmailSource(
        [
            _item("gmail", "", "Missing identity", metadata={"subject": "Missing identity"}),
            _item("gmail", "message-123", "   ", metadata={"subject": "", "from": "ada@example.test"}),
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
            "ref": "message-123",
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
            "ref": "message-123",
            "title": "Gmail message message-123",
        }
    ]


@pytest.mark.asyncio
async def test_calendar_search_uses_event_id_title_and_html_link():
    source = FakeCalendarSource(
        [
            _item(
                "calendar",
                "event-123",
                "Planning review",
                metadata={
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
            "ref": "event-123",
            "title": "Planning review",
            "url": "https://calendar.google.com/calendar/event?eid=event-123",
        }
    ]
