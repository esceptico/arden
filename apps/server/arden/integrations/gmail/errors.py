import json
from typing import NoReturn

import httplib2
from googleapiclient.errors import HttpError

from arden.integrations.base import (
    IntegrationConnectionError,
    IntegrationOperationError,
)

_RATE_LIMIT_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"})
_MAX_RETRY_AFTER_SECONDS = 86_400


def _provider_reasons(error: HttpError) -> frozenset[str]:
    try:
        payload = json.loads(error.content)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return frozenset()
    if not isinstance(payload, dict):
        return frozenset()
    detail = payload.get("error")
    if not isinstance(detail, dict):
        return frozenset()
    errors = detail.get("errors")
    if not isinstance(errors, list):
        return frozenset()
    return frozenset(
        reason for item in errors if isinstance(item, dict) and isinstance((reason := item.get("reason")), str)
    )


def _retry_after_seconds(error: HttpError) -> int | None:
    raw = error.resp.get("retry-after")
    if not isinstance(raw, str) or not raw or not raw.isascii() or not raw.isdecimal():
        return None
    seconds = int(raw)
    return seconds if seconds <= _MAX_RETRY_AFTER_SECONDS else None


def raise_gmail_error(
    error: HttpError | OSError | httplib2.ServerNotFoundError,
    *,
    required_scopes: tuple[str, ...],
    account_ref: str,
) -> NoReturn:
    if not isinstance(error, HttpError):
        raise IntegrationOperationError(
            code="provider_error",
            safe_message="Gmail provider request failed.",
            retryable=True,
        ) from error

    status = error.resp.status
    reasons = _provider_reasons(error)
    if status == 401:
        raise IntegrationConnectionError(
            integration_id="gmail",
            reason="auth_required",
            detail="Gmail authorization expired or was revoked.",
            retry_safe=True,
            account_ref=account_ref,
        ) from error
    if "insufficientPermissions" in reasons:
        raise IntegrationConnectionError(
            integration_id="gmail",
            reason="scope_required",
            detail="Gmail authorization is missing required permissions.",
            required_scopes=required_scopes,
            retry_safe=True,
            account_ref=account_ref,
        ) from error
    if status == 429 or reasons & _RATE_LIMIT_REASONS:
        raise IntegrationOperationError(
            code="rate_limited",
            safe_message="Gmail rate limit exceeded.",
            retryable=True,
            retry_after_seconds=_retry_after_seconds(error),
        ) from error
    if status == 404:
        raise IntegrationOperationError(
            code="not_found",
            safe_message="Gmail message not found. Search again and use an exact returned message_ref.",
            retryable=False,
        ) from error
    raise IntegrationOperationError(
        code="provider_error",
        safe_message="Gmail provider request failed.",
        retryable=status in {408, 500, 502, 503, 504},
    ) from error
