import base64
from datetime import UTC, datetime
from typing import Any, Literal

import aiohttp

from arden.core.content import ImageContent
from arden.integrations.base import IntegrationConnectionError, IntegrationOperationError
from arden.integrations.slack.block_kit import SlackBlockEnvelope
from arden.integrations.slack.models import (
    SlackAuthResult,
    SlackChannel,
    SlackDirectMessage,
    SlackHistoryMessage,
    SlackIdentity,
    SlackImageFile,
    SlackMessage,
    SlackPostReceipt,
    SlackThreadMessage,
    SlackThreadResult,
    SlackUser,
    SlackUserProfile,
)

_API = "https://slack.com/api"
_SLACK_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"})
_MODEL_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
_MAX_THREAD_IMAGES = 4
_MAX_SLACK_IMAGE_BYTES = 5 * 1024 * 1024
_MAX_THREAD_PAGES = 100
_THREAD_PAGE_SIZE = 200


class SlackPayloadError(IntegrationOperationError):
    """Slack returned a successful response with an unusable payload."""

    def __init__(self, *, context: str, detail: str):
        super().__init__(
            code="invalid_provider_response",
            safe_message=f"Slack returned invalid data for {context}: {detail}",
            retryable=False,
        )


def _payload_error(context: str, detail: str) -> SlackPayloadError:
    return SlackPayloadError(context=context, detail=detail)


def _mapping(value: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _payload_error(context, "expected object")
    return value


def _required_string(payload: dict[str, Any], key: str, *, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise _payload_error(context, f"expected non-empty string {key!r}")
    return value


def _required_string_allow_empty(payload: dict[str, Any], key: str, *, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise _payload_error(context, f"expected string {key!r}")
    return value


def _optional_string(payload: dict[str, Any], key: str, *, context: str) -> str | None:
    if key not in payload or payload[key] is None:
        return None
    value = payload[key]
    if not isinstance(value, str):
        raise _payload_error(context, f"expected string {key!r}")
    return value or None


def _optional_int(payload: dict[str, Any], key: str, *, context: str) -> int | None:
    if key not in payload or payload[key] is None:
        return None
    value = payload[key]
    if type(value) is not int:
        raise _payload_error(context, f"expected integer {key!r}")
    return value


def _required_bool(payload: dict[str, Any], key: str, *, context: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise _payload_error(context, f"expected boolean {key!r}")
    return value


def _required_list(payload: dict[str, Any], key: str, *, context: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise _payload_error(context, f"expected array {key!r}")
    return value


def _next_cursor(payload: dict[str, Any], *, context: str) -> str:
    metadata = _mapping(payload.get("response_metadata"), context=f"{context}.response_metadata")
    return _required_string_allow_empty(metadata, "next_cursor", context=f"{context}.response_metadata")


def _ts_to_datetime(ts: str) -> datetime:
    try:
        return datetime.fromtimestamp(float(ts), tz=UTC)
    except (ValueError, OverflowError, OSError) as exc:
        raise _payload_error("message timestamp", f"invalid timestamp {ts!r}") from exc


def _format_message(user_name: str, text: str, ts: str, channel_name: str | None = None) -> str:
    when = _ts_to_datetime(ts).isoformat()
    prefix = f"[{when}] {user_name}"
    if channel_name:
        prefix += f" in #{channel_name}"
    return f"{prefix}:\n{text}"


_USER_TOKEN_METHODS = frozenset({"assistant.search.context", "chat.postMessage"})


class SlackClient:
    name = "slack"

    def __init__(self, bot_token: str | None = None, user_token: str | None = None):
        read_token = user_token or bot_token
        if not read_token:
            raise ValueError("SlackClient requires at least one of bot_token or user_token")
        self._bot_token = bot_token
        self._user_token = user_token
        # User token sees more (all the user's channels, search) — prefer it for reads.
        self._read_token: str = read_token
        self._user_cache: dict[str, str] = {}
        self._channel_name_cache: dict[str, str] = {}
        self._channel_id_by_name: dict[str, str] = {}
        # DM channels: channel_id (D*) -> peer user_id; reverse by username + real_name.
        self._dm_peer_by_channel: dict[str, str] = {}
        self._dm_channel_by_user: dict[str, str] = {}
        # Whether we've refreshed the DM/MPIM index yet.
        self._dm_index_loaded = False

    def _token_for(self, method: str) -> str:
        if method in _USER_TOKEN_METHODS:
            if not self._user_token:
                raise IntegrationConnectionError(
                    integration_id="slack",
                    reason="scope_required",
                    detail=f"Slack {method} requires a user token.",
                    retry_safe=True,
                )
            return self._user_token
        return self._read_token

    def _raise_for_error(self, method: str, data: dict, headers: dict) -> None:
        error = _required_string(data, "error", context=method)
        # Slack returns `needed` (scope) and `provided` on missing_scope errors,
        # both in JSON body and X-OAuth-Scopes / X-Accepted-OAuth-Scopes headers.
        needed = data.get("needed") or headers.get("X-Accepted-OAuth-Scopes")
        provided = data.get("provided") or headers.get("X-OAuth-Scopes")
        token_kind = "user" if method in _USER_TOKEN_METHODS or self._token_for(method) is self._user_token else "bot"
        msg = f"Slack API {method} failed: {error}"
        if needed:
            msg += f" — needs scope: {needed}"
        if provided:
            msg += f" (have: {provided})"
        msg += f" [{token_kind} token]"
        if error in {"invalid_auth", "token_revoked", "account_inactive", "not_authed"}:
            raise IntegrationConnectionError(
                integration_id="slack",
                reason="auth_required",
                detail=msg,
                retry_safe=True,
            )
        if error == "missing_scope":
            required_scopes = tuple(scope.strip() for scope in str(needed or "").split(",") if scope.strip())
            raise IntegrationConnectionError(
                integration_id="slack",
                reason="scope_required",
                detail=msg,
                required_scopes=required_scopes,
                retry_safe=True,
            )
        raise IntegrationOperationError(code=error, safe_message=msg, retryable=False)

    async def _get(self, session: aiohttp.ClientSession, method: str, **params: Any) -> dict:
        headers = {"Authorization": f"Bearer {self._token_for(method)}"}
        async with session.get(f"{_API}/{method}", headers=headers, params=params) as resp:
            data = _mapping(await resp.json(), context=method)
            if not _required_bool(data, "ok", context=method):
                self._raise_for_error(method, data, dict(resp.headers))
            return data

    async def _post(self, session: aiohttp.ClientSession, method: str, **payload: Any) -> dict:
        headers = {
            "Authorization": f"Bearer {self._token_for(method)}",
            "Content-Type": "application/json; charset=utf-8",
        }
        async with session.post(f"{_API}/{method}", headers=headers, json=payload) as resp:
            data = _mapping(await resp.json(), context=method)
            if not _required_bool(data, "ok", context=method):
                self._raise_for_error(method, data, dict(resp.headers))
            return data

    async def auth_test(self, token_kind: Literal["bot", "user", "read"] = "read") -> SlackAuthResult:
        method = "auth.test"
        if token_kind == "bot":
            if not self._bot_token:
                raise RuntimeError("Slack auth.test requires a bot token (xoxb-)")
            token = self._bot_token
        elif token_kind == "user":
            if not self._user_token:
                raise RuntimeError("Slack auth.test requires a user token (xoxp-)")
            token = self._user_token
        else:
            token = self._read_token
        headers = {"Authorization": f"Bearer {token}"}
        async with aiohttp.ClientSession() as session, session.get(f"{_API}/{method}", headers=headers) as resp:
            data = _mapping(await resp.json(), context=method)
            if not _required_bool(data, "ok", context=method):
                self._raise_for_error(method, data, dict(resp.headers))
            return self._decode_auth_result(data)

    async def verify_connection(self) -> None:
        await self.auth_test()

    @staticmethod
    def _decode_auth_result(data: dict[str, Any]) -> SlackAuthResult:
        context = "auth.test"
        return SlackAuthResult(
            team_name=_optional_string(data, "team", context=context),
            team_ref=_optional_string(data, "team_id", context=context),
            user_ref=_required_string(data, "user_id", context=context),
            user_name=_required_string(data, "user", context=context),
            bot_ref=_optional_string(data, "bot_id", context=context),
        )

    async def _resolve_user(self, session: aiohttp.ClientSession, user_id: str) -> str:
        if user_id in self._user_cache:
            return self._user_cache[user_id]
        data = await self._get(session, "users.info", user=user_id)
        user = _mapping(data.get("user"), context="users.info")
        name = _optional_string(user, "real_name", context="users.info.user") or _required_string(
            user, "name", context="users.info.user"
        )
        self._user_cache[user_id] = name
        return name

    async def _decode_search_message(self, session: aiohttp.ClientSession, payload: dict[str, Any]) -> SlackMessage:
        context = "assistant.search.context message"
        channel = payload.get("channel")
        channel_payload = _mapping(channel, context=f"{context}.channel") if channel is not None else None
        channel_ref = _optional_string(payload, "channel_id", context=context)
        if channel_ref is None and channel_payload is not None:
            channel_ref = _required_string(channel_payload, "id", context=f"{context}.channel")
        if channel_ref is None:
            raise _payload_error(context, "expected channel_id or channel.id")
        channel_name = _optional_string(payload, "channel_name", context=context)
        if channel_name is None and channel_payload is not None:
            channel_name = _optional_string(channel_payload, "name", context=f"{context}.channel")
        timestamp = _required_string(payload, "ts", context=context)
        author_ref = _optional_string(payload, "user_id", context=context)
        if author_ref is None:
            author_ref = _optional_string(payload, "user", context=context)
        author_name = _optional_string(payload, "author_user_name", context=context)
        if author_name is None:
            if author_ref is None:
                raise _payload_error(context, "expected author_user_name or user_id")
            author_name = await self._resolve_user(session, author_ref)
        text_key = "content" if "content" in payload else "text"
        return SlackMessage(
            ref=f"{channel_ref}:{timestamp}",
            channel_ref=channel_ref,
            channel_name=channel_name,
            author_ref=author_ref,
            author_name=author_name,
            text=_required_string_allow_empty(payload, text_key, context=context),
            created_at=_ts_to_datetime(timestamp),
            permalink=_optional_string(payload, "permalink", context=context),
        )

    async def _decode_history_message(
        self,
        session: aiohttp.ClientSession,
        payload: dict[str, Any],
        channel_ref: str,
        channel_name: str,
    ) -> SlackMessage:
        context = "conversations.history message"
        timestamp = _required_string(payload, "ts", context=context)
        author_ref = _optional_string(payload, "user", context=context)
        author_name = _optional_string(payload, "username", context=context)
        if author_ref is not None:
            author_name = await self._resolve_user(session, author_ref)
        if author_name is None:
            raise _payload_error(context, "expected user or username")
        thread_ts = _optional_string(payload, "thread_ts", context=context)
        return SlackMessage(
            ref=f"{channel_ref}:{timestamp}",
            channel_ref=channel_ref,
            channel_name=channel_name,
            author_ref=author_ref,
            author_name=author_name,
            text=_required_string_allow_empty(payload, "text", context=context),
            created_at=_ts_to_datetime(timestamp),
            thread_ref=f"{channel_ref}:{thread_ts}" if thread_ts is not None else None,
            reply_count=_optional_int(payload, "reply_count", context=context),
        )

    @staticmethod
    def _decode_monitor_message(payload: dict[str, Any]) -> SlackHistoryMessage:
        context = "conversations.history message"
        subtype = _optional_string(payload, "subtype", context=context)
        bot_ref = _optional_string(payload, "bot_id", context=context)
        user_ref = _optional_string(payload, "user", context=context)
        if subtype is None and bot_ref is None and user_ref is None:
            raise _payload_error(context, "regular message has no user")
        return SlackHistoryMessage(
            timestamp=_required_string(payload, "ts", context=context),
            text=_required_string_allow_empty(payload, "text", context=context),
            user_ref=user_ref,
            thread_timestamp=_optional_string(payload, "thread_ts", context=context),
            subtype=subtype,
            bot_ref=bot_ref,
        )

    async def _resolve_channel_id(self, session: aiohttp.ClientSession, channel: str) -> SlackChannel:
        """Resolve an id or name to one exact Slack channel."""
        if channel.startswith("#"):
            channel = channel[1:]
        if channel and channel[0] in ("C", "G", "D") and channel.isalnum() and channel.isupper():
            cname = self._channel_name_cache.get(channel)
            if cname is None:
                data = await self._get(session, "conversations.info", channel=channel)
                record = _mapping(data.get("channel"), context="conversations.info.channel")
                returned_ref = _required_string(record, "id", context="conversations.info.channel")
                if returned_ref != channel:
                    raise _payload_error("conversations.info.channel", "returned a different channel id")
                cname = _optional_string(record, "name", context="conversations.info.channel")
                if cname is None:
                    if not _required_bool(record, "is_im", context="conversations.info.channel"):
                        raise _payload_error("conversations.info.channel", "non-DM channel has no name")
                    peer_ref = _required_string(record, "user", context="conversations.info.channel")
                    cname = await self._resolve_user(session, peer_ref)
                self._channel_name_cache[channel] = cname
            return SlackChannel(ref=channel, name=cname)
        if cid := self._channel_id_by_name.get(channel):
            return SlackChannel(ref=cid, name=channel)
        await self._refresh_channel_index(session)
        if cid := self._channel_id_by_name.get(channel):
            return SlackChannel(ref=cid, name=channel)
        raise IntegrationOperationError(
            code="not_found",
            safe_message=f"Slack channel {channel!r} was not found.",
            retryable=False,
        )

    async def _refresh_channel_index(
        self,
        session: aiohttp.ClientSession,
        *,
        types: str = "public_channel,private_channel",
    ) -> None:
        channel_names: dict[str, str] = {}
        channel_refs: dict[str, str] = {}
        dm_peers: dict[str, str] = {}
        dm_channels: dict[str, str] = {}
        cursor = ""
        while True:
            params: dict[str, Any] = {
                "limit": "1000",
                "exclude_archived": "true",
                "types": types,
            }
            if cursor:
                params["cursor"] = cursor
            data = await self._get(session, "conversations.list", **params)
            for raw_channel in _required_list(data, "channels", context="conversations.list"):
                ch = _mapping(raw_channel, context="conversations.list channel")
                cid = _required_string(ch, "id", context="conversations.list channel")
                if _required_bool(ch, "is_im", context="conversations.list channel"):
                    peer = _required_string(ch, "user", context="conversations.list channel")
                    dm_peers[cid] = peer
                    dm_channels[peer] = cid
                else:
                    cname = _required_string(ch, "name", context="conversations.list channel")
                    channel_names[cid] = cname
                    channel_refs[cname] = cid
            cursor = _next_cursor(data, context="conversations.list")
            if not cursor:
                break
        self._channel_name_cache.update(channel_names)
        self._channel_id_by_name.update(channel_refs)
        self._dm_peer_by_channel.update(dm_peers)
        self._dm_channel_by_user.update(dm_channels)
        if "im" in types:
            self._dm_index_loaded = True

    # -- public write methods --

    async def post_message(
        self,
        channel: str,
        text: str,
        thread_ts: str | None = None,
        blocks: SlackBlockEnvelope | None = None,
    ) -> SlackPostReceipt:
        """Post a Slack message with the configured user token. Returns Slack channel/ts metadata."""
        async with aiohttp.ClientSession() as session:
            resolved_channel = await self._resolve_channel_id(session, channel)
            payload: dict[str, Any] = {"channel": resolved_channel.ref, "text": text}
            if blocks is not None:
                payload["blocks"] = blocks.provider_payload()
            if thread_ts:
                payload["thread_ts"] = thread_ts
            data = await self._post(session, "chat.postMessage", **payload)
            ts = _required_string(data, "ts", context="chat.postMessage")
            returned_channel = _required_string(data, "channel", context="chat.postMessage")
            if returned_channel != resolved_channel.ref:
                raise _payload_error("chat.postMessage", "returned a different channel id")
            return SlackPostReceipt(
                channel_ref=returned_channel,
                channel_name=resolved_channel.name,
                message_ts=ts,
                thread_ts=thread_ts or ts,
            )

    # -- public read methods --

    async def search_messages(
        self,
        query: str,
        limit: int = 20,
        *,
        channel_types: list[str] | None = None,
        include_context_messages: bool = False,
    ) -> list[SlackMessage]:
        """Search via the Real-time Search API (assistant.search.context).

        Requires user token with granular `search:read.*` scopes. The legacy
        `search.messages` method (which needed `search:read`) is deprecated.
        """
        async with aiohttp.ClientSession() as session:
            payload: dict[str, Any] = {
                "query": query,
                "limit": min(limit, 20),
                "content_types": ["messages"],
            }
            if channel_types:
                payload["channel_types"] = channel_types
            if include_context_messages:
                payload["include_context_messages"] = True
            data = await self._post(session, "assistant.search.context", **payload)
            results = _mapping(data.get("results"), context="assistant.search.context.results")
            messages = results.get("messages")
            if not isinstance(messages, list):
                raise _payload_error("assistant.search.context.results", "expected messages array")
            return [
                await self._decode_search_message(session, _mapping(message, context="search message"))
                for message in messages
            ]

    async def search_channels(self, query: str | None = None, limit: int = 50) -> list[SlackChannel]:
        async with aiohttp.ClientSession() as session:
            await self._refresh_channel_index(session)
            results: list[SlackChannel] = []
            q = query.lower() if query else None
            for cid, cname in self._channel_name_cache.items():
                if q and q not in cname.lower():
                    continue
                results.append(SlackChannel(ref=cid, name=cname))
                if len(results) >= limit:
                    break
            return results

    async def list_dms(self, query: str | None = None, limit: int = 50) -> list[SlackDirectMessage]:
        """List open DMs (1-on-1) with resolved peer names.

        Requires `im:read` scope on the read token. Does NOT include group DMs (mpim).
        """
        async with aiohttp.ClientSession() as session:
            if not self._dm_index_loaded:
                await self._refresh_channel_index(session, types="im")
            results: list[SlackDirectMessage] = []
            q = query.lower() if query else None
            for cid, peer_id in self._dm_peer_by_channel.items():
                peer_name = await self._resolve_user(session, peer_id)
                if q and q not in peer_name.lower() and q not in peer_id.lower():
                    continue
                results.append(SlackDirectMessage(channel_ref=cid, user_ref=peer_id, peer_name=peer_name))
                if len(results) >= limit:
                    break
            return results

    async def open_dm(self, user_id: str) -> str:
        """Open (or fetch existing) DM channel with a user. Returns the DM channel id."""
        async with aiohttp.ClientSession() as session:
            # Check cache first to avoid an API call
            if cid := self._dm_channel_by_user.get(user_id):
                return cid
            data = await self._post(session, "conversations.open", users=user_id)
            channel = _mapping(data.get("channel"), context="conversations.open.channel")
            cid = _required_string(channel, "id", context="conversations.open.channel")
            self._dm_peer_by_channel[cid] = user_id
            self._dm_channel_by_user[user_id] = cid
            return cid

    async def resolve_dm_target(self, target: str) -> str:
        """Resolve a DM target to a channel_id.

        Accepts:
          - DM channel id (D*)
          - user id (U*/W*)
          - @username or username (real_name or handle, case-insensitive)
        """
        # Already a DM channel id
        if target and target[0] == "D" and target.isalnum():
            return target
        # User id -> open DM
        stripped = target.lstrip("@")
        if stripped and stripped[0] in ("U", "W") and stripped.isalnum() and stripped.isupper():
            return await self.open_dm(stripped)
        # Name -> resolve across the complete matching user set.
        users = await self.search_users(stripped, limit=200)
        if not users:
            raise IntegrationOperationError(
                code="not_found",
                safe_message=f"No Slack user matched {target!r}.",
                retryable=False,
            )
        normalized = stripped.casefold()
        exact = [
            user
            for user in users
            if normalized in {user.name.casefold(), user.username.casefold(), (user.email or "").casefold()}
        ]
        candidates = exact or users
        if len(candidates) != 1:
            refs = ", ".join(f"{user.name} ({user.ref})" for user in candidates[:10])
            raise IntegrationOperationError(
                code="ambiguous_ref",
                safe_message=f"Ambiguous Slack user {target!r}; choose an exact user id: {refs}",
                retryable=False,
            )
        return await self.open_dm(candidates[0].ref)

    async def search_users(self, query: str | None = None, limit: int = 50) -> list[SlackUser]:
        async with aiohttp.ClientSession() as session:
            results: list[SlackUser] = []
            q = query.lower() if query else None
            cursor = ""
            while len(results) < limit:
                data = await self._get(session, "users.list", limit="200", cursor=cursor)
                members = data.get("members")
                if not isinstance(members, list):
                    raise _payload_error("users.list", "expected members array")
                for member in members:
                    m = _mapping(member, context="users.list member")
                    deleted = _required_bool(m, "deleted", context="users.list member")
                    is_bot = _required_bool(m, "is_bot", context="users.list member")
                    if deleted or is_bot:
                        continue
                    profile = _mapping(m.get("profile"), context="users.list member.profile")
                    username = _required_string(m, "name", context="users.list member")
                    name = _optional_string(m, "real_name", context="users.list member") or username
                    email = _optional_string(profile, "email", context="users.list member.profile")
                    if q and q not in name.lower() and q not in (email or "").lower() and q not in username.lower():
                        continue
                    results.append(
                        SlackUser(
                            ref=_required_string(m, "id", context="users.list member"),
                            name=name,
                            username=username,
                            email=email,
                            title=_optional_string(profile, "title", context="users.list member.profile"),
                        )
                    )
                    if len(results) >= limit:
                        break
                cursor = _next_cursor(data, context="users.list")
                if not cursor:
                    break
            return results

    async def read_channel(self, channel: str, limit: int = 50) -> list[SlackMessage]:
        async with aiohttp.ClientSession() as session:
            resolved_channel = await self._resolve_channel_id(session, channel)
            data = await self._get(
                session,
                "conversations.history",
                channel=resolved_channel.ref,
                limit=str(limit),
            )
            messages = data.get("messages")
            if not isinstance(messages, list):
                raise _payload_error("conversations.history", "expected messages array")
            return [
                await self._decode_history_message(
                    session,
                    _mapping(message, context="channel message"),
                    resolved_channel.ref,
                    resolved_channel.name,
                )
                for message in messages
            ]

    async def history_since(
        self,
        channel: str,
        oldest: str | None = None,
        limit: int = 200,
    ) -> list[SlackHistoryMessage]:
        """Fetch channel messages since `oldest`, ordered oldest -> newest.

        conversations.history returns newest-first per page; we page via
        response_metadata.next_cursor and reverse to chronological order.
        """
        async with aiohttp.ClientSession() as session:
            resolved_channel = await self._resolve_channel_id(session, channel)
            messages: list[SlackHistoryMessage] = []
            cursor = ""
            while True:
                params: dict[str, Any] = {"channel": resolved_channel.ref, "limit": str(limit)}
                if oldest:
                    params["oldest"] = oldest
                if cursor:
                    params["cursor"] = cursor
                data = await self._get(session, "conversations.history", **params)
                messages.extend(
                    self._decode_monitor_message(_mapping(message, context="history message"))
                    for message in _required_list(data, "messages", context="conversations.history")
                )
                cursor = _next_cursor(data, context="conversations.history")
                if not cursor:
                    break
            messages.reverse()
            return messages

    async def resolve_channel(self, name: str) -> SlackChannel:
        async with aiohttp.ClientSession() as session:
            return await self._resolve_channel_id(session, name)

    async def resolve_user(self, name: str) -> SlackUser:
        candidates = await self.search_users(name)
        q = name.casefold()
        exact = [c for c in candidates if c.username.casefold() == q or c.name.casefold() == q]
        if len(exact) == 1:
            return exact[0]
        if not candidates:
            raise IntegrationOperationError(
                code="not_found",
                safe_message=f"No Slack user matched {name!r}.",
                retryable=False,
            )
        choices = ", ".join(f"{candidate.name} (@{candidate.username})" for candidate in candidates)
        raise IntegrationOperationError(
            code="ambiguous_ref",
            safe_message=f"Ambiguous Slack user {name!r}; candidates: {choices}",
            retryable=False,
        )

    async def resolve_user_name(self, user_id: str) -> str:
        """Return the cached or provider-resolved display name; raise when resolution fails."""
        async with aiohttp.ClientSession() as session:
            return await self._resolve_user(session, user_id)

    async def whoami(self) -> SlackIdentity:
        identity = await self.auth_test()
        return SlackIdentity(user_ref=identity.user_ref, user_name=identity.user_name)

    async def read_thread(self, source_id: str) -> SlackThreadResult | None:
        """Read every page of a Slack thread or fail on cursor cycles/page overflow."""
        if ":" not in source_id:
            raise IntegrationOperationError(
                code="invalid_ref",
                safe_message="Slack message references must be channel:timestamp.",
                retryable=False,
            )
        cid, ts = source_id.split(":", 1)
        if not cid or not ts:
            raise IntegrationOperationError(
                code="invalid_ref",
                safe_message="Slack message references must be channel:timestamp.",
                retryable=False,
            )
        async with aiohttp.ClientSession() as session:
            raw_messages = await self._read_all_thread_messages(session, channel_ref=cid, timestamp=ts)
            if not raw_messages:
                return None
            channel = await self._resolve_channel_id(session, cid)
            messages = [await self._decode_thread_message(session, message) for message in raw_messages]
            lines: list[str] = []
            model_content: list[ImageContent] = []
            for message in messages:
                text = message.text
                remaining = _MAX_THREAD_IMAGES - len(model_content)
                file_notes, image_blocks = await self._extract_thread_images(session, message.images, remaining)
                if file_notes:
                    text = "\n".join(part for part in [text, *file_notes] if part)
                model_content.extend(image_blocks)
                lines.append(_format_message(message.author_name, text, message.timestamp, channel.name))
            return SlackThreadResult(text="\n\n".join(lines), model_content=tuple(model_content))

    async def _read_all_thread_messages(
        self,
        session: aiohttp.ClientSession,
        *,
        channel_ref: str,
        timestamp: str,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        cursor = ""
        seen_cursors: set[str] = set()
        for _page in range(_MAX_THREAD_PAGES):
            params: dict[str, Any] = {
                "channel": channel_ref,
                "ts": timestamp,
                "limit": str(_THREAD_PAGE_SIZE),
            }
            if cursor:
                params["cursor"] = cursor
            data = await self._get(session, "conversations.replies", **params)
            messages.extend(
                _mapping(message, context="conversations.replies message")
                for message in _required_list(data, "messages", context="conversations.replies")
            )
            next_cursor = _next_cursor(data, context="conversations.replies")
            if not next_cursor:
                return messages
            if next_cursor in seen_cursors:
                raise SlackPayloadError(
                    context="conversations.replies pagination",
                    detail=f"cursor {next_cursor!r} repeated",
                )
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        raise IntegrationOperationError(
            code="pagination_limit",
            safe_message=f"Slack thread exceeded the {_MAX_THREAD_PAGES}-page safety limit.",
            retryable=False,
        )

    async def _decode_thread_message(
        self,
        session: aiohttp.ClientSession,
        payload: dict[str, Any],
    ) -> SlackThreadMessage:
        context = "conversations.replies message"
        author_ref = _optional_string(payload, "user", context=context)
        author_name = _optional_string(payload, "username", context=context)
        if author_ref is not None:
            author_name = await self._resolve_user(session, author_ref)
        if author_name is None:
            raise _payload_error(context, "expected user or username")
        raw_files = [] if "files" not in payload or payload["files"] is None else payload["files"]
        if not isinstance(raw_files, list):
            raise _payload_error(context, "expected files array when present")
        images = tuple(
            image
            for raw_file in raw_files
            if (image := self._decode_image_file(_mapping(raw_file, context="Slack file"))) is not None
        )
        return SlackThreadMessage(
            timestamp=_required_string(payload, "ts", context=context),
            text=_required_string_allow_empty(payload, "text", context=context),
            author_ref=author_ref,
            author_name=author_name,
            images=images,
        )

    async def read_file_image(self, file_id: str) -> SlackThreadResult | None:
        async with aiohttp.ClientSession() as session:
            data = await self._get(session, "files.info", file=file_id)
            file_obj = self._decode_image_file(_mapping(data.get("file"), context="files.info.file"))
            if file_obj is None:
                return None
            block = await self._download_slack_image(session, file_obj)
            return SlackThreadResult(
                text=f"Slack image: {file_obj.title}\n{self._format_image_note(file_obj)}",
                model_content=(block,),
            )

    async def _extract_thread_images(
        self,
        session: aiohttp.ClientSession,
        files: tuple[SlackImageFile, ...],
        remaining: int,
    ) -> tuple[list[str], list[ImageContent]]:
        notes: list[str] = []
        image_blocks: list[ImageContent] = []
        for file_obj in files:
            notes.append(self._format_image_note(file_obj))
            if len(image_blocks) >= remaining:
                continue
            image_blocks.append(await self._download_slack_image(session, file_obj))
        return notes, image_blocks

    @staticmethod
    def _normalized_image_mime(mime: str) -> str | None:
        mime = mime.lower()
        if mime == "image/jpg":
            return "image/jpeg"
        if mime in _SLACK_IMAGE_MIME_TYPES:
            return mime
        return None

    def _decode_image_file(self, payload: dict[str, Any]) -> SlackImageFile | None:
        context = "Slack file"
        raw_mime = _required_string(payload, "mimetype", context=context)
        mime = self._normalized_image_mime(raw_mime)
        if mime is None:
            return None
        title = _optional_string(payload, "title", context=context) or _optional_string(
            payload, "name", context=context
        )
        if title is None:
            raise _payload_error(context, "image has no title or name")
        download_url = _optional_string(payload, "url_private_download", context=context) or _optional_string(
            payload, "url_private", context=context
        )
        if download_url is None:
            raise _payload_error(context, "image has no private download URL")
        return SlackImageFile(
            ref=_required_string(payload, "id", context=context),
            title=title,
            mime_type=mime,
            size_bytes=_optional_int(payload, "size", context=context),
            download_url=download_url,
        )

    @staticmethod
    def _format_image_note(file_obj: SlackImageFile) -> str:
        details = [file_obj.mime_type]
        if file_obj.size_bytes is not None:
            details.append(f"{file_obj.size_bytes} bytes")
        details.append(f"id: {file_obj.ref}")
        return f"Attached image: {file_obj.title} ({', '.join(details)})"

    async def _download_slack_image(
        self,
        session: aiohttp.ClientSession,
        file_obj: SlackImageFile,
    ) -> ImageContent:
        if file_obj.mime_type not in _MODEL_IMAGE_MIME_TYPES:
            raise IntegrationOperationError(
                code="unsupported_media",
                safe_message=f"Slack image {file_obj.ref} uses unsupported media type {file_obj.mime_type}.",
                retryable=False,
            )
        if file_obj.size_bytes is not None and file_obj.size_bytes > _MAX_SLACK_IMAGE_BYTES:
            raise IntegrationOperationError(
                code="payload_too_large",
                safe_message=f"Slack image {file_obj.ref} exceeds the 5 MiB model limit.",
                retryable=False,
            )
        headers = {"Authorization": f"Bearer {self._token_for('files.info')}"}
        try:
            async with session.get(file_obj.download_url, headers=headers) as resp:
                if resp.status >= 400:
                    raise IntegrationOperationError(
                        code="provider_error",
                        safe_message=f"Slack image download failed with HTTP {resp.status}.",
                        retryable=resp.status in {408, 429, 500, 502, 503, 504},
                    )
                body = await resp.read()
        except aiohttp.ClientError as exc:
            raise IntegrationOperationError(
                code="provider_error",
                safe_message="Slack image download failed.",
                retryable=True,
            ) from exc
        if len(body) > _MAX_SLACK_IMAGE_BYTES:
            raise IntegrationOperationError(
                code="payload_too_large",
                safe_message=f"Slack image {file_obj.ref} exceeds the 5 MiB model limit.",
                retryable=False,
            )
        detected_mime = _detect_supported_image_mime(body)
        if not detected_mime:
            raise SlackPayloadError(
                context="image download",
                detail=f"file {file_obj.ref} did not contain supported image bytes",
            )
        return ImageContent(media_type=detected_mime, data=base64.b64encode(body).decode("ascii"))

    async def read_user(self, user_id: str) -> SlackUserProfile:
        async with aiohttp.ClientSession() as session:
            data = await self._get(session, "users.info", user=user_id)
            user = _mapping(data.get("user"), context="users.info")
            profile = _mapping(user.get("profile"), context="users.info.profile")
            return SlackUserProfile(
                ref=_required_string(user, "id", context="users.info.user"),
                name=_optional_string(user, "real_name", context="users.info.user")
                or _required_string(user, "name", context="users.info.user"),
                username=_required_string(user, "name", context="users.info.user"),
                email=_optional_string(profile, "email", context="users.info.profile"),
                title=_optional_string(profile, "title", context="users.info.profile"),
                status_text=_optional_string(profile, "status_text", context="users.info.profile"),
                status_emoji=_optional_string(profile, "status_emoji", context="users.info.profile"),
                timezone=_optional_string(user, "tz", context="users.info.user"),
            )


def _detect_supported_image_mime(body: bytes) -> str | None:
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if body.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(body) >= 12 and body.startswith(b"RIFF") and body[8:12] == b"WEBP":
        return "image/webp"
    if _is_static_gif(body):
        return "image/gif"
    return None


def _skip_gif_sub_blocks(body: bytes, offset: int) -> int | None:
    while offset < len(body):
        size = body[offset]
        offset += 1
        if size == 0:
            return offset
        offset += size
    return None


def _is_static_gif(body: bytes) -> bool:
    if not (body.startswith(b"GIF87a") or body.startswith(b"GIF89a")) or len(body) < 13:
        return False
    offset = 13
    packed = body[10]
    if packed & 0x80:
        offset += 3 * (2 ** ((packed & 0x07) + 1))

    frames = 0
    while offset < len(body):
        marker = body[offset]
        if marker == 0x3B:
            return frames == 1
        if marker == 0x21:
            offset = _skip_gif_sub_blocks(body, offset + 2)
            if offset is None:
                return False
            continue
        if marker != 0x2C or offset + 10 > len(body):
            return False

        frames += 1
        if frames > 1:
            return False
        image_packed = body[offset + 9]
        offset += 10
        if image_packed & 0x80:
            offset += 3 * (2 ** ((image_packed & 0x07) + 1))
        if offset >= len(body):
            return False
        offset += 1
        offset = _skip_gif_sub_blocks(body, offset)
        if offset is None:
            return False
    return False
