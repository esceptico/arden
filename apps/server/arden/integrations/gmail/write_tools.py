import asyncio
import json
from dataclasses import replace

from pydantic import BaseModel, ConfigDict, Field, field_validator

from arden.agent.types.tools import ToolSourceRef, canonical_source_title, normalize_source_refs
from arden.integrations.base import IntegrationOperationError
from arden.integrations.gmail.client import (
    GmailPreparedReply,
    GmailPreparedSend,
    MultiGmailSource,
    parse_message_ref,
)
from arden.integrations.gmail.compose import has_header_line_break
from arden.integrations.mutations import execute_idempotent, mutation_result
from arden.integrations.tool_errors import operation_error_result
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope

SEND_EMAIL_DESCRIPTION = "Send an email from an exact connected Gmail account_ref. Requires approval."
REPLY_EMAIL_DESCRIPTION = (
    "Reply in an existing Gmail thread using the exact message_ref returned by email_search or email_read. "
    "Pass the exact recipient and subject returned with the message; use null when the subject is absent. "
    "The tool rejects changed provider headers. "
    "Preserves the provider thread and reply headers. Requires approval."
)


class _GmailWriteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


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


class EmailSendInput(_GmailWriteInput):
    account_ref: str = Field(
        min_length=1,
        max_length=320,
        description="Exact sender account reference returned by Gmail setup",
    )
    recipient: str = Field(min_length=1, max_length=998, description="Recipient email address")
    subject: str = Field(max_length=998, description="Email subject")
    body: str = Field(max_length=100_000, description="Email body (plain text)")
    idempotency_key: str = Field(min_length=8, max_length=200, description="Unique stable key for this send attempt")

    @field_validator("account_ref")
    @classmethod
    def _exact_account_ref(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("account_ref must exactly match a returned Gmail account reference")
        return value

    @field_validator("account_ref", "recipient", "subject")
    @classmethod
    def _single_line_header(cls, value: str) -> str:
        if has_header_line_break(value):
            raise ValueError("email headers must not contain line breaks")
        return value


class EmailReplyInput(_GmailWriteInput):
    message_ref: str = Field(
        min_length=1,
        max_length=833,
        description="Exact account-qualified reference returned by email_search.",
    )
    recipient: str = Field(min_length=1, max_length=998, description="Exact reply_to value returned with the message.")
    subject: str | None = Field(
        max_length=16_384,
        description="Exact subject value returned with the message, including null when absent.",
    )
    body: str = Field(min_length=1, max_length=100_000, description="Plain-text reply body.")
    idempotency_key: str = Field(min_length=8, max_length=200, description="Unique stable key for this reply attempt.")

    @field_validator("message_ref")
    @classmethod
    def _account_qualified_ref(cls, value: str) -> str:
        try:
            parse_message_ref(value)
        except IntegrationOperationError:
            raise ValueError("message_ref must be account-qualified") from None
        return value

    @field_validator("recipient", "subject")
    @classmethod
    def _single_line_reply_header(cls, value: str | None) -> str | None:
        if value is not None and has_header_line_break(value):
            raise ValueError("email reply headers must not contain line breaks")
        return value


async def approve_email_send(execution: ToolExecution, args: EmailSendInput) -> ApprovalInfo | None:
    return ApprovalInfo(
        description=args.recipient,
        preview=f"Subject: {args.subject}\nFrom: {args.account_ref}\nTo: {args.recipient}\n\nBody:\n{args.body}",
        diff=None,
    )


async def email_send(execution: ToolExecution, args: EmailSendInput) -> ToolResult:
    source = execution.ctx.get_client("gmail", MultiGmailSource)
    prepared: GmailPreparedSend | None = None

    async def before_mutation() -> None:
        nonlocal prepared
        prepared = await asyncio.to_thread(
            source.prepare_send,
            account_ref=args.account_ref,
            recipient=args.recipient,
            subject=args.subject,
            body=args.body,
        )

    async def invoke() -> ToolResult:
        if prepared is None:
            raise RuntimeError("Gmail send preparation did not complete")
        receipt = await asyncio.to_thread(source.send_email, prepared)
        mutation = mutation_result(
            content=f"Sent email to {receipt.recipient}. ref: {receipt.message_ref}",
            preview="Sent",
            operation="send",
            target=args.recipient,
            receipt=receipt.message_ref,
            after_ref=receipt.message_ref,
            observed=f"Gmail returned message {receipt.message_ref}",
            data={"message_ref": receipt.message_ref, "thread_ref": receipt.thread_ref},
        )
        return replace(
            mutation,
            source_refs=_message_source_ref(receipt.message_ref, f"Sent to {receipt.recipient}"),
        )

    try:
        return await execute_idempotent(
            execution,
            namespace=f"gmail:send:{args.account_ref}",
            idempotency_key=args.idempotency_key,
            payload=args.model_dump(exclude={"idempotency_key"}),
            before_mutation=before_mutation,
            invoke=invoke,
        )
    except IntegrationOperationError as error:
        return operation_error_result(error, preview="Send failed")


async def approve_email_reply(execution: ToolExecution, args: EmailReplyInput) -> ApprovalInfo | None:
    return ApprovalInfo(
        description=f"Reply to {args.recipient}",
        preview=(
            f"To: {args.recipient}\nSubject: {json.dumps(args.subject, ensure_ascii=False)}\n"
            f"Thread: {args.message_ref}\n\nBody:\n{args.body}"
        ),
        diff=None,
    )


def _reply_source_changed() -> ToolResult:
    return ToolResult.failure(
        code="revision_conflict",
        message="The Gmail reply target changed after this message was read.",
        preview="Reply target changed",
        recovery_action="Read the message again and retry with its exact reply_to and subject values.",
    )


async def preflight_email_reply(execution: ToolExecution, args: EmailReplyInput) -> ToolResult | None:
    source = execution.ctx.get_client("gmail", MultiGmailSource)
    try:
        reply_source = await asyncio.to_thread(source.reply_source, args.message_ref)
    except IntegrationOperationError as error:
        return operation_error_result(error, preview="Reply preview failed")
    if reply_source.recipient != args.recipient or reply_source.subject != args.subject:
        return _reply_source_changed()
    return None


async def email_reply(execution: ToolExecution, args: EmailReplyInput) -> ToolResult:
    source = execution.ctx.get_client("gmail", MultiGmailSource)
    prepared: GmailPreparedReply | None = None

    async def before_mutation() -> None:
        nonlocal prepared
        prepared = await asyncio.to_thread(source.prepare_reply, args.message_ref, args.body)
        if prepared.recipient != args.recipient or prepared.subject != args.subject:
            raise IntegrationOperationError(
                code="revision_conflict",
                safe_message="The Gmail reply target changed after approval.",
                retryable=False,
            )

    async def invoke() -> ToolResult:
        if prepared is None:
            raise RuntimeError("Gmail reply preparation did not complete")
        receipt = await asyncio.to_thread(source.reply_email, prepared)
        mutation = mutation_result(
            content=f"Replied to {receipt.recipient}. ref: {receipt.message_ref}",
            preview="Replied",
            operation="reply",
            target=args.message_ref,
            receipt=receipt.message_ref,
            before_ref=args.message_ref,
            after_ref=receipt.message_ref,
            observed=f"Gmail returned reply {receipt.message_ref}",
            data={"message_ref": receipt.message_ref, "thread_ref": receipt.thread_ref},
        )
        return replace(
            mutation,
            source_refs=normalize_source_refs(
                (
                    *_message_source_ref(args.message_ref, args.message_ref),
                    *_message_source_ref(receipt.message_ref, f"Reply to {receipt.recipient}"),
                )
            ),
        )

    account_ref, _provider_message_id = parse_message_ref(args.message_ref)
    try:
        return await execute_idempotent(
            execution,
            namespace=f"gmail:reply:{account_ref}",
            idempotency_key=args.idempotency_key,
            payload=args.model_dump(exclude={"idempotency_key"}),
            before_mutation=before_mutation,
            invoke=invoke,
        )
    except IntegrationOperationError as error:
        return operation_error_result(error, preview="Reply failed")


email_send_tool = tool(
    display_name="SendEmail",
    display_description="Send a Gmail message after approval.",
    description=SEND_EMAIL_DESCRIPTION,
    input_model=EmailSendInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.EXTERNAL,
        requires_approval=True,
        permissions=frozenset({"gmail"}),
        deferred=True,
        destructive=False,
        open_world=True,
        idempotent=True,
    ),
    approval=approve_email_send,
    execute=email_send,
)

email_reply_tool = tool(
    display_name="ReplyEmail",
    display_description="Reply in a Gmail thread after approval.",
    description=REPLY_EMAIL_DESCRIPTION,
    input_model=EmailReplyInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.EXTERNAL,
        requires_approval=True,
        permissions=frozenset({"gmail"}),
        deferred=True,
        destructive=False,
        open_world=True,
        idempotent=True,
    ),
    approval=approve_email_reply,
    preflight=preflight_email_reply,
    execute=email_reply,
)
