import pytest

from arden.core.content import ImageContent
from arden.integrations.slack import SLACK
from arden.integrations.slack.client import SlackClient

STATIC_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"
    b"!\xf9\x04\x01\x00\x00\x00\x00"
    b",\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


class FakeResponse:
    def __init__(self, body: bytes, *, status: int = 200):
        self._body = body
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def read(self):
        return self._body


class FakeSession:
    def __init__(self, body: bytes, *, status: int = 200):
        self._body = body
        self._status = status

    def get(self, *_args, **_kwargs):
        return FakeResponse(self._body, status=self._status)


@pytest.mark.asyncio
async def test_read_thread_returns_image_blocks_from_slack_files(monkeypatch):
    client = SlackClient(bot_token="xoxb-test")

    async def fake_get(_session, method, **_params):
        assert method == "conversations.replies"
        return {
            "messages": [
                {
                    "user": "U1",
                    "text": "please inspect this",
                    "ts": "1710000000.000100",
                    "files": [
                        {
                            "id": "F1",
                            "name": "screen.png",
                            "title": "screen.png",
                            "mimetype": "image/png",
                            "size": 7,
                            "url_private_download": "https://files.slack.com/files-pri/T-F/download/screen.png",
                        }
                    ],
                }
            ]
        }

    async def fake_resolve_user(_session, _user_id):
        return "Ada"

    async def fake_resolve_channel_id(_session, channel):
        return channel, "engineering"

    async def fake_download(_session, file_obj):
        assert file_obj["id"] == "F1"
        return ImageContent(media_type="image/png", data="ZmFrZXBuZw==")

    monkeypatch.setattr(client, "_get", fake_get)
    monkeypatch.setattr(client, "_resolve_user", fake_resolve_user)
    monkeypatch.setattr(client, "_resolve_channel_id", fake_resolve_channel_id)
    monkeypatch.setattr(client, "_download_slack_image", fake_download, raising=False)

    result = await client.read_thread("C1:1710000000.000100")

    assert result is not None
    assert "please inspect this" in result.text
    assert "Attached image: screen.png" in result.text
    assert result.model_content == (ImageContent(media_type="image/png", data="ZmFrZXBuZw=="),)


@pytest.mark.asyncio
async def test_download_slack_image_rejects_non_image_bytes():
    client = SlackClient(bot_token="xoxb-test")
    file_obj = {
        "id": "F1",
        "mimetype": "image/png",
        "url_private_download": "https://files.slack.com/files-pri/T-F/download/screen.png",
    }

    result = await client._download_slack_image(FakeSession(b"<html>not an image</html>"), file_obj)

    assert result is None


@pytest.mark.asyncio
async def test_download_slack_image_uses_detected_mime_type():
    client = SlackClient(bot_token="xoxb-test")
    file_obj = {
        "id": "F1",
        "mimetype": "image/png",
        "url_private_download": "https://files.slack.com/files-pri/T-F/download/screen.png",
    }

    result = await client._download_slack_image(FakeSession(b"\xff\xd8\xff\xe0jpeg-bytes"), file_obj)

    assert result == ImageContent(media_type="image/jpeg", data="/9j/4GpwZWctYnl0ZXM=")


@pytest.mark.asyncio
async def test_download_slack_image_accepts_static_gif_for_model_payloads():
    client = SlackClient(bot_token="xoxb-test")
    file_obj = {
        "id": "F1",
        "mimetype": "image/gif",
        "url_private_download": "https://files.slack.com/files-pri/T-F/download/screen.gif",
    }

    result = await client._download_slack_image(FakeSession(STATIC_GIF), file_obj)

    assert result == ImageContent(
        media_type="image/gif", data="R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAICRAEAOw=="
    )


@pytest.mark.asyncio
async def test_download_slack_image_rejects_animated_gif_for_model_payloads():
    client = SlackClient(bot_token="xoxb-test")
    file_obj = {
        "id": "F1",
        "mimetype": "image/gif",
        "url_private_download": "https://files.slack.com/files-pri/T-F/download/screen.gif",
    }

    animated = STATIC_GIF[:-1] + STATIC_GIF[STATIC_GIF.index(b",") :]

    result = await client._download_slack_image(FakeSession(animated), file_obj)

    assert result is None


@pytest.mark.asyncio
async def test_read_file_image_returns_model_visible_image(monkeypatch):
    client = SlackClient(bot_token="xoxb-test")

    async def fake_get(_session, method, **params):
        assert method == "files.info"
        assert params == {"file": "F1"}
        return {
            "file": {
                "id": "F1",
                "name": "screen.png",
                "mimetype": "image/png",
                "url_private_download": "https://files.slack.com/files-pri/T-F/download/screen.png",
            }
        }

    async def fake_download(_session, file_obj):
        assert file_obj["id"] == "F1"
        return ImageContent(media_type="image/png", data="iVBORw0KGgo=")

    monkeypatch.setattr(client, "_get", fake_get)
    monkeypatch.setattr(client, "_download_slack_image", fake_download)

    result = await client.read_file_image("F1")

    assert result is not None
    assert "Slack image: screen.png" in result.text
    assert result.model_content == (ImageContent(media_type="image/png", data="iVBORw0KGgo="),)


def test_slack_integration_registers_file_tool():
    assert "slack_file" in SLACK.tools
