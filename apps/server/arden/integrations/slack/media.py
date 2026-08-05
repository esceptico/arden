import base64
from itertools import pairwise
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from arden.core.content import ImageContent
from arden.integrations.base import IntegrationOperationError
from arden.integrations.slack.directory import SlackDirectory
from arden.integrations.slack.messages import split_message_ref, timestamp_to_datetime
from arden.integrations.slack.models import (
    SlackFileInfoRequired,
    SlackFileResult,
    SlackImageFile,
    SlackOtherFile,
    SlackThreadAttachment,
    SlackThreadMessage,
    SlackThreadResult,
)
from arden.integrations.slack.transport import (
    SlackPayloadError,
    SlackTransport,
    mapping,
    next_cursor,
    optional_int,
    optional_string,
    payload_error,
    required_list,
    required_string,
    required_string_allow_empty,
)

_SLACK_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"})
_MODEL_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
_MAX_THREAD_IMAGES = 4
_MAX_SLACK_IMAGE_BYTES = 5 * 1024 * 1024
_THREAD_PAGE_SIZE = 200
_MAX_THREAD_MESSAGES = 200
_SLACK_FILE_HOST = "files.slack.com"
_SLACK_FILE_ORIGIN_HOST = "slack.com"
_SLACK_FILE_ORIGIN_PATH = "/files-pri"
_SLACK_IMAGE_DOWNLOAD_TIMEOUT_SECONDS = 15


def _format_message(user_name: str, text: str, timestamp: str, channel_name: str) -> str:
    return f"[{timestamp_to_datetime(timestamp).isoformat()}] {user_name} in #{channel_name}:\n{text}"


class SlackMedia:
    def __init__(self, transport: SlackTransport, directory: SlackDirectory):
        self._transport = transport
        self._directory = directory

    async def read_thread(self, thread_ref: str, *, after_ref: str | None = None) -> SlackThreadResult | None:
        channel_ref, timestamp = split_message_ref(thread_ref)
        params = {
            "channel": channel_ref,
            "ts": timestamp,
            "limit": str(_THREAD_PAGE_SIZE),
        }
        if after_ref is not None:
            after_channel_ref, after_timestamp = split_message_ref(after_ref)
            if after_channel_ref != channel_ref:
                raise IntegrationOperationError(
                    code="invalid_ref",
                    safe_message="Slack after_ref belongs to a different channel.",
                    retryable=False,
                )
            params["oldest"] = after_timestamp
            params["inclusive"] = "false"
        async with aiohttp.ClientSession() as session:
            data = await self._transport.get(session, "conversations.replies", **params)
            raw_messages = required_list(data, "messages", context="conversations.replies")
            if len(raw_messages) > _MAX_THREAD_MESSAGES:
                raise payload_error(
                    "conversations.replies",
                    f"returned more than {_MAX_THREAD_MESSAGES} messages in one page",
                )
            has_more = bool(next_cursor(data, context="conversations.replies"))
            messages = [
                await self._decode_thread_message(
                    session,
                    mapping(message, context="conversations.replies message"),
                )
                for message in raw_messages
            ]
            if has_more and not messages:
                raise payload_error("conversations.replies", "returned a continuation cursor without messages")
            if not messages:
                return None
            for earlier, later in pairwise(messages):
                if timestamp_to_datetime(earlier.timestamp) >= timestamp_to_datetime(later.timestamp):
                    raise payload_error("conversations.replies", "messages were not ordered oldest first")
            if after_ref is None and messages[0].timestamp != timestamp:
                raise payload_error("conversations.replies", "returned a different root message timestamp")
            if after_ref is not None and timestamp_to_datetime(messages[0].timestamp) <= timestamp_to_datetime(
                after_timestamp
            ):
                raise payload_error("conversations.replies", "returned a message outside the continuation bound")
            channel = await self._directory.resolve_channel_in_session(session, channel_ref)
            lines: list[str] = []
            model_content: list[ImageContent] = []
            for message in messages:
                text = message.text
                notes, image_blocks = await self._extract_thread_attachments(
                    session,
                    message.attachments,
                    _MAX_THREAD_IMAGES - len(model_content),
                )
                if notes:
                    text = "\n".join(part for part in [text, *notes] if part)
                model_content.extend(image_blocks)
                lines.append(_format_message(message.author_name, text, message.timestamp, channel.name))
            next_after_ref = f"{channel_ref}:{messages[-1].timestamp}" if has_more else None
            if next_after_ref:
                lines.append(f"[More replies available; continue after_ref: {next_after_ref}.]")
            return SlackThreadResult(
                text="\n\n".join(lines),
                model_content=tuple(model_content),
                has_more=has_more,
                next_after_ref=next_after_ref,
            )

    async def _decode_thread_message(
        self,
        session: aiohttp.ClientSession,
        payload: dict[str, Any],
    ) -> SlackThreadMessage:
        context = "conversations.replies message"
        if required_string(payload, "type", context=context) != "message":
            raise payload_error(context, "expected a message record")
        author_ref = optional_string(payload, "user", context=context)
        if author_ref is not None:
            author_name = await self._directory.user_name(session, author_ref)
        else:
            bot_ref = required_string(payload, "bot_id", context=context)
            bot_profile = mapping(payload.get("bot_profile"), context=f"{context}.bot_profile")
            if required_string(bot_profile, "id", context=f"{context}.bot_profile") != bot_ref:
                raise payload_error(context, "bot_profile.id does not match bot_id")
            author_ref = bot_ref
            author_name = required_string(bot_profile, "name", context=f"{context}.bot_profile")
        raw_files = payload.get("files", [])
        if not isinstance(raw_files, list):
            raise payload_error(context, "expected files array when present")
        attachments = tuple(
            attachment
            for raw_file in raw_files
            for attachment in (self._decode_thread_attachment(mapping(raw_file, context="Slack file")),)
        )
        return SlackThreadMessage(
            timestamp=required_string(payload, "ts", context=context),
            text=required_string_allow_empty(payload, "text", context=context),
            author_ref=author_ref,
            author_name=author_name,
            attachments=attachments,
        )

    async def read_file_image(self, file_ref: str) -> SlackFileResult:
        async with aiohttp.ClientSession() as session:
            data = await self._transport.get(session, "files.info", file=file_ref)
            attachment = self._decode_thread_attachment(mapping(data.get("file"), context="files.info.file"))
            if not isinstance(attachment, SlackImageFile):
                raise IntegrationOperationError(
                    code="unsupported_media",
                    safe_message=f"Slack file {file_ref} is not a supported image.",
                    retryable=False,
                )
            if attachment.ref != file_ref:
                raise payload_error("files.info.file", "returned a different file ref")
            block = await self._download_slack_image(session, attachment)
            return SlackFileResult(
                text=f"Slack image: {attachment.title}\n{self._format_image_note(attachment)}",
                model_content=(block,),
            )

    async def _extract_thread_attachments(
        self,
        session: aiohttp.ClientSession,
        attachments: tuple[SlackThreadAttachment, ...],
        remaining: int,
    ) -> tuple[list[str], list[ImageContent]]:
        notes: list[str] = []
        image_blocks: list[ImageContent] = []
        for attachment in attachments:
            if isinstance(attachment, SlackImageFile):
                notes.append(self._format_image_note(attachment))
                if len(image_blocks) < remaining:
                    image_blocks.append(await self._download_slack_image(session, attachment))
            elif isinstance(attachment, SlackFileInfoRequired):
                notes.append(f"Attached file unavailable: Slack Connect requires file info (ref: {attachment.ref})")
            else:
                notes.append(self._format_other_file_note(attachment))
        return notes, image_blocks

    @staticmethod
    def _normalized_image_mime(mime: str) -> str | None:
        normalized = mime.casefold()
        if normalized == "image/jpg":
            return "image/jpeg"
        if normalized in _SLACK_IMAGE_MIME_TYPES:
            return normalized
        return None

    def _decode_thread_attachment(self, payload: dict[str, Any]) -> SlackThreadAttachment:
        context = "Slack file"
        if optional_string(payload, "file_access", context=context) == "check_file_info":
            return SlackFileInfoRequired(ref=required_string(payload, "id", context=context))
        mime = self._normalized_image_mime(required_string(payload, "mimetype", context=context))
        title = optional_string(payload, "title", context=context) or optional_string(payload, "name", context=context)
        if title is None:
            raise payload_error(context, "image has no title or name")
        ref = required_string(payload, "id", context=context)
        size_bytes = optional_int(payload, "size", context=context)
        if mime is None:
            return SlackOtherFile(
                ref=ref,
                title=title,
                mime_type=required_string(payload, "mimetype", context=context),
                size_bytes=size_bytes,
            )
        download_url = optional_string(payload, "url_private_download", context=context) or required_string(
            payload,
            "url_private",
            context=context,
        )
        return SlackImageFile(
            ref=ref,
            title=title,
            mime_type=mime,
            size_bytes=size_bytes,
            download_url=download_url,
        )

    @staticmethod
    def _format_image_note(file_obj: SlackImageFile) -> str:
        details = [file_obj.mime_type]
        if file_obj.size_bytes is not None:
            details.append(f"{file_obj.size_bytes} bytes")
        details.append(f"ref: {file_obj.ref}")
        return f"Attached image: {file_obj.title} ({', '.join(details)})"

    @staticmethod
    def _format_other_file_note(file_obj: SlackOtherFile) -> str:
        details = [file_obj.mime_type]
        if file_obj.size_bytes is not None:
            details.append(f"{file_obj.size_bytes} bytes")
        details.append(f"ref: {file_obj.ref}")
        return f"Attached file: {file_obj.title} ({', '.join(details)})"

    async def _download_slack_image(
        self,
        session: aiohttp.ClientSession,
        file_obj: SlackImageFile,
    ) -> ImageContent:
        if file_obj.mime_type not in _MODEL_IMAGE_MIME_TYPES:
            raise IntegrationOperationError(
                code="unsupported_media",
                safe_message=f"Slack image {file_obj.ref} uses unsupported media type {file_obj.mime_type}.",
                retryable=False,
            )
        if file_obj.size_bytes is not None and file_obj.size_bytes > _MAX_SLACK_IMAGE_BYTES:
            raise IntegrationOperationError(
                code="payload_too_large",
                safe_message=f"Slack image {file_obj.ref} exceeds the 5 MiB model limit.",
                retryable=False,
            )
        if not _is_slack_file_url(file_obj.download_url):
            raise SlackPayloadError(
                context="image download",
                detail=f"file {file_obj.ref} had an invalid private download URL",
            )
        headers = {"Authorization": f"Bearer {self._transport.token_for('files.info')}"}
        try:
            async with session.get(
                file_obj.download_url,
                headers=headers,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=_SLACK_IMAGE_DOWNLOAD_TIMEOUT_SECONDS),
            ) as response:
                if 300 <= response.status < 400:
                    raise IntegrationOperationError(
                        code="provider_error",
                        safe_message="Slack image download redirected unexpectedly.",
                        retryable=False,
                    )
                if response.status >= 400:
                    raise IntegrationOperationError(
                        code="provider_error",
                        safe_message=f"Slack image download failed with HTTP {response.status}.",
                        retryable=response.status in {408, 429, 500, 502, 503, 504},
                    )
                body = await _read_bounded_body(response.content)
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise IntegrationOperationError(
                code="provider_error",
                safe_message="Slack image download failed.",
                retryable=True,
            ) from exc
        if len(body) > _MAX_SLACK_IMAGE_BYTES:
            raise IntegrationOperationError(
                code="payload_too_large",
                safe_message=f"Slack image {file_obj.ref} exceeds the 5 MiB model limit.",
                retryable=False,
            )
        detected_mime = _detect_supported_image_mime(body)
        if detected_mime is None:
            raise SlackPayloadError(
                context="image download",
                detail=f"file {file_obj.ref} did not contain supported image bytes",
            )
        return ImageContent(media_type=detected_mime, data=base64.b64encode(body).decode("ascii"))


async def _read_bounded_body(content: aiohttp.StreamReader) -> bytes:
    body = bytearray()
    while len(body) <= _MAX_SLACK_IMAGE_BYTES:
        chunk = await content.read(_MAX_SLACK_IMAGE_BYTES + 1 - len(body))
        if not chunk:
            break
        body.extend(chunk)
    return bytes(body)


def _is_slack_file_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and (
                parsed.hostname == _SLACK_FILE_HOST
                or (
                    parsed.hostname == _SLACK_FILE_ORIGIN_HOST
                    and (
                        parsed.path == _SLACK_FILE_ORIGIN_PATH or parsed.path.startswith(f"{_SLACK_FILE_ORIGIN_PATH}/")
                    )
                )
            )
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


def _detect_supported_image_mime(body: bytes) -> str | None:
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if body.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(body) >= 12 and body.startswith(b"RIFF") and body[8:12] == b"WEBP":
        return "image/webp"
    if _is_static_gif(body):
        return "image/gif"
    return None


def _skip_gif_sub_blocks(body: bytes, offset: int) -> int | None:
    while offset < len(body):
        size = body[offset]
        offset += 1
        if size == 0:
            return offset
        offset += size
    return None


def _is_static_gif(body: bytes) -> bool:
    if not (body.startswith(b"GIF87a") or body.startswith(b"GIF89a")) or len(body) < 13:
        return False
    offset = 13
    packed = body[10]
    if packed & 0x80:
        offset += 3 * (2 ** ((packed & 0x07) + 1))

    frames = 0
    while offset < len(body):
        marker = body[offset]
        if marker == 0x3B:
            return frames == 1
        if marker == 0x21:
            offset = _skip_gif_sub_blocks(body, offset + 2)
            if offset is None:
                return False
            continue
        if marker != 0x2C or offset + 10 > len(body):
            return False
        frames += 1
        if frames > 1:
            return False
        image_packed = body[offset + 9]
        offset += 10
        if image_packed & 0x80:
            offset += 3 * (2 ** ((image_packed & 0x07) + 1))
        if offset >= len(body):
            return False
        offset += 1
        offset = _skip_gif_sub_blocks(body, offset)
        if offset is None:
            return False
    return False
