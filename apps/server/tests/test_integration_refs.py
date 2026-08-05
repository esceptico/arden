from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from arden.integrations.base import IntegrationConnectionError, IntegrationOperationError
from arden.integrations.calendar.client import MultiCalendarSource, qualify_event_ref
from arden.integrations.calendar.models import (
    CalendarAccount,
    CalendarCreateDraft,
    CalendarEvent,
    CalendarEventPage,
    CalendarMutationResult,
    CalendarUpdateDraft,
)
from arden.integrations.gmail.client import MultiGmailSource
from arden.integrations.gmail.models import GmailMessage, GmailMessageSummary
from arden.integrations.slack.client import SlackClient
from arden.integrations.slack.messages import SlackMessages
from arden.integrations.slack.models import (
    SlackAuthResult,
    SlackChannel,
    SlackHistoryMessage,
    SlackIdentity,
    SlackMessage,
    SlackUser,
)
from arden.integrations.slack.transport import SlackPayloadError


def test_gmail_qualified_ref_reads_only_named_account():
    calls: list[tuple[str, str]] = []

    def source(account: str, body: str):
        message = GmailMessage(
            summary=GmailMessageSummary(
                ref=f"{account}:shared-id",
                account_ref=account,
                thread_ref=f"{account}:thread-id",
                subject="Subject",
                sender="sender@example.test",
                recipient=account,
                reply_to="sender@example.test",
                snippet="",
                received_at=datetime(2026, 8, 5, tzinfo=UTC),
                labels=(),
            ),
            content=body,
        )
        return SimpleNamespace(
            account_ref=account,
            read=lambda message_id: calls.append((account, message_id)) or message,
        )

    multi = object.__new__(MultiGmailSource)
    multi.sources = [source("first@example.test", "wrong"), source("second@example.test", "right")]

    result = multi.read("second@example.test:shared-id")

    assert result.content == "right"
    assert calls == [("second@example.test", "shared-id")]


def test_gmail_rejects_unqualified_message_ref_even_with_one_account():
    multi = object.__new__(MultiGmailSource)
    multi.sources = [SimpleNamespace(get_email_address=lambda: "only@example.test")]

    with pytest.raises(IntegrationOperationError, match="account-qualified"):
        multi.read("shared-id")


def test_calendar_qualified_ref_updates_only_named_account():
    calls: list[tuple[str, str]] = []

    def source(account: str):
        return SimpleNamespace(
            account=CalendarAccount(account),
            update_event=lambda event_id, _draft: (
                calls.append((account, event_id)) or CalendarMutationResult(event_ref=event_id, summary="Review")
            ),
        )

    multi = object.__new__(MultiCalendarSource)
    multi.sources = [source("first@example.test"), source("second@example.test")]

    result = multi.update_event(
        CalendarUpdateDraft(
            event_ref="second@example.test:shared-id",
            changed_fields=frozenset({"summary"}),
            summary="Review",
        )
    )

    assert result == CalendarMutationResult(
        event_ref="second@example.test:shared-id",
        summary="Review",
    )
    assert calls == [("second@example.test", "shared-id")]


def test_calendar_rejects_unqualified_event_ref_even_with_one_account():
    calls: list[str] = []
    multi = object.__new__(MultiCalendarSource)
    multi.sources = [
        SimpleNamespace(
            account=CalendarAccount("only@example.test"),
            update_event=lambda event_id, _draft: (
                calls.append(event_id) or CalendarMutationResult(event_ref=event_id)
            ),
        )
    ]

    with pytest.raises(ValueError, match="account-qualified"):
        CalendarUpdateDraft(
            event_ref="series:instance",
            changed_fields=frozenset({"summary"}),
            summary="Review",
        )
    with pytest.raises(IntegrationOperationError, match=r"other@example\.test"):
        multi.update_event(
            CalendarUpdateDraft(
                event_ref="other@example.test:event-1",
                changed_fields=frozenset({"summary"}),
                summary="Wrong account",
            )
        )
    assert calls == []


def test_calendar_empty_source_fails_closed():
    multi = object.__new__(MultiCalendarSource)
    multi.sources = ()

    with pytest.raises(IntegrationConnectionError, match="No Google Calendar account"):
        multi.search("review")


def test_calendar_create_requires_an_exact_account():
    with pytest.raises(ValueError, match="account"):
        CalendarCreateDraft(
            account_ref="",
            summary="Review",
            start=datetime(2026, 8, 5, tzinfo=UTC),
            end=datetime(2026, 8, 5, 1, tzinfo=UTC),
            description=None,
            location=None,
            attendees=(),
            all_day=False,
        )


def test_calendar_multi_account_read_fails_instead_of_returning_partial_results():
    now = datetime(2026, 8, 5, tzinfo=UTC)
    item = CalendarEvent(
        event_ref="first@example.test:event-1",
        account_ref="first@example.test",
        title="Review",
        start=now,
        end=now + timedelta(hours=1),
        is_all_day=False,
    )
    first = SimpleNamespace(
        account=CalendarAccount("first@example.test"),
        search=lambda _query, limit: CalendarEventPage(events=(item,)),
    )

    def fail(_query, limit):
        raise IntegrationOperationError(code="provider_error", safe_message="Second account failed.")

    second = SimpleNamespace(account=CalendarAccount("second@example.test"), search=fail)
    multi = object.__new__(MultiCalendarSource)
    multi.sources = [first, second]

    with pytest.raises(IntegrationOperationError, match="Second account failed"):
        multi.search("review")


def test_calendar_event_resolution_rejects_unknown_account_before_provider_call():
    calls: list[str] = []
    multi = object.__new__(MultiCalendarSource)
    multi.sources = [
        SimpleNamespace(
            account=CalendarAccount("second@example.test"),
            update_event=lambda *_args: (
                calls.append("update") or CalendarMutationResult(event_ref="event-1")
            ),
        ),
    ]

    with pytest.raises(IntegrationOperationError, match="Calendar account not found"):
        multi.update_event(
            CalendarUpdateDraft(
                event_ref="first@example.test:event-1",
                changed_fields=frozenset({"summary"}),
                summary="Review",
            )
        )

    assert calls == []


def test_calendar_connection_verification_checks_every_account():
    calls: list[str] = []

    def fail_second() -> None:
        calls.append("second")
        raise IntegrationConnectionError(
            integration_id="calendar",
            reason="auth_required",
            detail="Second account needs reconnection.",
        )

    multi = object.__new__(MultiCalendarSource)
    multi.sources = [
        SimpleNamespace(verify_connection=lambda: calls.append("first")),
        SimpleNamespace(verify_connection=fail_second),
    ]

    with pytest.raises(IntegrationConnectionError, match="Second account needs reconnection"):
        multi.verify_connection()

    assert calls == ["first", "second"]


def test_calendar_account_verification_checks_only_the_requested_account():
    calls: list[str] = []
    multi = object.__new__(MultiCalendarSource)
    multi.sources = [
        SimpleNamespace(
            account=CalendarAccount("first@example.test"),
            verify_connection=lambda: calls.append("first"),
        ),
        SimpleNamespace(
            account=CalendarAccount("second@example.test"),
            verify_connection=lambda: calls.append("second"),
        ),
    ]

    multi.verify_account("SECOND@example.test")

    assert calls == ["second"]


def test_calendar_event_ref_qualification_is_idempotent_and_case_insensitive():
    assert qualify_event_ref("me@example.test", "event-1") == "me@example.test:event-1"
    assert qualify_event_ref("ME@example.test", "me@example.test:event-1") == "me@example.test:event-1"
    assert qualify_event_ref("me@example.test", "series:instance") == "me@example.test:series:instance"


@pytest.mark.asyncio
async def test_slack_user_search_paginates_until_match():
    client = SlackClient(bot_token="xoxb-test")
    cursors: list[str] = []

    async def get(_session, _method, **params):
        cursors.append(params["cursor"])
        if not params["cursor"]:
            return {"members": [], "response_metadata": {"next_cursor": "page-2"}}
        return {
            "members": [
                {
                    "id": "U2",
                    "real_name": "Ada Lovelace",
                    "name": "ada",
                    "deleted": False,
                    "is_bot": False,
                    "profile": {},
                }
            ],
            "response_metadata": {"next_cursor": ""},
        }

    client.transport.get = get
    users = await client.directory.search_users("Ada", limit=50)

    assert [user.ref for user in users] == ["U2"]
    assert cursors == ["", "page-2"]


@pytest.mark.asyncio
async def test_slack_user_search_skips_unnamed_provider_member():
    client = SlackClient(bot_token="xoxb-test")

    async def get(_session, _method, **_params):
        return {"members": [{"id": "U2"}], "response_metadata": {"next_cursor": ""}}

    client.transport.get = get

    assert await client.directory.search_users("Ada", limit=50) == []


def test_slack_search_message_is_decoded_to_typed_record():
    client = SlackClient(bot_token="xoxb-test")

    message = client.messages._decode_search_message(
        {
            "channel_id": "C1",
            "channel_name": "product",
            "message_ts": "1710000000.000100",
            "author_user_id": "U1",
            "author_name": "Ada",
            "content": "ship it",
            "permalink": "https://example.slack.com/archives/C1/p1710000000000100",
        },
    )

    assert isinstance(message, SlackMessage)
    assert message.ref == "C1:1710000000.000100"
    assert message.author_name == "Ada"


def test_slack_search_message_rejects_missing_provider_identity():
    client = SlackClient(bot_token="xoxb-test")

    with pytest.raises(SlackPayloadError, match="expected non-empty string 'channel_id'"):
        client.messages._decode_search_message(
            {
                "message_ts": "1710000000.000100",
                "author_name": "Ada",
                "content": "ship it",
            },
        )


@pytest.mark.asyncio
async def test_slack_channel_resolution_rejects_missing_name():
    client = SlackClient(bot_token="xoxb-test")

    async def get(_session, _method, **_params):
        return {"channel": {"id": "C1", "is_im": False}}

    client.transport.get = get

    with pytest.raises(SlackPayloadError, match="non-DM channel has no name"):
        await client.directory.resolve_channel("C1")


@pytest.mark.asyncio
async def test_slack_channel_index_requires_pagination_metadata():
    client = SlackClient(bot_token="xoxb-test")

    async def get(_session, _method, **_params):
        return {"channels": []}

    client.transport.get = get

    with pytest.raises(SlackPayloadError, match="response_metadata"):
        await client.directory.search_channels()


@pytest.mark.asyncio
async def test_slack_channel_search_reuses_loaded_index():
    client = SlackClient(bot_token="xoxb-test")
    calls = 0

    async def get(_session, method, **_params):
        nonlocal calls
        assert method == "conversations.list"
        calls += 1
        return {
            "channels": [{"id": "C1", "name": "general", "is_im": False}],
            "response_metadata": {"next_cursor": ""},
        }

    client.transport.get = get

    assert await client.directory.search_channels() == [SlackChannel(ref="C1", name="general")]
    assert await client.directory.search_channels() == [SlackChannel(ref="C1", name="general")]
    assert calls == 1


@pytest.mark.asyncio
async def test_slack_open_dm_rejects_missing_channel_id():
    client = SlackClient(bot_token="xoxb-test")

    async def post(_session, _method, **_params):
        return {"channel": {}}

    client.transport.post = post

    with pytest.raises(SlackPayloadError, match="expected non-empty string 'id'"):
        await client.directory.open_dm("U1")


def test_slack_monitor_history_decoder_returns_typed_message():
    message = SlackMessages.decode_monitor_message({"type": "message", "ts": "100.0", "text": "hello", "user": "U1"})

    assert isinstance(message, SlackHistoryMessage)
    assert message.user_ref == "U1"


@pytest.mark.asyncio
async def test_slack_whoami_returns_typed_identity():
    client = SlackClient(bot_token="xoxb-test")

    async def auth_test(_token_kind="read"):
        return SlackAuthResult(
            team_name="Example",
            team_ref="T1",
            user_ref="U1",
            user_name="Ada",
            bot_ref=None,
        )

    client.transport.auth_test = auth_test

    assert await client.transport.whoami() == SlackIdentity(user_ref="U1", user_name="Ada")


@pytest.mark.asyncio
async def test_slack_semantic_user_resolution_requires_exact_match():
    client = SlackClient(bot_token="xoxb-test")

    async def users(_query, limit):
        return [
            SlackUser(ref="U1", name="Ada One", username="ada1", email=None, title=None),
            SlackUser(ref="U2", name="Ada Two", username="ada2", email=None, title=None),
        ]

    client.directory.search_users = users

    with pytest.raises(RuntimeError, match="No exact Slack user"):
        await client.directory.resolve_dm_target("Ada")
