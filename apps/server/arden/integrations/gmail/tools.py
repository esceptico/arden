import re
from dataclasses import replace

from pydantic import BaseModel, Field

from arden.agent.types.tools import ToolSourceRef, normalize_source_refs
from arden.constants import EMAIL_FROM_TRUNCATE, EMAIL_SUBJECT_TRUNCATE
from arden.integrations.base import IntegrationOperationError
from arden.integrations.gmail.client import MultiGmailSource
from arden.integrations.mutations import execute_idempotent, mutation_result
from arden.integrations.tool_errors import operation_error_result
from arden.tools.core import ToolResult, tool
from arden.tools.core.collections import format_timestamp
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope
from arden.utils import truncate

SEND_EMAIL_DESCRIPTION = "Send an email from a specified Gmail account. Requires approval."
REPLY_EMAIL_DESCRIPTION = (
    "Reply in an existing Gmail thread using the qualified account:message_id returned by emails or read_email. "
    "Preserves the provider thread and reply headers. Requires approval."
)

READ_EMAIL_DESCRIPTION = (
    "Read the full content of an email by its ID. Use emails() or emails(query) first to find email IDs."
)

EMAILS_DESCRIPTION = """Browse or search emails.

Without query: lists recent emails (subjects and senders). Use days to control time range.
With query: searches email content. Use specific keywords like names, subjects, or phrases.

Use read_email(id) to get full content of a specific email."""


def _qualified_message_ref(account: str, message_id: str) -> str:
    account = account.strip()
    message_id = message_id.strip()
    if account and message_id.startswith(f"{account}:"):
        return message_id
    return f"{account}:{message_id}" if account and message_id else ""


def _message_source_ref(account: str, message_id: str, title: str | None = None) -> tuple[ToolSourceRef, ...]:
    return normalize_source_refs(
        (
            ToolSourceRef(
                provider="gmail",
                kind="message",
                ref=_qualified_message_ref(account, message_id),
                title=(title or "").strip() or f"Gmail message {message_id}",
            ),
        )
    )


def _message_source_refs(items: list) -> tuple[ToolSourceRef, ...]:
    refs = []
    for item in items:
        message_id = getattr(item, "source_id", None) or getattr(item, "identity", "")
        account = getattr(item, "metadata", {}).get("account", "") or getattr(item, "account", "")
        refs.append(
            ToolSourceRef(
                provider="gmail",
                kind="message",
                ref=_qualified_message_ref(account, message_id),
                title=(item.title or "").strip() or f"Gmail message {message_id}",
            )
        )
    return normalize_source_refs(refs)


class SendEmailInput(BaseModel):
    account: str = Field(description="Sender email address (must match a connected Gmail account)")
    to: str = Field(description="Recipient email address")
    subject: str = Field(description="Email subject")
    body: str = Field(description="Email body (plain text)")
    idempotency_key: str = Field(min_length=8, max_length=200, description="Unique stable key for this send attempt")


class ReplyEmailInput(BaseModel):
    message_ref: str = Field(description="Qualified account:message_id returned by emails or read_email.")
    body: str = Field(min_length=1, max_length=100_000, description="Plain-text reply body.")
    idempotency_key: str = Field(min_length=8, max_length=200, description="Unique stable key for this reply attempt.")


async def approve_send_email(execution: ToolExecution, args: SendEmailInput) -> ApprovalInfo | None:
    preview = truncate(
        f"Subject: {args.subject}\nFrom: {args.account}\n\nBody:\n{args.body}",
        1_500,
    )
    return ApprovalInfo(description=args.to, preview=preview, diff=None)


async def send_email(execution: ToolExecution, args: SendEmailInput) -> ToolResult:
    async def invoke() -> ToolResult:
        source = execution.ctx.get_client("gmail", MultiGmailSource)
        try:
            result = source.send_email(account=args.account, to=args.to, subject=args.subject, body=args.body)
        except IntegrationOperationError as error:
            return operation_error_result(error, preview="Send failed")
        match = re.search(r"\(id: ([^)]+)\)", result)
        message_ref = f"{args.account}:{match.group(1)}" if match else None
        return mutation_result(
            content=result,
            preview="Sent" if message_ref else "Send unverified",
            operation="send",
            target=args.to,
            receipt=match.group(1) if match else args.idempotency_key,
            after_ref=message_ref,
            observed=(f"Gmail returned message {message_ref}" if message_ref else None),
            data={"message_ref": message_ref} if message_ref else None,
        )

    return await execute_idempotent(
        execution,
        namespace=f"gmail:send:{args.account}",
        idempotency_key=args.idempotency_key,
        payload=args.model_dump(exclude={"idempotency_key"}),
        invoke=invoke,
    )


async def approve_reply_email(execution: ToolExecution, args: ReplyEmailInput) -> ApprovalInfo | None:
    return ApprovalInfo(
        description=f"Reply to {args.message_ref}",
        preview=truncate(f"Thread: {args.message_ref}\n\nBody:\n{args.body}", 1_500),
        diff=None,
    )


async def reply_email(execution: ToolExecution, args: ReplyEmailInput) -> ToolResult:
    async def invoke() -> ToolResult:
        source = execution.ctx.get_client("gmail", MultiGmailSource)
        try:
            result = source.reply_email(args.message_ref, args.body)
        except IntegrationOperationError as error:
            return operation_error_result(error, preview="Reply failed")
        match = re.search(r"\(id: ([^)]+)\)", result)
        account, _, _message_id = args.message_ref.partition(":")
        reply_ref = _qualified_message_ref(account, match.group(1)) if match else None
        mutation = mutation_result(
            content=result,
            preview="Replied" if reply_ref else "Reply unverified",
            operation="reply",
            target=args.message_ref,
            receipt=match.group(1) if match else args.idempotency_key,
            before_ref=args.message_ref,
            after_ref=reply_ref,
            observed=(f"Gmail returned reply {reply_ref}" if reply_ref else None),
            data={"message_ref": reply_ref, "thread_ref": args.message_ref} if reply_ref else None,
        )
        return replace(
            mutation,
            source_refs=normalize_source_refs(
                (*_message_source_ref(account, args.message_ref), *_message_source_ref(account, reply_ref or ""))
            ),
        )

    account, separator, _message_id = args.message_ref.partition(":")
    namespace_account = account if separator else "invalid"
    return await execute_idempotent(
        execution,
        namespace=f"gmail:reply:{namespace_account}",
        idempotency_key=args.idempotency_key,
        payload=args.model_dump(exclude={"idempotency_key"}),
        invoke=invoke,
    )


class ReadEmailInput(BaseModel):
    email_id: str = Field(description="The email ID (from search or list results)")


async def read_email(execution: ToolExecution, args: ReadEmailInput) -> ToolResult:
    source = execution.ctx.get_client("gmail", MultiGmailSource)
    email = source.read(args.email_id)
    if not email:
        return ToolResult.failure(
            code="not_found",
            message=f"Email not found: {args.email_id}. Use emails() or emails(query) to find valid email IDs.",
            preview="Not found",
            recovery_action="Call emails with a query and retry using an exact returned email ID.",
        )

    lines = email.content.count("\n") + 1
    return ToolResult(
        content=email.content,
        preview=f"Read {lines} lines",
        source_refs=_message_source_ref(email.account, args.email_id),
    )


def _format_email_list(emails: list) -> str:
    output = []
    for email in emails:
        title = truncate(email.title, EMAIL_SUBJECT_TRUNCATE) if email.title else "(no subject)"
        preview = truncate(email.preview, EMAIL_FROM_TRUNCATE) if email.preview else ""
        timestamp = getattr(email, "timestamp", None)
        when = f" [{format_timestamp(timestamp)}]" if timestamp else ""
        line = f"•{when} {title}" + (f" ({preview})" if preview else "")
        if email.identity:
            line += f"  id: {_qualified_message_ref(email.account, email.identity)}"
        output.append(line)
    return "\n".join(output)


def _format_email_search(results: list) -> str:
    output = []
    for item in results:
        meta = item.metadata
        subj = truncate(meta.get("subject", "No subject"), EMAIL_SUBJECT_TRUNCATE)
        frm = truncate(meta.get("from", ""), EMAIL_FROM_TRUNCATE)
        output.append(f"• [{format_timestamp(item.created_at)}] {subj}")
        output.append(f"  from: {frm}, id: {_qualified_message_ref(meta.get('account', ''), item.source_id)}")
    return "\n".join(output)


_DEFAULT_EMAIL_DAYS = 7
_DEFAULT_EMAIL_LIMIT = 30


class EmailsInput(BaseModel):
    query: str | None = Field(default=None, description="Search query. Omit to list recent emails.")
    days: int = Field(
        default=_DEFAULT_EMAIL_DAYS,
        ge=1,
        le=3650,
        description=f"How many days back to look when listing (default: {_DEFAULT_EMAIL_DAYS})",
    )
    limit: int = Field(
        default=_DEFAULT_EMAIL_LIMIT, ge=1, le=100, description=f"Maximum results (default: {_DEFAULT_EMAIL_LIMIT})"
    )


def _list_emails(source: MultiGmailSource, days: int, limit: int) -> ToolResult:
    accounts = source.list_accounts()
    emails = source.list_recent(days=days, limit=limit)

    if not emails:
        if accounts:
            return ToolResult(
                content=f"No emails in last {days} days from {len(accounts)} accounts",
                preview="0 emails",
            )
        return ToolResult(content=f"No emails in last {days} days", preview="0 emails")

    content = _format_email_list(emails)
    if len(emails) == limit:
        content += f"\nShowing {limit} emails; more may exist. Narrow the date range to continue."
    return ToolResult(
        content=content,
        preview=f"{len(emails)} emails" + (" (possibly capped)" if len(emails) == limit else ""),
        data={"count": len(emails), "may_have_more": len(emails) == limit},
        source_refs=_message_source_refs(emails),
    )


def _search_emails(source: MultiGmailSource, query: str, limit: int) -> ToolResult:
    results = source.search(query, limit=limit)
    if not results:
        return ToolResult(content=f"No emails found for '{query}'", preview="0 emails")

    content = _format_email_search(results)
    if len(results) == limit:
        content += f"\nShowing {limit} emails; more may exist. Narrow the query to continue."
    return ToolResult(
        content=content,
        preview=f"{len(results)} emails" + (" (possibly capped)" if len(results) == limit else ""),
        data={"count": len(results), "may_have_more": len(results) == limit},
        source_refs=_message_source_refs(results),
    )


async def emails(execution: ToolExecution, args: EmailsInput) -> ToolResult:
    source = execution.ctx.get_client("gmail", MultiGmailSource)
    if args.query:
        return _search_emails(source, args.query, args.limit)
    return _list_emails(source, args.days, args.limit)


emails_tool = tool(
    display_name="Emails",
    display_description="Browse and search Gmail messages.",
    description=EMAILS_DESCRIPTION,
    input_model=EmailsInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.EXTERNAL, permissions=frozenset({"gmail"})),
    execute=emails,
)

read_email_tool = tool(
    display_name="ReadEmail",
    display_description="Read a Gmail message.",
    description=READ_EMAIL_DESCRIPTION,
    input_model=ReadEmailInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.EXTERNAL, permissions=frozenset({"gmail"})),
    execute=read_email,
)

send_email_tool = tool(
    display_name="SendEmail",
    display_description="Send a Gmail message after approval.",
    description=SEND_EMAIL_DESCRIPTION,
    input_model=SendEmailInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.EXTERNAL,
        requires_approval=True,
        permissions=frozenset({"gmail"}),
    ),
    approval=approve_send_email,
    execute=send_email,
)

reply_email_tool = tool(
    display_name="ReplyEmail",
    display_description="Reply in a Gmail thread after approval.",
    description=REPLY_EMAIL_DESCRIPTION,
    input_model=ReplyEmailInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.EXTERNAL,
        requires_approval=True,
        permissions=frozenset({"gmail"}),
    ),
    approval=approve_reply_email,
    execute=reply_email,
)
