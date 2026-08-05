from dataclasses import dataclass
from pathlib import Path
from threading import Lock, RLock

import httplib2
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from arden.integrations.base import IntegrationConnectionError, IntegrationOperationError
from arden.integrations.gmail.compose import compose_email, compose_reply
from arden.integrations.gmail.errors import raise_gmail_error
from arden.integrations.gmail.models import (
    GmailMessage,
    GmailMessagePage,
    GmailMessageSummary,
    GmailMutationReceipt,
    GmailPageRef,
    GmailReplySource,
)
from arden.integrations.gmail.payloads import (
    GmailPayloadError,
    decode_attachment_body,
    decode_message,
    decode_message_refs,
    decode_message_summary,
    decode_mutation_receipt,
    decode_profile,
    decode_reply_source,
)
from arden.integrations.google_auth.auth import (
    SCOPES_GMAIL_READ,
    SCOPES_GMAIL_SEND,
    get_google_credentials,
    has_scope,
)
from arden.logging import get_logger

_READ_SCOPES = tuple(SCOPES_GMAIL_READ)
_SEND_SCOPES = tuple(SCOPES_GMAIL_SEND)


@dataclass(frozen=True, slots=True)
class GmailPreparedSend:
    account_ref: str
    recipient: str
    raw_message: str


@dataclass(frozen=True, slots=True)
class GmailPreparedReply:
    account_ref: str
    recipient: str
    subject: str | None
    provider_thread_id: str
    raw_message: str


class _MetadataBatchResults:
    def __init__(self) -> None:
        self.responses: dict[str, object] = {}
        self.failures: dict[str, Exception] = {}

    def receive(self, request_id: str, response: object, exception: Exception | None) -> None:
        if exception is not None:
            self.failures[request_id] = exception
        else:
            self.responses[request_id] = response


def parse_message_ref(message_ref: str) -> tuple[str, str]:
    account_ref, separator, provider_message_id = message_ref.partition(":")
    if (
        not separator
        or not account_ref
        or not provider_message_id
        or account_ref != account_ref.strip()
        or provider_message_id != provider_message_id.strip()
        or len(account_ref) > 320
        or len(provider_message_id) > 512
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in message_ref)
    ):
        raise IntegrationOperationError(
            code="invalid_ref",
            safe_message="Use the account-qualified message_ref returned by email_search.",
        )
    return account_ref, provider_message_id


class GmailSource:
    name = "gmail"

    def __init__(
        self,
        account_ref: str,
        token_path: Path,
    ):
        if not account_ref or account_ref != account_ref.strip():
            raise ValueError("GmailSource requires an exact non-empty account_ref")
        self.account_ref = account_ref
        self.token_path = token_path

        self._service = None
        self._creds = None
        self._state_lock = RLock()
        self._request_lock = Lock()

    def _get_credentials(self):
        with self._state_lock:
            if self._creds is None or not self._creds.valid:
                try:
                    self._creds = get_google_credentials(
                        self.token_path,
                        require_scopes=SCOPES_GMAIL_READ,
                        integration_id="gmail",
                    )
                except IntegrationConnectionError as error:
                    raise self._connection_error(error) from error
            return self._creds

    def _connection_error(self, error: IntegrationConnectionError) -> IntegrationConnectionError:
        return IntegrationConnectionError(
            integration_id=error.integration_id,
            reason=error.reason,
            detail=error.detail,
            required_scopes=error.required_scopes,
            retry_safe=error.retry_safe,
            account_ref=self.account_ref,
        )

    def require_send_scope(self) -> None:
        creds = self._get_credentials()
        if not has_scope(creds, SCOPES_GMAIL_SEND[0]):
            raise IntegrationConnectionError(
                integration_id="gmail",
                reason="scope_required",
                detail="Gmail authorization is missing permission to send email.",
                required_scopes=_SEND_SCOPES,
                retry_safe=True,
                account_ref=self.account_ref,
            )

    def _get_service(self):
        with self._state_lock:
            if self._service is None:
                creds = self._get_credentials()
                try:
                    self._service = build("gmail", "v1", credentials=creds)
                except (HttpError, OSError, httplib2.ServerNotFoundError) as exc:
                    raise_gmail_error(exc, required_scopes=_READ_SCOPES, account_ref=self.account_ref)
            return self._service

    def _execute(self, request, operation: str, *, required_scopes: tuple[str, ...]) -> object:
        try:
            with self._request_lock:
                return request.execute()
        except (HttpError, OSError, httplib2.ServerNotFoundError) as exc:
            _logger.exception("Gmail %s failed", operation)
            raise_gmail_error(exc, required_scopes=required_scopes, account_ref=self.account_ref)

    def verify_connection(self) -> None:
        observed_account = decode_profile(
            self._execute(
                self._get_service().users().getProfile(userId="me"),
                "profile read",
                required_scopes=_READ_SCOPES,
            )
        )
        if observed_account.casefold() != self.account_ref.casefold():
            raise IntegrationConnectionError(
                integration_id="gmail",
                reason="auth_required",
                detail=f"Gmail authorization does not belong to {self.account_ref}.",
                retry_safe=False,
                account_ref=self.account_ref,
            )

    def prepare_send(
        self,
        recipient: str,
        subject: str,
        body: str,
        *,
        html: bool = False,
    ) -> GmailPreparedSend:
        if not recipient:
            raise IntegrationOperationError(
                code="invalid_ref",
                safe_message="A recipient email address is required.",
            )

        self.require_send_scope()
        self._get_service()

        return GmailPreparedSend(
            account_ref=self.account_ref,
            recipient=recipient,
            raw_message=compose_email(
                sender=self.account_ref,
                recipient=recipient,
                subject=subject,
                body=body,
                html=html,
            ),
        )

    def send(self, prepared: GmailPreparedSend) -> GmailMutationReceipt:
        sent = self._execute(
            self._get_service().users().messages().send(userId="me", body={"raw": prepared.raw_message}),
            "send",
            required_scopes=_SEND_SCOPES,
        )
        return decode_mutation_receipt(sent, self.account_ref, prepared.recipient, "send")

    def prepare_reply(self, provider_message_id: str, body: str) -> GmailPreparedReply:
        self.require_send_scope()
        original = self.reply_source(provider_message_id)

        return GmailPreparedReply(
            account_ref=self.account_ref,
            recipient=original.recipient,
            subject=original.subject,
            provider_thread_id=original.provider_thread_id,
            raw_message=compose_reply(
                sender=self.account_ref,
                recipient=original.recipient,
                subject_header=original.subject_header,
                body=body,
                message_id=original.message_id_header,
                references=original.references_header,
            ),
        )

    def reply_source(self, provider_message_id: str) -> GmailReplySource:
        original_raw = self._execute(
            self._get_service()
            .users()
            .messages()
            .get(
                userId="me",
                id=provider_message_id,
                format="metadata",
                metadataHeaders=["Reply-To", "From", "Subject", "Message-ID", "References"],
            ),
            "reply source read",
            required_scopes=_READ_SCOPES,
        )
        source = decode_reply_source(original_raw)
        if source.provider_message_id != provider_message_id:
            raise GmailPayloadError("reply source")
        return source

    def reply(self, prepared: GmailPreparedReply) -> GmailMutationReceipt:
        sent = self._execute(
            self._get_service()
            .users()
            .messages()
            .send(userId="me", body={"raw": prepared.raw_message, "threadId": prepared.provider_thread_id}),
            "reply",
            required_scopes=_SEND_SCOPES,
        )
        receipt = decode_mutation_receipt(sent, self.account_ref, prepared.recipient, "reply")
        expected_thread_ref = f"{self.account_ref}:{prepared.provider_thread_id}"
        if receipt.thread_ref != expected_thread_ref:
            raise IntegrationOperationError(
                code="invalid_provider_payload",
                safe_message="Gmail returned the reply in a different thread.",
                retryable=False,
            )
        return receipt

    def _metadata_request(self, provider_message_id: str):
        return (
            self._get_service()
            .users()
            .messages()
            .get(
                userId="me",
                id=provider_message_id,
                format="metadata",
                metadataHeaders=["From", "To", "Reply-To", "Subject"],
            )
        )

    def _fetch_message_metadata_page(
        self,
        provider_message_ids: tuple[str, ...],
    ) -> tuple[GmailMessageSummary, ...]:
        if len(provider_message_ids) != len(set(provider_message_ids)):
            raise GmailPayloadError("message list")

        decoded_messages: dict[str, GmailMessageSummary] = {}
        for offset in range(0, len(provider_message_ids), 50):
            message_ids = provider_message_ids[offset : offset + 50]
            results = _MetadataBatchResults()

            service = self._get_service()
            batch = service.new_batch_http_request()
            for message_id in message_ids:
                batch.add(self._metadata_request(message_id), callback=results.receive, request_id=message_id)
            self._execute(
                batch,
                "message metadata batch",
                required_scopes=_READ_SCOPES,
            )

            if results.failures:
                failure = next(iter(results.failures.values()))
                if isinstance(failure, (HttpError, OSError, httplib2.ServerNotFoundError)):
                    raise_gmail_error(
                        failure,
                        required_scopes=_READ_SCOPES,
                        account_ref=self.account_ref,
                    )
                raise failure
            if set(results.responses) != set(message_ids):
                raise GmailPayloadError("message metadata batch")

            for message_id in message_ids:
                message = decode_message_summary(results.responses[message_id], self.account_ref)
                if message.ref != f"{self.account_ref}:{message_id}":
                    raise GmailPayloadError("message metadata batch")
                decoded_messages[message_id] = message

        return tuple(decoded_messages[message_id] for message_id in provider_message_ids)

    def _fetch_message_full(self, provider_message_id: str) -> GmailMessage:
        raw = self._execute(
            self._get_service().users().messages().get(userId="me", id=provider_message_id, format="full"),
            "message read",
            required_scopes=_READ_SCOPES,
        )
        service = self._get_service()

        def load_attachment(attachment_id: str):
            request = (
                service.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=provider_message_id, id=attachment_id)
            )
            return decode_attachment_body(
                self._execute(
                    request,
                    "message attachment read",
                    required_scopes=_READ_SCOPES,
                )
            )

        message = decode_message(raw, self.account_ref, load_attachment)
        if message.summary.ref != f"{self.account_ref}:{provider_message_id}":
            raise GmailPayloadError("message read")
        return message

    def read(self, provider_message_id: str) -> GmailMessage:
        return self._fetch_message_full(provider_message_id)

    def search(
        self,
        query: str,
        limit: int = 50,
        *,
        page_token: str | None = None,
    ) -> GmailMessagePage:
        params: dict[str, object] = {"userId": "me", "q": query, "maxResults": limit}
        if page_token is not None:
            params["pageToken"] = page_token
        raw_page = self._execute(
            self._get_service().users().messages().list(**params),
            "message search",
            required_scopes=_READ_SCOPES,
        )
        refs = decode_message_refs(raw_page)
        if refs.next_page_token is not None and refs.next_page_token == page_token:
            raise IntegrationOperationError(
                code="invalid_provider_payload",
                safe_message="Gmail returned the current page_ref again.",
                retryable=False,
            )
        messages = self._fetch_message_metadata_page(refs.provider_message_ids)
        return GmailMessagePage(
            account_ref=self.account_ref,
            messages=messages,
            next_page_ref=(
                GmailPageRef(
                    account_ref=self.account_ref,
                    provider_token=refs.next_page_token,
                    query=query,
                    days=None,
                    limit=limit,
                )
                if refs.next_page_token is not None
                else None
            ),
        )

    def list_recent(
        self,
        days: int = 7,
        limit: int = 50,
        *,
        page_token: str | None = None,
    ) -> GmailMessagePage:
        page = self.search(f"newer_than:{days}d", limit=limit, page_token=page_token)
        return GmailMessagePage(
            account_ref=page.account_ref,
            messages=page.messages,
            next_page_ref=(
                GmailPageRef(
                    account_ref=self.account_ref,
                    provider_token=page.next_page_ref.provider_token,
                    query=None,
                    days=days,
                    limit=limit,
                )
                if page.next_page_ref is not None
                else None
            ),
        )


_logger = get_logger(__name__)


class MultiGmailSource:
    """Wrapper for multiple Gmail accounts."""

    name = "gmail"

    def __init__(self, account_sources: list[tuple[str, Path]]):
        self.sources = [
            GmailSource(account_ref=account_ref, token_path=token_path) for account_ref, token_path in account_sources
        ]
        normalized_accounts = [source.account_ref.casefold() for source in self.sources]
        if len(normalized_accounts) != len(set(normalized_accounts)):
            raise IntegrationConnectionError(
                integration_id="gmail",
                reason="auth_required",
                detail="Reconnect Gmail: connected account emails must be unique.",
                retry_safe=True,
            )

    def _require_sources(self) -> None:
        if not self.sources:
            raise IntegrationConnectionError(
                integration_id="gmail",
                reason="not_configured",
                detail="No Gmail account is connected.",
            )

    def verify_connection(self) -> None:
        self._require_sources()
        for source in self.sources:
            source.verify_connection()

    def verify_account(self, account_ref: str) -> None:
        self._source_for(account_ref).verify_connection()

    def list_accounts(self) -> list[str]:
        return [source.account_ref for source in self.sources]

    def _source_for(self, account_ref: str | None) -> GmailSource:
        self._require_sources()
        if account_ref is None:
            if len(self.sources) == 1:
                return self.sources[0]
            raise IntegrationOperationError(
                code="ambiguous_ref",
                safe_message=f"Choose a Gmail account_ref: {', '.join(self.list_accounts())}",
                retryable=False,
            )
        source = next(
            (candidate for candidate in self.sources if candidate.account_ref == account_ref),
            None,
        )
        if source is None:
            raise IntegrationOperationError(
                code="invalid_ref",
                safe_message=f"Gmail account not found. Available: {', '.join(self.list_accounts())}",
                retryable=False,
            )
        return source

    def prepare_send(
        self,
        account_ref: str,
        recipient: str,
        subject: str,
        body: str,
        html: bool = False,
    ) -> GmailPreparedSend:
        source = self._source_for(account_ref)
        return source.prepare_send(
            recipient=recipient,
            subject=subject,
            body=body,
            html=html,
        )

    def send_email(self, prepared: GmailPreparedSend) -> GmailMutationReceipt:
        return self._source_for(prepared.account_ref).send(prepared)

    def prepare_reply(self, message_ref: str, body: str) -> GmailPreparedReply:
        account_ref, provider_message_id = parse_message_ref(message_ref)
        source = self._source_for(account_ref)
        return source.prepare_reply(provider_message_id, body)

    def reply_source(self, message_ref: str) -> GmailReplySource:
        account_ref, provider_message_id = parse_message_ref(message_ref)
        return self._source_for(account_ref).reply_source(provider_message_id)

    def reply_email(self, prepared: GmailPreparedReply) -> GmailMutationReceipt:
        return self._source_for(prepared.account_ref).reply(prepared)

    def read(self, message_ref: str) -> GmailMessage:
        account_ref, provider_message_id = parse_message_ref(message_ref)
        return self._source_for(account_ref).read(provider_message_id)

    def search(
        self,
        query: str,
        limit: int = 50,
        *,
        account_ref: str | None = None,
        page_ref: GmailPageRef | None = None,
    ) -> GmailMessagePage:
        if page_ref is not None:
            if page_ref.query is None:
                raise IntegrationOperationError(
                    code="invalid_ref",
                    safe_message="The Gmail page_ref belongs to a recent-email listing.",
                    retryable=False,
                )
            if query != page_ref.query or limit != page_ref.limit:
                raise IntegrationOperationError(
                    code="invalid_ref",
                    safe_message="The Gmail page_ref does not match this search.",
                    retryable=False,
                )
            if account_ref is not None and account_ref != page_ref.account_ref:
                raise IntegrationOperationError(
                    code="invalid_ref",
                    safe_message="The Gmail page_ref belongs to a different account_ref.",
                    retryable=False,
                )
            account_ref = page_ref.account_ref
        return self._source_for(account_ref).search(
            query,
            limit=limit,
            page_token=page_ref.provider_token if page_ref is not None else None,
        )

    def list_recent(
        self,
        days: int = 7,
        limit: int = 50,
        *,
        account_ref: str | None = None,
        page_ref: GmailPageRef | None = None,
    ) -> GmailMessagePage:
        if page_ref is not None:
            if page_ref.days is None:
                raise IntegrationOperationError(
                    code="invalid_ref",
                    safe_message="The Gmail page_ref belongs to a query search.",
                    retryable=False,
                )
            if days != page_ref.days or limit != page_ref.limit:
                raise IntegrationOperationError(
                    code="invalid_ref",
                    safe_message="The Gmail page_ref does not match this recent-email listing.",
                    retryable=False,
                )
            if account_ref is not None and account_ref != page_ref.account_ref:
                raise IntegrationOperationError(
                    code="invalid_ref",
                    safe_message="The Gmail page_ref belongs to a different account_ref.",
                    retryable=False,
                )
            account_ref = page_ref.account_ref
        return self._source_for(account_ref).list_recent(
            days=days,
            limit=limit,
            page_token=page_ref.provider_token if page_ref is not None else None,
        )
