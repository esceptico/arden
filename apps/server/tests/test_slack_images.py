import aiohttp
import pytest

from arden.core.content import ImageContent
from arden.integrations.base import IntegrationOperationError
from arden.integrations.slack import SLACK
from arden.integrations.slack.client import SlackClient
from arden.integrations.slack.models import SlackChannel, SlackImageFile
from arden.integrations.slack.transport import SlackPayloadError

MAX_SLACK_IMAGE_BYTES = 5 * 1024 * 1024

STATIC_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"
    b"!\xf9\x04\x01\x00\x00\x00\x00"
    b",\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


def _image(*, mime_type: str = "image/png", url: str | None = None) -> SlackImageFile:
    return SlackImageFile(
        ref="F1",
        title="screen.png",
        mime_type=mime_type,
        size_bytes=None,
        download_url=url or "https://files.slack.com/files-pri/T-F/download/screen.png",
    )


def _bot_message(
    timestamp: str,
    text: str,
    *,
    files: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "type": "message",
        "bot_id": "B1",
        "bot_profile": {"id": "B1", "name": "Ada"},
        "text": text,
        "ts": timestamp,
        "files": files or [],
    }


class FakeResponse:
    def __init__(self, body: bytes, *, status: int = 200):
        self.content = FakeContent(body)
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class FakeContent:
    def __init__(self, body: bytes):
        self._body = body
        self._offset = 0
        self.read_sizes: list[int] = []

    async def read(self, size: int = -1):
        self.read_sizes.append(size)
        end = len(self._body) if size < 0 else self._offset + size
        chunk = self._body[self._offset : end]
        self._offset += len(chunk)
        return chunk


class FakeSession:
    def __init__(self, body: bytes, *, status: int = 200):
        self._body = body
        self._status = status
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.response: FakeResponse | None = None

    def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        self.response = FakeResponse(self._body, status=self._status)
        return self.response


class TimeoutSession:
    def get(self, *_args, **_kwargs):
        raise TimeoutError


@pytest.mark.asyncio
async def test_read_thread_returns_image_blocks_from_slack_files(monkeypatch):
    client = SlackClient(bot_token="xoxb-test")

    async def fake_get(_session, method, **_params):
        assert method == "conversations.replies"
        return {
            "messages": [
                _bot_message(
                    "1710000000.000100",
                    "please inspect this",
                    files=[
                        {
                            "id": "F1",
                            "name": "screen.png",
                            "title": "screen.png",
                            "mimetype": "image/png",
                            "size": 7,
                            "url_private_download": "https://files.slack.com/files-pri/T-F/download/screen.png",
                        }
                    ],
                )
            ],
            "response_metadata": {"next_cursor": ""},
        }

    async def fake_resolve_channel_id(_session, channel):
        return SlackChannel(ref=channel, name="engineering")

    async def fake_download(_session, file_obj):
        assert file_obj.ref == "F1"
        return ImageContent(media_type="image/png", data="ZmFrZXBuZw==")

    monkeypatch.setattr(client.transport, "get", fake_get)
    monkeypatch.setattr(client.directory, "resolve_channel_in_session", fake_resolve_channel_id)
    monkeypatch.setattr(client.media, "_download_slack_image", fake_download)

    result = await client.media.read_thread("C1:1710000000.000100")

    assert result is not None
    assert "please inspect this" in result.text
    assert "Attached image: screen.png" in result.text
    assert result.model_content == (ImageContent(media_type="image/png", data="ZmFrZXBuZw=="),)


@pytest.mark.asyncio
async def test_read_thread_returns_a_bounded_page_with_a_semantic_continuation_ref(monkeypatch):
    client = SlackClient(bot_token="xoxb-test")
    oldest_values: list[str | None] = []

    async def fake_get(_session, method, **params):
        assert method == "conversations.replies"
        oldest_values.append(params.get("oldest"))
        if "oldest" not in params:
            return {
                "messages": [_bot_message("100.0", "first")],
                "response_metadata": {"next_cursor": "provider-page-2"},
            }
        return {
            "messages": [_bot_message("200.0", "second")],
            "response_metadata": {"next_cursor": ""},
        }

    async def fake_resolve_channel_id(_session, channel):
        return SlackChannel(ref=channel, name="engineering")

    monkeypatch.setattr(client.transport, "get", fake_get)
    monkeypatch.setattr(client.directory, "resolve_channel_in_session", fake_resolve_channel_id)

    first_page = await client.media.read_thread("C1:100.0")
    assert first_page is not None
    assert "first" in first_page.text
    assert "second" not in first_page.text
    assert first_page.has_more is True
    assert first_page.next_after_ref == "C1:100.0"
    assert "continue after_ref: C1:100.0" in first_page.text

    second_page = await client.media.read_thread("C1:100.0", after_ref=first_page.next_after_ref)
    assert second_page is not None
    assert "second" in second_page.text
    assert second_page.has_more is False
    assert second_page.next_after_ref is None
    assert oldest_values == [None, "100.0"]


@pytest.mark.asyncio
async def test_read_thread_rejects_empty_continuation_page(monkeypatch):
    client = SlackClient(bot_token="xoxb-test")

    async def fake_get(_session, _method, **_params):
        return {"messages": [], "response_metadata": {"next_cursor": "page-2"}}

    monkeypatch.setattr(client.transport, "get", fake_get)

    with pytest.raises(SlackPayloadError, match="continuation cursor without messages"):
        await client.media.read_thread("C1:100.0")


@pytest.mark.asyncio
async def test_read_thread_rejects_out_of_order_messages(monkeypatch):
    client = SlackClient(bot_token="xoxb-test")

    async def fake_get(_session, _method, **_params):
        return {
            "messages": [_bot_message("100.0", "root"), _bot_message("90.0", "older")],
            "response_metadata": {"next_cursor": ""},
        }

    monkeypatch.setattr(client.transport, "get", fake_get)

    with pytest.raises(SlackPayloadError, match="not ordered oldest first"):
        await client.media.read_thread("C1:100.0")


@pytest.mark.asyncio
async def test_read_thread_keeps_slack_connect_file_placeholders_visible(monkeypatch):
    client = SlackClient(bot_token="xoxb-test")

    async def fake_get(_session, _method, **_params):
        return {
            "messages": [
                _bot_message(
                    "100.0",
                    "shared file",
                    files=[{"id": "F1", "file_access": "check_file_info"}],
                )
            ],
            "response_metadata": {"next_cursor": ""},
        }

    async def fake_resolve_channel_id(_session, channel):
        return SlackChannel(ref=channel, name="engineering")

    monkeypatch.setattr(client.transport, "get", fake_get)
    monkeypatch.setattr(client.directory, "resolve_channel_in_session", fake_resolve_channel_id)

    result = await client.media.read_thread("C1:100.0")

    assert result is not None
    assert "Attached file unavailable: Slack Connect requires file info (ref: F1)" in result.text


@pytest.mark.asyncio
async def test_read_thread_keeps_non_image_attachments_visible(monkeypatch):
    client = SlackClient(bot_token="xoxb-test")

    async def fake_get(_session, _method, **_params):
        return {
            "messages": [
                _bot_message(
                    "100.0",
                    "report attached",
                    files=[{"id": "F1", "name": "report.pdf", "mimetype": "application/pdf", "size": 42}],
                )
            ],
            "response_metadata": {"next_cursor": ""},
        }

    async def fake_resolve_channel_id(_session, channel):
        return SlackChannel(ref=channel, name="engineering")

    monkeypatch.setattr(client.transport, "get", fake_get)
    monkeypatch.setattr(client.directory, "resolve_channel_in_session", fake_resolve_channel_id)

    result = await client.media.read_thread("C1:100.0")

    assert result is not None
    assert "Attached file: report.pdf (application/pdf, 42 bytes, ref: F1)" in result.text


@pytest.mark.asyncio
async def test_read_thread_accepts_url_private_images_from_slack_origin(monkeypatch):
    client = SlackClient(bot_token="xoxb-test")

    async def fake_get(_session, _method, **_params):
        return {
            "messages": [
                _bot_message(
                    "100.0",
                    "legacy image URL",
                    files=[
                        {
                            "id": "F1",
                            "name": "screen.png",
                            "mimetype": "image/png",
                            "url_private": "https://files.slack.com/files-pri/T-F/screen.png",
                        }
                    ],
                )
            ],
            "response_metadata": {"next_cursor": ""},
        }

    async def fake_resolve_channel_id(_session, channel):
        return SlackChannel(ref=channel, name="engineering")

    async def fake_download(_session, file_obj):
        assert file_obj.download_url == "https://files.slack.com/files-pri/T-F/screen.png"
        return ImageContent(media_type="image/png", data="iVBORw0KGgo=")

    monkeypatch.setattr(client.transport, "get", fake_get)
    monkeypatch.setattr(client.directory, "resolve_channel_in_session", fake_resolve_channel_id)
    monkeypatch.setattr(client.media, "_download_slack_image", fake_download)

    result = await client.media.read_thread("C1:100.0")

    assert result is not None
    assert result.model_content == (ImageContent(media_type="image/png", data="iVBORw0KGgo="),)


@pytest.mark.asyncio
async def test_thread_pagination_rejects_cursor_cycle(monkeypatch):
    client = SlackClient(bot_token="xoxb-test")

    async def fake_get(_session, _method, **_params):
        return {"messages": [], "response_metadata": {"next_cursor": "same"}}

    monkeypatch.setattr(client.transport, "get", fake_get)

    with pytest.raises(SlackPayloadError, match="cursor 'same' repeated"):
        async for _page in client.transport.cursor_pages(None, "conversations.replies"):
            pass


@pytest.mark.asyncio
async def test_thread_pagination_fails_at_explicit_page_limit(monkeypatch):
    client = SlackClient(bot_token="xoxb-test")
    page = 0

    async def fake_get(_session, _method, **_params):
        nonlocal page
        page += 1
        return {"messages": [], "response_metadata": {"next_cursor": f"page-{page}"}}

    monkeypatch.setattr(client.transport, "get", fake_get)

    with pytest.raises(IntegrationOperationError, match="2-page safety limit") as raised:
        async for _page in client.transport.cursor_pages(None, "conversations.replies", page_limit=2):
            pass
    assert raised.value.code == "pagination_limit"


@pytest.mark.asyncio
async def test_download_slack_image_rejects_non_image_bytes():
    client = SlackClient(bot_token="xoxb-test")

    with pytest.raises(SlackPayloadError, match="did not contain supported image bytes"):
        await client.media._download_slack_image(FakeSession(b"<html>not an image</html>"), _image())


@pytest.mark.asyncio
async def test_download_slack_image_uses_detected_mime_type():
    client = SlackClient(bot_token="xoxb-test")
    session = FakeSession(b"\xff\xd8\xff\xe0jpeg-bytes")

    result = await client.media._download_slack_image(session, _image())

    assert result == ImageContent(media_type="image/jpeg", data="/9j/4GpwZWctYnl0ZXM=")
    assert session.response is not None
    assert session.response.content.read_sizes == [MAX_SLACK_IMAGE_BYTES + 1, MAX_SLACK_IMAGE_BYTES - 13]


@pytest.mark.asyncio
async def test_download_slack_image_reads_at_most_the_limit_plus_one_byte():
    client = SlackClient(bot_token="xoxb-test")
    session = FakeSession(b"\x89PNG\r\n\x1a\n" + b"x" * MAX_SLACK_IMAGE_BYTES)

    with pytest.raises(IntegrationOperationError, match="exceeds the 5 MiB model limit") as raised:
        await client.media._download_slack_image(session, _image())

    assert raised.value.code == "payload_too_large"
    assert session.response is not None
    assert session.response.content.read_sizes == [MAX_SLACK_IMAGE_BYTES + 1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://files.slack.com/files-pri/T-F/download/screen.png",
        "https://example.test/download/screen.png",
        "https://files.slack.com.evil.test/download/screen.png",
        "https://files.slack.com:444/download/screen.png",
    ],
)
async def test_download_slack_image_rejects_untrusted_url_before_request(url):
    client = SlackClient(bot_token="xoxb-test")
    session = FakeSession(b"\x89PNG\r\n\x1a\n")

    with pytest.raises(SlackPayloadError, match="invalid private download URL") as raised:
        await client.media._download_slack_image(session, _image(url=url))

    assert raised.value.code == "invalid_provider_response"
    assert session.calls == []


@pytest.mark.asyncio
async def test_download_slack_image_disables_redirects_before_sending_bearer_token():
    client = SlackClient(bot_token="xoxb-test")
    session = FakeSession(b"", status=302)

    with pytest.raises(IntegrationOperationError, match="redirected unexpectedly") as raised:
        await client.media._download_slack_image(session, _image())

    assert raised.value.code == "provider_error"
    assert len(session.calls) == 1
    args, kwargs = session.calls[0]
    assert args == ("https://files.slack.com/files-pri/T-F/download/screen.png",)
    assert kwargs["headers"] == {"Authorization": "Bearer xoxb-test"}
    assert kwargs["allow_redirects"] is False
    assert isinstance(kwargs["timeout"], aiohttp.ClientTimeout)
    assert kwargs["timeout"].total == 15


@pytest.mark.asyncio
async def test_download_slack_image_translates_timeout_to_typed_provider_error():
    client = SlackClient(bot_token="xoxb-test")

    with pytest.raises(IntegrationOperationError, match="Slack image download failed") as raised:
        await client.media._download_slack_image(TimeoutSession(), _image())

    assert raised.value.retryable is True


@pytest.mark.asyncio
async def test_download_slack_image_accepts_slack_origin_files_pri_url():
    client = SlackClient(bot_token="xoxb-test")
    session = FakeSession(b"\x89PNG\r\n\x1a\n")

    result = await client.media._download_slack_image(
        session,
        _image(url="https://slack.com/files-pri/T-F/download/screen.png"),
    )

    assert result == ImageContent(media_type="image/png", data="iVBORw0KGgo=")


@pytest.mark.asyncio
async def test_download_slack_image_accepts_static_gif_for_model_payloads():
    client = SlackClient(bot_token="xoxb-test")

    result = await client.media._download_slack_image(
        FakeSession(STATIC_GIF),
        _image(mime_type="image/gif", url="https://files.slack.com/files-pri/T-F/download/screen.gif"),
    )

    assert result == ImageContent(
        media_type="image/gif", data="R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAICRAEAOw=="
    )


@pytest.mark.asyncio
async def test_download_slack_image_rejects_animated_gif_for_model_payloads():
    client = SlackClient(bot_token="xoxb-test")

    animated = STATIC_GIF[:-1] + STATIC_GIF[STATIC_GIF.index(b",") :]

    with pytest.raises(SlackPayloadError, match="did not contain supported image bytes"):
        await client.media._download_slack_image(
            FakeSession(animated),
            _image(mime_type="image/gif", url="https://files.slack.com/files-pri/T-F/download/screen.gif"),
        )


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
        assert file_obj.ref == "F1"
        return ImageContent(media_type="image/png", data="iVBORw0KGgo=")

    monkeypatch.setattr(client.transport, "get", fake_get)
    monkeypatch.setattr(client.media, "_download_slack_image", fake_download)

    result = await client.media.read_file_image("F1")

    assert result is not None
    assert "Slack image: screen.png" in result.text
    assert result.model_content == (ImageContent(media_type="image/png", data="iVBORw0KGgo="),)


@pytest.mark.asyncio
async def test_read_file_image_rejects_non_image_file(monkeypatch):
    client = SlackClient(bot_token="xoxb-test")

    async def fake_get(_session, _method, **_params):
        return {
            "file": {
                "id": "F1",
                "name": "report.pdf",
                "mimetype": "application/pdf",
                "url_private_download": "https://files.slack.com/report.pdf",
            }
        }

    monkeypatch.setattr(client.transport, "get", fake_get)

    with pytest.raises(IntegrationOperationError, match="not a supported image") as raised:
        await client.media.read_file_image("F1")
    assert raised.value.code == "unsupported_media"


@pytest.mark.asyncio
async def test_read_file_image_rejects_a_different_returned_ref(monkeypatch):
    client = SlackClient(bot_token="xoxb-test")

    async def fake_get(_session, _method, **_params):
        return {
            "file": {
                "id": "F2",
                "name": "screen.png",
                "mimetype": "image/png",
                "url_private_download": "https://files.slack.com/screen.png",
            }
        }

    monkeypatch.setattr(client.transport, "get", fake_get)

    with pytest.raises(SlackPayloadError, match="different file ref"):
        await client.media.read_file_image("F1")


def test_slack_integration_registers_file_tool():
    assert "slack_file" in SLACK.tools
