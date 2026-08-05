import base64
import codecs
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.header import decode_header as decode_rfc2047
from email.message import Message
from html import unescape

from arden.integrations.base import IntegrationOperationError
from arden.integrations.gmail.compose import has_header_line_break
from arden.integrations.gmail.models import (
    GmailAttachmentBody,
    GmailMessage,
    GmailMessageRefPage,
    GmailMessageSummary,
    GmailMutationReceipt,
    GmailReplySource,
)

_MAX_TEXT_BYTES = 5 * 1024 * 1024
_MAX_MIME_PARTS = 512
_MAX_MIME_DEPTH = 32
_MAX_HEADERS = 256
_MAX_HEADER_NAME_CHARS = 256
_MAX_HEADER_VALUE_CHARS = 16_384
_MAX_HEADER_BYTES = 64 * 1024
_MAX_MESSAGE_LIST_ITEMS = 100
_MAX_PROVIDER_REF_CHARS = 512
_MAX_PAGE_TOKEN_CHARS = 4096
_MAX_SNIPPET_CHARS = 4096
_MAX_LABELS = 256
_MAX_LABEL_CHARS = 512


class GmailPayloadError(IntegrationOperationError):
    """Gmail returned a successful response that violates its API contract."""

    def __init__(self, operation: str):
        super().__init__(
            code="invalid_provider_payload",
            safe_message=f"Gmail returned an invalid response for {operation}.",
        )


class GmailPayloadTooLargeError(IntegrationOperationError):
    def __init__(self):
        super().__init__(
            code="payload_too_large",
            safe_message="Gmail message content exceeds the supported size limit.",
            retryable=False,
        )


def qualify_message_ref(account_ref: str, provider_ref: str) -> str:
    return f"{account_ref}:{provider_ref}"


def _mapping(value: object, operation: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise GmailPayloadError(operation)
    return value


def _required_string(
    payload: dict[str, object],
    field: str,
    operation: str,
    *,
    max_length: int | None = None,
) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value or (max_length is not None and len(value) > max_length):
        raise GmailPayloadError(operation)
    return value


def _optional_string(
    payload: dict[str, object],
    field: str,
    operation: str,
    *,
    max_length: int | None = None,
) -> str:
    value = payload.get(field, "")
    if not isinstance(value, str) or (max_length is not None and len(value) > max_length):
        raise GmailPayloadError(operation)
    return value


def _string_tuple(payload: dict[str, object], field: str, operation: str) -> tuple[str, ...]:
    value = payload.get(field, [])
    if (
        not isinstance(value, list)
        or len(value) > _MAX_LABELS
        or any(not isinstance(item, str) or not item or len(item) > _MAX_LABEL_CHARS for item in value)
    ):
        raise GmailPayloadError(operation)
    return tuple(value)


def _non_negative_size(payload: dict[str, object], operation: str) -> int:
    size = payload.get("size")
    if type(size) is not int or size < 0:
        raise GmailPayloadError(operation)
    return size


def _decode_base64_bytes(data: str, operation: str) -> bytes:
    try:
        padded = data + ("=" * (-len(data) % 4))
        return base64.b64decode(padded, altchars=b"-_", validate=True)
    except ValueError as exc:
        raise GmailPayloadError(operation) from exc


def decode_attachment_body(raw: object) -> GmailAttachmentBody:
    body = _mapping(raw, "message attachment")
    size = _non_negative_size(body, "message attachment")
    if size > _MAX_TEXT_BYTES:
        raise GmailPayloadTooLargeError
    data_value = body.get("data")
    if not isinstance(data_value, str):
        raise GmailPayloadError("message attachment")
    data = _decode_base64_bytes(data_value, "message attachment")
    if len(data) != size:
        raise GmailPayloadError("message attachment")
    return GmailAttachmentBody(data=data, size_bytes=size)


def _charset(part: dict[str, object]) -> str:
    raw_headers = part.get("headers", [])
    headers = _headers(raw_headers, "message content headers")
    content_type = headers.get("content-type")
    if content_type is None:
        return "utf-8"
    parsed = Message()
    parsed["content-type"] = content_type
    charset = parsed.get_param("charset")
    if charset is None:
        return "utf-8"
    if not isinstance(charset, str) or not charset.strip():
        raise GmailPayloadError("message content charset")
    try:
        return codecs.lookup(charset.strip()).name
    except LookupError as exc:
        raise GmailPayloadError("message content charset") from exc


@dataclass(slots=True)
class _MimeBudget:
    parts: int = 0
    text_bytes: int = 0

    def enter_part(self, depth: int) -> None:
        self.parts += 1
        if self.parts > _MAX_MIME_PARTS or depth > _MAX_MIME_DEPTH:
            raise GmailPayloadTooLargeError

    def reserve(self, size: int) -> None:
        if self.text_bytes + size > _MAX_TEXT_BYTES:
            raise GmailPayloadTooLargeError
        self.text_bytes += size


def _part_text(
    part: dict[str, object],
    load_attachment: Callable[[str], GmailAttachmentBody],
    budget: _MimeBudget,
) -> str:
    body = _mapping(part.get("body"), "message content")
    advertised_size = _non_negative_size(body, "message content")
    budget.reserve(advertised_size)
    data = body.get("data")
    attachment_id = body.get("attachmentId")
    if data is not None and not isinstance(data, str):
        raise GmailPayloadError("message content")
    if isinstance(data, str) and data and attachment_id is not None:
        raise GmailPayloadError("message content")
    if attachment_id is not None:
        if not isinstance(attachment_id, str) or not attachment_id or len(attachment_id) > _MAX_PROVIDER_REF_CHARS:
            raise GmailPayloadError("message content")
        attachment = load_attachment(attachment_id)
        if not isinstance(attachment, GmailAttachmentBody):
            raise GmailPayloadError("message attachment")
        if (
            not isinstance(attachment.data, bytes)
            or type(attachment.size_bytes) is not int
            or attachment.size_bytes < 0
            or attachment.size_bytes != advertised_size
            or len(attachment.data) != advertised_size
        ):
            raise GmailPayloadError("message attachment")
        decoded = attachment.data
    elif data is not None:
        decoded = _decode_base64_bytes(data, "message content")
        if len(decoded) != advertised_size:
            raise GmailPayloadError("message content")
    elif advertised_size == 0:
        decoded = b""
    else:
        raise GmailPayloadError("message content")
    try:
        return decoded.decode(_charset(part))
    except UnicodeError as exc:
        raise GmailPayloadError("message content charset") from exc


def _is_attachment(part: dict[str, object]) -> bool:
    if _optional_string(part, "filename", "message content", max_length=1024):
        return True
    headers = _headers(part.get("headers", []), "message content headers")
    disposition = headers.get("content-disposition")
    if disposition is None:
        return False
    return disposition.partition(";")[0].strip().casefold() == "attachment"


def _email_parts(
    part: dict[str, object],
    load_attachment: Callable[[str], GmailAttachmentBody],
    budget: _MimeBudget,
    depth: int = 0,
) -> tuple[str, str]:
    budget.enter_part(depth)
    plain_content: list[str] = []
    html_content: list[str] = []
    mime_type = _required_string(part, "mimeType", "message content", max_length=256)

    if _is_attachment(part):
        return "", ""

    if mime_type.startswith("multipart/"):
        parts = part.get("parts")
        if not isinstance(parts, list):
            raise GmailPayloadError("message content")
        for raw_sub_part in parts:
            plain, html = _email_parts(
                _mapping(raw_sub_part, "message content"),
                load_attachment,
                budget,
                depth + 1,
            )
            plain_content.append(plain)
            html_content.append(html)
    elif mime_type == "text/plain":
        plain_content.append(_part_text(part, load_attachment, budget))
    elif mime_type == "text/html":
        html_content.append(_part_text(part, load_attachment, budget))

    return "".join(plain_content), "".join(html_content)


def _html_to_plain(html: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    decoded: list[str] = []
    try:
        for part, encoding in decode_rfc2047(value):
            if isinstance(part, bytes):
                part = part.decode(encoding or "utf-8")
            decoded.append(part)
    except (LookupError, UnicodeError) as exc:
        raise GmailPayloadError("message headers") from exc
    result = "".join(decoded).strip()
    if has_header_line_break(result) or len(result) > _MAX_HEADER_VALUE_CHARS:
        raise GmailPayloadError("message headers")
    return result


def _headers(raw_headers: object, operation: str) -> dict[str, str]:
    if not isinstance(raw_headers, list) or len(raw_headers) > _MAX_HEADERS:
        raise GmailPayloadError(operation)
    headers: dict[str, str] = {}
    total_bytes = 0
    for raw_header in raw_headers:
        header = _mapping(raw_header, operation)
        name = _required_string(header, "name", operation, max_length=_MAX_HEADER_NAME_CHARS)
        value = _optional_string(header, "value", operation, max_length=_MAX_HEADER_VALUE_CHARS)
        if has_header_line_break(name) or has_header_line_break(value):
            raise GmailPayloadError(operation)
        try:
            total_bytes += len(name.encode()) + len(value.encode())
        except UnicodeError as exc:
            raise GmailPayloadError(operation) from exc
        if total_bytes > _MAX_HEADER_BYTES:
            raise GmailPayloadTooLargeError
        headers[name.casefold()] = value
    return headers


def _received_at(internal_date: str) -> datetime:
    try:
        return datetime.fromtimestamp(int(internal_date) / 1000, tz=UTC)
    except (ValueError, OSError, OverflowError) as exc:
        raise GmailPayloadError("message date") from exc


def decode_profile(raw: object) -> str:
    return _required_string(_mapping(raw, "profile"), "emailAddress", "profile", max_length=320)


def decode_message_summary(raw: object, account_ref: str) -> GmailMessageSummary:
    message = _mapping(raw, "message")
    provider_message_id = _required_string(message, "id", "message", max_length=_MAX_PROVIDER_REF_CHARS)
    provider_thread_id = _required_string(message, "threadId", "message", max_length=_MAX_PROVIDER_REF_CHARS)
    internal_date = _required_string(message, "internalDate", "message", max_length=32)
    payload = _mapping(message.get("payload"), "message")
    headers = _headers(payload.get("headers"), "message headers")
    return GmailMessageSummary(
        ref=qualify_message_ref(account_ref, provider_message_id),
        account_ref=account_ref,
        thread_ref=qualify_message_ref(account_ref, provider_thread_id),
        subject=_decode_header(headers["subject"]) if "subject" in headers else None,
        sender=_decode_header(headers.get("from")),
        recipient=_decode_header(headers.get("to")),
        reply_to=_decode_header(headers.get("reply-to") or headers.get("from")),
        snippet=_optional_string(message, "snippet", "message", max_length=_MAX_SNIPPET_CHARS),
        received_at=_received_at(internal_date),
        labels=_string_tuple(message, "labelIds", "message"),
    )


def decode_message(
    raw: object,
    account_ref: str,
    load_attachment: Callable[[str], GmailAttachmentBody],
) -> GmailMessage:
    message = _mapping(raw, "message")
    summary = decode_message_summary(message, account_ref)
    plain_text, html_text = _email_parts(
        _mapping(message.get("payload"), "message content"),
        load_attachment,
        _MimeBudget(),
    )
    return GmailMessage(summary=summary, content=plain_text.strip() or _html_to_plain(html_text))


def decode_message_refs(raw: object) -> GmailMessageRefPage:
    page = _mapping(raw, "message list")
    raw_messages = page.get("messages", [])
    if not isinstance(raw_messages, list) or len(raw_messages) > _MAX_MESSAGE_LIST_ITEMS:
        raise GmailPayloadError("message list")
    message_ids = tuple(
        _required_string(
            _mapping(item, "message list"),
            "id",
            "message list",
            max_length=_MAX_PROVIDER_REF_CHARS,
        )
        for item in raw_messages
    )
    next_page_token = page.get("nextPageToken")
    if next_page_token is not None and (
        not isinstance(next_page_token, str)
        or not next_page_token
        or len(next_page_token) > _MAX_PAGE_TOKEN_CHARS
        or any(not 0x21 <= ord(character) <= 0x7E for character in next_page_token)
    ):
        raise GmailPayloadError("message list")
    return GmailMessageRefPage(
        provider_message_ids=message_ids,
        next_page_token=next_page_token,
    )


def decode_mutation_receipt(
    raw: object,
    account_ref: str,
    recipient: str,
    operation: str,
) -> GmailMutationReceipt:
    payload = _mapping(raw, operation)
    return GmailMutationReceipt(
        message_ref=qualify_message_ref(
            account_ref,
            _required_string(payload, "id", operation, max_length=_MAX_PROVIDER_REF_CHARS),
        ),
        thread_ref=qualify_message_ref(
            account_ref,
            _required_string(payload, "threadId", operation, max_length=_MAX_PROVIDER_REF_CHARS),
        ),
        recipient=recipient,
    )


def decode_reply_source(raw: object) -> GmailReplySource:
    message = _mapping(raw, "reply source")
    payload = _mapping(message.get("payload"), "reply source")
    headers = _headers(payload.get("headers"), "reply source headers")
    recipient = _decode_header(headers.get("reply-to") or headers.get("from"))
    if not recipient:
        raise IntegrationOperationError(
            code="invalid_ref",
            safe_message="The original Gmail message has no reply sender.",
        )
    message_id_header = headers.get("message-id", "").strip()
    if not message_id_header:
        raise GmailPayloadError("reply source headers")
    return GmailReplySource(
        provider_message_id=_required_string(
            message,
            "id",
            "reply source",
            max_length=_MAX_PROVIDER_REF_CHARS,
        ),
        provider_thread_id=_required_string(
            message,
            "threadId",
            "reply source",
            max_length=_MAX_PROVIDER_REF_CHARS,
        ),
        recipient=recipient,
        subject_header=headers.get("subject"),
        subject=_decode_header(headers["subject"]) if "subject" in headers else None,
        message_id_header=message_id_header,
        references_header=headers.get("references", "").strip(),
    )
