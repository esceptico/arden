import pytest

from arden.integrations.slack.client import SlackClient
from arden.integrations.slack.models import SlackChannel
from arden.integrations.slack.transport import SlackPayloadError


@pytest.mark.asyncio
async def test_channel_search_does_not_return_cached_direct_messages(monkeypatch):
    client = SlackClient(bot_token="xoxb-test")

    async def get(_session, method, **_params):
        if method == "conversations.info":
            return {"channel": {"id": "D1", "is_im": True, "user": "U1"}}
        if method == "users.info":
            return {"user": {"id": "U1", "name": "ada", "real_name": "Ada", "profile": None}}
        assert method == "conversations.list"
        return {
            "channels": [{"id": "C1", "name": "general", "is_im": False}],
            "response_metadata": {"next_cursor": ""},
        }

    monkeypatch.setattr(client.transport, "get", get)
    await client.directory.resolve_channel("D1")

    assert await client.directory.search_channels() == [SlackChannel(ref="C1", name="general")]


@pytest.mark.asyncio
async def test_user_profile_lookup_rejects_a_different_returned_ref(monkeypatch):
    client = SlackClient(bot_token="xoxb-test")

    async def get(_session, _method, **_params):
        return {"user": {"id": "U2", "name": "ada", "profile": {}}}

    monkeypatch.setattr(client.transport, "get", get)

    with pytest.raises(SlackPayloadError, match="different user ref"):
        await client.directory.read_user("U1")


@pytest.mark.asyncio
async def test_author_lookup_reuses_users_info_for_deleted_slack_connect_user(monkeypatch):
    client = SlackClient(bot_token="xoxb-test")
    methods: list[str] = []

    async def get(_session, method, **_params):
        methods.append(method)
        assert method == "users.info"
        return {
            "user": {
                "id": "U1",
                "name": "ada-external",
                "real_name": "Ada External",
                "deleted": True,
                "is_bot": False,
                "is_stranger": True,
                "profile": None,
            }
        }

    monkeypatch.setattr(client.transport, "get", get)

    assert await client.directory.resolve_user_name("U1") == "Ada External"
    assert await client.directory.resolve_user_name("U1") == "Ada External"
    profile = await client.directory.read_user("U1")

    assert methods == ["users.info"]
    assert profile.deleted is True
    assert profile.is_stranger is True
    assert profile.email is None


@pytest.mark.asyncio
async def test_dm_and_user_search_reuse_one_paginated_user_directory(monkeypatch):
    client = SlackClient(bot_token="xoxb-test")
    user_list_cursors: list[str] = []

    async def get(_session, method, **params):
        if method == "conversations.list":
            return {
                "channels": [
                    {"id": "D1", "is_im": True, "user": "U1"},
                    {"id": "D2", "is_im": True, "user": "U2"},
                ],
                "response_metadata": {"next_cursor": ""},
            }
        assert method == "users.list"
        user_list_cursors.append(params["cursor"])
        if not params["cursor"]:
            return {
                "members": [
                    {
                        "id": "U1",
                        "name": "ada",
                        "real_name": "Ada Lovelace",
                        "deleted": False,
                        "is_bot": False,
                        "profile": {},
                    }
                ],
                "response_metadata": {"next_cursor": "page-2"},
            }
        return {
            "members": [
                {
                    "id": "U2",
                    "name": "grace",
                    "real_name": "Grace Hopper",
                    "deleted": False,
                    "is_bot": False,
                    "is_stranger": True,
                    "profile": None,
                }
            ],
            "response_metadata": {"next_cursor": ""},
        }

    monkeypatch.setattr(client.transport, "get", get)

    assert [dm.peer_name for dm in await client.directory.list_dms()] == ["Ada Lovelace", "Grace Hopper"]
    assert [user.ref for user in await client.directory.search_users("grace")] == ["U2"]
    assert user_list_cursors == ["", "page-2"]


@pytest.mark.asyncio
async def test_user_info_uses_exact_ref_when_all_documented_names_are_empty(monkeypatch):
    client = SlackClient(bot_token="xoxb-test")

    async def get(_session, _method, **_params):
        return {"user": {"id": "U1", "name": "ada", "real_name": "", "profile": {}}}

    monkeypatch.setattr(client.transport, "get", get)

    assert await client.directory.resolve_user_name("U1") == "ada"


@pytest.mark.asyncio
async def test_directory_skips_unnamed_deleted_member_without_failing_other_users(monkeypatch):
    client = SlackClient(bot_token="xoxb-test")

    async def get(_session, _method, **_params):
        return {
            "members": [
                {"id": "U0", "deleted": True, "profile": None},
                {"id": "U1", "name": "ada", "profile": {"display_name": "Ada"}},
            ],
            "response_metadata": {"next_cursor": ""},
        }

    monkeypatch.setattr(client.transport, "get", get)

    assert [user.ref for user in await client.directory.search_users()] == ["U1"]


@pytest.mark.asyncio
async def test_user_directory_rejects_duplicate_refs_before_caching(monkeypatch):
    client = SlackClient(bot_token="xoxb-test")

    async def get(_session, _method, **_params):
        return {
            "members": [
                {
                    "id": "U1",
                    "name": "ada",
                    "real_name": "Ada",
                    "deleted": False,
                    "is_bot": False,
                    "profile": {},
                },
                {
                    "id": "U1",
                    "name": "ada2",
                    "real_name": "Ada Two",
                    "deleted": False,
                    "is_bot": False,
                    "profile": {},
                },
            ],
            "response_metadata": {"next_cursor": ""},
        }

    monkeypatch.setattr(client.transport, "get", get)

    with pytest.raises(SlackPayloadError, match="duplicate user ref"):
        await client.directory.search_users()
    assert not client.directory._user_directory_loaded
