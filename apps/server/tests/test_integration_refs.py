from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from arden.integrations.base import IntegrationConnectionError, IntegrationOperationError
from arden.integrations.calendar.client import CalendarMutationResult, MultiCalendarSource, qualify_event_ref
from arden.integrations.gmail.client import MultiGmailSource
from arden.integrations.slack.client import SlackClient
from arden.search.types import RawItem


def test_gmail_qualified_ref_reads_only_named_account():
    calls: list[tuple[str, str]] = []

    def source(account: str, body: str):
        return SimpleNamespace(
            get_email_address=lambda: account,
            read=lambda message_id: calls.append((account, message_id)) or body,
        )

    multi = object.__new__(MultiGmailSource)
    multi.sources = [source("first@example.test", "wrong"), source("second@example.test", "right")]

    result = multi.read("second@example.test:shared-id")

    assert result is not None and result.content == "right"
    assert calls == [("second@example.test", "shared-id")]


def test_calendar_qualified_ref_updates_only_named_account():
    calls: list[tuple[str, str]] = []

    def source(account: str):
        return SimpleNamespace(
            get_email_address=lambda: account,
            update_event=lambda event_id, **kwargs: (
                calls.append((account, event_id)) or CalendarMutationResult(event_ref=event_id, summary="Review")
            ),
        )

    multi = object.__new__(MultiCalendarSource)
    multi.sources = [source("first@example.test"), source("second@example.test")]

    result = multi.update_event("second@example.test:shared-id", summary="Review")

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
            get_email_address=lambda: "only@example.test",
            update_event=lambda event_id, **_kwargs: (
                calls.append(event_id) or CalendarMutationResult(event_ref=event_id)
            ),
        )
    ]

    with pytest.raises(IntegrationOperationError, match="account-qualified"):
        multi.update_event("series:instance", summary="Review")
    with pytest.raises(IntegrationOperationError, match=r"other@example\.test"):
        multi.update_event("other@example.test:event-1", summary="Wrong account")
    assert calls == []


def test_calendar_missing_account_identity_fails_before_read_or_create():
    calls: list[str] = []
    source = SimpleNamespace(
        get_email_address=lambda: "",
        search=lambda *_args, **_kwargs: calls.append("search") or [],
        create_event=lambda **_kwargs: calls.append("create") or CalendarMutationResult(event_ref="event-1"),
        update_event=lambda *_args, **_kwargs: calls.append("update") or CalendarMutationResult(event_ref="event-1"),
        delete_event=lambda *_args, **_kwargs: calls.append("delete") or CalendarMutationResult(event_ref="event-1"),
        token_path=SimpleNamespace(name="calendar-token.json"),
    )
    multi = object.__new__(MultiCalendarSource)
    multi.sources = [source]

    with pytest.raises(IntegrationConnectionError, match="account identity is unavailable"):
        multi.search("review")
    with pytest.raises(IntegrationConnectionError, match="account identity is unavailable"):
        multi.create_event(account="me@example.test", summary="Review", start=datetime(2026, 8, 5, tzinfo=UTC))
    with pytest.raises(IntegrationConnectionError, match="account identity is unavailable"):
        multi.update_event("me@example.test:event-1", summary="Review")
    with pytest.raises(IntegrationConnectionError, match="account identity is unavailable"):
        multi.delete_event("unknown@example.test:event-1")

    assert calls == []


def test_calendar_create_requires_an_exact_account():
    calls: list[str] = []
    unavailable = SimpleNamespace(
        get_email_address=lambda: "",
        create_event=lambda **_kwargs: calls.append("unavailable") or CalendarMutationResult(event_ref="wrong"),
    )
    available = SimpleNamespace(
        get_email_address=lambda: "second@example.test",
        create_event=lambda **_kwargs: calls.append("second") or CalendarMutationResult(event_ref="event-1"),
    )
    multi = object.__new__(MultiCalendarSource)
    multi.sources = [unavailable, available]

    with pytest.raises(IntegrationOperationError, match="requires an exact account"):
        multi.create_event(account="", summary="Review", start=datetime(2026, 8, 5, tzinfo=UTC))

    assert calls == []


def test_calendar_multi_account_read_fails_instead_of_returning_partial_results():
    now = datetime(2026, 8, 5, tzinfo=UTC)
    item = RawItem(
        source="calendar",
        source_id="event-1",
        title="Review",
        content="",
        created_at=now,
        updated_at=now,
        metadata={"start": now.isoformat()},
    )
    first = SimpleNamespace(
        get_email_address=lambda: "first@example.test",
        search=lambda _query, limit: [item],
    )

    def fail(_query, limit):
        raise IntegrationOperationError(code="provider_error", safe_message="Second account failed.")

    second = SimpleNamespace(get_email_address=lambda: "second@example.test", search=fail)
    multi = object.__new__(MultiCalendarSource)
    multi.sources = [first, second]

    with pytest.raises(IntegrationOperationError, match="Second account failed"):
        multi.search("review")


def test_calendar_event_resolution_does_not_skip_account_identity_failure():
    calls: list[str] = []

    def missing_identity() -> str:
        raise IntegrationConnectionError(
            integration_id="calendar",
            reason="degraded",
            detail="First account identity failed.",
        )

    multi = object.__new__(MultiCalendarSource)
    multi.sources = [
        SimpleNamespace(get_email_address=missing_identity),
        SimpleNamespace(
            get_email_address=lambda: "second@example.test",
            update_event=lambda *_args, **_kwargs: (
                calls.append("update") or CalendarMutationResult(event_ref="event-1")
            ),
        ),
    ]

    with pytest.raises(IntegrationConnectionError, match="First account identity failed"):
        multi.update_event("second@example.test:event-1", summary="Review")

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


def test_calendar_event_ref_qualification_is_idempotent_and_case_insensitive():
    assert qualify_event_ref("me@example.test", "event-1") == "me@example.test:event-1"
    assert qualify_event_ref("ME@example.test", "me@example.test:event-1") == "me@example.test:event-1"
    assert qualify_event_ref("me@example.test", "series:instance") == "me@example.test:series:instance"


@pytest.mark.asyncio
async def test_slack_user_search_paginates_until_match():
    client = object.__new__(SlackClient)
    cursors: list[str] = []

    async def get(_session, _method, **params):
        cursors.append(params["cursor"])
        if not params["cursor"]:
            return {"members": [], "response_metadata": {"next_cursor": "page-2"}}
        return {
            "members": [{"id": "U2", "real_name": "Ada Lovelace", "name": "ada", "profile": {}}],
            "response_metadata": {"next_cursor": ""},
        }

    client._get = get
    users = await client.search_users("Ada", limit=50)

    assert [user["id"] for user in users] == ["U2"]
    assert cursors == ["", "page-2"]


@pytest.mark.asyncio
async def test_slack_semantic_user_resolution_rejects_ambiguity():
    client = object.__new__(SlackClient)

    async def users(_query, limit):
        return [
            {"id": "U1", "name": "Ada One", "username": "ada1", "email": ""},
            {"id": "U2", "name": "Ada Two", "username": "ada2", "email": ""},
        ]

    client.search_users = users

    with pytest.raises(RuntimeError, match="Ambiguous Slack user"):
        await client.resolve_dm_target("Ada")
