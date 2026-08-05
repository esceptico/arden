from typing import Literal

from pydantic import Field

from arden.agent.types.tools import ToolSourceRef, canonical_source_title
from arden.integrations.base import IntegrationOperationError
from arden.integrations.slack.client import SlackClient
from arden.integrations.slack.tool_formatting import (
    bounded_content,
    entity_source_refs,
    format_messages,
    message_source_ref,
    message_source_refs,
    paged_content,
)
from arden.integrations.slack.tool_input import SlackToolInput
from arden.integrations.tool_errors import operation_error_result
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.error_results import invalid_ref_result, not_found_result
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope

_DEFAULT_LIMIT = 20


class SlackSearchInput(SlackToolInput):
    query: str = Field(description="Slack search query. Supports operators: from:@user in:#channel before:2024-01-01")
    limit: int = Field(default=_DEFAULT_LIMIT, ge=1, le=20, description="Max results (Slack API maximum: 20)")
    scope: Literal["dms", "channels"] | None = Field(
        default=None,
        description="Optional scope: 'dms' (DMs + group DMs only), 'channels' (public/private only), or None for all.",
    )
    page_ref: str | None = Field(
        default=None,
        max_length=4096,
        description="Exact page_ref from the previous slack_search result",
    )


_SCOPE_MAP = {
    "dms": ["im", "mpim"],
    "channels": ["public_channel", "private_channel"],
}


SLACK_SEARCH_DESCRIPTION = (
    "Search Slack messages across the workspace using the Real-time Search API. "
    "Supports natural-language queries (semantic search) or keywords. "
    "Use scope='dms' to search only direct messages. "
    "Requires a Slack user token with granular search:read.* scopes."
)


async def slack_search(execution: ToolExecution, args: SlackSearchInput) -> ToolResult:
    source = execution.ctx.get_client("slack", SlackClient)
    channel_types = _SCOPE_MAP[args.scope] if args.scope is not None else None
    try:
        page = await source.messages.search_messages(
            args.query,
            limit=args.limit,
            channel_types=channel_types,
            page_ref=args.page_ref,
        )
    except IntegrationOperationError as error:
        return operation_error_result(error, preview="Slack search failed")
    results = page.messages
    if not results:
        return ToolResult(
            content=paged_content(
                f"No Slack messages found for {args.query!r}",
                count=0,
                next_page_ref=page.next_page_ref,
                noun="messages",
            ),
            preview="0 results" + (" (more available)" if page.next_page_ref else ""),
            data={"count": 0, "next_page_ref": page.next_page_ref},
        )
    content = paged_content(
        format_messages(results),
        count=len(results),
        next_page_ref=page.next_page_ref,
        noun="messages",
    )
    return ToolResult(
        content=content,
        preview=f"{len(results)} messages" + (" (more available)" if page.next_page_ref else ""),
        data={"count": len(results), "next_page_ref": page.next_page_ref},
        source_refs=message_source_refs(results),
    )


class SlackChannelInput(SlackToolInput):
    channel: str = Field(
        min_length=1,
        description="Channel name (for example 'general') or exact Slack channel ref (for example 'C0123456789')",
    )
    limit: int = Field(default=50, ge=1, le=100, description="Max messages to fetch")
    page_ref: str | None = Field(
        default=None,
        max_length=4096,
        description="Exact page_ref from the previous slack_channel result",
    )


async def slack_channel(execution: ToolExecution, args: SlackChannelInput) -> ToolResult:
    source = execution.ctx.get_client("slack", SlackClient)
    try:
        page = await source.messages.read_channel(args.channel, limit=args.limit, page_ref=args.page_ref)
    except IntegrationOperationError as error:
        return operation_error_result(error, preview="Slack channel read failed")
    results = page.messages
    if not results:
        return ToolResult(
            content=paged_content(
                f"No messages in #{args.channel}",
                count=0,
                next_page_ref=page.next_page_ref,
                noun="messages",
            ),
            preview="0 messages" + (" (more available)" if page.next_page_ref else ""),
            data={"count": 0, "next_page_ref": page.next_page_ref},
        )
    content = paged_content(
        format_messages(results),
        count=len(results),
        next_page_ref=page.next_page_ref,
        noun="messages",
    )
    return ToolResult(
        content=content,
        preview=f"{len(results)} messages" + (" (more available)" if page.next_page_ref else ""),
        data={"count": len(results), "next_page_ref": page.next_page_ref},
        source_refs=message_source_refs(results),
    )


class SlackThreadInput(SlackToolInput):
    thread_ref: str = Field(
        pattern=r"^[CGD][A-Z0-9]+:[0-9]+(?:\.[0-9]+)?$",
        description="Exact thread ref (channel_ref:parent_timestamp) from a previous Slack result",
    )
    after_ref: str | None = Field(
        default=None,
        pattern=r"^[CGD][A-Z0-9]+:[0-9]+(?:\.[0-9]+)?$",
        description="Continue after this exact message ref from a prior capped thread result",
    )


SLACK_THREAD_DESCRIPTION = (
    "Read a Slack message and its thread replies. Pass the thread_ref from slack_search or slack_channel."
)


async def slack_thread(execution: ToolExecution, args: SlackThreadInput) -> ToolResult:
    source = execution.ctx.get_client("slack", SlackClient)
    try:
        result = await source.media.read_thread(args.thread_ref, after_ref=args.after_ref)
    except IntegrationOperationError as error:
        return operation_error_result(error, preview="Slack thread read failed")
    if not result:
        return not_found_result("thread", args.thread_ref, call_first="slack_search")
    lines = result.text.count("\n") + 1
    return ToolResult(
        content=result.text,
        preview=f"Read {lines} lines",
        model_content=result.model_content,
        data={"has_more": result.has_more, "next_after_ref": result.next_after_ref},
        source_refs=message_source_ref(args.thread_ref, title=f"Slack thread {args.thread_ref}"),
    )


class SlackChannelsInput(SlackToolInput):
    query: str | None = Field(default=None, description="Optional substring to filter channel names")
    limit: int = Field(default=50, ge=1, le=200, description="Max channels to return")


async def slack_channels(execution: ToolExecution, args: SlackChannelsInput) -> ToolResult:
    source = execution.ctx.get_client("slack", SlackClient)
    try:
        results = await source.directory.search_channels(args.query, limit=args.limit)
    except IntegrationOperationError as error:
        return operation_error_result(error, preview="Slack channel list failed")
    if not results:
        return ToolResult(content="No matching channels", preview="0 channels")
    results.sort(key=lambda channel: (channel.name.casefold(), channel.ref))
    content, may_have_more = bounded_content(
        "\n".join(f"• #{channel.name}  (ref: {channel.ref})" for channel in results),
        count=len(results),
        limit=args.limit,
        noun="channels",
    )
    return ToolResult(
        content=content,
        preview=f"{len(results)} channels" + (" (possibly capped)" if may_have_more else ""),
        data={
            "items": [{"ref": channel.ref, "name": channel.name} for channel in results],
            "count": len(results),
            "may_have_more": may_have_more,
        },
        source_refs=entity_source_refs(results, kind="channel"),
    )


class SlackUsersInput(SlackToolInput):
    query: str | None = Field(default=None, description="Optional substring to filter by name, username, or email")
    limit: int = Field(default=50, ge=1, le=200, description="Max users to return")


async def slack_users(execution: ToolExecution, args: SlackUsersInput) -> ToolResult:
    source = execution.ctx.get_client("slack", SlackClient)
    try:
        results = await source.directory.search_users(args.query, limit=args.limit)
    except IntegrationOperationError as error:
        return operation_error_result(error, preview="Slack user list failed")
    if not results:
        return ToolResult(content="No matching users", preview="0 users")
    results.sort(key=lambda user: (user.name.casefold(), user.ref))
    lines = []
    for user in results:
        line = f"• {user.name}"
        if user.username:
            line += f" (@{user.username})"
        if user.title:
            line += f" — {user.title}"
        line += f"  ref: {user.ref}"
        if user.email:
            line += f"  {user.email}"
        lines.append(line)
    content, may_have_more = bounded_content("\n".join(lines), count=len(results), limit=args.limit, noun="users")
    return ToolResult(
        content=content,
        preview=f"{len(results)} users" + (" (possibly capped)" if may_have_more else ""),
        data={
            "items": [
                {
                    "ref": user.ref,
                    "name": user.name,
                    "username": user.username,
                    "email": user.email,
                    "title": user.title,
                }
                for user in results
            ],
            "count": len(results),
            "may_have_more": may_have_more,
        },
        source_refs=entity_source_refs(results, kind="user"),
    )


class SlackUserInput(SlackToolInput):
    user_ref: str = Field(
        pattern=r"^[UW][A-Z0-9]+$",
        description="Exact Slack user ref (for example U0123456789)",
    )


async def slack_user(execution: ToolExecution, args: SlackUserInput) -> ToolResult:
    source = execution.ctx.get_client("slack", SlackClient)
    try:
        profile = await source.directory.read_user(args.user_ref)
    except IntegrationOperationError as error:
        return operation_error_result(error, preview="Slack user read failed")
    lines = [
        f"name: {profile.name}",
        *([f"username: {profile.username}"] if profile.username else []),
        *([f"email: {profile.email}"] if profile.email else []),
        *([f"title: {profile.title}"] if profile.title else []),
        *([f"status_text: {profile.status_text}"] if profile.status_text else []),
        *([f"status_emoji: {profile.status_emoji}"] if profile.status_emoji else []),
        *([f"timezone: {profile.timezone}"] if profile.timezone else []),
    ]
    return ToolResult(
        content="\n".join(lines),
        preview=profile.name,
        source_refs=(
            ToolSourceRef(
                provider="slack",
                kind="user",
                ref=profile.ref,
                title=canonical_source_title(profile.name, fallback=profile.ref),
            ),
        ),
    )


class SlackFileInput(SlackToolInput):
    file_ref: str = Field(
        pattern=r"^F[A-Z0-9]+$",
        description="Exact Slack file ref (for example F0123456789) from a message attachment",
    )


SLACK_FILE_DESCRIPTION = (
    "Fetch and inspect a Slack image file by ref. Use when Slack results expose an attached file ref (F*) "
    "and the user asks about screenshot/image contents."
)


async def slack_file(execution: ToolExecution, args: SlackFileInput) -> ToolResult:
    source = execution.ctx.get_client("slack", SlackClient)
    try:
        result = await source.media.read_file_image(args.file_ref)
    except IntegrationOperationError as error:
        return operation_error_result(error, preview="Slack file read failed")
    return ToolResult(content=result.text, preview=f"Read image {args.file_ref}", model_content=result.model_content)


class SlackDmsInput(SlackToolInput):
    query: str | None = Field(default=None, description="Optional substring to filter by peer name or user ref")
    limit: int = Field(default=50, ge=1, le=200, description="Max DMs to return")


async def slack_dms(execution: ToolExecution, args: SlackDmsInput) -> ToolResult:
    source = execution.ctx.get_client("slack", SlackClient)
    try:
        dms = await source.directory.list_dms(args.query, limit=args.limit)
    except IntegrationOperationError as error:
        return operation_error_result(error, preview="Slack DM list failed")
    if not dms:
        return ToolResult(content="No open DMs", preview="0 DMs")
    dms.sort(key=lambda dm: (dm.peer_name.casefold(), dm.channel_ref))
    content, may_have_more = bounded_content(
        "\n".join(f"• {dm.peer_name}  (dm_ref: {dm.channel_ref}, user_ref: {dm.user_ref})" for dm in dms),
        count=len(dms),
        limit=args.limit,
        noun="DMs",
    )
    return ToolResult(
        content=content,
        preview=f"{len(dms)} DMs" + (" (possibly capped)" if may_have_more else ""),
        data={
            "items": [
                {"channel_ref": dm.channel_ref, "user_ref": dm.user_ref, "peer_name": dm.peer_name} for dm in dms
            ],
            "count": len(dms),
            "may_have_more": may_have_more,
        },
        source_refs=entity_source_refs(dms, kind="channel"),
    )


class SlackDmInput(SlackToolInput):
    target: str = Field(
        min_length=1,
        description="Exact DM channel ref (D*), exact user ref (U*/W*), or exact name/handle",
    )
    limit: int = Field(default=50, ge=1, le=100, description="Max messages to fetch")
    page_ref: str | None = Field(
        default=None,
        max_length=4096,
        description="Exact page_ref from the previous slack_dm result",
    )


SLACK_DM_DESCRIPTION = (
    "Read recent messages from a direct message conversation. "
    "Target can be a DM channel ref, user ref, or an exact name/handle."
)


async def slack_dm(execution: ToolExecution, args: SlackDmInput) -> ToolResult:
    source = execution.ctx.get_client("slack", SlackClient)
    try:
        channel_ref = await source.directory.resolve_dm_target(args.target)
    except IntegrationOperationError as exc:
        if exc.code in {"not_found", "ambiguous_ref"}:
            return invalid_ref_result("Slack DM", args.target, call_first="slack_dms or slack_users")
        return operation_error_result(exc, preview="Slack DM resolution failed")
    try:
        page = await source.messages.read_channel(channel_ref, limit=args.limit, page_ref=args.page_ref)
    except IntegrationOperationError as error:
        return operation_error_result(error, preview="Slack DM read failed")
    results = page.messages
    if not results:
        return ToolResult(
            content=paged_content(
                f"No messages in DM with {args.target!r}",
                count=0,
                next_page_ref=page.next_page_ref,
                noun="messages",
            ),
            preview="0 messages" + (" (more available)" if page.next_page_ref else ""),
            data={"count": 0, "next_page_ref": page.next_page_ref},
        )
    content = paged_content(
        format_messages(results),
        count=len(results),
        next_page_ref=page.next_page_ref,
        noun="messages",
    )
    return ToolResult(
        content=content,
        preview=f"{len(results)} messages" + (" (more available)" if page.next_page_ref else ""),
        data={"count": len(results), "next_page_ref": page.next_page_ref},
        source_refs=message_source_refs(results),
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

slack_dms_tool = tool(
    display_name="SlackDMs",
    display_description="List Slack direct-message conversations.",
    description="List open Slack direct messages (1-on-1). Shows peer name and exact DM channel ref.",
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
