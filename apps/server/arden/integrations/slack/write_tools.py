import json

from pydantic import Field

from arden.integrations.base import IntegrationOperationError
from arden.integrations.mutations import execute_idempotent, mutation_result
from arden.integrations.slack.block_kit import SlackBlockEnvelope
from arden.integrations.slack.client import SlackClient
from arden.integrations.slack.models import SlackPostReceipt
from arden.integrations.slack.tool_input import SlackToolInput
from arden.integrations.tool_errors import operation_error_result
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope
from arden.utils import truncate


class SlackPostMessageInput(SlackToolInput):
    channel: str = Field(
        min_length=1,
        description="Channel name (for example 'general') or exact Slack channel ref (for example 'C0123456789')",
    )
    text: str = Field(
        min_length=1,
        max_length=40_000,
        description=(
            "Plain Slack message text. Use this for simple unstructured messages only. "
            "For rich layouts, tables, status cards, diagnosis reports, or structured summaries, use slack_post_blocks instead. "
            "Supports basic Slack mrkdwn: *bold*, _italic_, ~strike~, `code`, ```code block```, <url|label>, mentions, and bullet lines."
        ),
    )
    thread_ref: str | None = Field(
        default=None,
        pattern=r"^[CGD][A-Z0-9]+:[0-9]+(?:\.[0-9]+)?$",
        description="Optional exact thread ref (channel_ref:parent_timestamp) from a Slack read result",
    )
    idempotency_key: str = Field(min_length=8, max_length=200)


class SlackPostBlocksInput(SlackToolInput):
    channel: str = Field(
        min_length=1,
        description="Channel name (for example 'general') or exact Slack channel ref (for example 'C0123456789')",
    )
    text: str = Field(
        min_length=1,
        max_length=40_000,
        description=(
            "Required fallback text for Slack notifications and accessibility. Keep concise; Slack may show this in previews/search."
        ),
    )
    blocks: SlackBlockEnvelope = Field(
        description=(
            "Required Slack Block Kit blocks array for rich message layout. Use native JSON objects, not a JSON string. "
            "Supported blocks: header, section, context, divider, image, and actions containing buttons. "
            "Text objects are {'type':'mrkdwn','text':'...'} or {'type':'plain_text','text':'...'}. "
            "Use section.fields for compact key/value cards, max 10 fields. "
            "Use real newline characters in mrkdwn text, not literal \\n. "
            "Limits: max 50 blocks, section text 3000 chars, field text 2000 chars."
        ),
    )
    thread_ref: str | None = Field(
        default=None,
        pattern=r"^[CGD][A-Z0-9]+:[0-9]+(?:\.[0-9]+)?$",
        description="Optional exact thread ref (channel_ref:parent_timestamp) from a Slack read result",
    )
    idempotency_key: str = Field(min_length=8, max_length=200)


SLACK_FORMATTING_GUIDE = (
    "Slack formatting quick guide: "
    "mrkdwn supports *bold*, _italic_, ~strike~, `inline code`, ```code blocks```, bullets with - or •, "
    "links as <https://example.com|label>, user/channel mentions, and real newlines. "
    "Block Kit supports header, section, fields, context, divider, image, actions. "
    "Use slack_post_blocks for structured cards/reports; use slack_post_message only for simple text."
)


SLACK_POST_MESSAGE_DESCRIPTION = (
    "Post a simple plain-text Slack message using the configured Slack token. "
    "Use thread_ref from a Slack read result to reply in a thread. "
    "Do not use this for rich layouts, diagnosis cards, tables, or structured reports; use slack_post_blocks instead. "
    + SLACK_FORMATTING_GUIDE
)


SLACK_POST_BLOCKS_DESCRIPTION = (
    "Post a rich Slack Block Kit message using the configured Slack token. "
    "Use this for formatted reports, alert diagnoses, status cards, field grids, summaries, or anything needing layout. "
    "Requires both fallback text and a native blocks array. Returns the posted message timestamp. "
    + SLACK_FORMATTING_GUIDE
)


async def approve_slack_post_message(execution: ToolExecution, args: SlackPostMessageInput) -> ApprovalInfo | None:
    location = f"{args.channel} thread {args.thread_ref}" if args.thread_ref else args.channel
    preview = truncate(args.text, 1000)
    return ApprovalInfo(description=f"Post Slack message to {location}", preview=preview, diff=None)


async def approve_slack_post_blocks(execution: ToolExecution, args: SlackPostBlocksInput) -> ApprovalInfo | None:
    location = f"{args.channel} thread {args.thread_ref}" if args.thread_ref else args.channel
    blocks = json.dumps(args.blocks.provider_payload(), ensure_ascii=False, separators=(",", ":"))
    preview = truncate(f"Fallback text:\n{args.text}\n\nBlocks:\n{blocks}", 1_500)
    return ApprovalInfo(description=f"Post Slack Block Kit message to {location}", preview=preview, diff=None)


def posted_message_result(args: SlackPostMessageInput | SlackPostBlocksInput, result: SlackPostReceipt) -> ToolResult:
    content = f"Posted to #{result.channel_name}\nmessage_ref: {result.message_ref}\nthread_ref: {result.thread_ref}"
    return mutation_result(
        content=content,
        preview=f"Posted to #{result.channel_name}",
        operation="post",
        target=args.channel,
        receipt=result.message_ref,
        after_ref=result.message_ref,
        observed=f"Slack returned message {result.message_ref}",
        data={"message_ref": result.message_ref, "thread_ref": result.thread_ref},
    )


async def slack_post_message(execution: ToolExecution, args: SlackPostMessageInput) -> ToolResult:
    async def invoke() -> ToolResult:
        source = execution.ctx.get_client("slack", SlackClient)
        try:
            result = await source.messages.post_message(args.channel, args.text, thread_ref=args.thread_ref)
        except IntegrationOperationError as error:
            return operation_error_result(error, preview="Slack post failed")
        return posted_message_result(args, result)

    return await execute_idempotent(
        execution,
        namespace="slack:post_message",
        idempotency_key=args.idempotency_key,
        payload=args.model_dump(exclude={"idempotency_key"}),
        invoke=invoke,
    )


async def slack_post_blocks(execution: ToolExecution, args: SlackPostBlocksInput) -> ToolResult:
    async def invoke() -> ToolResult:
        source = execution.ctx.get_client("slack", SlackClient)
        try:
            result = await source.messages.post_message(
                args.channel, args.text, thread_ref=args.thread_ref, blocks=args.blocks
            )
        except IntegrationOperationError as error:
            return operation_error_result(error, preview="Slack post failed")
        return posted_message_result(args, result)

    return await execute_idempotent(
        execution,
        namespace="slack:post_blocks",
        idempotency_key=args.idempotency_key,
        payload=args.model_dump(exclude={"idempotency_key"}),
        invoke=invoke,
    )


slack_post_message_tool = tool(
    display_name="SlackPostMessage",
    display_description="Post a plain-text Slack message.",
    description=SLACK_POST_MESSAGE_DESCRIPTION,
    input_model=SlackPostMessageInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.EXTERNAL,
        requires_approval=True,
        permissions=frozenset({"slack"}),
        deferred=True,
        destructive=False,
        open_world=True,
        idempotent=True,
    ),
    approval=approve_slack_post_message,
    execute=slack_post_message,
)

slack_post_blocks_tool = tool(
    display_name="SlackPostBlocks",
    display_description="Post a formatted Slack message.",
    description=SLACK_POST_BLOCKS_DESCRIPTION,
    input_model=SlackPostBlocksInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.EXTERNAL,
        requires_approval=True,
        permissions=frozenset({"slack"}),
        deferred=True,
        destructive=False,
        open_world=True,
        idempotent=True,
    ),
    approval=approve_slack_post_blocks,
    execute=slack_post_blocks,
)
