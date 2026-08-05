import threading
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from arden.agent import ToolOutcomeStatus
from arden.context.models import SessionState
from arden.integrations.base import IntegrationOperationError
from arden.integrations.gmail.client import GmailPreparedSend, GmailSource, MultiGmailSource
from arden.integrations.gmail.models import (
    GmailMessage,
    GmailMessagePage,
    GmailMessageSummary,
    GmailMutationReceipt,
    GmailPageRef,
)
from arden.integrations.gmail.read_tools import EmailReadInput, EmailSearchInput, email_read, email_search
from arden.integrations.gmail.write_tools import (
    EmailSendInput,
    approve_email_send,
    email_send,
)
from arden.integrations.mutations import IDEMPOTENCY_LEDGER_SERVICE, IdempotencyLedger
from arden.tools.core.context import IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.registry import ToolRegistry

_GMAIL_TOKEN_PATH = Path("unused-gmail-token.json")


def _execution(service: object, tool_name: str) -> ToolExecution:
    return ToolExecution(
        tool_id="call-1",
        tool_name=tool_name,
        ctx=ToolContext(
            session_state=SessionState(session_id="session-1", started_at=datetime(2026, 7, 10, tzinfo=UTC)),
            registry=ToolRegistry(),
            run=RunContext(run_id="run-1"),
            io=IOBridge(),
            services={"gmail": service},
        ),
    )


class FakeGmailSource(MultiGmailSource):
    def __init__(
        self,
        search_results: list[GmailMessageSummary] | None = None,
        *,
        next_page_ref: GmailPageRef | None = None,
    ):
        self.search_results = search_results or []
        self.next_page_ref = next_page_ref
        self.search_call: dict[str, object] | None = None
        self.list_call: dict[str, object] | None = None

    def search(
        self,
        query: str,
        limit: int = 50,
        *,
        account_ref: str | None = None,
        page_ref: GmailPageRef | None = None,
    ) -> GmailMessagePage:
        resolved_account = account_ref or "me@example.test"
        self.search_call = {
            "query": query,
            "limit": limit,
            "account_ref": account_ref,
            "page_ref": page_ref,
        }
        return GmailMessagePage(
            account_ref=resolved_account,
            messages=tuple(self.search_results[:limit]),
            next_page_ref=self.next_page_ref,
        )

    def list_accounts(self) -> list[str]:
        return ["me@example.test"]

    def prepare_send(
        self,
        account_ref: str,
        recipient: str,
        subject: str,
        body: str,
        html: bool = False,
    ) -> GmailPreparedSend:
        return GmailPreparedSend(account_ref=account_ref, recipient=recipient, raw_message="encoded")

    def list_recent(
        self,
        days: int = 7,
        limit: int = 50,
        *,
        account_ref: str | None = None,
        page_ref: GmailPageRef | None = None,
    ) -> GmailMessagePage:
        resolved_account = account_ref or "me@example.test"
        self.list_call = {
            "days": days,
            "limit": limit,
            "account_ref": account_ref,
            "page_ref": page_ref,
        }
        return GmailMessagePage(
            account_ref=resolved_account,
            messages=(_gmail_message("message-recent", "Recent message", account_ref=resolved_account),),
            next_page_ref=self.next_page_ref,
        )

    def read(self, message_ref: str) -> GmailMessage:
        return GmailMessage(
            summary=_gmail_message(message_ref.rpartition(":")[2], "Read message"),
            content="Body",
        )


def _gmail_message(
    provider_message_id: str,
    subject: str,
    *,
    account_ref: str = "me@example.test",
    sender: str = "ada@example.test",
) -> GmailMessageSummary:
    return GmailMessageSummary(
        ref=f"{account_ref}:{provider_message_id}",
        account_ref=account_ref,
        thread_ref=f"{account_ref}:thread-{provider_message_id}",
        subject=subject,
        sender=sender,
        recipient=account_ref,
        reply_to=sender,
        snippet="",
        received_at=datetime(2026, 7, 10, tzinfo=UTC),
        labels=(),
    )


class FailingGoogleService:
    def users(self):
        return self

    def messages(self):
        return self

    def send(self, **kwargs):
        return self

    def execute(self):
        raise RuntimeError("provider secret")


class FailingGmailService(FailingGoogleService):
    def execute(self):
        raise OSError("provider secret")


def test_gmail_send_raises_sanitized_provider_failure(monkeypatch):
    source = GmailSource(account_ref="me@example.test", token_path=_GMAIL_TOKEN_PATH)
    monkeypatch.setattr(source, "require_send_scope", lambda: None)
    monkeypatch.setattr(source, "_get_service", lambda: FailingGmailService())

    with pytest.raises(RuntimeError, match="Gmail provider request failed") as exc_info:
        source.send(
            source.prepare_send(
                recipient="you@example.test",
                subject="Status",
                body="Ready",
            )
        )

    assert "provider secret" not in str(exc_info.value)


def test_gmail_send_does_not_relabel_internal_failures(monkeypatch):
    source = GmailSource(account_ref="me@example.test", token_path=_GMAIL_TOKEN_PATH)
    monkeypatch.setattr(source, "require_send_scope", lambda: None)
    monkeypatch.setattr(source, "_get_service", lambda: FailingGoogleService())

    with pytest.raises(RuntimeError, match="provider secret"):
        source.send(
            source.prepare_send(
                recipient="you@example.test",
                subject="Status",
                body="Ready",
            )
        )


@pytest.mark.asyncio
async def test_send_email_maps_provider_failure_to_typed_result(tmp_path):
    source = FakeGmailSource()

    def fail_send(_prepared):
        raise IntegrationOperationError(
            code="provider_error",
            safe_message="Gmail provider request failed.",
            retryable=True,
        )

    source.send_email = fail_send
    execution = _execution(source, "email_send")
    execution.ctx.services[IDEMPOTENCY_LEDGER_SERVICE] = IdempotencyLedger(tmp_path / "idempotency.sqlite3")
    result = await email_send(
        execution,
        EmailSendInput(
            account_ref="me@example.test",
            recipient="you@example.test",
            subject="Status",
            body="Ready",
            idempotency_key="send-status-1",
        ),
    )

    assert result.is_error
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.status is ToolOutcomeStatus.UNCERTAIN
    assert result.outcome.error.code == "mutation_uncertain"
    assert result.data["original_error"] == {"code": "provider_error", "retryable": True}


@pytest.mark.asyncio
async def test_gmail_preflight_failure_is_retryable_without_running_mutation(tmp_path):
    source = FakeGmailSource()
    event_loop_thread = threading.get_ident()
    worker_threads: list[int] = []
    preflight_calls = 0
    mutation_calls = 0
    prepared = GmailPreparedSend("me@example.test", "you@example.test", "encoded")

    def prepare_send(**_kwargs):
        nonlocal preflight_calls
        preflight_calls += 1
        worker_threads.append(threading.get_ident())
        if preflight_calls == 1:
            raise IntegrationOperationError(
                code="provider_error",
                safe_message="Gmail preflight failed.",
                retryable=True,
            )
        return prepared

    def send_email(target):
        nonlocal mutation_calls
        mutation_calls += 1
        worker_threads.append(threading.get_ident())
        assert target is prepared
        return GmailMutationReceipt(
            message_ref="me@example.test:message-1",
            thread_ref="me@example.test:thread-1",
            recipient="you@example.test",
        )

    source.prepare_send = prepare_send
    source.send_email = send_email
    execution = _execution(source, "email_send")
    execution.ctx.services[IDEMPOTENCY_LEDGER_SERVICE] = IdempotencyLedger(tmp_path / "idempotency.sqlite3")
    args = EmailSendInput(
        account_ref="me@example.test",
        recipient="you@example.test",
        subject="Status",
        body="Ready",
        idempotency_key="send-status-1",
    )

    first = await email_send(execution, args)
    retry = await email_send(execution, args)

    assert first.outcome is not None and first.outcome.status is ToolOutcomeStatus.FAILED
    assert first.outcome.error is not None and first.outcome.error.code == "provider_error"
    assert retry.outcome is not None and retry.outcome.status is ToolOutcomeStatus.SUCCEEDED
    assert retry.outcome.verification is not None
    assert retry.outcome.verification.postcondition == "Provider returned a mutation reference"
    assert [ref.ref for ref in retry.source_refs] == ["me@example.test:message-1"]
    assert preflight_calls == 2
    assert mutation_calls == 1
    assert worker_threads and all(thread_id != event_loop_thread for thread_id in worker_threads)


@pytest.mark.asyncio
async def test_gmail_search_uses_returned_message_id_without_inventing_a_url():
    source = FakeGmailSource(
        [
            _gmail_message(
                "message-123",
                "Quarterly plan",
            )
        ]
    )

    result = await email_search(
        _execution(source, "email_search"),
        EmailSearchInput(query="quarterly plan"),
    )

    assert [ref.to_dict() for ref in result.source_refs] == [
        {
            "provider": "gmail",
            "kind": "message",
            "ref": "me@example.test:message-123",
            "title": "Quarterly plan",
        }
    ]
    assert "reply_to: ada@example.test" in result.content


@pytest.mark.asyncio
async def test_gmail_source_title_projects_long_multiline_subject_safely():
    subject = "Review\nhttps://user:secret@example.test/private " + "x" * 300
    source = FakeGmailSource([_gmail_message("message-123", subject)])

    result = await email_search(
        _execution(source, "email_search"),
        EmailSearchInput(query="review"),
    )

    title = result.source_refs[0].title
    assert title.startswith("Review [redacted URL]")
    assert "secret" not in title
    assert "\n" not in title
    assert len(title) == 256


@pytest.mark.asyncio
async def test_gmail_reads_and_searches_off_the_event_loop():
    source = FakeGmailSource([_gmail_message("message-123", "Quarterly plan")])
    event_loop_thread = threading.get_ident()
    worker_threads: list[int] = []
    search = source.search
    read = source.read

    def tracked_search(*args, **kwargs):
        worker_threads.append(threading.get_ident())
        return search(*args, **kwargs)

    def tracked_read(*args, **kwargs):
        worker_threads.append(threading.get_ident())
        return read(*args, **kwargs)

    source.search = tracked_search
    source.read = tracked_read

    await email_search(
        _execution(source, "email_search"),
        EmailSearchInput(query="quarterly"),
    )
    await email_read(
        _execution(source, "email_read"),
        EmailReadInput(message_ref="me@example.test:message-123"),
    )

    assert len(worker_threads) == 2
    assert all(thread_id != event_loop_thread for thread_id in worker_threads)


@pytest.mark.asyncio
async def test_gmail_search_uses_explicit_no_subject_title():
    source = FakeGmailSource(
        [
            _gmail_message("message-123", ""),
        ]
    )

    result = await email_search(
        _execution(source, "email_search"),
        EmailSearchInput(query="quarterly plan"),
    )

    assert [ref.to_dict() for ref in result.source_refs] == [
        {
            "provider": "gmail",
            "kind": "message",
            "ref": "me@example.test:message-123",
            "title": "(no subject)",
        }
    ]


@pytest.mark.asyncio
async def test_gmail_read_uses_exact_account_qualified_message_ref():
    source = FakeGmailSource()

    result = await email_read(
        _execution(source, "email_read"),
        EmailReadInput(message_ref="me@example.test:message-123"),
    )

    assert [ref.to_dict() for ref in result.source_refs] == [
        {
            "provider": "gmail",
            "kind": "message",
            "ref": "me@example.test:message-123",
            "title": "Read message",
        }
    ]
    assert "Reply-To: ada@example.test" in result.content


def test_multi_gmail_read_returns_the_matching_account_identity():
    source = object.__new__(MultiGmailSource)
    expected = GmailMessage(
        summary=_gmail_message("message-123", "Message", account_ref="second@example.test"), content="message body"
    )
    source.sources = [
        SimpleNamespace(read=lambda _source_id: expected, account_ref="first@example.test"),
        SimpleNamespace(read=lambda _source_id: expected, account_ref="second@example.test"),
    ]

    result = source.read("second@example.test:message-123")

    assert result.content == "message body"
    assert result.summary.account_ref == "second@example.test"


@pytest.mark.asyncio
async def test_send_email_approval_includes_the_body():
    info = await approve_email_send(
        _execution(FakeGmailSource(), "email_send"),
        EmailSendInput(
            account_ref="me@example.test",
            recipient="you@example.test",
            subject="Release status",
            body="The release is ready for final review.",
            idempotency_key="release-status-1",
        ),
    )

    assert info is not None
    assert info.description == "you@example.test"
    assert "Subject: Release status" in (info.preview or "")
    assert "Body:\nThe release is ready for final review." in (info.preview or "")


@pytest.mark.asyncio
async def test_send_email_approval_exposes_the_complete_mutation_without_truncation():
    body = "x" * 10_000 + "END-OF-APPROVED-BODY"
    info = await approve_email_send(
        _execution(FakeGmailSource(), "email_send"),
        EmailSendInput(
            account_ref="me@example.test",
            recipient="you@example.test",
            subject="Exact release",
            body=body,
            idempotency_key="exact-release-1",
        ),
    )

    assert info is not None
    assert info.preview is not None and info.preview.endswith("END-OF-APPROVED-BODY")
    assert f"Body:\n{body}" in info.preview


@pytest.mark.asyncio
async def test_gmail_search_passes_exact_account_and_page_refs():
    current_page = GmailPageRef(
        account_ref="second@example.test",
        provider_token="provider-page-1",
        query="same",
        days=None,
        limit=30,
    )
    next_page = GmailPageRef(
        account_ref="second@example.test",
        provider_token="provider-page-2",
        query="same",
        days=None,
        limit=30,
    )
    source = FakeGmailSource(
        [
            _gmail_message("same-id", "Second", account_ref="second@example.test"),
        ],
        next_page_ref=next_page,
    )

    result = await email_search(
        _execution(source, "email_search"),
        EmailSearchInput(page_ref=current_page.encode()),
    )

    assert [ref.ref for ref in result.source_refs] == ["second@example.test:same-id"]
    assert source.search_call == {
        "query": "same",
        "limit": 30,
        "account_ref": "second@example.test",
        "page_ref": current_page,
    }
    assert f"page_ref: {next_page.encode()}" in result.content
    assert result.data == {
        "count": 1,
        "account_ref": "second@example.test",
        "next_page_ref": next_page.encode(),
    }


@pytest.mark.asyncio
async def test_gmail_empty_page_still_exposes_exact_continuation():
    current_page = GmailPageRef(
        account_ref="second@example.test",
        provider_token="provider-page-1",
        query=None,
        days=7,
        limit=30,
    )
    next_page = GmailPageRef(
        account_ref="second@example.test",
        provider_token="provider-page-2",
        query=None,
        days=7,
        limit=30,
    )
    source = FakeGmailSource(next_page_ref=next_page)
    source.list_recent = lambda **_kwargs: GmailMessagePage(
        account_ref="second@example.test",
        messages=(),
        next_page_ref=next_page,
    )

    result = await email_search(
        _execution(source, "email_search"),
        EmailSearchInput(page_ref=current_page.encode()),
    )

    assert f"page_ref: {next_page.encode()}" in result.content
    assert result.data == {
        "count": 0,
        "account_ref": "second@example.test",
        "next_page_ref": next_page.encode(),
    }


def test_gmail_page_ref_cannot_be_combined_with_other_search_fields():
    page_ref = GmailPageRef(
        account_ref="first@example.test",
        provider_token="provider-page-1",
        query="same",
        days=None,
        limit=30,
    )

    with pytest.raises(ValidationError, match="page_ref must be passed alone"):
        EmailSearchInput(account_ref="second@example.test", page_ref=page_ref.encode())


@pytest.mark.asyncio
async def test_gmail_search_maps_account_selection_error_to_tool_failure():
    source = FakeGmailSource()

    def ambiguous(*_args, **_kwargs):
        raise IntegrationOperationError(
            code="ambiguous_ref",
            safe_message="Choose a Gmail account_ref: first@example.test, second@example.test",
        )

    source.search = ambiguous
    result = await email_search(
        _execution(source, "email_search"),
        EmailSearchInput(query="same"),
    )

    assert result.is_error
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "ambiguous_ref"
