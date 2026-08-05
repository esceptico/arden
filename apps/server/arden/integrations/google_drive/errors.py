"""Safe Google Drive provider-error translation."""

import json
from typing import NoReturn

import httplib2
from google.auth.exceptions import GoogleAuthError, RefreshError
from googleapiclient.errors import HttpError
from requests import RequestException

from arden.integrations.base import IntegrationConnectionError, IntegrationOperationError
from arden.integrations.google_auth.auth import SCOPES_DRIVE

KNOWN_GOOGLE_DRIVE_ERRORS = (
    HttpError,
    GoogleAuthError,
    RequestException,
    OSError,
    httplib2.ServerNotFoundError,
)
_RATE_LIMIT_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"})
_MAX_RETRY_AFTER_SECONDS = 86_400
_REQUIRED_SCOPES = tuple(SCOPES_DRIVE)


class GoogleDrivePayloadError(IntegrationOperationError):
    """Google Drive returned a successful response that violates its contract."""

    def __init__(self, operation: str):
        super().__init__(
            code="invalid_provider_payload",
            safe_message=f"Google Drive returned an invalid response for {operation}.",
        )


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
        reason
        for item in errors
        if isinstance(item, dict) and isinstance((reason := item.get("reason")), str)
    )


def _retry_after_seconds(error: HttpError) -> int | None:
    raw = error.resp.get("retry-after")
    if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal():
        return None
    seconds = int(raw)
    return seconds if seconds <= _MAX_RETRY_AFTER_SECONDS else None


def raise_google_drive_error(error: Exception, *, account_ref: str) -> NoReturn:
    if isinstance(error, RefreshError):
        raise IntegrationConnectionError(
            integration_id="google_drive",
            reason="auth_required",
            detail=f"Google Drive authorization failed for {account_ref}. Reconnect that account.",
            retry_safe=True,
            account_ref=account_ref,
        ) from error
    if not isinstance(error, HttpError):
        raise IntegrationOperationError(
            code="provider_error",
            safe_message="Google Drive provider request failed.",
            retryable=True,
        ) from error

    status = error.resp.status
    reasons = _provider_reasons(error)
    if status == 401:
        raise IntegrationConnectionError(
            integration_id="google_drive",
            reason="auth_required",
            detail=f"Google Drive authorization expired for {account_ref}. Reconnect that account.",
            retry_safe=True,
            account_ref=account_ref,
        ) from error
    if "insufficientPermissions" in reasons:
        raise IntegrationConnectionError(
            integration_id="google_drive",
            reason="scope_required",
            detail=f"Google Drive authorization for {account_ref} is missing required permissions.",
            required_scopes=_REQUIRED_SCOPES,
            retry_safe=True,
            account_ref=account_ref,
        ) from error
    if status == 429 or reasons & _RATE_LIMIT_REASONS:
        raise IntegrationOperationError(
            code="rate_limited",
            safe_message="Google Drive rate limit exceeded.",
            retryable=True,
            retry_after_seconds=_retry_after_seconds(error),
        ) from error
    if status == 404:
        raise IntegrationOperationError(
            code="not_found",
            safe_message="Google Drive file not found. Search again and use an exact returned file reference.",
            retryable=False,
        ) from error
    raise IntegrationOperationError(
        code="provider_error",
        safe_message="Google Drive provider request failed.",
        retryable=status in {408, 500, 502, 503, 504},
    ) from error
