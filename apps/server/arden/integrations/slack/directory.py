import aiohttp

from arden.integrations.base import IntegrationOperationError
from arden.integrations.slack.models import SlackChannel, SlackDirectMessage, SlackUser, SlackUserProfile
from arden.integrations.slack.transport import (
    SlackTransport,
    mapping,
    optional_bool,
    optional_mapping,
    optional_string,
    payload_error,
    required_bool,
    required_list,
    required_string,
)


class SlackDirectory:
    def __init__(self, transport: SlackTransport):
        self._transport = transport
        self._users_by_ref: dict[str, SlackUser | None] = {}
        self._searchable_user_refs: tuple[str, ...] = ()
        self._user_directory_loaded = False
        self._profiles_by_ref: dict[str, SlackUserProfile] = {}
        self._channel_name_cache: dict[str, str] = {}
        self._channel_ref_by_name: dict[str, str] = {}
        self._dm_peer_by_channel: dict[str, str] = {}
        self._dm_channel_by_user: dict[str, str] = {}
        self._channel_index_loaded = False
        self._dm_index_loaded = False

    async def user_name(self, session: aiohttp.ClientSession, user_ref: str) -> str:
        return (await self._read_user_in_session(session, user_ref)).name

    @staticmethod
    def _decode_directory_user(raw_member: object) -> tuple[str, SlackUser | None, bool]:
        context = "users.list member"
        member = mapping(raw_member, context=context)
        profile = optional_mapping(member, "profile", context=context)
        member_ref = required_string(member, "id", context=context)
        profile_context = f"{context}.profile"
        username = optional_string(member, "name", context=context)
        names = (
            optional_string(profile, "display_name", context=profile_context) if profile is not None else None,
            optional_string(profile, "real_name", context=profile_context) if profile is not None else None,
            optional_string(member, "real_name", context=context),
            username,
        )
        name = next((candidate for candidate in names if candidate is not None), None)
        if name is None:
            return member_ref, None, False
        user = SlackUser(
            ref=member_ref,
            name=name,
            username=username,
            email=optional_string(profile, "email", context=profile_context) if profile is not None else None,
            title=optional_string(profile, "title", context=profile_context) if profile is not None else None,
        )
        deleted = optional_bool(member, "deleted", context=context)
        is_bot = optional_bool(member, "is_bot", context=context)
        return member_ref, user, deleted is not True and is_bot is not True

    async def _load_user_directory_in_session(self, session: aiohttp.ClientSession) -> None:
        """Load the complete directory only for Slack user discovery and DMs."""
        if self._user_directory_loaded:
            return
        users_by_ref: dict[str, SlackUser | None] = {}
        searchable_user_refs: list[str] = []
        async for data in self._transport.cursor_pages(session, "users.list", limit="200"):
            for raw_member in required_list(data, "members", context="users.list"):
                user_ref, user, searchable = self._decode_directory_user(raw_member)
                if user_ref in users_by_ref:
                    raise payload_error("users.list", f"returned duplicate user ref {user_ref!r}")
                users_by_ref[user_ref] = user
                if searchable:
                    searchable_user_refs.append(user_ref)
        self._users_by_ref = users_by_ref
        self._searchable_user_refs = tuple(searchable_user_refs)
        self._user_directory_loaded = True

    async def _read_user_in_session(self, session: aiohttp.ClientSession, user_ref: str) -> SlackUserProfile:
        cached = self._profiles_by_ref.get(user_ref)
        if cached is not None:
            return cached
        data = await self._transport.get(session, "users.info", user=user_ref)
        user = mapping(data.get("user"), context="users.info.user")
        returned_ref = required_string(user, "id", context="users.info.user")
        if returned_ref != user_ref:
            raise payload_error("users.info.user", "returned a different user ref")
        profile = optional_mapping(user, "profile", context="users.info.user")
        profile_context = "users.info.user.profile"
        username = optional_string(user, "name", context="users.info.user")
        names = (
            optional_string(profile, "display_name", context=profile_context) if profile is not None else None,
            optional_string(profile, "real_name", context=profile_context) if profile is not None else None,
            optional_string(user, "real_name", context="users.info.user"),
            username,
        )
        result = SlackUserProfile(
            ref=returned_ref,
            name=next((candidate for candidate in names if candidate is not None), returned_ref),
            username=username or returned_ref,
            email=optional_string(profile, "email", context="users.info.user.profile") if profile is not None else None,
            title=optional_string(profile, "title", context="users.info.user.profile") if profile is not None else None,
            status_text=optional_string(profile, "status_text", context="users.info.user.profile")
            if profile is not None
            else None,
            status_emoji=optional_string(profile, "status_emoji", context="users.info.user.profile")
            if profile is not None
            else None,
            timezone=optional_string(user, "tz", context="users.info.user"),
            deleted=optional_bool(user, "deleted", context="users.info.user"),
            is_bot=optional_bool(user, "is_bot", context="users.info.user"),
            is_stranger=optional_bool(user, "is_stranger", context="users.info.user"),
        )
        self._profiles_by_ref[user_ref] = result
        return result

    async def resolve_channel_in_session(self, session: aiohttp.ClientSession, locator: str) -> SlackChannel:
        channel_ref = locator.removeprefix("#")
        if channel_ref and channel_ref[0] in ("C", "G", "D") and channel_ref.isalnum() and channel_ref.isupper():
            name = self._channel_name_cache.get(channel_ref)
            if name is None:
                data = await self._transport.get(session, "conversations.info", channel=channel_ref)
                record = mapping(data.get("channel"), context="conversations.info.channel")
                returned_ref = required_string(record, "id", context="conversations.info.channel")
                if returned_ref != channel_ref:
                    raise payload_error("conversations.info.channel", "returned a different channel ref")
                name = optional_string(record, "name", context="conversations.info.channel")
                if name is None:
                    if not required_bool(record, "is_im", context="conversations.info.channel"):
                        raise payload_error("conversations.info.channel", "non-DM channel has no name")
                    name = await self.user_name(
                        session,
                        required_string(record, "user", context="conversations.info.channel"),
                    )
                self._channel_name_cache[channel_ref] = name
            return SlackChannel(ref=channel_ref, name=name)

        known_ref = self._channel_ref_by_name.get(channel_ref)
        if known_ref is None:
            await self._refresh_channel_index(session)
            known_ref = self._channel_ref_by_name.get(channel_ref)
        if known_ref is None:
            raise IntegrationOperationError(
                code="not_found",
                safe_message=f"Slack channel {locator!r} was not found.",
                retryable=False,
            )
        return SlackChannel(ref=known_ref, name=channel_ref)

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
        async for data in self._transport.cursor_pages(
            session,
            "conversations.list",
            limit="1000",
            exclude_archived="true",
            types=types,
        ):
            for raw_channel in required_list(data, "channels", context="conversations.list"):
                channel = mapping(raw_channel, context="conversations.list channel")
                channel_ref = required_string(channel, "id", context="conversations.list channel")
                if required_bool(channel, "is_im", context="conversations.list channel"):
                    peer_ref = required_string(channel, "user", context="conversations.list channel")
                    dm_peers[channel_ref] = peer_ref
                    dm_channels[peer_ref] = channel_ref
                else:
                    channel_name = required_string(channel, "name", context="conversations.list channel")
                    channel_names[channel_ref] = channel_name
                    channel_refs[channel_name] = channel_ref
        self._channel_name_cache.update(channel_names)
        self._channel_ref_by_name.update(channel_refs)
        self._dm_peer_by_channel.update(dm_peers)
        self._dm_channel_by_user.update(dm_channels)
        if "im" in types:
            self._dm_index_loaded = True
        if "public_channel" in types and "private_channel" in types:
            self._channel_index_loaded = True

    async def search_channels(self, query: str | None = None, limit: int = 50) -> list[SlackChannel]:
        async with aiohttp.ClientSession() as session:
            if not self._channel_index_loaded:
                await self._refresh_channel_index(session)
            normalized_query = query.casefold() if query else None
            results = [
                SlackChannel(ref=channel_ref, name=name)
                for name, channel_ref in self._channel_ref_by_name.items()
                if normalized_query is None or normalized_query in name.casefold()
            ]
            return results[:limit]

    async def list_dms(self, query: str | None = None, limit: int = 50) -> list[SlackDirectMessage]:
        async with aiohttp.ClientSession() as session:
            if not self._dm_index_loaded:
                await self._refresh_channel_index(session, types="im")
            await self._load_user_directory_in_session(session)
            normalized_query = query.casefold() if query else None
            results: list[SlackDirectMessage] = []
            for channel_ref, peer_ref in self._dm_peer_by_channel.items():
                peer = self._users_by_ref.get(peer_ref)
                if peer is None:
                    peer_name = peer_ref
                else:
                    peer_name = peer.name
                if (
                    normalized_query
                    and normalized_query not in peer_name.casefold()
                    and normalized_query not in peer_ref.casefold()
                ):
                    continue
                results.append(SlackDirectMessage(channel_ref=channel_ref, user_ref=peer_ref, peer_name=peer_name))
                if len(results) == limit:
                    break
            return results

    async def open_dm(self, user_ref: str) -> str:
        async with aiohttp.ClientSession() as session:
            known_channel = self._dm_channel_by_user.get(user_ref)
            if known_channel is not None:
                return known_channel
            data = await self._transport.post(session, "conversations.open", users=user_ref)
            channel = mapping(data.get("channel"), context="conversations.open.channel")
            channel_ref = required_string(channel, "id", context="conversations.open.channel")
            self._dm_peer_by_channel[channel_ref] = user_ref
            self._dm_channel_by_user[user_ref] = channel_ref
            return channel_ref

    async def resolve_dm_target(self, target: str) -> str:
        if target.startswith("D") and target.isalnum() and target.isupper():
            return target
        stripped = target.removeprefix("@")
        if stripped and stripped[0] in ("U", "W") and stripped.isalnum() and stripped.isupper():
            return await self.open_dm(stripped)
        users = await self.search_users(stripped, limit=200)
        normalized = stripped.casefold()
        exact = [
            user
            for user in users
            if normalized
            in {
                user.name.casefold(),
                (user.username or "").casefold(),
                (user.email or "").casefold(),
            }
        ]
        if len(exact) == 1:
            return await self.open_dm(exact[0].ref)
        if not exact:
            raise IntegrationOperationError(
                code="not_found",
                safe_message=f"No exact Slack user matched {target!r}.",
                retryable=False,
            )
        refs = ", ".join(f"{user.name} ({user.ref})" for user in exact[:10])
        raise IntegrationOperationError(
            code="ambiguous_ref",
            safe_message=f"Ambiguous Slack user {target!r}; choose an exact user ref: {refs}",
            retryable=False,
        )

    async def search_users(self, query: str | None = None, limit: int = 50) -> list[SlackUser]:
        async with aiohttp.ClientSession() as session:
            await self._load_user_directory_in_session(session)
            normalized_query = query.casefold() if query else None
            results = [
                self._users_by_ref[user_ref]
                for user_ref in self._searchable_user_refs
                if normalized_query is None
                or any(
                    normalized_query in candidate.casefold()
                    for candidate in (
                        user_ref,
                        self._users_by_ref[user_ref].name,
                        self._users_by_ref[user_ref].username,
                        self._users_by_ref[user_ref].email or "",
                    )
                )
            ]
            return results[:limit]

    async def resolve_channel(self, locator: str) -> SlackChannel:
        async with aiohttp.ClientSession() as session:
            return await self.resolve_channel_in_session(session, locator)

    async def resolve_user(self, locator: str) -> SlackUser:
        direct_ref = locator.removeprefix("@")
        if direct_ref and direct_ref[0] in ("U", "W") and direct_ref.isalnum() and direct_ref.isupper():
            profile = await self.read_user(direct_ref)
            return SlackUser(
                ref=profile.ref,
                name=profile.name,
                username=profile.username,
                email=profile.email,
                title=profile.title,
            )
        candidates = await self.search_users(locator)
        normalized = locator.removeprefix("@").casefold()
        exact = [
            candidate
            for candidate in candidates
            if normalized
            in {
                (candidate.username or "").casefold(),
                candidate.name.casefold(),
                candidate.ref.casefold(),
            }
        ]
        if len(exact) == 1:
            return exact[0]
        if not exact:
            raise IntegrationOperationError(
                code="not_found",
                safe_message=f"No exact Slack user matched {locator!r}.",
                retryable=False,
            )
        choices = ", ".join(
            f"{candidate.name}" + (f" (@{candidate.username})" if candidate.username else "") + f" ({candidate.ref})"
            for candidate in exact
        )
        raise IntegrationOperationError(
            code="ambiguous_ref",
            safe_message=f"Ambiguous Slack user {locator!r}; candidates: {choices}",
            retryable=False,
        )

    async def resolve_user_name(self, user_ref: str) -> str:
        async with aiohttp.ClientSession() as session:
            return await self.user_name(session, user_ref)

    async def read_user(self, user_ref: str) -> SlackUserProfile:
        async with aiohttp.ClientSession() as session:
            return await self._read_user_in_session(session, user_ref)
