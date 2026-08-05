from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as URLRequest
from urllib.request import urlopen

import httplib2
from google.auth.exceptions import GoogleAuthError, RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from oauthlib.oauth2 import OAuth2Error
from oauthlib.oauth2.rfc6749.tokens import OAuth2Token
from requests import RequestException

from arden.integrations.base import IntegrationConnectionError, IntegrationOperationError
from arden.integrations.google_auth.accounts import (
    GoogleAccount,
    GoogleAccountStore,
    GoogleService,
    validate_google_account_email,
)
from arden.integrations.google_auth.credentials import CREDENTIALS_PATH
from arden.integrations.google_auth.storage import write_private_text
from arden.settings import ARDEN_DIR

SCOPES_GMAIL_READ = ["https://www.googleapis.com/auth/gmail.readonly"]
SCOPES_GMAIL_SEND = ["https://www.googleapis.com/auth/gmail.send"]
SCOPES_CALENDAR = ["https://www.googleapis.com/auth/calendar"]
SCOPES_IDENTITY = ["openid", "https://www.googleapis.com/auth/userinfo.email"]
SCOPES_DRIVE = [
    "https://www.googleapis.com/auth/drive.metadata.readonly",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
]

GOOGLE_SERVICE_SCOPES: dict[GoogleService, list[str]] = {
    "gmail": SCOPES_IDENTITY + SCOPES_GMAIL_READ + SCOPES_GMAIL_SEND,
    "calendar": SCOPES_IDENTITY + SCOPES_CALENDAR,
    "google_drive": SCOPES_IDENTITY + SCOPES_DRIVE,
}

def scopes_for_google_service(service: GoogleService) -> list[str]:
    return list(GOOGLE_SERVICE_SCOPES[service])


def google_account_store() -> GoogleAccountStore:
    return GoogleAccountStore(ARDEN_DIR)


def _credential_scopes(creds: Credentials) -> tuple[str, ...]:
    scopes = creds.scopes
    if scopes is None:
        return ()
    if not isinstance(scopes, (list, tuple)) or any(
        not isinstance(scope, str) or not scope.strip() or scope != scope.strip() for scope in scopes
    ):
        raise IntegrationOperationError(
            code="invalid_response",
            safe_message="Google authorization returned invalid permissions.",
        )
    if len(scopes) != len(set(scopes)):
        raise IntegrationOperationError(
            code="invalid_response",
            safe_message="Google authorization returned duplicate permissions.",
        )
    return tuple(scopes)


def get_google_credentials(
    token_path: Path,
    require_scopes: list[str] | None = None,
    integration_id: str = "google",
) -> Credentials:
    """Load runtime credentials without starting an interactive OAuth flow."""
    if not token_path.exists():
        raise IntegrationConnectionError(
            integration_id=integration_id,
            reason="auth_required",
            detail="Google authorization is missing. Reconnect the account.",
            retry_safe=True,
        )

    try:
        creds = Credentials.from_authorized_user_file(str(token_path))
    except (OSError, TypeError, ValueError) as exc:
        raise IntegrationConnectionError(
            integration_id=integration_id,
            reason="auth_required",
            detail="Google authorization is unreadable. Reconnect the account.",
            retry_safe=True,
        ) from exc
    if not creds.valid:
        if not (creds.expired and creds.refresh_token):
            raise IntegrationConnectionError(
                integration_id=integration_id,
                reason="auth_required",
                detail="Google authorization is invalid. Reconnect the account.",
                retry_safe=True,
            )
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            raise IntegrationConnectionError(
                integration_id=integration_id,
                reason="auth_required",
                detail="Google authorization expired or was revoked. Reconnect the account.",
                retry_safe=True,
            ) from exc
        except (OSError, TransportError) as exc:
            raise IntegrationOperationError(
                code="provider_error",
                safe_message="Google authorization refresh failed.",
                retryable=True,
            ) from exc

        if not creds.valid:
            raise IntegrationConnectionError(
                integration_id=integration_id,
                reason="auth_required",
                detail="Google authorization could not be refreshed. Reconnect the account.",
                retry_safe=True,
            )

        try:
            write_private_text(token_path, creds.to_json())
        except (OSError, TypeError, ValueError) as exc:
            raise IntegrationOperationError(
                code="persistence_error",
                safe_message="Could not persist refreshed Google authorization.",
                retryable=False,
            ) from exc

    granted_scopes = _credential_scopes(creds)
    required_scopes = () if require_scopes is None else tuple(require_scopes)
    missing_scopes = tuple(scope for scope in required_scopes if scope not in granted_scopes)
    if missing_scopes:
        raise IntegrationConnectionError(
            integration_id=integration_id,
            reason="scope_required",
            detail="Google authorization is missing required permissions.",
            required_scopes=missing_scopes,
            retry_safe=True,
        )

    return creds


def has_scope(creds: Credentials, scope: str) -> bool:
    """Check if credentials have a specific scope."""
    return scope in _credential_scopes(creds)


def _run_local_oauth(flow) -> Credentials:
    try:
        return flow.run_local_server(port=0, include_granted_scopes="true")
    except Warning as warning:
        token = warning.token
        old_scopes = warning.old_scope
        new_scopes = warning.new_scope
        if (
            not isinstance(token, OAuth2Token)
            or not isinstance(old_scopes, list)
            or not isinstance(new_scopes, list)
            or any(not isinstance(scope, str) for scope in (*old_scopes, *new_scopes))
        ):
            raise
        old_scope_set = set(old_scopes)
        new_scope_set = set(new_scopes)
        if (
            not old_scope_set < new_scope_set
            or set(token.old_scopes) != old_scope_set
            or set(token.scopes) != new_scope_set
        ):
            raise
        flow.oauth2session.token = dict(token)
        return flow.credentials


def _authorize_google_credentials(
    service: GoogleService,
    requested_scopes: list[str],
    account_ref: str | None,
) -> Credentials:
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), requested_scopes)
    except (FileNotFoundError, ValueError) as exc:
        raise IntegrationOperationError(
            code="configuration_error",
            safe_message="Google OAuth credentials are invalid or unavailable.",
        ) from exc
    try:
        return _run_local_oauth(flow)
    except (OAuth2Error, Warning) as exc:
        raise IntegrationConnectionError(
            integration_id=service,
            reason="auth_required",
            detail="Google authorization did not complete.",
            retry_safe=True,
            account_ref=account_ref,
        ) from exc
    except (GoogleAuthError, RequestException, OSError, httplib2.ServerNotFoundError) as exc:
        raise IntegrationOperationError(
            code="provider_error",
            safe_message="Google authorization service is unavailable.",
            retryable=True,
        ) from exc


def _google_profile_email(
    service: GoogleService,
    credentials: Credentials,
    account_ref: str | None,
) -> str:
    try:
        profile = build("oauth2", "v2", credentials=credentials).userinfo().get().execute()
    except HttpError as exc:
        status = exc.resp.status
        if status in {401, 403}:
            raise IntegrationConnectionError(
                integration_id=service,
                reason="auth_required",
                detail="Google could not verify the authorized account.",
                retry_safe=True,
                account_ref=account_ref,
            ) from exc
        raise IntegrationOperationError(
            code="provider_error",
            safe_message="Google profile request failed.",
            retryable=status in {408, 429, 500, 502, 503, 504},
        ) from exc
    except (GoogleAuthError, RequestException, OSError, httplib2.ServerNotFoundError) as exc:
        raise IntegrationOperationError(
            code="provider_error",
            safe_message="Google profile request failed.",
            retryable=True,
        ) from exc

    if not isinstance(profile, Mapping):
        raise IntegrationOperationError(
            code="invalid_response",
            safe_message="Google profile response was invalid.",
        )
    email = profile.get("email")
    if not isinstance(email, str):
        raise IntegrationOperationError(
            code="invalid_response",
            safe_message="Google profile did not include an email address.",
        )
    try:
        return validate_google_account_email(email)
    except ValueError as exc:
        raise IntegrationOperationError(
            code="invalid_response",
            safe_message="Google profile returned an invalid email address.",
        ) from exc


def authorize_google_service(
    service: GoogleService,
    *,
    account_id: str | None = None,
    expected_account_ref: str | None = None,
    store: GoogleAccountStore | None = None,
) -> GoogleAccount:
    if not CREDENTIALS_PATH.exists():
        raise IntegrationOperationError(
            code="configuration_error",
            safe_message="Google OAuth credentials are not configured.",
        )
    if store is None:
        store = google_account_store()
    requested = scopes_for_google_service(service)
    target_account_ref = expected_account_ref
    if target_account_ref is not None:
        try:
            validate_google_account_email(target_account_ref)
        except ValueError as exc:
            raise IntegrationOperationError(
                code="invalid_ref",
                safe_message="Google account_ref must be an exact email address.",
            ) from exc
    if account_id is not None:
        account = next((item for item in store.list_accounts() if item.id == account_id), None)
        if account is None:
            raise IntegrationConnectionError(
                integration_id=service,
                reason="auth_required",
                detail="The selected Google account is unavailable.",
            )
        selected_account_ref = account.email
        if target_account_ref is not None and target_account_ref.casefold() != selected_account_ref.casefold():
            raise IntegrationOperationError(
                code="invalid_ref",
                safe_message="The selected Google account does not match the requested account.",
            )
        target_account_ref = selected_account_ref

    creds = _authorize_google_credentials(service, requested, target_account_ref)
    granted = _credential_scopes(creds)
    missing = tuple(scope for scope in GOOGLE_SERVICE_SCOPES[service] if scope not in granted)
    if missing:
        raise IntegrationConnectionError(
            integration_id=service,
            reason="scope_required",
            detail="Google authorization is missing required permissions.",
            required_scopes=missing,
            retry_safe=True,
            account_ref=target_account_ref,
        )
    email = _google_profile_email(service, creds, target_account_ref)
    if target_account_ref is not None and email.casefold() != target_account_ref.casefold():
        raise IntegrationConnectionError(
            integration_id=service,
            reason="auth_required",
            detail=f"Google authorization must use {target_account_ref}.",
            retry_safe=True,
            account_ref=target_account_ref,
        )
    stable_account_ref = email if target_account_ref is None else target_account_ref
    return store.upsert_authorization(
        account_id=account_id,
        email=stable_account_ref,
        credential_json=creds.to_json(),
        scopes=granted,
        service=service,
    )


@dataclass(frozen=True, slots=True)
class GoogleRevocationPlan:
    tokens: tuple[str, ...]


def prepare_google_account_revocation(
    account: GoogleAccount,
    store: GoogleAccountStore | None = None,
) -> GoogleRevocationPlan:
    if store is None:
        store = google_account_store()
    tokens: list[str] = []
    for token_path in store.token_paths(account):
        try:
            creds = Credentials.from_authorized_user_file(str(token_path))
        except (GoogleAuthError, OSError, TypeError, ValueError) as exc:
            raise IntegrationOperationError(
                code="invalid_credentials",
                safe_message="Google authorization credentials could not be read for revocation.",
                retryable=False,
            ) from exc
        token = creds.refresh_token if creds.refresh_token is not None else creds.token
        if not isinstance(token, str) or not token:
            raise IntegrationOperationError(
                code="invalid_credentials",
                safe_message="Google authorization credentials do not contain a revocable token.",
                retryable=False,
            )
        tokens.append(token)
    return GoogleRevocationPlan(tokens=tuple(dict.fromkeys(tokens)))


def _revocation_error(*, completed: int, retryable: bool, uncertain: bool = False) -> IntegrationOperationError:
    if completed:
        return IntegrationOperationError(
            code="partial_revocation",
            safe_message="Some Google authorizations were revoked, but the remaining revocations failed.",
            retryable=retryable,
        )
    return IntegrationOperationError(
        code="revocation_uncertain" if uncertain else "provider_error",
        safe_message=(
            "Google authorization revocation was not confirmed."
            if uncertain
            else "Google authorization revocation failed."
        ),
        retryable=retryable,
    )


def execute_google_account_revocation(plan: GoogleRevocationPlan) -> None:
    completed = 0
    for token in plan.tokens:
        body = urlencode({"token": token}).encode()
        request = URLRequest(
            "https://oauth2.googleapis.com/revoke",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=15) as response:
                status = response.status
        except HTTPError as exc:
            # Google returns 400 when a token is already invalid. That is the
            # desired terminal state, so local removal may continue.
            if exc.code == 400:
                completed += 1
                continue
            raise _revocation_error(
                completed=completed,
                retryable=exc.code in {408, 429, 500, 502, 503, 504},
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise _revocation_error(completed=completed, retryable=True, uncertain=True) from exc
        if status not in {200, 400}:
            raise _revocation_error(
                completed=completed,
                retryable=status in {408, 429, 500, 502, 503, 504},
            )
        completed += 1


def revoke_google_account(account: GoogleAccount, store: GoogleAccountStore | None = None) -> None:
    execute_google_account_revocation(prepare_google_account_revocation(account, store))
