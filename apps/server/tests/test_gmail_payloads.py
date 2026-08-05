import base64

import pytest

from arden.integrations.gmail import payloads
from arden.integrations.gmail.models import GmailAttachmentBody
from arden.integrations.gmail.payloads import (
    GmailPayloadError,
    GmailPayloadTooLargeError,
    decode_attachment_body,
    decode_message,
    decode_message_refs,
    decode_message_summary,
    decode_reply_source,
)


def _encoded(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _message(part: dict[str, object]) -> dict[str, object]:
    return {
        "id": "message-1",
        "threadId": "thread-1",
        "internalDate": "1785888000000",
        "snippet": "provider preview",
        "labelIds": ["INBOX"],
        "payload": {
            "headers": [
                {"name": "From", "value": "ada@example.test"},
                {"name": "To", "value": "me@example.test"},
            ],
            **part,
        },
    }


def _text_part(
    data: bytes,
    *,
    content_type: str = "text/plain; charset=utf-8",
) -> dict[str, object]:
    return {
        "mimeType": "text/plain",
        "headers": [{"name": "Content-Type", "value": content_type}],
        "body": {"size": len(data), "data": _encoded(data)},
    }


def _unexpected_attachment(_attachment_id: str) -> GmailAttachmentBody:
    raise AssertionError("attachment loader must not be called")


@pytest.mark.parametrize("size", [-1, True, "1", None])
def test_attachment_body_rejects_invalid_size(size: object):
    with pytest.raises(GmailPayloadError):
        decode_attachment_body({"size": size, "data": "YQ"})


def test_attachment_body_decodes_to_typed_bytes_and_validates_length():
    assert decode_attachment_body({"size": 1, "data": "YQ"}) == GmailAttachmentBody(
        data=b"a",
        size_bytes=1,
    )
    assert decode_attachment_body({"size": 0, "data": ""}) == GmailAttachmentBody(
        data=b"",
        size_bytes=0,
    )
    with pytest.raises(GmailPayloadError):
        decode_attachment_body({"size": 2, "data": "YQ"})


def test_empty_inline_data_with_attachment_id_loads_typed_attachment():
    loaded: list[str] = []
    part = {
        "mimeType": "text/plain",
        "headers": [],
        "body": {"size": 5, "data": "", "attachmentId": "attachment-1"},
    }

    message = decode_message(
        _message(part),
        "me@example.test",
        lambda attachment_id: loaded.append(attachment_id) or GmailAttachmentBody(b"hello", 5),
    )

    assert message.content == "hello"
    assert loaded == ["attachment-1"]


@pytest.mark.parametrize(
    "attachment",
    [
        GmailAttachmentBody(data=b"hello", size_bytes=True),
        GmailAttachmentBody(data=b"hello", size_bytes=-1),
        GmailAttachmentBody(data=b"tiny", size_bytes=5),
    ],
)
def test_attachment_loader_result_is_validated(attachment: GmailAttachmentBody):
    part = {
        "mimeType": "text/plain",
        "headers": [],
        "body": {"size": 5, "attachmentId": "attachment-1"},
    }
    with pytest.raises(GmailPayloadError):
        decode_message(_message(part), "me@example.test", lambda _ref: attachment)


def test_nonempty_inline_data_and_attachment_id_is_invalid_without_fetch():
    fetched = False

    def load(_attachment_id: str) -> GmailAttachmentBody:
        nonlocal fetched
        fetched = True
        return GmailAttachmentBody(b"hello", 5)

    part = {
        "mimeType": "text/plain",
        "headers": [],
        "body": {"size": 5, "data": _encoded(b"hello"), "attachmentId": "attachment-1"},
    }
    with pytest.raises(GmailPayloadError):
        decode_message(_message(part), "me@example.test", load)
    assert fetched is False


def test_advertised_oversize_rejects_before_attachment_fetch():
    fetched = False

    def load(_attachment_id: str) -> GmailAttachmentBody:
        nonlocal fetched
        fetched = True
        return GmailAttachmentBody(b"", 0)

    part = {
        "mimeType": "text/plain",
        "headers": [],
        "body": {"size": 5 * 1024 * 1024 + 1, "attachmentId": "attachment-1"},
    }
    with pytest.raises(GmailPayloadTooLargeError) as raised:
        decode_message(_message(part), "me@example.test", load)
    assert raised.value.code == "payload_too_large"
    assert fetched is False


def test_text_budget_is_shared_across_mime_parts(monkeypatch):
    monkeypatch.setattr(payloads, "_MAX_TEXT_BYTES", 3)
    multipart = {
        "mimeType": "multipart/mixed",
        "parts": [_text_part(b"ab"), _text_part(b"cd")],
    }
    with pytest.raises(GmailPayloadTooLargeError):
        decode_message(_message(multipart), "me@example.test", _unexpected_attachment)


@pytest.mark.parametrize("marker", ["filename", "content-disposition"])
def test_text_attachments_are_excluded_from_message_body(marker: str):
    attachment = _text_part(b"attachment instructions")
    if marker == "filename":
        attachment["filename"] = "instructions.txt"
    else:
        attachment["headers"].append({"name": "Content-Disposition", "value": "attachment"})
    multipart = {
        "mimeType": "multipart/mixed",
        "parts": [_text_part(b"message body"), attachment],
    }

    message = decode_message(_message(multipart), "me@example.test", _unexpected_attachment)

    assert message.content == "message body"


def test_external_text_attachment_is_not_fetched_as_message_body():
    fetched = False

    def load(_attachment_id: str) -> GmailAttachmentBody:
        nonlocal fetched
        fetched = True
        return GmailAttachmentBody(b"attachment instructions", 23)

    attachment = {
        "filename": "instructions.txt",
        "mimeType": "text/plain",
        "headers": [],
        "body": {"size": 23, "attachmentId": "attachment-1"},
    }
    multipart = {
        "mimeType": "multipart/mixed",
        "parts": [_text_part(b"message body"), attachment],
    }

    message = decode_message(_message(multipart), "me@example.test", load)

    assert message.content == "message body"
    assert fetched is False


def test_mime_part_count_and_depth_are_bounded(monkeypatch):
    monkeypatch.setattr(payloads, "_MAX_MIME_PARTS", 2)
    too_many = {
        "mimeType": "multipart/mixed",
        "parts": [_text_part(b""), _text_part(b"")],
    }
    with pytest.raises(GmailPayloadTooLargeError):
        decode_message(_message(too_many), "me@example.test", _unexpected_attachment)

    monkeypatch.setattr(payloads, "_MAX_MIME_PARTS", 512)
    monkeypatch.setattr(payloads, "_MAX_MIME_DEPTH", 1)
    too_deep = {
        "mimeType": "multipart/mixed",
        "parts": [{"mimeType": "multipart/mixed", "parts": [_text_part(b"")]}],
    }
    with pytest.raises(GmailPayloadTooLargeError):
        decode_message(_message(too_deep), "me@example.test", _unexpected_attachment)


def test_declared_charset_is_used_strictly():
    message = decode_message(
        _message(_text_part("café".encode("iso-8859-1"), content_type="text/plain; charset=iso-8859-1")),
        "me@example.test",
        _unexpected_attachment,
    )
    assert message.content == "café"

    with pytest.raises(GmailPayloadError, match="charset"):
        decode_message(
            _message(_text_part(b"hello", content_type="text/plain; charset=unknown-arden-charset")),
            "me@example.test",
            _unexpected_attachment,
        )
    with pytest.raises(GmailPayloadError, match="charset"):
        decode_message(
            _message(_text_part(b"\xff", content_type="text/plain; charset=utf-8")),
            "me@example.test",
            _unexpected_attachment,
        )


def test_subject_is_none_when_header_is_absent():
    summary = decode_message_summary(_message(_text_part(b"hello")), "me@example.test")
    assert summary.subject is None

    source = decode_reply_source(
        {
            "id": "message-1",
            "threadId": "thread-1",
            "payload": {
                "headers": [
                    {"name": "From", "value": "ada@example.test"},
                    {"name": "Message-ID", "value": "<message-1@example.test>"},
                ]
            },
        }
    )
    assert source.subject_header is None


def test_reply_source_preserves_exact_subject_header():
    subject_header = "=?utf-8?q?Roadmap_=E2=9C=93?="

    source = decode_reply_source(
        {
            "id": "message-1",
            "threadId": "thread-1",
            "payload": {
                "headers": [
                    {"name": "From", "value": "ada@example.test"},
                    {"name": "Subject", "value": subject_header},
                    {"name": "Message-ID", "value": "<message-1@example.test>"},
                ]
            },
        }
    )

    assert source.subject_header == subject_header
    assert source.subject == "Roadmap ✓"


def test_provider_message_headers_are_strictly_bounded():
    message = _message(_text_part(b"hello"))
    message["payload"]["headers"] = [
        {"name": f"X-Header-{index}", "value": "value"}
        for index in range(257)
    ]

    with pytest.raises(GmailPayloadError):
        decode_message(message, "me@example.test", _unexpected_attachment)

    message = _message(_text_part(b"hello"))
    message["payload"]["headers"].append({"name": "Subject", "value": "x" * 16_385})
    with pytest.raises(GmailPayloadError):
        decode_message(message, "me@example.test", _unexpected_attachment)


def test_provider_summary_and_page_fields_are_strictly_bounded():
    message = _message(_text_part(b"hello"))
    message["snippet"] = "x" * 4097
    with pytest.raises(GmailPayloadError):
        decode_message_summary(message, "me@example.test")

    with pytest.raises(GmailPayloadError):
        decode_message_refs({"messages": [{"id": f"message-{index}"} for index in range(101)]})


@pytest.mark.parametrize("name", ["From", "Subject", "Message-ID", "References"])
def test_reply_source_rejects_header_line_breaks(name: str):
    with pytest.raises(GmailPayloadError):
        decode_reply_source(
            {
                "id": "message-1",
                "threadId": "thread-1",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "ada@example.test"},
                        {"name": "Message-ID", "value": "<message-1@example.test>"},
                        {"name": name, "value": "valid\nInjected: value"},
                    ]
                },
            }
        )


def test_invalid_advertised_or_decoded_inline_size_is_rejected():
    for body in (
        {"size": True, "data": ""},
        {"size": -1, "data": ""},
        {"size": 2, "data": _encoded(b"a")},
    ):
        part = {"mimeType": "text/plain", "headers": [], "body": body}
        with pytest.raises(GmailPayloadError):
            decode_message(_message(part), "me@example.test", _unexpected_attachment)
