import base64
from datetime import UTC, datetime
from email import message_from_bytes

import pytest

from arden.context.models import SessionState
from arden.integrations.base import IntegrationOperationError
from arden.integrations.gmail.client import GmailSource, MultiGmailSource
from arden.integrations.gmail.tools import EmailReplyInput, approve_email_reply, email_reply
from arden.integrations.mutations import IDEMPOTENCY_LEDGER_SERVICE, IdempotencyLedger
from arden.tools.core.context import IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.registry import ToolRegistry


class _Request:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class _Messages:
    def __init__(self, original):
        self.original = original
        self.sent_body = None

    def get(self, **_kwargs):
        return _Request(self.original)

    def send(self, *, userId, body):
        assert userId == "me"
        self.sent_body = body
        return _Request({"id": "reply-1", "threadId": body.get("threadId")})


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
    source = GmailSource()
    monkeypatch.setattr(source, "has_send_scope", lambda: True)
    monkeypatch.setattr(source, "_get_service", lambda: service)

    result = source.reply("message-1", "Thanks — confirmed.", from_email="me@example.test")

    assert "reply-1" in result
    assert service.message_api.sent_body["threadId"] == "thread-1"
    raw = base64.urlsafe_b64decode(service.message_api.sent_body["raw"])
    message = message_from_bytes(raw)
    assert message["To"] == "Ada <ada@example.test>"
    assert message["Subject"] == "Re: Roadmap"
    assert message["In-Reply-To"] == "<original@example.test>"
    assert message["References"] == "<root@example.test> <original@example.test>"


def test_gmail_reply_rejects_original_without_sender(monkeypatch):
    service = _Service({"id": "message-1", "threadId": "thread-1", "payload": {"headers": []}})
    source = GmailSource()
    monkeypatch.setattr(source, "has_send_scope", lambda: True)
    monkeypatch.setattr(source, "_get_service", lambda: service)

    with pytest.raises(IntegrationOperationError, match="sender"):
        source.reply("message-1", "Hello")


@pytest.mark.asyncio
async def test_reply_tool_returns_idempotent_receipt_and_refs(tmp_path, monkeypatch):
    source = object.__new__(MultiGmailSource)
    monkeypatch.setattr(
        source,
        "reply_email",
        lambda message_ref, body: "Replied to Ada <ada@example.test> (id: reply-1)",
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
        body="Thanks — confirmed.",
        idempotency_key="reply-roadmap-1",
    )

    approval = await approve_email_reply(execution, args)
    result = await email_reply(execution, args)
    replay = await email_reply(execution, args)

    assert approval is not None and args.body in approval.preview
    assert result.outcome is not None
    assert result.outcome.receipt == "reply-1"
    assert result.outcome.effect is not None
    assert result.outcome.effect.before_ref == args.message_ref
    assert result.outcome.effect.after_ref == "me@example.test:reply-1"
    assert [ref.ref for ref in result.source_refs] == [args.message_ref, "me@example.test:reply-1"]
    assert replay.data["idempotent_replay"] is True
    assert replay.source_refs == result.source_refs
