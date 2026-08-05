import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from arden.agent.types.tools import ToolSourceRef, normalize_source_refs
from arden.integrations.mutations import execute_idempotent, mutation_result
from arden.integrations.slack.client import SlackClient
from arden.tools.core import ToolResult, tool
from arden.tools.core.collections import format_timestamp
from arden.tools.core.context import ToolExecution
from arden.tools.core.error_results import invalid_ref_result, not_found_result
from arden.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope
from arden.utils import truncate

_TEXT_TRUNCATE = 280
_DEFAULT_LIMIT = 20


def _bounded_content(content: str, *, count: int, limit: int, noun: str) -> tuple[str, bool]:
    may_have_more = count == limit
    if may_have_more:
        content += f"\nShowing {count} {noun}; more may exist. Narrow the query or target to continue."
    return content, may_have_more


def _message_source_refs(items: list) -> tuple[ToolSourceRef, ...]:
    return normalize_source_refs(
        ToolSourceRef(
            provider="slack",
            kind="message",
            ref=item.source_id,
            title=(item.title or "").strip() or f"Slack message {item.source_id}",
            url=item.metadata.get("permalink"),
        )
        for item in items
    )


def _message_source_ref(message_id: str, *, title: str) -> tuple[ToolSourceRef, ...]:
    return normalize_source_refs(
        (
            ToolSourceRef(
                provider="slack",
                kind="message",
                ref=message_id,
                title=title,
            ),
        )
    )


def _entity_source_refs(items: list[dict], *, kind: str, id_key: str, title_key: str) -> tuple[ToolSourceRef, ...]:
    return normalize_source_refs(
        ToolSourceRef(
            provider="slack",
            kind=kind,
            ref=str(item[id_key]),
            title=str(item.get(title_key) or item[id_key]),
        )
        for item in items
        if item.get(id_key)
    )


def _format_messages(items: list, *, show_thread_hint: bool = True) -> str:
    lines = []
    for item in items:
        meta = item.metadata
        when = format_timestamp(item.created_at)
        cname = meta.get("channel_name", "")
        user = meta.get("user_name", "unknown")
        text = truncate(item.content or "(empty)", _TEXT_TRUNCATE)
        header = f"• [{when}] #{cname} — {user}"
        lines.append(header)
        lines.append(f"    {text}")
        suffix = []
        if show_thread_hint and meta.get("reply_count"):
            suffix.append(f"thread: {meta['reply_count']} replies")
        suffix.append(f"id: {item.source_id}")
        lines.append(f"    ({', '.join(suffix)})")
    return "\n".join(lines)


class SlackSearchInput(BaseModel):
    query: str = Field(description="Slack search query. Supports operators: from:@user in:#channel before:2024-01-01")
    limit: int = Field(default=_DEFAULT_LIMIT, ge=1, le=20, description="Max results (Slack API maximum: 20)")
    scope: Literal["dms", "channels"] | None = Field(
        default=None,
        description="Optional scope: 'dms' (DMs + group DMs only), 'channels' (public/private only), or None for all.",
    )


_SCOPE_MAP = {
    "dms": ["im", "mpim"],
    "channels": ["public_channel", "private_channel"],
    "all": None,
}


SLACK_SEARCH_DESCRIPTION = (
    "Search Slack messages across the workspace using the Real-time Search API. "
    "Supports natural-language queries (semantic search) or keywords. "
    "Use scope='dms' to search only direct messages. "
    "Requires a Slack user token with granular search:read.* scopes."
)


async def slack_search(execution: ToolExecution, args: SlackSearchInput) -> ToolResult:
    source = execution.ctx.get_client("slack", SlackClient)
    channel_types = _SCOPE_MAP.get(args.scope.lower()) if args.scope else None
    results = await source.search_messages(args.query, limit=args.limit, channel_types=channel_types)
    if not results:
        return ToolResult(content=f"No Slack messages found for {args.query!r}", preview="0 results")
    content, may_have_more = _bounded_content(
        _format_messages(results), count=len(results), limit=args.limit, noun="messages"
    )
    return ToolResult(
        content=content,
        preview=f"{len(results)} messages" + (" (possibly capped)" if may_have_more else ""),
        data={"count": len(results), "may_have_more": may_have_more},
        source_refs=_message_source_refs(results),
    )


class SlackChannelInput(BaseModel):
    channel: str = Field(description="Channel name (e.g. 'general' or '#general') or channel ID (e.g. 'C0123456789')")
    limit: int = Field(default=50, ge=1, le=100, description="Max messages to fetch")


async def slack_channel(execution: ToolExecution, args: SlackChannelInput) -> ToolResult:
    source = execution.ctx.get_client("slack", SlackClient)
    results = await source.read_channel(args.channel, limit=args.limit)
    if not results:
        return ToolResult(content=f"No messages in #{args.channel}", preview="0 messages")
    content, may_have_more = _bounded_content(
        _format_messages(results), count=len(results), limit=args.limit, noun="messages"
    )
    return ToolResult(
        content=content,
        preview=f"{len(results)} messages" + (" (possibly capped)" if may_have_more else ""),
        data={"count": len(results), "may_have_more": may_have_more},
        source_refs=_message_source_refs(results),
    )


class SlackThreadInput(BaseModel):
    message_id: str = Field(description="Message id (channel_id:ts) from a previous search/channel result")


SLACK_THREAD_DESCRIPTION = (
    "Read a Slack message and all its thread replies. Pass the message id from slack_search or slack_channel."
)


async def slack_thread(execution: ToolExecution, args: SlackThreadInput) -> ToolResult:
    source = execution.ctx.get_client("slack", SlackClient)
    result = await source.read_thread(args.message_id)
    if not result:
        return not_found_result("message", args.message_id, call_first="slack_search")
    lines = result.text.count("\n") + 1
    return ToolResult(
        content=result.text,
        preview=f"Read {lines} lines",
        model_content=result.model_content,
        source_refs=_message_source_ref(args.message_id, title=f"Slack message {args.message_id}"),
    )


class SlackChannelsInput(BaseModel):
    query: str | None = Field(default=None, description="Optional substring to filter channel names")
    limit: int = Field(default=50, ge=1, le=200, description="Max channels to return")


async def slack_channels(execution: ToolExecution, args: SlackChannelsInput) -> ToolResult:
    source = execution.ctx.get_client("slack", SlackClient)
    results = await source.search_channels(args.query, limit=args.limit)
    if not results:
        return ToolResult(content="No matching channels", preview="0 channels")
    results.sort(key=lambda channel: (channel["name"].casefold(), channel["id"]))
    content, may_have_more = _bounded_content(
        "\n".join(f"• #{c['name']}  ({c['id']})" for c in results),
        count=len(results),
        limit=args.limit,
        noun="channels",
    )
    return ToolResult(
        content=content,
        preview=f"{len(results)} channels" + (" (possibly capped)" if may_have_more else ""),
        data={"items": results, "count": len(results), "may_have_more": may_have_more},
        source_refs=_entity_source_refs(results, kind="channel", id_key="id", title_key="name"),
    )


class SlackUsersInput(BaseModel):
    query: str | None = Field(default=None, description="Optional substring to filter by name, username, or email")
    limit: int = Field(default=50, ge=1, le=200, description="Max users to return")


async def slack_users(execution: ToolExecution, args: SlackUsersInput) -> ToolResult:
    source = execution.ctx.get_client("slack", SlackClient)
    results = await source.search_users(args.query, limit=args.limit)
    if not results:
        return ToolResult(content="No matching users", preview="0 users")
    results.sort(key=lambda user: (user["name"].casefold(), user["id"]))
    lines = []
    for user in results:
        line = f"• {user['name']}"
        if user.get("username"):
            line += f" (@{user['username']})"
        if user.get("title"):
            line += f" — {user['title']}"
        line += f"  id: {user['id']}"
        if user.get("email"):
            line += f"  {user['email']}"
        lines.append(line)
    content, may_have_more = _bounded_content("\n".join(lines), count=len(results), limit=args.limit, noun="users")
    return ToolResult(
        content=content,
        preview=f"{len(results)} users" + (" (possibly capped)" if may_have_more else ""),
        data={"items": results, "count": len(results), "may_have_more": may_have_more},
        source_refs=_entity_source_refs(results, kind="user", id_key="id", title_key="name"),
    )


class SlackUserInput(BaseModel):
    user_id: str = Field(description="Slack user ID (e.g. U0123456789)")


async def slack_user(execution: ToolExecution, args: SlackUserInput) -> ToolResult:
    source = execution.ctx.get_client("slack", SlackClient)
    profile = await source.read_user(args.user_id)
    if not profile:
        return not_found_result("user", args.user_id, call_first="slack_users")
    lines = [f"{key}: {value}" for key, value in profile.items() if value]
    return ToolResult(
        content="\n".join(lines),
        preview=profile.get("name", args.user_id),
        source_refs=(
            ToolSourceRef(
                provider="slack",
                kind="user",
                ref=args.user_id,
                title=profile.get("name") or args.user_id,
            ),
        ),
    )


class SlackFileInput(BaseModel):
    file_id: str = Field(description="Slack file ID (e.g. F0123456789) from a message attachment/file result")


SLACK_FILE_DESCRIPTION = (
    "Fetch and inspect a Slack image file by file ID. Use when Slack results expose an attached file ID (F*) "
    "and the user asks about screenshot/image contents."
)


async def slack_file(execution: ToolExecution, args: SlackFileInput) -> ToolResult:
    source = execution.ctx.get_client("slack", SlackClient)
    result = await source.read_file_image(args.file_id)
    if not result:
        return not_found_result("Slack image file", args.file_id, call_first="slack_search")
    return ToolResult(content=result.text, preview=f"Read image {args.file_id}", model_content=result.model_content)


class SlackPostMessageInput(BaseModel):
    channel: str = Field(description="Channel name (e.g. 'general' or '#general') or channel ID (e.g. 'C0123456789')")
    text: str = Field(
        description=(
            "Plain Slack message text. Use this for simple unstructured messages only. "
            "For rich layouts, tables, status cards, diagnosis reports, or structured summaries, use slack_post_blocks instead. "
            "Supports basic Slack mrkdwn: *bold*, _italic_, ~strike~, `code`, ```code block```, <url|label>, mentions, and bullet lines."
        )
    )
    thread_ts: str | None = Field(
        default=None, description="Optional parent message timestamp to post as a thread reply"
    )
    idempotency_key: str = Field(min_length=8, max_length=200)


class SlackPostBlocksInput(BaseModel):
    channel: str = Field(description="Channel name (e.g. 'general' or '#general') or channel ID (e.g. 'C0123456789')")
    text: str = Field(
        description=(
            "Required fallback text for Slack notifications and accessibility. Keep concise; Slack may show this in previews/search."
        )
    )
    blocks: list[dict[str, Any]] = Field(
        min_length=1,
        max_length=50,
        description=(
            "Required Slack Block Kit blocks array for rich message layout. Use native JSON objects, not a JSON string. "
            "Common blocks: header, section, context, divider, actions. "
            "Text objects are {'type':'mrkdwn','text':'...'} or {'type':'plain_text','text':'...'}. "
            "Use section.fields for compact key/value cards, max 10 fields. "
            "Use real newline characters in mrkdwn text, not literal \\n. "
            "Limits: max 50 blocks, section text 3000 chars, field text 2000 chars."
        ),
    )
    thread_ts: str | None = Field(
        default=None, description="Optional parent message timestamp to post as a thread reply"
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
    "Post a simple plain-text Slack message using the configured Slack user token (SLACK_USER_TOKEN / xoxp-). "
    "Use thread_ts to reply in a thread. "
    "Do not use this for rich layouts, diagnosis cards, tables, or structured reports; use slack_post_blocks instead. "
    + SLACK_FORMATTING_GUIDE
)


SLACK_POST_BLOCKS_DESCRIPTION = (
    "Post a rich Slack Block Kit message using the configured Slack user token (SLACK_USER_TOKEN / xoxp-). "
    "Use this for formatted reports, alert diagnoses, status cards, field grids, summaries, or anything needing layout. "
    "Requires both fallback text and a native blocks array. Returns the posted message timestamp. "
    + SLACK_FORMATTING_GUIDE
)


async def approve_slack_post_message(execution: ToolExecution, args: SlackPostMessageInput) -> ApprovalInfo | None:
    location = f"{args.channel} thread {args.thread_ts}" if args.thread_ts else args.channel
    preview = truncate(args.text, 1000)
    return ApprovalInfo(description=f"Post Slack message to {location}", preview=preview, diff=None)


async def approve_slack_post_blocks(execution: ToolExecution, args: SlackPostBlocksInput) -> ApprovalInfo | None:
    location = f"{args.channel} thread {args.thread_ts}" if args.thread_ts else args.channel
    blocks = json.dumps(args.blocks, ensure_ascii=False, separators=(",", ":"))
    preview = truncate(f"Fallback text:\n{args.text}\n\nBlocks:\n{blocks}", 1_500)
    return ApprovalInfo(description=f"Post Slack Block Kit message to {location}", preview=preview, diff=None)


def _posted_message_result(args: SlackPostMessageInput | SlackPostBlocksInput, result: dict[str, str]) -> ToolResult:
    channel_label = result.get("channel_name") or result.get("channel") or args.channel
    ts = result.get("ts", "")
    thread_ts = result.get("thread_ts", ts)
    content = (
        f"Posted to #{channel_label} at {ts}\nchannel: {result.get('channel', args.channel)}\nthread_ts: {thread_ts}"
    )
    message_ref = f"{result.get('channel', args.channel)}:{ts}" if ts else None
    return mutation_result(
        content=content,
        preview=f"Posted to #{channel_label}" if message_ref else "Post unverified",
        operation="post",
        target=args.channel,
        receipt=ts or args.idempotency_key,
        after_ref=message_ref,
        observed=(f"Slack returned message {message_ref}" if message_ref else None),
        data={"message_ref": message_ref, "thread_ts": thread_ts} if message_ref else None,
    )


async def slack_post_message(execution: ToolExecution, args: SlackPostMessageInput) -> ToolResult:
    async def invoke() -> ToolResult:
        source = execution.ctx.get_client("slack", SlackClient)
        result = await source.post_message(args.channel, args.text, thread_ts=args.thread_ts)
        return _posted_message_result(args, result)

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
        result = await source.post_message(args.channel, args.text, thread_ts=args.thread_ts, blocks=args.blocks)
        return _posted_message_result(args, result)

    return await execute_idempotent(
        execution,
        namespace="slack:post_blocks",
        idempotency_key=args.idempotency_key,
        payload=args.model_dump(exclude={"idempotency_key"}),
        invoke=invoke,
    )


class SlackDmsInput(BaseModel):
    query: str | None = Field(default=None, description="Optional substring to filter by peer name or user id")
    limit: int = Field(default=50, ge=1, le=200, description="Max DMs to return")


async def slack_dms(execution: ToolExecution, args: SlackDmsInput) -> ToolResult:
    source = execution.ctx.get_client("slack", SlackClient)
    dms = await source.list_dms(args.query, limit=args.limit)
    if not dms:
        return ToolResult(content="No open DMs", preview="0 DMs")
    dms.sort(key=lambda dm: (dm["peer"].casefold(), dm["channel_id"]))
    content, may_have_more = _bounded_content(
        "\n".join(f"• {dm['peer']}  (dm: {dm['channel_id']}, user: {dm['user_id']})" for dm in dms),
        count=len(dms),
        limit=args.limit,
        noun="DMs",
    )
    return ToolResult(
        content=content,
        preview=f"{len(dms)} DMs" + (" (possibly capped)" if may_have_more else ""),
        data={"items": dms, "count": len(dms), "may_have_more": may_have_more},
        source_refs=_entity_source_refs(dms, kind="channel", id_key="channel_id", title_key="peer"),
    )


class SlackDmInput(BaseModel):
    target: str = Field(description="DM channel id (D*), user id (U*/W*), or a name/handle to resolve to a DM.")
    limit: int = Field(default=50, ge=1, le=100, description="Max messages to fetch")


SLACK_DM_DESCRIPTION = (
    "Read recent messages from a direct message conversation. "
    "Target can be a DM channel id, user id, or a name (fuzzy match via users.list)."
)


async def slack_dm(execution: ToolExecution, args: SlackDmInput) -> ToolResult:
    source = execution.ctx.get_client("slack", SlackClient)
    try:
        channel_id = await source.resolve_dm_target(args.target)
    except RuntimeError:
        return invalid_ref_result("Slack DM", args.target, call_first="slack_dms or slack_users")
    results = await source.read_channel(channel_id, limit=args.limit)
    if not results:
        return ToolResult(content=f"No messages in DM with {args.target!r}", preview="0 messages")
    content, may_have_more = _bounded_content(
        _format_messages(results), count=len(results), limit=args.limit, noun="messages"
    )
    return ToolResult(
        content=content,
        preview=f"{len(results)} messages" + (" (possibly capped)" if may_have_more else ""),
        data={"count": len(results), "may_have_more": may_have_more},
        source_refs=_message_source_refs(results),
    )


slack_search_tool = tool(
    display_name="SlackSearch",
    display_description="Search connected Slack messages.",
    description=SLACK_SEARCH_DESCRIPTION,
    input_model=SlackSearchInput,
    policy=ToolPolicy(
        action=ToolAction.READ, scope=ToolScope.EXTERNAL, permissions=frozenset({"slack"}), deferred=True
    ),
    execute=slack_search,
)

slack_channel_tool = tool(
    display_name="SlackChannel",
    description="Read recent message history from a Slack channel.",
    input_model=SlackChannelInput,
    policy=ToolPolicy(
        action=ToolAction.READ, scope=ToolScope.EXTERNAL, permissions=frozenset({"slack"}), deferred=True
    ),
    execute=slack_channel,
)

slack_thread_tool = tool(
    display_name="SlackThread",
    display_description="Read a Slack message thread.",
    description=SLACK_THREAD_DESCRIPTION,
    input_model=SlackThreadInput,
    policy=ToolPolicy(
        action=ToolAction.READ, scope=ToolScope.EXTERNAL, permissions=frozenset({"slack"}), deferred=True
    ),
    execute=slack_thread,
)

slack_channels_tool = tool(
    display_name="SlackChannels",
    display_description="List accessible Slack channels.",
    description="List Slack channels you can access. Optional query filters by name substring.",
    input_model=SlackChannelsInput,
    policy=ToolPolicy(
        action=ToolAction.READ, scope=ToolScope.EXTERNAL, permissions=frozenset({"slack"}), deferred=True
    ),
    execute=slack_channels,
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

slack_dms_tool = tool(
    display_name="SlackDMs",
    display_description="List Slack direct-message conversations.",
    description="List open Slack direct messages (1-on-1). Shows peer name and DM channel id.",
    input_model=SlackDmsInput,
    policy=ToolPolicy(
        action=ToolAction.READ, scope=ToolScope.EXTERNAL, permissions=frozenset({"slack"}), deferred=True
    ),
    execute=slack_dms,
)

slack_dm_tool = tool(
    display_name="SlackDM",
    display_description="Read a Slack direct-message conversation.",
    description=SLACK_DM_DESCRIPTION,
    input_model=SlackDmInput,
    policy=ToolPolicy(
        action=ToolAction.READ, scope=ToolScope.EXTERNAL, permissions=frozenset({"slack"}), deferred=True
    ),
    execute=slack_dm,
)

slack_users_tool = tool(
    display_name="SlackUsers",
    description="Search Slack workspace members by name, username, or email.",
    input_model=SlackUsersInput,
    policy=ToolPolicy(
        action=ToolAction.READ, scope=ToolScope.EXTERNAL, permissions=frozenset({"slack"}), deferred=True
    ),
    execute=slack_users,
)

slack_user_tool = tool(
    display_name="SlackUser",
    description="Read a Slack user's profile (name, email, title, status, timezone).",
    input_model=SlackUserInput,
    policy=ToolPolicy(
        action=ToolAction.READ, scope=ToolScope.EXTERNAL, permissions=frozenset({"slack"}), deferred=True
    ),
    execute=slack_user,
)

slack_file_tool = tool(
    display_name="SlackFile",
    display_description="Inspect a Slack image attachment.",
    description=SLACK_FILE_DESCRIPTION,
    input_model=SlackFileInput,
    policy=ToolPolicy(
        action=ToolAction.READ, scope=ToolScope.EXTERNAL, permissions=frozenset({"slack"}), deferred=True
    ),
    execute=slack_file,
)
