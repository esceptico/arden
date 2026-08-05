from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from arden.integrations.base import IntegrationConnectionError, IntegrationOperationError
from arden.integrations.calendar.client import MultiCalendarSource, qualify_event_ref
from arden.integrations.gmail.client import MultiGmailSource
from arden.integrations.slack.client import SlackClient


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
            update_event=lambda event_id, **kwargs: calls.append((account, event_id)) or "updated",
        )

    multi = object.__new__(MultiCalendarSource)
    multi.sources = [source("first@example.test"), source("second@example.test")]

    result = multi.update_event("second@example.test:shared-id", summary="Review")

    assert result == "updated"
    assert calls == [("second@example.test", "shared-id")]


def test_calendar_single_account_raw_ref_preserves_colons():
    calls: list[str] = []
    multi = object.__new__(MultiCalendarSource)
    multi.sources = [
        SimpleNamespace(
            get_email_address=lambda: "only@example.test",
            update_event=lambda event_id, **_kwargs: calls.append(event_id) or "updated",
        )
    ]

    assert multi.update_event("series:instance", summary="Review") == "updated"
    assert calls == ["series:instance"]
    with pytest.raises(IntegrationOperationError, match=r"other@example\.test"):
        multi.update_event("other@example.test:event-1", summary="Wrong account")
    assert calls == ["series:instance"]


def test_calendar_missing_account_identity_fails_before_read_or_create():
    calls: list[str] = []
    source = SimpleNamespace(
        get_email_address=lambda: "",
        search=lambda *_args, **_kwargs: calls.append("search") or [],
        create_event=lambda **_kwargs: calls.append("create") or "Created (id: event-1)",
        update_event=lambda *_args, **_kwargs: calls.append("update") or "updated",
        delete_event=lambda *_args, **_kwargs: calls.append("delete") or "deleted",
        token_path=SimpleNamespace(name="calendar-token.json"),
        auth_error=None,
    )
    multi = object.__new__(MultiCalendarSource)
    multi.sources = [source]

    with pytest.raises(IntegrationConnectionError, match="account identity is unavailable"):
        multi.search("review")
    with pytest.raises(IntegrationConnectionError, match="account identity is unavailable"):
        multi.create_event(account="", summary="Review", start=datetime(2026, 8, 5, tzinfo=UTC))
    with pytest.raises(IntegrationConnectionError, match="account identity is unavailable"):
        multi.update_event("event-1", summary="Review")
    with pytest.raises(IntegrationConnectionError, match="account identity is unavailable"):
        multi.delete_event("unknown@example.test:event-1")

    assert calls == []


def test_calendar_default_create_skips_source_without_identity():
    calls: list[str] = []
    unavailable = SimpleNamespace(
        get_email_address=lambda: "",
        create_event=lambda **_kwargs: calls.append("unavailable") or "Created (id: wrong)",
        auth_error=None,
    )
    available = SimpleNamespace(
        get_email_address=lambda: "second@example.test",
        create_event=lambda **_kwargs: calls.append("second") or "Created (id: event-1)",
        auth_error=None,
    )
    multi = object.__new__(MultiCalendarSource)
    multi.sources = [unavailable, available]

    result = multi.create_event(account="", summary="Review", start=datetime(2026, 8, 5, tzinfo=UTC))

    assert result == "Created (id: second@example.test:event-1)"
    assert calls == ["second"]


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
