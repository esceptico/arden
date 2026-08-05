from datetime import UTC, datetime
from itertools import pairwise
from typing import Any
from urllib.parse import parse_qs, urlparse

import aiohttp

from arden.integrations.base import IntegrationOperationError
from arden.integrations.slack.block_kit import SlackBlockEnvelope
from arden.integrations.slack.directory import SlackDirectory
from arden.integrations.slack.models import (
    SlackHistoryMessage,
    SlackHistoryPage,
    SlackMessage,
    SlackMessagePage,
    SlackPostReceipt,
)
from arden.integrations.slack.transport import (
    SlackTransport,
    mapping,
    next_cursor,
    optional_int,
    optional_string,
    payload_error,
    required_bool,
    required_list,
    required_string,
    required_string_allow_empty,
)

_ALL_SEARCH_CHANNEL_TYPES = ("public_channel", "private_channel", "mpim", "im")
_CONTENT_MESSAGE_SUBTYPES = frozenset({None, "bot_message", "file_share", "me_message", "thread_broadcast"})


def timestamp_to_datetime(timestamp: str) -> datetime:
    try:
        return datetime.fromtimestamp(float(timestamp), tz=UTC)
    except (ValueError, OverflowError, OSError) as exc:
        raise payload_error("message timestamp", f"invalid timestamp {timestamp!r}") from exc


def split_message_ref(message_ref: str) -> tuple[str, str]:
    channel_ref, separator, timestamp = message_ref.partition(":")
    if not separator or not channel_ref or not timestamp:
        raise IntegrationOperationError(
            code="invalid_ref",
            safe_message="Slack message refs must be channel_ref:timestamp.",
            retryable=False,
        )
    timestamp_to_datetime(timestamp)
    return channel_ref, timestamp


def _thread_ref_from_permalink(*, permalink: str, channel_ref: str, message_ts: str) -> str:
    query = parse_qs(urlparse(permalink).query)
    thread_values = query.get("thread_ts")
    if thread_values is None:
        return f"{channel_ref}:{message_ts}"
    if len(thread_values) != 1:
        raise payload_error("assistant.search.context message", "permalink has multiple thread timestamps")
    thread_ts = thread_values[0]
    timestamp_to_datetime(thread_ts)
    channel_values = query.get("cid")
    if channel_values is not None and channel_values != [channel_ref]:
        raise payload_error("assistant.search.context message", "permalink channel does not match channel_id")
    return f"{channel_ref}:{thread_ts}"


class SlackMessages:
    def __init__(self, transport: SlackTransport, directory: SlackDirectory):
        self._transport = transport
        self._directory = directory

    def _decode_search_message(
        self,
        payload: dict[str, Any],
    ) -> SlackMessage:
        context = "assistant.search.context message"
        channel_ref = required_string(payload, "channel_id", context=context)
        channel_name = required_string(payload, "channel_name", context=context)
        timestamp = required_string(payload, "message_ts", context=context)
        author_ref = required_string(payload, "author_user_id", context=context)
        author_name = required_string(payload, "author_name", context=context)
        permalink = required_string(payload, "permalink", context=context)
        return SlackMessage(
            ref=f"{channel_ref}:{timestamp}",
            channel_ref=channel_ref,
            channel_name=channel_name,
            author_ref=author_ref,
            author_name=author_name,
            text=required_string_allow_empty(payload, "content", context=context),
            created_at=timestamp_to_datetime(timestamp),
            thread_ref=_thread_ref_from_permalink(
                permalink=permalink,
                channel_ref=channel_ref,
                message_ts=timestamp,
            ),
            permalink=permalink,
        )

    async def _decode_history_message(
        self,
        session: aiohttp.ClientSession,
        payload: dict[str, Any],
        channel_ref: str,
        channel_name: str,
    ) -> SlackMessage | None:
        context = "conversations.history message"
        if required_string(payload, "type", context=context) != "message":
            return None
        subtype = optional_string(payload, "subtype", context=context)
        if subtype not in _CONTENT_MESSAGE_SUBTYPES:
            return None
        timestamp = required_string(payload, "ts", context=context)
        author_ref = optional_string(payload, "user", context=context)
        if author_ref is not None:
            author_name = await self._directory.user_name(session, author_ref)
        else:
            bot_ref = required_string(payload, "bot_id", context=context)
            bot_profile = mapping(payload.get("bot_profile"), context=f"{context}.bot_profile")
            if required_string(bot_profile, "id", context=f"{context}.bot_profile") != bot_ref:
                raise payload_error(context, "bot_profile.id does not match bot_id")
            author_ref = bot_ref
            author_name = required_string(bot_profile, "name", context=f"{context}.bot_profile")
        thread_timestamp = optional_string(payload, "thread_ts", context=context)
        return SlackMessage(
            ref=f"{channel_ref}:{timestamp}",
            channel_ref=channel_ref,
            channel_name=channel_name,
            author_ref=author_ref,
            author_name=author_name,
            text=required_string_allow_empty(payload, "text", context=context),
            created_at=timestamp_to_datetime(timestamp),
            thread_ref=f"{channel_ref}:{thread_timestamp or timestamp}",
            reply_count=optional_int(payload, "reply_count", context=context),
        )

    @staticmethod
    def decode_monitor_message(payload: dict[str, Any]) -> SlackHistoryMessage:
        context = "conversations.history message"
        record_type = required_string(payload, "type", context=context)
        if record_type != "message":
            return SlackHistoryMessage(
                timestamp=required_string(payload, "ts", context=context),
                text="",
                user_ref=None,
                thread_timestamp=None,
                subtype=f"event:{record_type}",
                bot_ref=None,
            )
        subtype = optional_string(payload, "subtype", context=context)
        bot_ref = optional_string(payload, "bot_id", context=context)
        user_ref = optional_string(payload, "user", context=context)
        if subtype is None and bot_ref is None and user_ref is None:
            raise payload_error(context, "regular message has no user")
        return SlackHistoryMessage(
            timestamp=required_string(payload, "ts", context=context),
            text=required_string_allow_empty(payload, "text", context=context),
            user_ref=user_ref,
            thread_timestamp=optional_string(payload, "thread_ts", context=context),
            subtype=subtype,
            bot_ref=bot_ref,
        )

    async def post_message(
        self,
        channel: str,
        text: str,
        thread_ref: str | None = None,
        blocks: SlackBlockEnvelope | None = None,
    ) -> SlackPostReceipt:
        async with aiohttp.ClientSession() as session:
            resolved_channel = await self._directory.resolve_channel_in_session(session, channel)
            payload: dict[str, Any] = {"channel": resolved_channel.ref, "text": text}
            if blocks is not None:
                payload["blocks"] = blocks.provider_payload()
            if thread_ref is not None:
                thread_channel_ref, thread_timestamp = split_message_ref(thread_ref)
                if thread_channel_ref != resolved_channel.ref:
                    raise IntegrationOperationError(
                        code="invalid_ref",
                        safe_message="Slack thread_ref belongs to a different channel.",
                        retryable=False,
                    )
                payload["thread_ts"] = thread_timestamp
            data = await self._transport.post(session, "chat.postMessage", **payload)
            timestamp = required_string(data, "ts", context="chat.postMessage")
            returned_channel = required_string(data, "channel", context="chat.postMessage")
            if returned_channel != resolved_channel.ref:
                raise payload_error("chat.postMessage", "returned a different channel ref")
            return SlackPostReceipt(
                channel_ref=returned_channel,
                channel_name=resolved_channel.name,
                message_ref=f"{returned_channel}:{timestamp}",
                thread_ref=thread_ref or f"{returned_channel}:{timestamp}",
            )

    async def search_messages(
        self,
        query: str,
        limit: int = 20,
        *,
        channel_types: list[str] | None = None,
        include_context_messages: bool = False,
        page_ref: str | None = None,
    ) -> SlackMessagePage:
        async with aiohttp.ClientSession() as session:
            payload: dict[str, Any] = {
                "query": query,
                "limit": min(limit, 20),
                "content_types": ["messages"],
            }
            payload["channel_types"] = channel_types or list(_ALL_SEARCH_CHANNEL_TYPES)
            if include_context_messages:
                payload["include_context_messages"] = True
            if page_ref is not None:
                payload["cursor"] = page_ref
            data = await self._transport.post(session, "assistant.search.context", **payload)
            results = mapping(data.get("results"), context="assistant.search.context.results")
            messages = tuple(
                self._decode_search_message(mapping(message, context="search message"))
                for message in required_list(results, "messages", context="assistant.search.context.results")
            )
            if len(messages) > limit:
                raise payload_error("assistant.search.context", f"returned more than the requested {limit} messages")
            cursor = next_cursor(data, context="assistant.search.context")
            if cursor and cursor == page_ref:
                raise payload_error("assistant.search.context", "returned the current page ref again")
            return SlackMessagePage(messages=messages, next_page_ref=cursor or None)

    async def read_channel(
        self,
        channel: str,
        limit: int = 50,
        *,
        page_ref: str | None = None,
    ) -> SlackMessagePage:
        async with aiohttp.ClientSession() as session:
            resolved_channel = await self._directory.resolve_channel_in_session(session, channel)
            params = {"channel": resolved_channel.ref, "limit": str(limit)}
            if page_ref is not None:
                params["cursor"] = page_ref
            data = await self._transport.get(session, "conversations.history", **params)
            raw_messages = required_list(data, "messages", context="conversations.history")
            if len(raw_messages) > limit:
                raise payload_error("conversations.history", f"returned more than the requested {limit} messages")
            messages: list[SlackMessage] = []
            for raw_message in raw_messages:
                message = await self._decode_history_message(
                    session,
                    mapping(raw_message, context="channel message"),
                    resolved_channel.ref,
                    resolved_channel.name,
                )
                if message is not None:
                    messages.append(message)
            cursor = next_cursor(data, context="conversations.history")
            if cursor and cursor == page_ref:
                raise payload_error("conversations.history", "returned the current page ref again")
            return SlackMessagePage(messages=tuple(messages), next_page_ref=cursor or None)

    async def history_since(
        self,
        channel: str,
        *,
        oldest: str,
        latest: str,
        limit: int = 100,
    ) -> SlackHistoryPage:
        async with aiohttp.ClientSession() as session:
            resolved_channel = await self._directory.resolve_channel_in_session(session, channel)
            oldest_bound = timestamp_to_datetime(oldest)
            latest_bound = timestamp_to_datetime(latest)
            if latest_bound <= oldest_bound:
                raise payload_error("conversations.history", "latest bound must be newer than oldest bound")
            params: dict[str, Any] = {
                "channel": resolved_channel.ref,
                "limit": str(limit),
                "oldest": oldest,
                "latest": latest,
            }
            data = await self._transport.get(session, "conversations.history", **params)
            newest_first = [
                self.decode_monitor_message(mapping(message, context="history message"))
                for message in required_list(data, "messages", context="conversations.history")
            ]
            if len(newest_first) > limit:
                raise payload_error(
                    "conversations.history",
                    f"returned more than the requested {limit} records",
                )
            for message in newest_first:
                message_time = timestamp_to_datetime(message.timestamp)
                if message_time <= oldest_bound:
                    raise payload_error("conversations.history", "returned a message outside the oldest bound")
                if message_time >= latest_bound:
                    raise payload_error("conversations.history", "returned a message outside the latest bound")
            for newer, older in pairwise(newest_first):
                if timestamp_to_datetime(newer.timestamp) <= timestamp_to_datetime(older.timestamp):
                    raise payload_error(
                        "conversations.history",
                        "messages were not ordered newest first",
                    )
            has_more = required_bool(data, "has_more", context="conversations.history")
            if has_more and not newest_first:
                raise payload_error("conversations.history", "reported more messages after an empty page")
            oldest_first = tuple(reversed(newest_first))
            return SlackHistoryPage(
                messages=oldest_first,
                has_more=has_more,
                next_latest=oldest_first[0].timestamp if has_more else None,
            )
