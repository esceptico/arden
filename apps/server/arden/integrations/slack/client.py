import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import aiohttp

from arden.core.content import ImageContent
from arden.integrations.base import IntegrationConnectionError
from arden.logging import get_logger
from arden.search.types import RawItem

_logger = get_logger(__name__)
_API = "https://slack.com/api"
_SLACK_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"})
_MODEL_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
_MAX_THREAD_IMAGES = 4
_MAX_SLACK_IMAGE_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class SlackThreadResult:
    text: str
    model_content: tuple[ImageContent, ...] = ()


def _ts_to_datetime(ts: str) -> datetime:
    try:
        return datetime.fromtimestamp(float(ts), tz=UTC)
    except (ValueError, TypeError):
        return datetime.now(UTC)


def _format_message(user_name: str, text: str, ts: str, channel_name: str | None = None) -> str:
    when = _ts_to_datetime(ts).isoformat()
    prefix = f"[{when}] {user_name}"
    if channel_name:
        prefix += f" in #{channel_name}"
    return f"{prefix}:\n{text}"


_USER_TOKEN_METHODS = frozenset({"assistant.search.context", "chat.postMessage"})


def _normalize_block_kit(value: Any) -> Any:
    """Normalize Block Kit payloads before sending to Slack.

    Tool-call arguments can arrive with escaped newlines (`\\n`) inside nested
    block text fields. Slack then renders the literal characters and mrkdwn gets
    ugly. Convert those back to real newlines recursively without touching the
    rest of the payload shape.
    """
    if isinstance(value, str):
        return value.replace("\\n", "\n")
    if isinstance(value, list):
        return [_normalize_block_kit(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_block_kit(item) for key, item in value.items()}
    return value


class SlackClient:
    name = "slack"

    def __init__(self, bot_token: str | None = None, user_token: str | None = None):
        if not bot_token and not user_token:
            raise ValueError("SlackClient requires at least one of bot_token or user_token")
        self._bot_token = bot_token
        self._user_token = user_token
        # User token sees more (all the user's channels, search) — prefer it for reads.
        self._read_token = user_token or bot_token
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
        return self._read_token  # type: ignore[return-value]

    def _raise_for_error(self, method: str, data: dict, headers: dict) -> None:
        error = data.get("error", "unknown")
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
        raise RuntimeError(msg)

    async def _get(self, session: aiohttp.ClientSession, method: str, **params: Any) -> dict:
        headers = {"Authorization": f"Bearer {self._token_for(method)}"}
        async with session.get(f"{_API}/{method}", headers=headers, params=params) as resp:
            data = await resp.json()
            if not data.get("ok"):
                self._raise_for_error(method, data, dict(resp.headers))
            return data

    async def _post(self, session: aiohttp.ClientSession, method: str, **payload: Any) -> dict:
        headers = {
            "Authorization": f"Bearer {self._token_for(method)}",
            "Content-Type": "application/json; charset=utf-8",
        }
        async with session.post(f"{_API}/{method}", headers=headers, json=payload) as resp:
            data = await resp.json()
            if not data.get("ok"):
                self._raise_for_error(method, data, dict(resp.headers))
            return data

    async def auth_test(self, token_kind: Literal["bot", "user", "read"] = "read") -> dict[str, Any]:
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
            data = await resp.json()
            if not data.get("ok"):
                self._raise_for_error(method, data, dict(resp.headers))
            return data

    async def verify_connection(self) -> None:
        await self.auth_test()

    async def _resolve_user(self, session: aiohttp.ClientSession, user_id: str) -> str:
        if not user_id:
            return "unknown"
        if user_id in self._user_cache:
            return self._user_cache[user_id]
        try:
            data = await self._get(session, "users.info", user=user_id)
            user = data["user"]
            name = user.get("real_name") or user.get("name") or user_id
        except Exception:
            name = user_id
        self._user_cache[user_id] = name
        return name

    async def _resolve_channel_id(self, session: aiohttp.ClientSession, channel: str) -> tuple[str, str]:
        """Return (channel_id, channel_name). Accepts id, '#name', or 'name'."""
        if channel.startswith("#"):
            channel = channel[1:]
        # Already a slack ID
        if channel and channel[0] in ("C", "G", "D") and channel.isalnum() and channel.isupper():
            cname = self._channel_name_cache.get(channel)
            if cname is None:
                try:
                    data = await self._get(session, "conversations.info", channel=channel)
                    cname = data["channel"].get("name", channel)
                except Exception:
                    cname = channel
                self._channel_name_cache[channel] = cname
            return channel, cname
        # Treat as name
        if cid := self._channel_id_by_name.get(channel):
            return cid, channel
        await self._refresh_channel_index(session)
        if cid := self._channel_id_by_name.get(channel):
            return cid, channel
        raise RuntimeError(f"Slack channel not found: {channel}")

    async def _refresh_channel_index(
        self,
        session: aiohttp.ClientSession,
        *,
        types: str = "public_channel,private_channel",
    ) -> None:
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
            for ch in data.get("channels", []):
                cid = ch["id"]
                cname = ch.get("name", "")
                # Regular (public/private) channels have a name
                if cname:
                    self._channel_name_cache[cid] = cname
                    self._channel_id_by_name[cname] = cid
                # Direct messages (1-on-1): `is_im` + `user` field = peer user id
                if ch.get("is_im") and (peer := ch.get("user")):
                    self._dm_peer_by_channel[cid] = peer
                    self._dm_channel_by_user[peer] = cid
            cursor = data.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                break
        if "im" in types:
            self._dm_index_loaded = True

    # -- public write methods --

    async def post_message(
        self,
        channel: str,
        text: str,
        thread_ts: str | None = None,
        blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, str]:
        """Post a Slack message with the configured user token. Returns Slack channel/ts metadata."""
        async with aiohttp.ClientSession() as session:
            channel_id, channel_name = await self._resolve_channel_id(session, channel)
            payload: dict[str, Any] = {"channel": channel_id, "text": text}
            if blocks:
                payload["blocks"] = _normalize_block_kit(blocks[:50])
            if thread_ts:
                payload["thread_ts"] = thread_ts
            data = await self._post(session, "chat.postMessage", **payload)
            ts = data.get("ts", "")
            return {
                "channel": data.get("channel", channel_id),
                "channel_name": channel_name,
                "ts": ts,
                "thread_ts": thread_ts or ts,
            }

    # -- public read methods --

    async def search_messages(
        self,
        query: str,
        limit: int = 20,
        *,
        channel_types: list[str] | None = None,
        include_context_messages: bool = False,
    ) -> list[RawItem]:
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
            messages = data.get("results", {}).get("messages", [])
            items: list[RawItem] = []
            for m in messages:
                cid = m.get("channel_id") or m.get("channel", {}).get("id", "")
                cname = m.get("channel_name") or m.get("channel", {}).get("name", "")
                ts = m.get("ts", "")
                user_id = m.get("user_id") or m.get("user", "")
                user_name = m.get("author_user_name") or await self._resolve_user(session, user_id)
                created = _ts_to_datetime(ts)
                items.append(
                    RawItem(
                        source=self.name,
                        source_id=f"{cid}:{ts}",
                        title=f"#{cname} — {user_name}",
                        content=m.get("content") or m.get("text", ""),
                        created_at=created,
                        updated_at=created,
                        metadata={
                            "channel_id": cid,
                            "channel_name": cname,
                            "user_id": user_id,
                            "user_name": user_name,
                            "ts": ts,
                            "permalink": m.get("permalink", ""),
                            "score": m.get("score"),
                        },
                    )
                )
            return items

    async def search_channels(self, query: str | None = None, limit: int = 50) -> list[dict[str, str]]:
        async with aiohttp.ClientSession() as session:
            await self._refresh_channel_index(session)
            results = []
            q = query.lower() if query else None
            for cid, cname in self._channel_name_cache.items():
                if q and q not in cname.lower():
                    continue
                results.append({"id": cid, "name": cname})
                if len(results) >= limit:
                    break
            return results

    async def list_dms(self, query: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """List open DMs (1-on-1) with resolved peer names.

        Requires `im:read` scope on the read token. Does NOT include group DMs (mpim).
        """
        async with aiohttp.ClientSession() as session:
            if not self._dm_index_loaded:
                await self._refresh_channel_index(session, types="im")
            results: list[dict[str, Any]] = []
            q = query.lower() if query else None
            for cid, peer_id in self._dm_peer_by_channel.items():
                peer_name = await self._resolve_user(session, peer_id)
                if q and q not in peer_name.lower() and q not in peer_id.lower():
                    continue
                results.append({"channel_id": cid, "user_id": peer_id, "peer": peer_name})
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
            cid = data.get("channel", {}).get("id", "")
            if cid:
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
            raise RuntimeError(f"No Slack user found matching {target!r}")
        normalized = stripped.casefold()
        exact = [
            user
            for user in users
            if normalized
            in {user.get("name", "").casefold(), user.get("username", "").casefold(), user.get("email", "").casefold()}
        ]
        candidates = exact or users
        if len(candidates) != 1:
            refs = ", ".join(f"{user['name']} ({user['id']})" for user in candidates[:10])
            raise RuntimeError(f"Ambiguous Slack user {target!r}; choose an exact user id: {refs}")
        user_id = candidates[0]["id"]
        return await self.open_dm(user_id)

    async def search_users(self, query: str | None = None, limit: int = 50) -> list[dict[str, str]]:
        async with aiohttp.ClientSession() as session:
            results = []
            q = query.lower() if query else None
            cursor = ""
            while len(results) < limit:
                data = await self._get(session, "users.list", limit="200", cursor=cursor)
                for m in data.get("members", []):
                    if m.get("deleted") or m.get("is_bot"):
                        continue
                    profile = m.get("profile", {})
                    name = m.get("real_name") or m.get("name", "")
                    email = profile.get("email", "")
                    username = m.get("name", "")
                    if q and q not in name.lower() and q not in email.lower() and q not in username.lower():
                        continue
                    results.append(
                        {
                            "id": m.get("id", ""),
                            "name": name,
                            "username": username,
                            "email": email,
                            "title": profile.get("title", ""),
                        }
                    )
                    if len(results) >= limit:
                        break
                cursor = data.get("response_metadata", {}).get("next_cursor", "")
                if not cursor:
                    break
            return results

    async def read_channel(self, channel: str, limit: int = 50) -> list[RawItem]:
        async with aiohttp.ClientSession() as session:
            cid, cname = await self._resolve_channel_id(session, channel)
            data = await self._get(session, "conversations.history", channel=cid, limit=str(limit))
            items: list[RawItem] = []
            for m in data.get("messages", []):
                ts = m.get("ts", "")
                user_id = m.get("user", "")
                user_name = await self._resolve_user(session, user_id) if user_id else (m.get("username") or "bot")
                created = _ts_to_datetime(ts)
                items.append(
                    RawItem(
                        source=self.name,
                        source_id=f"{cid}:{ts}",
                        title=f"#{cname} — {user_name}",
                        content=m.get("text", ""),
                        created_at=created,
                        updated_at=created,
                        metadata={
                            "channel_id": cid,
                            "channel_name": cname,
                            "user_id": user_id,
                            "user_name": user_name,
                            "ts": ts,
                            "thread_ts": m.get("thread_ts"),
                            "reply_count": m.get("reply_count", 0),
                        },
                    )
                )
            return items

    async def history_since(
        self,
        channel: str,
        oldest: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Fetch channel messages since `oldest`, ordered oldest -> newest.

        conversations.history returns newest-first per page; we page via
        response_metadata.next_cursor and reverse to chronological order.
        """
        async with aiohttp.ClientSession() as session:
            cid, _ = await self._resolve_channel_id(session, channel)
            messages: list[dict[str, Any]] = []
            cursor = ""
            while True:
                params: dict[str, Any] = {"channel": cid, "limit": str(limit)}
                if oldest:
                    params["oldest"] = oldest
                if cursor:
                    params["cursor"] = cursor
                data = await self._get(session, "conversations.history", **params)
                messages.extend(data.get("messages", []))
                cursor = data.get("response_metadata", {}).get("next_cursor", "")
                if not cursor:
                    break
            messages.reverse()
            return messages

    async def resolve_channel(self, name: str) -> tuple[str, str]:
        """Public wrapper over _resolve_channel_id. Returns (channel_id, channel_name); raises on miss."""
        async with aiohttp.ClientSession() as session:
            return await self._resolve_channel_id(session, name)

    async def resolve_user(self, name: str) -> dict[str, str] | list[dict[str, str]]:
        """Resolve a user name to a single {id, name} or, on 0/>1 matches, the candidate list."""
        candidates = await self.search_users(name)
        q = name.lower()
        exact = [c for c in candidates if c["username"].lower() == q or c["name"].lower() == q]
        if len(exact) == 1:
            return {"id": exact[0]["id"], "name": exact[0]["name"]}
        return candidates

    async def resolve_user_name(self, user_id: str) -> str:
        """Public wrapper over _resolve_user. Returns a display name (cached); falls back to the id."""
        async with aiohttp.ClientSession() as session:
            return await self._resolve_user(session, user_id)

    async def whoami(self) -> dict[str, str]:
        """auth.test -> the authed identity used for self-message filtering."""
        async with aiohttp.ClientSession() as session:
            data = await self._get(session, "auth.test")
            return {"user_id": data.get("user_id", ""), "user": data.get("user", "")}

    async def read_thread(self, source_id: str) -> SlackThreadResult | None:
        """Read a message + thread replies. source_id is 'channel_id:ts'."""
        if ":" not in source_id:
            return None
        cid, ts = source_id.split(":", 1)
        async with aiohttp.ClientSession() as session:
            try:
                data = await self._get(session, "conversations.replies", channel=cid, ts=ts)
            except RuntimeError as e:
                _logger.warning("Slack read_thread failed: %s", e)
                return None
            messages = data.get("messages", [])
            if not messages:
                return None
            _, cname = await self._resolve_channel_id(session, cid)
            lines: list[str] = []
            model_content: list[ImageContent] = []
            for m in messages:
                user_id = m.get("user", "")
                user_name = await self._resolve_user(session, user_id) if user_id else (m.get("username") or "bot")
                text = m.get("text", "")
                remaining = _MAX_THREAD_IMAGES - len(model_content)
                file_notes, image_blocks = await self._extract_thread_images(session, m.get("files"), remaining)
                if file_notes:
                    text = "\n".join(part for part in [text, *file_notes] if part)
                model_content.extend(image_blocks)
                lines.append(_format_message(user_name, text, m.get("ts", ""), cname))
            return SlackThreadResult(text="\n\n".join(lines), model_content=tuple(model_content))

    async def read_file_image(self, file_id: str) -> SlackThreadResult | None:
        async with aiohttp.ClientSession() as session:
            data = await self._get(session, "files.info", file=file_id)
            file_obj = data.get("file")
            if not isinstance(file_obj, dict) or not self._is_image_file(file_obj):
                return None
            block = await self._download_slack_image(session, file_obj)
            if not block:
                return None
            title = file_obj.get("title") or file_obj.get("name") or file_obj.get("id") or file_id
            return SlackThreadResult(
                text=f"Slack image: {title}\n{self._format_image_note(file_obj)}",
                model_content=(block,),
            )

    async def _extract_thread_images(
        self,
        session: aiohttp.ClientSession,
        files: Any,
        remaining: int,
    ) -> tuple[list[str], list[ImageContent]]:
        if not isinstance(files, list):
            return [], []

        notes: list[str] = []
        image_blocks: list[ImageContent] = []
        for file_obj in files:
            if not isinstance(file_obj, dict) or not self._is_image_file(file_obj):
                continue
            notes.append(self._format_image_note(file_obj))
            if len(image_blocks) >= remaining:
                continue
            block = await self._download_slack_image(session, file_obj)
            if block:
                image_blocks.append(block)
        return notes, image_blocks

    def _is_image_file(self, file_obj: dict[str, Any]) -> bool:
        return self._normalized_image_mime(file_obj) is not None

    def _normalized_image_mime(self, file_obj: dict[str, Any]) -> str | None:
        mime = str(file_obj.get("mimetype") or "").lower()
        if mime == "image/jpg":
            return "image/jpeg"
        if mime in _SLACK_IMAGE_MIME_TYPES:
            return mime
        return None

    def _format_image_note(self, file_obj: dict[str, Any]) -> str:
        title = file_obj.get("title") or file_obj.get("name") or file_obj.get("id") or "image"
        details = [self._normalized_image_mime(file_obj) or "image"]
        if size := file_obj.get("size"):
            details.append(f"{size} bytes")
        if file_id := file_obj.get("id"):
            details.append(f"id: {file_id}")
        return f"Attached image: {title} ({', '.join(details)})"

    async def _download_slack_image(
        self,
        session: aiohttp.ClientSession,
        file_obj: dict[str, Any],
    ) -> ImageContent | None:
        mime = self._normalized_image_mime(file_obj)
        if not mime:
            return None
        if mime not in _MODEL_IMAGE_MIME_TYPES:
            return None
        size = _int_or_none(file_obj.get("size"))
        if size and size > _MAX_SLACK_IMAGE_BYTES:
            return None
        url = file_obj.get("url_private_download") or file_obj.get("url_private")
        if not isinstance(url, str) or not url:
            return None
        headers = {"Authorization": f"Bearer {self._token_for('files.info')}"}
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status >= 400:
                    _logger.warning("Slack image download failed with status %s", resp.status)
                    return None
                body = await resp.read()
        except Exception:
            _logger.warning("Slack image download failed", exc_info=True)
            return None
        if len(body) > _MAX_SLACK_IMAGE_BYTES:
            return None
        detected_mime = _detect_supported_image_mime(body)
        if not detected_mime:
            _logger.warning("Slack image download did not contain supported image bytes")
            return None
        return ImageContent(media_type=detected_mime, data=base64.b64encode(body).decode("ascii"))

    async def read_user(self, user_id: str) -> dict[str, Any] | None:
        async with aiohttp.ClientSession() as session:
            try:
                data = await self._get(session, "users.info", user=user_id)
            except RuntimeError as e:
                _logger.warning("Slack read_user failed: %s", e)
                return None
            user = data.get("user")
            if not user:
                return None
            profile = user.get("profile", {})
            return {
                "id": user.get("id", ""),
                "name": user.get("real_name") or user.get("name", ""),
                "username": user.get("name", ""),
                "email": profile.get("email", ""),
                "title": profile.get("title", ""),
                "status_text": profile.get("status_text", ""),
                "status_emoji": profile.get("status_emoji", ""),
                "tz": user.get("tz", ""),
            }


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
