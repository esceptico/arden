from pydantic import BaseModel, Field

from ntrp.agent.types.tools import ToolSourceRef, normalize_source_refs
from ntrp.constants import EMAIL_FROM_TRUNCATE, EMAIL_SUBJECT_TRUNCATE
from ntrp.integrations.gmail.client import MultiGmailSource
from ntrp.tools.core import ToolResult, tool
from ntrp.tools.core.context import ToolExecution
from ntrp.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope
from ntrp.utils import truncate

SEND_EMAIL_DESCRIPTION = "Send an email from a specified Gmail account. Requires approval."

READ_EMAIL_DESCRIPTION = (
    "Read the full content of an email by its ID. Use emails() or emails(query) first to find email IDs."
)

EMAILS_DESCRIPTION = """Browse or search emails.

Without query: lists recent emails (subjects and senders). Use days to control time range.
With query: searches email content. Use specific keywords like names, subjects, or phrases.

Use read_email(id) to get full content of a specific email."""


def _message_source_ref(message_id: str, title: str | None = None) -> tuple[ToolSourceRef, ...]:
    return normalize_source_refs(
        (
            ToolSourceRef(
                provider="gmail",
                kind="message",
                ref=message_id,
                title=(title or "").strip() or f"Gmail message {message_id}",
            ),
        )
    )


def _message_source_refs(items: list) -> tuple[ToolSourceRef, ...]:
    refs = []
    for item in items:
        message_id = getattr(item, "source_id", None) or getattr(item, "identity", "")
        refs.append(
            ToolSourceRef(
                provider="gmail",
                kind="message",
                ref=message_id,
                title=(item.title or "").strip() or f"Gmail message {message_id}",
            )
        )
    return normalize_source_refs(refs)


class SendEmailInput(BaseModel):
    account: str = Field(description="Sender email address (must match a connected Gmail account)")
    to: str = Field(description="Recipient email address")
    subject: str = Field(description="Email subject")
    body: str = Field(description="Email body (plain text)")


async def approve_send_email(execution: ToolExecution, args: SendEmailInput) -> ApprovalInfo | None:
    return ApprovalInfo(description=args.to, preview=f"Subject: {args.subject}\nFrom: {args.account}", diff=None)


async def send_email(execution: ToolExecution, args: SendEmailInput) -> ToolResult:
    source = execution.ctx.get_client("gmail", MultiGmailSource)
    result = source.send_email(account=args.account, to=args.to, subject=args.subject, body=args.body)
    return ToolResult(content=result, preview="Sent")


class ReadEmailInput(BaseModel):
    email_id: str = Field(description="The email ID (from search or list results)")


async def read_email(execution: ToolExecution, args: ReadEmailInput) -> ToolResult:
    source = execution.ctx.get_client("gmail", MultiGmailSource)
    content = source.read(args.email_id)
    if not content:
        return ToolResult(
            content=f"Email not found: {args.email_id}. Use emails() or emails(query) to find valid email IDs.",
            preview="Not found",
        )

    lines = content.count("\n") + 1
    return ToolResult(
        content=content,
        preview=f"Read {lines} lines",
        source_refs=_message_source_ref(args.email_id),
    )


def _format_email_list(emails: list) -> str:
    output = []
    for email in emails:
        title = truncate(email.title, EMAIL_SUBJECT_TRUNCATE) if email.title else "(no subject)"
        preview = truncate(email.preview, EMAIL_FROM_TRUNCATE) if email.preview else ""
        line = f"• {title}" + (f" ({preview})" if preview else "")
        if email.identity:
            line += f"  id: {email.identity}"
        output.append(line)
    return "\n".join(output)


def _format_email_search(results: list) -> str:
    output = []
    for item in results:
        meta = item.metadata
        subj = truncate(meta.get("subject", "No subject"), EMAIL_SUBJECT_TRUNCATE)
        frm = truncate(meta.get("from", ""), EMAIL_FROM_TRUNCATE)
        output.append(f"• {subj}")
        output.append(f"  from: {frm}, id: {item.source_id}")
    return "\n".join(output)


_DEFAULT_EMAIL_DAYS = 7
_DEFAULT_EMAIL_LIMIT = 30


class EmailsInput(BaseModel):
    query: str | None = Field(default=None, description="Search query. Omit to list recent emails.")
    days: int = Field(
        default=_DEFAULT_EMAIL_DAYS,
        description=f"How many days back to look when listing (default: {_DEFAULT_EMAIL_DAYS})",
    )
    limit: int = Field(default=_DEFAULT_EMAIL_LIMIT, description=f"Maximum results (default: {_DEFAULT_EMAIL_LIMIT})")


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

    trimmed = emails[:limit]
    content = _format_email_list(trimmed)
    return ToolResult(
        content=content,
        preview=f"{len(emails)} emails",
        source_refs=_message_source_refs(trimmed),
    )


def _search_emails(source: MultiGmailSource, query: str, limit: int) -> ToolResult:
    results = source.search(query, limit=limit)
    if not results:
        return ToolResult(content=f"No emails found for '{query}'", preview="0 emails")

    trimmed = results[:limit]
    content = _format_email_search(trimmed)
    return ToolResult(
        content=content,
        preview=f"{len(results)} emails",
        source_refs=_message_source_refs(trimmed),
    )


async def emails(execution: ToolExecution, args: EmailsInput) -> ToolResult:
    source = execution.ctx.get_client("gmail", MultiGmailSource)
    if args.query:
        return _search_emails(source, args.query, args.limit)
    return _list_emails(source, args.days, args.limit)


emails_tool = tool(
    display_name="Emails",
    description=EMAILS_DESCRIPTION,
    input_model=EmailsInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.EXTERNAL, permissions=frozenset({"gmail"})),
    execute=emails,
)

read_email_tool = tool(
    display_name="ReadEmail",
    description=READ_EMAIL_DESCRIPTION,
    input_model=ReadEmailInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.EXTERNAL, permissions=frozenset({"gmail"})),
    execute=read_email,
)

send_email_tool = tool(
    display_name="SendEmail",
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
