import asyncio
import json

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from arden.agent.types.tools import ToolSourceRef, canonical_source_title, normalize_source_refs
from arden.constants import EMAIL_FROM_TRUNCATE, EMAIL_SUBJECT_TRUNCATE
from arden.integrations.base import IntegrationOperationError
from arden.integrations.gmail.client import MultiGmailSource, parse_message_ref
from arden.integrations.gmail.models import GmailMessage, GmailMessageSummary, GmailPageRef
from arden.integrations.tool_errors import operation_error_result
from arden.tools.core import ToolResult, tool
from arden.tools.core.collections import format_timestamp
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope
from arden.utils import truncate

READ_EMAIL_DESCRIPTION = "Read a full email using the exact message_ref returned by email_search."

EMAILS_DESCRIPTION = """Browse or search emails.

Without query: lists recent emails (subjects and senders). Use days to control time range.
With query: searches email content. Use specific keywords like names, subjects, or phrases.
When multiple Gmail accounts are connected, account_ref is required. Continue a page by
passing back the exact account_ref and page_ref returned by this tool.

Use email_read(message_ref) to get full content of a specific email."""


class _GmailReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _message_source_ref(message_ref: str, title: str) -> tuple[ToolSourceRef, ...]:
    return normalize_source_refs(
        (
            ToolSourceRef(
                provider="gmail",
                kind="message",
                ref=message_ref,
                title=canonical_source_title(title, fallback=message_ref),
            ),
        )
    )


def _message_source_refs(messages: tuple[GmailMessageSummary, ...]) -> tuple[ToolSourceRef, ...]:
    return normalize_source_refs(
        tuple(
            ToolSourceRef(
                provider="gmail",
                kind="message",
                ref=message.ref,
                title=canonical_source_title(message.title, fallback=message.ref),
            )
            for message in messages
        )
    )


class EmailReadInput(_GmailReadInput):
    message_ref: str = Field(
        min_length=1,
        max_length=833,
        description="Exact account-qualified reference returned by email_search",
    )

    @field_validator("message_ref")
    @classmethod
    def _account_qualified_ref(cls, value: str) -> str:
        try:
            parse_message_ref(value)
        except IntegrationOperationError:
            raise ValueError("message_ref must be account-qualified") from None
        return value


async def email_read(execution: ToolExecution, args: EmailReadInput) -> ToolResult:
    source = execution.ctx.get_client("gmail", MultiGmailSource)
    try:
        email = await asyncio.to_thread(source.read, args.message_ref)
    except IntegrationOperationError as error:
        return operation_error_result(error, preview="Read failed")

    content = _format_email(email)
    lines = content.count("\n") + 1
    return ToolResult(
        content=content,
        preview=f"Read {lines} lines",
        source_refs=_message_source_ref(email.summary.ref, email.summary.title),
    )


def _format_email(email: GmailMessage) -> str:
    summary = email.summary
    return "\n".join(
        (
            f"From: {summary.sender}",
            f"To: {summary.recipient}",
            f"Reply-To: {summary.reply_to}",
            f"Subject: {json.dumps(summary.subject, ensure_ascii=False)}",
            f"Date: {summary.received_at.isoformat()}",
            f"Message ref: {summary.ref}",
            "",
            email.content,
        )
    )


def _format_email_list(emails: tuple[GmailMessageSummary, ...]) -> str:
    output = []
    for email in emails:
        title = truncate(email.title, EMAIL_SUBJECT_TRUNCATE)
        preview = truncate(email.snippet, EMAIL_FROM_TRUNCATE) if email.snippet else ""
        when = f" [{format_timestamp(email.received_at)}]"
        line = f"•{when} {title}" + (f" ({preview})" if preview else "")
        line += f"  ref: {email.ref}"
        line += f"  reply_to: {email.reply_to}"
        line += f"  subject: {json.dumps(email.subject, ensure_ascii=False)}"
        output.append(line)
    return "\n".join(output)


def _format_email_search(results: tuple[GmailMessageSummary, ...]) -> str:
    output = []
    for message in results:
        subject = truncate(message.title, EMAIL_SUBJECT_TRUNCATE)
        sender = truncate(message.sender, EMAIL_FROM_TRUNCATE)
        output.append(f"• [{format_timestamp(message.received_at)}] {subject}")
        output.append(
            f"  from: {sender}, reply_to: {message.reply_to}, ref: {message.ref}, "
            f"subject: {json.dumps(message.subject, ensure_ascii=False)}"
        )
    return "\n".join(output)


_DEFAULT_EMAIL_DAYS = 7
_DEFAULT_EMAIL_LIMIT = 30


class EmailSearchInput(_GmailReadInput):
    query: str | None = Field(
        default=None,
        min_length=1,
        max_length=4096,
        description="Non-blank search query. Omit to list recent emails.",
    )
    account_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=320,
        description="Exact Gmail account reference. Required when multiple accounts are connected.",
    )
    page_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=12_000,
        description="Exact continuation returned by email_search. Pass it alone, without other fields.",
    )
    days: int = Field(
        default=_DEFAULT_EMAIL_DAYS,
        ge=1,
        le=3650,
        description=f"How many days back to look when listing (default: {_DEFAULT_EMAIL_DAYS})",
    )
    limit: int = Field(
        default=_DEFAULT_EMAIL_LIMIT, ge=1, le=100, description=f"Maximum results (default: {_DEFAULT_EMAIL_LIMIT})"
    )

    @field_validator("query")
    @classmethod
    def _non_blank_query(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or value != value.strip()):
            raise ValueError("query must be exact non-blank text")
        return value

    @field_validator("account_ref")
    @classmethod
    def _exact_account_ref(cls, value: str | None) -> str | None:
        if value is not None and value != value.strip():
            raise ValueError("account_ref must exactly match a returned Gmail account reference")
        return value

    @field_validator("page_ref")
    @classmethod
    def _canonical_page_ref(cls, value: str | None) -> str | None:
        if value is not None:
            GmailPageRef.decode(value)
        return value

    @model_validator(mode="after")
    def _continuation_is_self_contained(self):
        if self.page_ref is not None and self.model_fields_set != {"page_ref"}:
            raise ValueError("page_ref must be passed alone")
        return self


def _continuation(page_ref: GmailPageRef | None) -> str:
    if page_ref is None:
        return ""
    return f"\nContinue with page_ref: {page_ref.encode()}"


def _page_data(*, count: int, account_ref: str, next_page_ref: GmailPageRef | None) -> dict[str, object]:
    return {
        "count": count,
        "account_ref": account_ref,
        "next_page_ref": next_page_ref.encode() if next_page_ref is not None else None,
    }


async def _list_emails(
    source: MultiGmailSource,
    days: int,
    limit: int,
    *,
    account_ref: str | None,
    page_ref: GmailPageRef | None,
) -> ToolResult:
    page = await asyncio.to_thread(
        source.list_recent,
        days=days,
        limit=limit,
        account_ref=account_ref,
        page_ref=page_ref,
    )
    emails = page.messages
    continuation = _continuation(page.next_page_ref)
    data = _page_data(count=len(emails), account_ref=page.account_ref, next_page_ref=page.next_page_ref)

    if not emails:
        return ToolResult(
            content=f"No emails in last {days} days for {page.account_ref}{continuation}",
            preview="0 emails",
            data=data,
        )

    return ToolResult(
        content=_format_email_list(emails) + continuation,
        preview=f"{len(emails)} emails" + (" (more available)" if page.next_page_ref else ""),
        data=data,
        source_refs=_message_source_refs(emails),
    )


async def _search_emails(
    source: MultiGmailSource,
    query: str,
    limit: int,
    *,
    account_ref: str | None,
    page_ref: GmailPageRef | None,
) -> ToolResult:
    page = await asyncio.to_thread(
        source.search,
        query,
        limit=limit,
        account_ref=account_ref,
        page_ref=page_ref,
    )
    results = page.messages
    continuation = _continuation(page.next_page_ref)
    data = _page_data(count=len(results), account_ref=page.account_ref, next_page_ref=page.next_page_ref)
    if not results:
        return ToolResult(
            content=f"No emails found for '{query}' in {page.account_ref}{continuation}",
            preview="0 emails",
            data=data,
        )

    return ToolResult(
        content=_format_email_search(results) + continuation,
        preview=f"{len(results)} emails" + (" (more available)" if page.next_page_ref else ""),
        data=data,
        source_refs=_message_source_refs(results),
    )


async def email_search(execution: ToolExecution, args: EmailSearchInput) -> ToolResult:
    source = execution.ctx.get_client("gmail", MultiGmailSource)
    page_ref = GmailPageRef.decode(args.page_ref) if args.page_ref is not None else None
    try:
        if page_ref is not None:
            if page_ref.query is not None:
                return await _search_emails(
                    source,
                    page_ref.query,
                    page_ref.limit,
                    account_ref=page_ref.account_ref,
                    page_ref=page_ref,
                )
            return await _list_emails(
                source,
                page_ref.days,
                page_ref.limit,
                account_ref=page_ref.account_ref,
                page_ref=page_ref,
            )
        if args.query:
            return await _search_emails(
                source,
                args.query,
                args.limit,
                account_ref=args.account_ref,
                page_ref=page_ref,
            )
        return await _list_emails(
            source,
            args.days,
            args.limit,
            account_ref=args.account_ref,
            page_ref=page_ref,
        )
    except IntegrationOperationError as error:
        return operation_error_result(error, preview="Email search failed")


email_search_tool = tool(
    display_name="Emails",
    display_description="Browse and search Gmail messages.",
    description=EMAILS_DESCRIPTION,
    input_model=EmailSearchInput,
    policy=ToolPolicy(
        action=ToolAction.READ, scope=ToolScope.EXTERNAL, permissions=frozenset({"gmail"}), deferred=True
    ),
    execute=email_search,
)

email_read_tool = tool(
    display_name="ReadEmail",
    display_description="Read a Gmail message.",
    description=READ_EMAIL_DESCRIPTION,
    input_model=EmailReadInput,
    policy=ToolPolicy(
        action=ToolAction.READ, scope=ToolScope.EXTERNAL, permissions=frozenset({"gmail"}), deferred=True
    ),
    execute=email_read,
)
