from types import SimpleNamespace

import pytest

from ntrp.integrations.calendar.client import MultiCalendarSource
from ntrp.integrations.gmail.client import MultiGmailSource
from ntrp.integrations.slack.client import SlackClient


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
