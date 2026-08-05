from collections.abc import Sequence

from arden.agent.types.tools import ToolSourceRef, canonical_source_title, normalize_source_refs
from arden.integrations.slack.models import (
    SlackChannel,
    SlackDirectMessage,
    SlackMessage,
    SlackUser,
)
from arden.tools.core.collections import format_timestamp
from arden.utils import truncate

TEXT_TRUNCATE = 280


def bounded_content(content: str, *, count: int, limit: int, noun: str) -> tuple[str, bool]:
    may_have_more = count == limit
    if may_have_more:
        content += f"\nShowing {count} {noun}; more may exist. Narrow the query or target to continue."
    return content, may_have_more


def paged_content(content: str, *, count: int, next_page_ref: str | None, noun: str) -> str:
    if next_page_ref is not None:
        return f"{content}\nShowing {count} {noun}; continue with page_ref: {next_page_ref}"
    return content


def message_source_refs(items: Sequence[SlackMessage]) -> tuple[ToolSourceRef, ...]:
    return normalize_source_refs(
        ToolSourceRef(
            provider="slack",
            kind="message",
            ref=item.ref,
            title=canonical_source_title(
                f"#{item.channel_name} — {item.author_name}" if item.channel_name else item.author_name,
                fallback=item.ref,
            ),
            url=item.permalink,
        )
        for item in items
    )


def message_source_ref(message_ref: str, *, title: str) -> tuple[ToolSourceRef, ...]:
    return normalize_source_refs(
        (
            ToolSourceRef(
                provider="slack",
                kind="message",
                ref=message_ref,
                title=canonical_source_title(title, fallback=message_ref),
            ),
        )
    )


def entity_source_refs(
    items: list[SlackChannel | SlackUser | SlackDirectMessage], *, kind: str
) -> tuple[ToolSourceRef, ...]:
    return normalize_source_refs(
        ToolSourceRef(
            provider="slack",
            kind=kind,
            ref=entity_ref(item),
            title=canonical_source_title(entity_title(item), fallback=entity_ref(item)),
        )
        for item in items
    )


def entity_ref(item: SlackChannel | SlackUser | SlackDirectMessage) -> str:
    if isinstance(item, SlackDirectMessage):
        return item.channel_ref
    return item.ref


def entity_title(item: SlackChannel | SlackUser | SlackDirectMessage) -> str:
    if isinstance(item, SlackChannel):
        return item.name
    if isinstance(item, SlackDirectMessage):
        return item.peer_name
    return item.name


def format_messages(items: Sequence[SlackMessage], *, show_thread_hint: bool = True) -> str:
    lines = []
    for item in items:
        when = format_timestamp(item.created_at)
        channel = f"#{item.channel_name}" if item.channel_name else "direct message"
        text = truncate(item.text or "(empty)", TEXT_TRUNCATE)
        header = f"• [{when}] {channel} — {item.author_name}"
        lines.append(header)
        lines.append(f"    {text}")
        suffix = []
        if show_thread_hint and item.reply_count:
            suffix.append(f"thread: {item.reply_count} replies")
        suffix.append(f"message_ref: {item.ref}")
        suffix.append(f"thread_ref: {item.thread_ref or item.ref}")
        lines.append(f"    ({', '.join(suffix)})")
    return "\n".join(lines)
