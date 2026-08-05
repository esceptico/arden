import json

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from arden.integrations.base import IntegrationConnectionError, IntegrationOperationError
from arden.integrations.google_drive.errors import raise_google_drive_error


def _http_error(status: int, reason: str | None = None, *, retry_after: str | None = None) -> HttpError:
    errors = [] if reason is None else [{"reason": reason}]
    headers = {"status": str(status)}
    if retry_after is not None:
        headers["retry-after"] = retry_after
    return HttpError(
        Response(headers),
        json.dumps({"error": {"errors": errors}}).encode(),
    )


@pytest.mark.parametrize(
    ("status", "reason", "expected"),
    [
        (429, None, "rate_limited"),
        (403, "rateLimitExceeded", "rate_limited"),
        (404, None, "not_found"),
        (503, None, "provider_error"),
    ],
)
def test_google_drive_operation_errors_are_classified(status: int, reason: str | None, expected: str):
    with pytest.raises(IntegrationOperationError) as raised:
        raise_google_drive_error(
            _http_error(status, reason),
            account_ref="user@example.com",
        )

    assert raised.value.code == expected
    assert raised.value.retryable is (status in {429, 503} or reason == "rateLimitExceeded")


@pytest.mark.parametrize(
    ("status", "reason", "expected"),
    [
        (401, None, "auth_required"),
        (403, "insufficientPermissions", "scope_required"),
    ],
)
def test_google_drive_connection_errors_name_the_account(
    status: int,
    reason: str | None,
    expected: str,
):
    with pytest.raises(IntegrationConnectionError) as raised:
        raise_google_drive_error(
            _http_error(status, reason),
            account_ref="user@example.com",
        )

    assert raised.value.reason == expected
    assert raised.value.account_ref == "user@example.com"
    if expected == "scope_required":
        assert raised.value.required_scopes
        assert isinstance(raised.value.required_scopes, tuple)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("120", 120), ("0", 0), (" 120", None), ("1.5", None), ("86401", None), ("١٢", None)],
)
def test_google_drive_retry_after_is_strict_and_bounded(raw: str, expected: int | None):
    with pytest.raises(IntegrationOperationError) as raised:
        raise_google_drive_error(
            _http_error(429, retry_after=raw),
            account_ref="user@example.com",
        )

    assert raised.value.retry_after_seconds == expected
