import json

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from arden.integrations.base import IntegrationConnectionError, IntegrationOperationError
from arden.integrations.gmail.errors import raise_gmail_error

_READ_SCOPE = ("https://www.googleapis.com/auth/gmail.readonly",)


def _http_error(status: int, reason: str | None = None, *, retry_after: str | None = None) -> HttpError:
    errors = [] if reason is None else [{"reason": reason}]
    headers = {"status": str(status)}
    if retry_after is not None:
        headers["retry-after"] = retry_after
    return HttpError(
        Response(headers),
        json.dumps({"error": {"errors": errors}}).encode(),
    )


def test_gmail_401_requires_reauthorization():
    with pytest.raises(IntegrationConnectionError) as raised:
        raise_gmail_error(
            _http_error(401),
            required_scopes=_READ_SCOPE,
            account_ref="me@example.test",
        )

    assert raised.value.reason == "auth_required"
    assert raised.value.retry_safe is True
    assert raised.value.account_ref == "me@example.test"


def test_gmail_insufficient_permissions_exposes_required_scopes():
    with pytest.raises(IntegrationConnectionError) as raised:
        raise_gmail_error(
            _http_error(403, "insufficientPermissions"),
            required_scopes=_READ_SCOPE,
            account_ref="me@example.test",
        )

    assert raised.value.reason == "scope_required"
    assert raised.value.required_scopes == _READ_SCOPE
    assert raised.value.account_ref == "me@example.test"


@pytest.mark.parametrize("status, reason", [(429, None), (403, "rateLimitExceeded")])
def test_gmail_rate_limits_are_retryable(status: int, reason: str | None):
    with pytest.raises(IntegrationOperationError) as raised:
        raise_gmail_error(
            _http_error(status, reason),
            required_scopes=_READ_SCOPE,
            account_ref="me@example.test",
        )

    assert raised.value.code == "rate_limited"
    assert raised.value.retryable is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("120", 120),
        ("0", 0),
        (" 120", None),
        ("1.5", None),
        ("86401", None),
        ("١٢", None),
    ],
)
def test_gmail_retry_after_is_strict_and_bounded(raw: str, expected: int | None):
    with pytest.raises(IntegrationOperationError) as raised:
        raise_gmail_error(
            _http_error(429, retry_after=raw),
            required_scopes=_READ_SCOPE,
            account_ref="me@example.test",
        )

    assert raised.value.retry_after_seconds == expected


def test_unknown_gmail_forbidden_error_stays_provider_error():
    with pytest.raises(IntegrationOperationError) as raised:
        raise_gmail_error(
            _http_error(403, "domainPolicy"),
            required_scopes=_READ_SCOPE,
            account_ref="me@example.test",
        )

    assert raised.value.code == "provider_error"


def test_gmail_missing_message_is_not_found():
    with pytest.raises(IntegrationOperationError) as raised:
        raise_gmail_error(
            _http_error(404),
            required_scopes=_READ_SCOPE,
            account_ref="me@example.test",
        )

    assert raised.value.code == "not_found"
    assert "Search again" in raised.value.safe_message
