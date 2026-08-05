import base64
from datetime import UTC, datetime
from email import message_from_bytes
from pathlib import Path
from types import SimpleNamespace

import pytest

from arden.context.models import SessionState
from arden.integrations.base import IntegrationOperationError
from arden.integrations.gmail.client import GmailPreparedReply, GmailSource, MultiGmailSource
from arden.integrations.gmail.models import GmailMutationReceipt, GmailReplySource
from arden.integrations.gmail.write_tools import (
    EmailReplyInput,
    approve_email_reply,
    email_reply,
    preflight_email_reply,
)
from arden.integrations.mutations import IDEMPOTENCY_LEDGER_SERVICE, IdempotencyLedger
from arden.tools.core.context import IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.registry import ToolRegistry

_TOKEN_PATH = Path("unused-gmail-token.json")


class _Request:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class _Messages:
    def __init__(self, original, *, sent_thread_id=None):
        self.original = original
        self.sent_thread_id = sent_thread_id
        self.sent_body = None
        self.get_kwargs = None

    def get(self, **kwargs):
        self.get_kwargs = kwargs
        return _Request(self.original)

    def send(self, *, userId, body):
        assert userId == "me"
        self.sent_body = body
        return _Request({"id": "reply-1", "threadId": self.sent_thread_id or body.get("threadId")})


class _Service:
    def __init__(self, original):
        self.message_api = _Messages(original)

    def users(self):
        return self

    def messages(self):
        return self.message_api


def test_gmail_reply_preserves_thread_and_rfc_headers(monkeypatch):
    original = {
        "id": "message-1",
        "threadId": "thread-1",
        "payload": {
            "headers": [
                {"name": "From", "value": "Ada <ada@example.test>"},
                {"name": "Subject", "value": "Roadmap"},
                {"name": "Message-ID", "value": "<original@example.test>"},
                {"name": "References", "value": "<root@example.test>"},
            ]
        },
    }
    service = _Service(original)
    source = GmailSource("me@example.test", _TOKEN_PATH)
    monkeypatch.setattr(source, "require_send_scope", lambda: None)
    monkeypatch.setattr(source, "_get_service", lambda: service)

    result = source.reply(source.prepare_reply("message-1", "Thanks — confirmed."))

    assert result.message_ref == "me@example.test:reply-1"
    assert result.thread_ref == "me@example.test:thread-1"
    assert service.message_api.get_kwargs == {
        "userId": "me",
        "id": "message-1",
        "format": "metadata",
        "metadataHeaders": ["Reply-To", "From", "Subject", "Message-ID", "References"],
    }
    assert service.message_api.sent_body["threadId"] == "thread-1"
    raw = base64.urlsafe_b64decode(service.message_api.sent_body["raw"])
    message = message_from_bytes(raw)
    assert message["To"] == "Ada <ada@example.test>"
    assert message["Subject"] == "Roadmap"
    assert message["In-Reply-To"] == "<original@example.test>"
    assert message["References"] == "<root@example.test> <original@example.test>"


def test_gmail_reply_rejects_original_without_sender(monkeypatch):
    service = _Service({"id": "message-1", "threadId": "thread-1", "payload": {"headers": []}})
    source = GmailSource("me@example.test", _TOKEN_PATH)
    monkeypatch.setattr(source, "require_send_scope", lambda: None)
    monkeypatch.setattr(source, "_get_service", lambda: service)

    with pytest.raises(IntegrationOperationError, match="sender"):
        source.prepare_reply("message-1", "Hello")


def test_gmail_reply_requires_original_message_id(monkeypatch):
    service = _Service(
        {
            "id": "message-1",
            "threadId": "thread-1",
            "payload": {"headers": [{"name": "From", "value": "Ada <ada@example.test>"}]},
        }
    )
    source = GmailSource("me@example.test", _TOKEN_PATH)
    monkeypatch.setattr(source, "require_send_scope", lambda: None)
    monkeypatch.setattr(source, "_get_service", lambda: service)

    with pytest.raises(IntegrationOperationError) as raised:
        source.prepare_reply("message-1", "Hello")

    assert raised.value.code == "invalid_provider_payload"


def test_gmail_reply_rejects_provider_thread_change(monkeypatch):
    original = {
        "id": "message-1",
        "threadId": "thread-1",
        "payload": {
            "headers": [
                {"name": "From", "value": "Ada <ada@example.test>"},
                {"name": "Message-ID", "value": "<original@example.test>"},
            ]
        },
    }
    service = _Service(original)
    service.message_api.sent_thread_id = "other-thread"
    source = GmailSource("me@example.test", _TOKEN_PATH)
    monkeypatch.setattr(source, "require_send_scope", lambda: None)
    monkeypatch.setattr(source, "_get_service", lambda: service)

    with pytest.raises(IntegrationOperationError, match="different thread"):
        source.reply(source.prepare_reply("message-1", "Hello"))


def test_gmail_reply_omits_absent_subject(monkeypatch):
    original = {
        "id": "message-1",
        "threadId": "thread-1",
        "payload": {
            "headers": [
                {"name": "From", "value": "Ada <ada@example.test>"},
                {"name": "Message-ID", "value": "<original@example.test>"},
            ]
        },
    }
    service = _Service(original)
    source = GmailSource("me@example.test", _TOKEN_PATH)
    monkeypatch.setattr(source, "require_send_scope", lambda: None)
    monkeypatch.setattr(source, "_get_service", lambda: service)

    source.reply(source.prepare_reply("message-1", "Thanks."))

    raw = base64.urlsafe_b64decode(service.message_api.sent_body["raw"])
    assert message_from_bytes(raw)["Subject"] is None


@pytest.mark.asyncio
async def test_reply_tool_returns_idempotent_receipt_and_refs(tmp_path, monkeypatch):
    source = object.__new__(MultiGmailSource)
    prepared = GmailPreparedReply(
        account_ref="me@example.test",
        recipient="Ada <ada@example.test>",
        subject="Roadmap",
        provider_thread_id="thread-1",
        raw_message="encoded",
    )
    monkeypatch.setattr(source, "prepare_reply", lambda message_ref, body: prepared)
    monkeypatch.setattr(
        source,
        "reply_source",
        lambda message_ref: GmailReplySource(
            provider_message_id="message-1",
            provider_thread_id="thread-1",
            recipient="Ada <ada@example.test>",
            subject_header="Roadmap",
            subject="Roadmap",
            message_id_header="<original@example.test>",
            references_header="",
        ),
    )
    monkeypatch.setattr(
        source,
        "reply_email",
        lambda target: GmailMutationReceipt(
            message_ref="me@example.test:reply-1",
            thread_ref="me@example.test:thread-1",
            recipient="Ada <ada@example.test>",
        ),
    )
    ctx = ToolContext(
        session_state=SessionState(session_id="session-1", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1"),
        io=IOBridge(),
        services={
            "gmail": source,
            IDEMPOTENCY_LEDGER_SERVICE: IdempotencyLedger(tmp_path / "ledger.sqlite3"),
        },
    )
    execution = ToolExecution(tool_id="reply-call-1", tool_name="email_reply", ctx=ctx)
    args = EmailReplyInput(
        message_ref="me@example.test:message-1",
        recipient="Ada <ada@example.test>",
        subject="Roadmap",
        body="Thanks — confirmed.",
        idempotency_key="reply-roadmap-1",
    )

    approval = await approve_email_reply(execution, args)
    preflight = await preflight_email_reply(execution, args)
    result = await email_reply(execution, args)
    replay = await email_reply(execution, args)

    assert approval is not None and args.body in approval.preview
    assert preflight is None
    assert args.recipient in approval.description
    assert f"To: {args.recipient}" in approval.preview
    assert 'Subject: "Roadmap"' in approval.preview
    assert result.outcome is not None
    assert result.outcome.receipt == "me@example.test:reply-1"
    assert result.outcome.effect is not None
    assert result.outcome.effect.before_ref == args.message_ref
    assert result.outcome.effect.after_ref == "me@example.test:reply-1"
    assert [ref.ref for ref in result.source_refs] == [args.message_ref, "me@example.test:reply-1"]
    assert replay.data["idempotent_replay"] is True
    assert replay.source_refs == result.source_refs


@pytest.mark.asyncio
async def test_reply_preflight_rejects_changed_provider_recipient():
    source = SimpleNamespace(
        reply_source=lambda _message_ref: GmailReplySource(
            provider_message_id="message-1",
            provider_thread_id="thread-1",
            recipient="Mallory <mallory@example.test>",
            subject_header="Roadmap",
            subject="Roadmap",
            message_id_header="<original@example.test>",
            references_header="",
        )
    )
    execution = ToolExecution(
        tool_id="reply-call-1",
        tool_name="email_reply",
        ctx=SimpleNamespace(get_client=lambda _name, _type: source),
    )
    args = EmailReplyInput(
        message_ref="me@example.test:message-1",
        recipient="Ada <ada@example.test>",
        subject="Roadmap",
        body="Thanks — confirmed.",
        idempotency_key="reply-roadmap-1",
    )

    result = await preflight_email_reply(execution, args)

    assert result is not None
    assert result.outcome.error.code == "revision_conflict"
    assert result.outcome.error.recovery_action == (
        "Read the message again and retry with its exact reply_to and subject values."
    )


@pytest.mark.asyncio
async def test_reply_rechecks_recipient_immediately_before_mutation(tmp_path, monkeypatch):
    source = object.__new__(MultiGmailSource)
    prepared = GmailPreparedReply(
        account_ref="me@example.test",
        recipient="Mallory <mallory@example.test>",
        subject="Roadmap",
        provider_thread_id="thread-1",
        raw_message="encoded",
    )
    monkeypatch.setattr(source, "prepare_reply", lambda _message_ref, _body: prepared)
    mutation_called = False

    def reply_email(_prepared):
        nonlocal mutation_called
        mutation_called = True
        raise AssertionError("mutation must not run")

    monkeypatch.setattr(source, "reply_email", reply_email)
    ctx = ToolContext(
        session_state=SessionState(session_id="session-1", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1"),
        io=IOBridge(),
        services={
            "gmail": source,
            IDEMPOTENCY_LEDGER_SERVICE: IdempotencyLedger(tmp_path / "ledger.sqlite3"),
        },
    )
    execution = ToolExecution(tool_id="reply-call-1", tool_name="email_reply", ctx=ctx)
    args = EmailReplyInput(
        message_ref="me@example.test:message-1",
        recipient="Ada <ada@example.test>",
        subject="Roadmap",
        body="Thanks — confirmed.",
        idempotency_key="reply-roadmap-1",
    )

    result = await email_reply(execution, args)

    assert result.outcome.error.code == "revision_conflict"
    assert mutation_called is False
