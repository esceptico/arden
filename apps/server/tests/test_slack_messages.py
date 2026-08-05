import pytest

from arden.integrations.base import IntegrationOperationError
from arden.integrations.slack.client import SlackClient
from arden.integrations.slack.models import SlackChannel
from arden.integrations.slack.transport import SlackPayloadError


def _bot_message(timestamp: str, text: str) -> dict[str, object]:
    return {
        "type": "message",
        "subtype": "bot_message",
        "bot_id": "B1",
        "bot_profile": {"id": "B1", "name": "Build Bot"},
        "text": text,
        "ts": timestamp,
    }


@pytest.mark.asyncio
async def test_search_decodes_current_contract_and_requests_every_channel_type(monkeypatch):
    client = SlackClient(bot_token="xoxb-test", user_token="xoxp-test")
    payloads: list[dict[str, object]] = []

    async def post(_session, method, **payload):
        assert method == "assistant.search.context"
        payloads.append(payload)
        return {
            "results": {
                "messages": [
                    {
                        "author_name": "Ada",
                        "author_user_id": "U1",
                        "channel_id": "C1",
                        "channel_name": "product",
                        "message_ts": "200.0",
                        "content": "reply",
                        "permalink": ("https://workspace.slack.com/archives/C1/p200000?thread_ts=100.0&cid=C1"),
                    }
                ]
            },
            "response_metadata": {"next_cursor": "page-3" if payload.get("cursor") == "next-page" else "next-page"},
        }

    monkeypatch.setattr(client.transport, "post", post)

    first_page = await client.messages.search_messages("release")
    second_page = await client.messages.search_messages("release", page_ref="next-page")

    assert payloads[0]["channel_types"] == ["public_channel", "private_channel", "mpim", "im"]
    assert "cursor" not in payloads[0]
    assert payloads[1]["cursor"] == "next-page"
    assert first_page.messages[0].ref == "C1:200.0"
    assert first_page.messages[0].thread_ref == "C1:100.0"
    assert first_page.next_page_ref == "next-page"
    assert second_page.next_page_ref == "page-3"


@pytest.mark.asyncio
async def test_search_rejects_repeated_page_ref(monkeypatch):
    client = SlackClient(bot_token="xoxb-test", user_token="xoxp-test")

    async def post(_session, _method, **_payload):
        return {
            "results": {"messages": []},
            "response_metadata": {"next_cursor": "same-page"},
        }

    monkeypatch.setattr(client.transport, "post", post)

    with pytest.raises(SlackPayloadError, match="current page ref again"):
        await client.messages.search_messages("release", page_ref="same-page")


def test_search_rejects_obsolete_message_fields():
    client = SlackClient(bot_token="xoxb-test")

    with pytest.raises(SlackPayloadError, match="message_ts"):
        client.messages._decode_search_message(
            {
                "author_name": "Ada",
                "author_user_id": "U1",
                "channel_id": "C1",
                "channel_name": "product",
                "ts": "200.0",
                "content": "reply",
                "permalink": "https://workspace.slack.com/archives/C1/p200000",
            }
        )


@pytest.mark.asyncio
async def test_channel_read_round_trips_exact_page_ref(monkeypatch):
    client = SlackClient(bot_token="xoxb-test")
    cursors: list[str] = []

    async def get(_session, method, **params):
        assert method == "conversations.history"
        cursors.append(params.get("cursor", ""))
        if "cursor" not in params:
            return {
                "messages": [_bot_message("300.0", "newer")],
                "response_metadata": {"next_cursor": "page-2"},
            }
        return {
            "messages": [_bot_message("200.0", "older")],
            "response_metadata": {"next_cursor": "page-3"},
        }

    async def resolve_channel(_session, _locator):
        return SlackChannel(ref="C1", name="product")

    monkeypatch.setattr(client.transport, "get", get)
    monkeypatch.setattr(client.directory, "resolve_channel_in_session", resolve_channel)

    first_page = await client.messages.read_channel("C1", limit=2)
    second_page = await client.messages.read_channel("C1", limit=2, page_ref=first_page.next_page_ref)

    assert [message.text for message in first_page.messages] == ["newer"]
    assert first_page.next_page_ref == "page-2"
    assert [message.text for message in second_page.messages] == ["older"]
    assert second_page.next_page_ref == "page-3"
    assert cursors == ["", "page-2"]


@pytest.mark.asyncio
async def test_channel_read_rejects_repeated_page_ref(monkeypatch):
    client = SlackClient(bot_token="xoxb-test")

    async def get(_session, _method, **_params):
        return {"messages": [], "response_metadata": {"next_cursor": "same-page"}}

    async def resolve_channel(_session, _locator):
        return SlackChannel(ref="C1", name="product")

    monkeypatch.setattr(client.transport, "get", get)
    monkeypatch.setattr(client.directory, "resolve_channel_in_session", resolve_channel)

    with pytest.raises(SlackPayloadError, match="current page ref again"):
        await client.messages.read_channel("C1", page_ref="same-page")


@pytest.mark.asyncio
async def test_channel_read_filters_event_records_without_failing_the_page(monkeypatch):
    client = SlackClient(bot_token="xoxb-test")

    async def get(_session, _method, **_params):
        return {
            "messages": [
                {
                    "type": "message",
                    "subtype": "channel_join",
                    "user": "U1",
                    "text": "joined",
                    "ts": "100.0",
                },
                _bot_message("200.0", "visible"),
            ],
            "response_metadata": {"next_cursor": ""},
        }

    async def resolve_channel(_session, _locator):
        return SlackChannel(ref="C1", name="product")

    monkeypatch.setattr(client.transport, "get", get)
    monkeypatch.setattr(client.directory, "resolve_channel_in_session", resolve_channel)

    page = await client.messages.read_channel("C1", limit=2)

    assert [message.text for message in page.messages] == ["visible"]
    assert page.next_page_ref is None


@pytest.mark.asyncio
async def test_monitor_history_returns_one_oldest_first_time_window(monkeypatch):
    client = SlackClient(bot_token="xoxb-test")
    history_params: list[dict[str, str]] = []

    async def get(_session, method, **params):
        if method == "conversations.info":
            return {"channel": {"id": "C1", "name": "product", "is_im": False}}
        assert method == "conversations.history"
        history_params.append(params)
        return {
            "messages": [
                {"type": "message", "user": "U1", "text": "newest", "ts": "400.0"},
                {"type": "message", "user": "U1", "text": "newer", "ts": "300.0"},
            ],
            "has_more": True,
        }

    monkeypatch.setattr(client.transport, "get", get)

    page = await client.messages.history_since("C1", oldest="50.0", latest="500.0", limit=2)

    assert [message.text for message in page.messages] == ["newer", "newest"]
    assert page.has_more is True
    assert page.next_latest == "300.0"
    assert history_params == [{"channel": "C1", "limit": "2", "oldest": "50.0", "latest": "500.0"}]


@pytest.mark.asyncio
async def test_monitor_history_rejects_an_empty_continuation_page(monkeypatch):
    client = SlackClient(bot_token="xoxb-test")

    async def get(_session, method, **_params):
        if method == "conversations.info":
            return {"channel": {"id": "C1", "name": "product", "is_im": False}}
        return {"messages": [], "has_more": True}

    monkeypatch.setattr(client.transport, "get", get)

    with pytest.raises(SlackPayloadError, match="empty page"):
        await client.messages.history_since("C1", oldest="50.0", latest="500.0")


@pytest.mark.asyncio
async def test_post_rejects_thread_ref_from_a_different_channel(monkeypatch):
    client = SlackClient(bot_token="xoxb-test", user_token="xoxp-test")

    async def resolve_channel(_session, _locator):
        return SlackChannel(ref="C1", name="product")

    async def post(*_args, **_kwargs):
        raise AssertionError("provider post must not run")

    monkeypatch.setattr(client.directory, "resolve_channel_in_session", resolve_channel)
    monkeypatch.setattr(client.transport, "post", post)

    with pytest.raises(IntegrationOperationError, match="different channel") as raised:
        await client.messages.post_message("C1", "reply", thread_ref="C2:100.0")

    assert raised.value.code == "invalid_ref"
