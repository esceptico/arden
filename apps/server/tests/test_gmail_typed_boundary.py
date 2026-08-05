from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import arden.integrations.gmail.client as gmail_client
from arden.integrations.base import IntegrationConnectionError, IntegrationOperationError
from arden.integrations.gmail.client import GmailSource, MultiGmailSource
from arden.integrations.gmail.models import GmailPageRef
from arden.integrations.gmail.payloads import GmailPayloadError
from arden.integrations.gmail.read_tools import EmailReadInput
from arden.integrations.gmail.write_tools import EmailReplyInput, EmailSendInput

_TOKEN_PATH = Path("unused-gmail-token.json")


def test_multi_gmail_rejects_duplicate_semantic_account_refs():
    with pytest.raises(IntegrationConnectionError, match="emails must be unique") as raised:
        MultiGmailSource(
            [
                ("Me@example.test", _TOKEN_PATH),
                ("me@example.test", _TOKEN_PATH),
            ]
        )

    assert raised.value.reason == "auth_required"


class _Request:
    def __init__(self, result: object, *, exception: Exception | None = None, omit: bool = False):
        self.result = result
        self.exception = exception
        self.omit = omit

    def execute(self) -> object:
        return self.result


class _Messages:
    def __init__(
        self,
        *,
        page: object = None,
        message: object = None,
        messages: dict[str, _Request] | None = None,
        attachment: object = None,
        sent: object = None,
    ):
        self.page = page
        self.message = message
        self.messages = messages or {}
        self.attachment = attachment
        self.sent = sent
        self.list_kwargs: dict[str, object] = {}
        self.attachment_kwargs: dict[str, object] | None = None

    def list(self, **kwargs) -> _Request:
        self.list_kwargs = kwargs
        return _Request(self.page)

    def get(self, **kwargs) -> _Request:
        if "messageId" in kwargs:
            self.attachment_kwargs = kwargs
            return _Request(self.attachment)
        return self.messages.get(str(kwargs["id"]), _Request(self.message))

    def attachments(self) -> "_Messages":
        return self

    def send(self, **_kwargs) -> _Request:
        return _Request(self.sent)


class _Service:
    def __init__(self, messages: _Messages, profile: object = None):
        self._messages = messages
        self._profile = profile
        self.batch_sizes: list[int] = []

    def users(self) -> "_Service":
        return self

    def messages(self) -> _Messages:
        return self._messages

    def getProfile(self, **_kwargs) -> _Request:
        return _Request(self._profile)

    def new_batch_http_request(self) -> "_Batch":
        return _Batch(self)


class _Batch:
    def __init__(self, service: _Service):
        self.service = service
        self.requests: list[tuple[str, _Request, object]] = []

    def add(self, request: _Request, *, callback, request_id: str) -> None:
        self.requests.append((request_id, request, callback))

    def execute(self) -> None:
        self.service.batch_sizes.append(len(self.requests))
        for request_id, request, callback in self.requests:
            if not request.omit:
                callback(request_id, request.result, request.exception)


def _metadata_message(message_id: str = "message-1") -> dict[str, object]:
    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "internalDate": "1785888000000",
        "snippet": "Preview",
        "labelIds": ["INBOX"],
        "payload": {
            "headers": [
                {"name": "From", "value": "Ada <ada@example.test>"},
                {"name": "To", "value": "me@example.test"},
                {"name": "Subject", "value": "Roadmap"},
            ]
        },
    }


def test_search_decodes_provider_payload_into_typed_page_and_exposes_pagination(monkeypatch):
    service = _Service(
        _Messages(
            page={"messages": [{"id": "message-1"}], "nextPageToken": "next-page"},
            message=_metadata_message(),
        )
    )
    source = GmailSource("me@example.test", _TOKEN_PATH)
    monkeypatch.setattr(source, "_get_service", lambda: service)

    page = source.search("roadmap", page_token="current-page")

    assert page.next_page_ref == GmailPageRef(
        account_ref="me@example.test",
        provider_token="next-page",
        query="roadmap",
        days=None,
        limit=50,
    )
    assert service._messages.list_kwargs["pageToken"] == "current-page"
    assert len(page.messages) == 1
    message = page.messages[0]
    assert message.ref == "me@example.test:message-1"
    assert message.thread_ref == "me@example.test:thread-message-1"
    assert message.reply_to == "Ada <ada@example.test>"
    assert message.labels == ("INBOX",)
    assert message.received_at == datetime.fromtimestamp(1785888000, tz=UTC)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"messages": "invalid"},
        {"messages": [{}]},
        {"messages": [], "nextPageToken": 42},
    ],
)
def test_search_rejects_malformed_success_payload(monkeypatch, payload):
    source = GmailSource("me@example.test", _TOKEN_PATH)
    monkeypatch.setattr(source, "_get_service", lambda: _Service(_Messages(page=payload)))

    with pytest.raises(GmailPayloadError) as raised:
        source.search("roadmap")

    assert raised.value.code == "invalid_provider_payload"


def test_verification_rejects_missing_account_identity(monkeypatch):
    source = GmailSource("me@example.test", _TOKEN_PATH)
    monkeypatch.setattr(source, "_get_service", lambda: _Service(_Messages(), profile={}))

    with pytest.raises(GmailPayloadError, match="profile"):
        source.verify_connection()


def test_send_requires_provider_message_and_thread_refs(monkeypatch):
    source = GmailSource("me@example.test", _TOKEN_PATH)
    monkeypatch.setattr(source, "require_send_scope", lambda: None)
    monkeypatch.setattr(source, "_get_service", lambda: _Service(_Messages(sent={"id": "message-1"})))

    prepared = source.prepare_send(
        recipient="you@example.test",
        subject="Status",
        body="Ready",
    )
    with pytest.raises(GmailPayloadError, match="send"):
        source.send(prepared)


def test_read_rejects_malformed_message_content(monkeypatch):
    message = _metadata_message()
    message["payload"] = {
        **message["payload"],
        "mimeType": "text/plain",
        "body": {"data": "%%%"},
    }
    source = GmailSource("me@example.test", _TOKEN_PATH)
    monkeypatch.setattr(source, "_get_service", lambda: _Service(_Messages(message=message)))

    with pytest.raises(GmailPayloadError, match="message content"):
        source.read("message-1")


def test_read_fetches_external_text_attachment_by_exact_refs(monkeypatch):
    message = _metadata_message()
    message["payload"] = {
        **message["payload"],
        "mimeType": "text/plain",
        "body": {"size": 5, "attachmentId": "attachment-1"},
    }
    messages = _Messages(
        message=message,
        attachment={"size": 5, "data": "aGVsbG8"},
    )
    source = GmailSource("me@example.test", _TOKEN_PATH)
    monkeypatch.setattr(source, "_get_service", lambda: _Service(messages))

    result = source.read("message-1")

    assert result.content == "hello"
    assert messages.attachment_kwargs == {
        "userId": "me",
        "messageId": "message-1",
        "id": "attachment-1",
    }


def test_multi_account_verification_targets_one_exact_account():
    verified: list[str] = []
    first = SimpleNamespace(
        account_ref="first@example.test",
        verify_connection=lambda: verified.append("first@example.test"),
    )
    second = SimpleNamespace(
        account_ref="second@example.test",
        verify_connection=lambda: verified.append("second@example.test"),
    )
    source = object.__new__(MultiGmailSource)
    source.sources = [first, second]

    source.verify_account("second@example.test")

    assert verified == ["second@example.test"]


def test_aggregate_verification_never_hides_a_broken_account():
    checked: list[str] = []

    def fail_second() -> None:
        checked.append("second@example.test")
        raise IntegrationConnectionError(
            integration_id="gmail",
            reason="auth_required",
            detail="Second account needs reconnection.",
        )

    source = object.__new__(MultiGmailSource)
    source.sources = [
        SimpleNamespace(
            account_ref="first@example.test",
            verify_connection=lambda: checked.append("first@example.test"),
        ),
        SimpleNamespace(account_ref="second@example.test", verify_connection=fail_second),
    ]

    with pytest.raises(IntegrationConnectionError, match="Second account needs reconnection"):
        source.verify_connection()

    assert checked == ["first@example.test", "second@example.test"]


def test_gmail_credentials_require_read_scope(monkeypatch):
    captured: dict[str, object] = {}
    credentials = SimpleNamespace(valid=True, scopes=())

    def load(token_path, *, require_scopes, integration_id):
        captured.update(
            token_path=token_path,
            require_scopes=require_scopes,
            integration_id=integration_id,
        )
        return credentials

    monkeypatch.setattr(gmail_client, "get_google_credentials", load)
    source = GmailSource("me@example.test", _TOKEN_PATH)

    assert source._get_credentials() is credentials
    assert captured == {
        "token_path": _TOKEN_PATH,
        "require_scopes": gmail_client.SCOPES_GMAIL_READ,
        "integration_id": "gmail",
    }


def test_gmail_credentials_bind_recovery_to_exact_account(monkeypatch):
    source = GmailSource("me@example.test", _TOKEN_PATH)
    error = IntegrationConnectionError(
        integration_id="gmail",
        reason="auth_required",
        detail="Reconnect Gmail.",
        retry_safe=True,
    )
    monkeypatch.setattr(
        gmail_client,
        "get_google_credentials",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    with pytest.raises(IntegrationConnectionError) as raised:
        source._get_credentials()

    assert raised.value.account_ref == "me@example.test"


def test_read_rejects_unqualified_message_ref_with_one_account():
    source = object.__new__(MultiGmailSource)
    source.sources = [SimpleNamespace(account_ref="me@example.test")]

    with pytest.raises(IntegrationOperationError, match="account-qualified"):
        source.read("message-1")


@pytest.mark.parametrize("account_ref", ["ME@example.test", " me@example.test", "me@example.test "])
def test_multi_gmail_requires_exact_account_ref(account_ref: str):
    source = object.__new__(MultiGmailSource)
    source.sources = [SimpleNamespace(account_ref="me@example.test")]

    with pytest.raises(IntegrationOperationError, match="account not found"):
        source._source_for(account_ref)


@pytest.mark.parametrize("message_ref", [" me@example.test:message-1", "me@example.test:message-1 "])
def test_reply_input_rejects_message_ref_whitespace(message_ref: str):
    with pytest.raises(ValidationError):
        EmailReplyInput(
            message_ref=message_ref,
            recipient="ada@example.test",
            subject="Roadmap",
            body="Hello",
            idempotency_key="reply-key-1",
        )


def test_reply_rejects_message_ref_account_alias():
    args = EmailReplyInput(
        message_ref="ME@example.test:message-1",
        recipient="ada@example.test",
        subject="Roadmap",
        body="Hello",
        idempotency_key="reply-key-1",
    )
    source = object.__new__(MultiGmailSource)
    source.sources = [SimpleNamespace(account_ref="me@example.test")]

    with pytest.raises(IntegrationOperationError, match="account not found"):
        source.reply_source(args.message_ref)


def test_search_batches_metadata_in_provider_order_and_chunks_of_50(monkeypatch):
    message_ids = [f"message-{index}" for index in range(51)]
    messages = {message_id: _Request(_metadata_message(message_id)) for message_id in reversed(message_ids)}
    service = _Service(_Messages(page={"messages": [{"id": item} for item in message_ids]}, messages=messages))
    source = GmailSource("me@example.test", _TOKEN_PATH)
    monkeypatch.setattr(source, "_get_service", lambda: service)

    page = source.search("roadmap", limit=51)

    assert service.batch_sizes == [50, 1]
    assert [message.ref for message in page.messages] == [f"me@example.test:{item}" for item in message_ids]


def test_search_rethrows_batch_callback_code_failures(monkeypatch):
    failure = RuntimeError("metadata failed")
    service = _Service(
        _Messages(
            page={"messages": [{"id": "message-1"}, {"id": "message-2"}]},
            messages={
                "message-1": _Request(_metadata_message("message-1")),
                "message-2": _Request(None, exception=failure),
            },
        )
    )
    source = GmailSource("me@example.test", _TOKEN_PATH)
    monkeypatch.setattr(source, "_get_service", lambda: service)

    with pytest.raises(RuntimeError, match="metadata failed"):
        source.search("roadmap")


def test_search_maps_known_batch_transport_failure(monkeypatch):
    service = _Service(
        _Messages(
            page={"messages": [{"id": "message-1"}]},
            messages={"message-1": _Request(None, exception=OSError("network unavailable"))},
        )
    )
    source = GmailSource("me@example.test", _TOKEN_PATH)
    monkeypatch.setattr(source, "_get_service", lambda: service)

    with pytest.raises(IntegrationOperationError) as raised:
        source.search("roadmap")

    assert raised.value.code == "provider_error"
    assert raised.value.retryable is True


def test_search_rejects_missing_batch_response(monkeypatch):
    service = _Service(
        _Messages(
            page={"messages": [{"id": "message-1"}]},
            messages={"message-1": _Request(_metadata_message(), omit=True)},
        )
    )
    source = GmailSource("me@example.test", _TOKEN_PATH)
    monkeypatch.setattr(source, "_get_service", lambda: service)

    with pytest.raises(GmailPayloadError, match="metadata batch"):
        source.search("roadmap")


def test_search_rejects_repeated_page_ref(monkeypatch):
    service = _Service(_Messages(page={"messages": [], "nextPageToken": "same-page"}))
    source = GmailSource("me@example.test", _TOKEN_PATH)
    monkeypatch.setattr(source, "_get_service", lambda: service)

    with pytest.raises(IntegrationOperationError) as raised:
        source.search("roadmap", page_token="same-page")

    assert raised.value.code == "invalid_provider_payload"


def test_read_rejects_provider_response_for_another_message(monkeypatch):
    message = _metadata_message("message-2")
    message["payload"] = {
        **message["payload"],
        "mimeType": "text/plain",
        "body": {"size": 0, "data": ""},
    }
    source = GmailSource("me@example.test", _TOKEN_PATH)
    monkeypatch.setattr(source, "_get_service", lambda: _Service(_Messages(message=message)))

    with pytest.raises(GmailPayloadError, match="message read"):
        source.read("message-1")


def test_reply_source_rejects_provider_response_for_another_message(monkeypatch):
    message = _metadata_message("message-2")
    message["payload"]["headers"].append(
        {"name": "Message-ID", "value": "<message-2@example.test>"}
    )
    source = GmailSource("me@example.test", _TOKEN_PATH)
    monkeypatch.setattr(source, "_get_service", lambda: _Service(_Messages(message=message)))

    with pytest.raises(GmailPayloadError, match="reply source"):
        source.reply_source("message-1")


def test_model_facing_inputs_forbid_legacy_names_and_unknown_fields():
    with pytest.raises(ValidationError):
        EmailReadInput(email_id="message-1")
    with pytest.raises(ValidationError):
        EmailReadInput(message_ref="me@example.test:message-1", unknown=True)
    with pytest.raises(ValidationError):
        EmailSendInput(
            account="me@example.test",
            to="you@example.test",
            subject="Status",
            body="Ready",
            idempotency_key="status-send-1",
        )
