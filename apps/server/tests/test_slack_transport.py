import json

import pytest

from arden.integrations.base import IntegrationConnectionError, IntegrationOperationError
from arden.integrations.slack.transport import SlackPayloadError, SlackTransport
from arden.integrations.tool_errors import operation_error_result


class FakeResponse:
    def __init__(self, *, status: int, headers: dict[str, str], payload: object = None, error=None):
        self.status = status
        self.headers = headers
        self._payload = payload
        self._error = error

    async def json(self):
        if self._error is not None:
            raise self._error
        return self._payload


def _transport() -> SlackTransport:
    return SlackTransport(bot_token="xoxb-test", user_token="xoxp-test")


def test_message_post_uses_available_bot_token_but_search_requires_user_token():
    transport = SlackTransport(bot_token="xoxb-test", user_token=None)

    assert transport.token_for("chat.postMessage") == "xoxb-test"
    with pytest.raises(IntegrationConnectionError, match="requires a user token"):
        transport.token_for("assistant.search.context")


def test_rate_limit_error_is_canonical_and_preserves_retry_after():
    with pytest.raises(IntegrationOperationError) as raised:
        _transport().raise_for_error(
            "assistant.search.context",
            {"error": "ratelimited"},
            {"retry-after": "37"},
        )

    assert raised.value.code == "rate_limited"
    assert raised.value.retryable is True
    assert raised.value.retry_after_seconds == 37
    result = operation_error_result(raised.value, preview="Search failed")
    assert result.data == {"retry_after_seconds": 37}


def test_expired_token_routes_to_connection_recovery():
    with pytest.raises(IntegrationConnectionError) as raised:
        _transport().raise_for_error("users.list", {"error": "token_expired"}, {})

    assert raised.value.reason == "auth_required"


@pytest.mark.parametrize(
    ("method", "retryable"),
    [("conversations.history", True), ("chat.postMessage", False)],
)
def test_transient_provider_errors_do_not_make_mutations_blindly_retryable(method, retryable):
    with pytest.raises(IntegrationOperationError) as raised:
        _transport().raise_for_error(method, {"error": "service_unavailable"}, {})

    assert raised.value.code == "provider_error"
    assert raised.value.retryable is retryable


@pytest.mark.asyncio
async def test_http_rate_limit_requires_and_preserves_retry_after():
    response = FakeResponse(status=429, headers={"Retry-After": "12"})

    with pytest.raises(IntegrationOperationError) as raised:
        await _transport()._decode_response("users.list", response)

    assert raised.value.code == "rate_limited"
    assert raised.value.retry_after_seconds == 12


@pytest.mark.asyncio
async def test_http_rate_limit_without_retry_after_is_invalid_provider_data():
    response = FakeResponse(status=429, headers={})

    with pytest.raises(SlackPayloadError, match="omitted Retry-After"):
        await _transport()._decode_response("users.list", response)


@pytest.mark.asyncio
async def test_success_with_malformed_json_is_invalid_provider_data():
    response = FakeResponse(
        status=200,
        headers={},
        error=json.JSONDecodeError("bad JSON", "{", 0),
    )

    with pytest.raises(SlackPayloadError, match="not valid JSON"):
        await _transport()._decode_response("users.list", response)


@pytest.mark.asyncio
async def test_http_status_is_classified_before_body_parsing():
    response = FakeResponse(status=503, headers={}, error=AssertionError("must not parse"))

    with pytest.raises(IntegrationOperationError) as raised:
        await _transport()._decode_response("conversations.history", response)

    assert raised.value.code == "provider_error"
    assert raised.value.retryable is True
