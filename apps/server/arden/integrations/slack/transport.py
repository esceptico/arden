import json
from collections.abc import AsyncIterator
from typing import Any, Literal

import aiohttp

from arden.integrations.base import IntegrationConnectionError, IntegrationOperationError
from arden.integrations.slack.models import SlackAuthResult, SlackIdentity

_API = "https://slack.com/api"
_MAX_CURSOR_PAGES = 100
_USER_TOKEN_METHODS = frozenset({"assistant.search.context"})
_MUTATION_METHODS = frozenset({"chat.postMessage", "conversations.open"})
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
_AUTH_ERRORS = frozenset({"account_inactive", "invalid_auth", "not_authed", "token_expired", "token_revoked"})
_NOT_FOUND_ERRORS = frozenset(
    {"channel_not_found", "file_not_found", "message_not_found", "thread_not_found", "user_not_found"}
)
_PERMISSION_ERRORS = frozenset({"access_denied", "accesslimited", "no_permission", "not_in_channel"})
_TRANSIENT_ERRORS = frozenset({"fatal_error", "internal_error", "request_timeout", "service_unavailable"})
_RATE_LIMIT_ERRORS = frozenset({"rate_limited", "ratelimited"})


class SlackPayloadError(IntegrationOperationError):
    """Slack returned a successful response with an unusable payload."""

    def __init__(self, *, context: str, detail: str):
        super().__init__(
            code="invalid_provider_response",
            safe_message=f"Slack returned invalid data for {context}: {detail}",
            retryable=False,
        )


def payload_error(context: str, detail: str) -> SlackPayloadError:
    return SlackPayloadError(context=context, detail=detail)


def mapping(value: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise payload_error(context, "expected object")
    return value


def required_string(payload: dict[str, Any], key: str, *, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise payload_error(context, f"expected non-empty string {key!r}")
    return value


def required_string_allow_empty(payload: dict[str, Any], key: str, *, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise payload_error(context, f"expected string {key!r}")
    return value


def optional_string(payload: dict[str, Any], key: str, *, context: str) -> str | None:
    if key not in payload or payload[key] is None:
        return None
    value = payload[key]
    if not isinstance(value, str):
        raise payload_error(context, f"expected string {key!r}")
    return value or None


def optional_bool(payload: dict[str, Any], key: str, *, context: str) -> bool | None:
    if key not in payload or payload[key] is None:
        return None
    value = payload[key]
    if not isinstance(value, bool):
        raise payload_error(context, f"expected boolean {key!r}")
    return value


def optional_mapping(payload: dict[str, Any], key: str, *, context: str) -> dict[str, Any] | None:
    if key not in payload or payload[key] is None:
        return None
    return mapping(payload[key], context=f"{context}.{key}")


def optional_int(payload: dict[str, Any], key: str, *, context: str) -> int | None:
    if key not in payload or payload[key] is None:
        return None
    value = payload[key]
    if type(value) is not int:
        raise payload_error(context, f"expected integer {key!r}")
    return value


def required_bool(payload: dict[str, Any], key: str, *, context: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise payload_error(context, f"expected boolean {key!r}")
    return value


def required_list(payload: dict[str, Any], key: str, *, context: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise payload_error(context, f"expected array {key!r}")
    return value


def next_cursor(payload: dict[str, Any], *, context: str) -> str:
    metadata = mapping(payload.get("response_metadata"), context=f"{context}.response_metadata")
    cursor = required_string_allow_empty(metadata, "next_cursor", context=f"{context}.response_metadata")
    if len(cursor) > 4096 or any(ord(character) < 32 or ord(character) == 127 for character in cursor):
        raise payload_error(f"{context}.response_metadata", "next_cursor was not a valid opaque ref")
    return cursor


def _header(headers: dict[str, str], name: str) -> str | None:
    normalized_name = name.casefold()
    return next((value for key, value in headers.items() if key.casefold() == normalized_name), None)


def _retry_after_seconds(headers: dict[str, str], *, context: str) -> int | None:
    value = _header(headers, "Retry-After")
    if value is None:
        return None
    try:
        seconds = int(value)
    except ValueError as exc:
        raise payload_error(context, "Retry-After must be an integer number of seconds") from exc
    if seconds < 0:
        raise payload_error(context, "Retry-After must not be negative")
    return seconds


class SlackTransport:
    def __init__(self, *, bot_token: str | None, user_token: str | None):
        read_token = user_token or bot_token
        if not read_token:
            raise ValueError("SlackClient requires at least one of bot_token or user_token")
        self._bot_token = bot_token
        self._user_token = user_token
        self._read_token = read_token

    def token_for(self, method: str) -> str:
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

    def raise_for_error(self, method: str, data: dict[str, Any], headers: dict[str, str]) -> None:
        error = required_string(data, "error", context=method)
        body_needed = optional_string(data, "needed", context=method)
        body_provided = optional_string(data, "provided", context=method)
        needed = body_needed or _header(headers, "X-Accepted-OAuth-Scopes")
        provided = body_provided or _header(headers, "X-OAuth-Scopes")
        token_kind = "user" if method in _USER_TOKEN_METHODS or self.token_for(method) == self._user_token else "bot"
        message = f"Slack API {method} failed: {error}"
        if needed:
            message += f" — needs scope: {needed}"
        if provided:
            message += f" (have: {provided})"
        message += f" [{token_kind} token]"
        if error in _AUTH_ERRORS:
            raise IntegrationConnectionError(
                integration_id="slack",
                reason="auth_required",
                detail=message,
                retry_safe=True,
            )
        if error == "missing_scope":
            required_scopes = tuple(scope.strip() for scope in (needed or "").split(",") if scope.strip())
            raise IntegrationConnectionError(
                integration_id="slack",
                reason="scope_required",
                detail=message,
                required_scopes=required_scopes,
                retry_safe=True,
            )
        if error in _RATE_LIMIT_ERRORS:
            retry_after = _retry_after_seconds(headers, context=method)
            raise IntegrationOperationError(
                code="rate_limited",
                safe_message=message,
                retryable=True,
                retry_after_seconds=retry_after,
            )
        if error in _NOT_FOUND_ERRORS:
            code = "not_found"
        elif error in _PERMISSION_ERRORS:
            code = "permission_denied"
        else:
            code = "provider_error"
        retryable = error in _TRANSIENT_ERRORS and method not in _MUTATION_METHODS
        raise IntegrationOperationError(code=code, safe_message=message, retryable=retryable)

    async def _decode_response(self, method: str, response: aiohttp.ClientResponse) -> dict[str, Any]:
        headers = dict(response.headers)
        if response.status == 429:
            retry_after = _retry_after_seconds(headers, context=method)
            if retry_after is None:
                raise payload_error(method, "HTTP 429 response omitted Retry-After")
            raise IntegrationOperationError(
                code="rate_limited",
                safe_message=f"Slack API {method} was rate limited.",
                retryable=True,
                retry_after_seconds=retry_after,
            )
        if response.status == 401:
            raise IntegrationConnectionError(
                integration_id="slack",
                reason="auth_required",
                detail=f"Slack API {method} rejected the configured token.",
                retry_safe=True,
            )
        if response.status >= 400:
            raise IntegrationOperationError(
                code="provider_error",
                safe_message=f"Slack API {method} failed with HTTP {response.status}.",
                retryable=response.status in {408, 500, 502, 503, 504} and method not in _MUTATION_METHODS,
            )
        try:
            raw = await response.json()
        except (aiohttp.ContentTypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise payload_error(method, "response was not valid JSON") from exc
        data = mapping(raw, context=method)
        if not required_bool(data, "ok", context=method):
            self.raise_for_error(method, data, headers)
        return data

    @staticmethod
    def _request_failure(method: str, exc: BaseException) -> IntegrationOperationError:
        code = "provider_timeout" if isinstance(exc, TimeoutError) else "provider_error"
        return IntegrationOperationError(
            code=code,
            safe_message=f"Slack {method} request failed.",
            retryable=method not in _MUTATION_METHODS,
        )

    async def get(self, session: aiohttp.ClientSession, method: str, **params: Any) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.token_for(method)}"}
        try:
            async with session.get(
                f"{_API}/{method}", headers=headers, params=params, timeout=_REQUEST_TIMEOUT
            ) as response:
                return await self._decode_response(method, response)
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise self._request_failure(method, exc) from exc

    async def post(self, session: aiohttp.ClientSession, method: str, **payload: Any) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.token_for(method)}",
            "Content-Type": "application/json; charset=utf-8",
        }
        try:
            async with session.post(
                f"{_API}/{method}",
                headers=headers,
                json=payload,
                timeout=_REQUEST_TIMEOUT,
            ) as response:
                return await self._decode_response(method, response)
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise self._request_failure(method, exc) from exc

    async def cursor_pages(
        self,
        session: aiohttp.ClientSession,
        method: str,
        *,
        page_limit: int = _MAX_CURSOR_PAGES,
        **params: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        cursor = ""
        seen_cursors: set[str] = set()
        for _page in range(page_limit):
            page_params = dict(params)
            page_params["cursor"] = cursor
            data = await self.get(session, method, **page_params)
            yield data
            cursor = next_cursor(data, context=method)
            if not cursor:
                return
            if cursor in seen_cursors:
                raise SlackPayloadError(context=f"{method} pagination", detail=f"cursor {cursor!r} repeated")
            seen_cursors.add(cursor)
        raise IntegrationOperationError(
            code="pagination_limit",
            safe_message=f"Slack {method} exceeded the {page_limit}-page safety limit.",
            retryable=False,
        )

    async def auth_test(self, token_kind: Literal["bot", "user", "read"] = "read") -> SlackAuthResult:
        method = "auth.test"
        if token_kind == "bot":
            if not self._bot_token:
                raise ValueError("Slack auth.test requires a bot token (xoxb-)")
            token = self._bot_token
        elif token_kind == "user":
            if not self._user_token:
                raise ValueError("Slack auth.test requires a user token (xoxp-)")
            token = self._user_token
        else:
            token = self._read_token
        headers = {"Authorization": f"Bearer {token}"}
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.get(f"{_API}/{method}", headers=headers, timeout=_REQUEST_TIMEOUT) as response,
            ):
                data = await self._decode_response(method, response)
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise self._request_failure(method, exc) from exc
        return SlackAuthResult(
            team_name=optional_string(data, "team", context=method),
            team_ref=optional_string(data, "team_id", context=method),
            user_ref=required_string(data, "user_id", context=method),
            user_name=required_string(data, "user", context=method),
            bot_ref=optional_string(data, "bot_id", context=method),
        )

    async def whoami(self) -> SlackIdentity:
        identity = await self.auth_test()
        return SlackIdentity(user_ref=identity.user_ref, user_name=identity.user_name)
