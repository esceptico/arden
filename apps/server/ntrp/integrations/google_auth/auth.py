import json
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode
from urllib.request import Request as URLRequest
from urllib.request import urlopen

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from ntrp.integrations.base import IntegrationConnectionError
from ntrp.integrations.google_auth.accounts import GoogleAccount, GoogleAccountStore, GoogleService
from ntrp.settings import NTRP_DIR

CREDENTIALS_PATH = NTRP_DIR / "gmail_credentials.json"

SCOPES_GMAIL_READ = ["https://www.googleapis.com/auth/gmail.readonly"]
SCOPES_GMAIL_SEND = ["https://www.googleapis.com/auth/gmail.send"]
SCOPES_CALENDAR = ["https://www.googleapis.com/auth/calendar"]
SCOPES_PUBSUB = ["https://www.googleapis.com/auth/pubsub"]
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

# Default scopes for new tokens (Gmail + Calendar + Pub/Sub for push notifications)
SCOPES_ALL = SCOPES_GMAIL_READ + SCOPES_GMAIL_SEND + SCOPES_CALENDAR + SCOPES_PUBSUB

GoogleServiceChoice = Literal["email", "email_calendar", "calendar", "all"]
GOOGLE_SCOPE_CHOICES: dict[str, list[str]] = {
    "email": SCOPES_GMAIL_READ + SCOPES_GMAIL_SEND,
    "email_calendar": SCOPES_GMAIL_READ + SCOPES_GMAIL_SEND + SCOPES_CALENDAR,
    "calendar": SCOPES_CALENDAR,
    "all": SCOPES_ALL,
}


def scopes_for_google_choice(choice: str) -> list[str]:
    try:
        return list(GOOGLE_SCOPE_CHOICES[choice])
    except KeyError:
        raise ValueError("service_choice must be one of: email, email_calendar, calendar, all")


def scopes_for_google_service(service: GoogleService) -> list[str]:
    return list(GOOGLE_SERVICE_SCOPES[service])


def google_account_store() -> GoogleAccountStore:
    store = GoogleAccountStore(NTRP_DIR)
    store.migrate_legacy()
    return store


def _normalized_installed_payload(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Google credentials JSON must be an object")
    if "web" in data and "installed" not in data:
        raise ValueError("Use OAuth client type Desktop app, not Web application.")
    installed = data.get("installed", data)
    if not isinstance(installed, dict):
        raise ValueError("Google credentials installed client must be an object")
    if installed.get("client_type") == "web":
        raise ValueError("Use OAuth client type Desktop app, not Web application.")
    return installed


def validate_google_credentials_payload(data: dict) -> dict:
    installed = _normalized_installed_payload(data)
    required = ("client_id", "client_secret", "auth_uri", "token_uri")
    missing = [key for key in required if not installed.get(key)]
    if missing:
        raise ValueError(f"Google Desktop app credentials missing required field(s): {', '.join(missing)}")
    return {"installed": dict(installed)}


def save_google_credentials_json(data: dict) -> Path:
    normalized = validate_google_credentials_payload(data)
    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_PATH.write_text(json.dumps(normalized, indent=2))
    return CREDENTIALS_PATH


def import_google_credentials_file(path: str | Path) -> Path:
    source = Path(path).expanduser()
    if not source.is_file():
        raise ValueError(f"Google credentials file not found: {source}")
    try:
        data = json.loads(source.read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"Google credentials file is not valid JSON: {e}")
    return save_google_credentials_json(data)


def google_credentials_status() -> dict:
    status = {
        "path": str(CREDENTIALS_PATH),
        "exists": CREDENTIALS_PATH.exists(),
        "valid": False,
        "client_id": None,
        "client_type": None,
        "error": None,
    }
    if not CREDENTIALS_PATH.exists():
        status["error"] = (
            f"Google credentials not found at {CREDENTIALS_PATH}. "
            "Download OAuth Desktop app credentials from Google Cloud Console."
        )
        return status
    try:
        data = json.loads(CREDENTIALS_PATH.read_text())
        if "web" in data and "installed" not in data:
            status["client_type"] = "web"
            status["client_id"] = data.get("web", {}).get("client_id")
            raise ValueError("Use OAuth client type Desktop app, not Web application.")
        installed = _normalized_installed_payload(data)
        status["client_type"] = "installed"
        status["client_id"] = installed.get("client_id")
        validate_google_credentials_payload(data)
        status["valid"] = True
    except Exception as e:
        status["error"] = str(e)
    return status


def discover_gmail_tokens() -> list[Path]:
    """Find all Gmail token files in ~/.ntrp/"""
    if not NTRP_DIR.exists():
        return []
    return sorted(list(NTRP_DIR.glob("gmail_token*.json")))


def discover_calendar_tokens() -> list[Path]:
    """Find all token files that have calendar scope (Gmail tokens work too)."""
    if not NTRP_DIR.exists():
        return []
    # Check both calendar_token*.json AND gmail_token*.json (unified auth)
    calendar_tokens = list(NTRP_DIR.glob("calendar_token*.json"))
    gmail_tokens = list(NTRP_DIR.glob("gmail_token*.json"))
    return sorted(calendar_tokens + gmail_tokens)


def gmail_token_path(email: str) -> Path:
    """Get token path for a Gmail account by email."""
    return NTRP_DIR / f"gmail_token_{email}.json"


def _next_calendar_token_path() -> Path:
    base = NTRP_DIR / "calendar_token.json"
    if not base.exists():
        return base
    for idx in range(1, 1000):
        candidate = NTRP_DIR / f"calendar_token_{idx}.json"
        if not candidate.exists():
            return candidate
    raise RuntimeError("Could not allocate a calendar token filename")


def get_google_credentials(
    token_path: Path,
    scopes: list[str] | None = None,
    require_scopes: list[str] | None = None,
    integration_id: str = "google",
) -> Credentials:
    """
    Get or refresh OAuth credentials from token file.

    Args:
        token_path: Path to the token JSON file
        scopes: Scopes to request for new tokens (default: SCOPES_ALL)
        require_scopes: If set, require every listed OAuth scope
        integration_id: Native integration identifier for typed recovery

    Returns:
        Valid Credentials object

    Raises:
        FileNotFoundError: If credentials file doesn't exist
        IntegrationConnectionError: If authorization expired or required scopes are absent
    """
    scopes = scopes or SCOPES_ALL
    creds = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path))

    if not creds or not creds.valid:
        refreshed = False
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                refreshed = True
            except RefreshError as exc:
                raise IntegrationConnectionError(
                    integration_id=integration_id,
                    reason="auth_required",
                    detail=f"Google authorization expired or was revoked for {token_path.name}.",
                    retry_safe=True,
                ) from exc
        if not refreshed:
            if not CREDENTIALS_PATH.exists():
                raise FileNotFoundError(
                    f"Google credentials not found at {CREDENTIALS_PATH}. "
                    "Download OAuth Desktop app credentials from Google Cloud Console."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), scopes)
            creds = flow.run_local_server(port=0)

        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())

    missing_scopes = tuple(scope for scope in require_scopes or () if scope not in (creds.scopes or ()))
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
    if not creds.scopes:
        return False
    return scope in creds.scopes


def authorize_google_service(
    service: GoogleService,
    *,
    account_id: str | None = None,
    store: GoogleAccountStore | None = None,
) -> GoogleAccount:
    if not CREDENTIALS_PATH.exists():
        raise FileNotFoundError(
            f"Google credentials not found at {CREDENTIALS_PATH}. "
            "Download OAuth Desktop app credentials from Google Cloud Console."
        )
    store = store or google_account_store()
    requested = scopes_for_google_service(service)
    if account_id:
        account = next((item for item in store.list_accounts() if item.id == account_id), None)
        if account is None:
            raise ValueError(f"Unknown Google account: {account_id}")
        requested = list(dict.fromkeys((*account.scopes, *requested)))

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), requested)
    creds = flow.run_local_server(port=0, include_granted_scopes="true")
    granted = tuple(creds.scopes or requested)
    missing = tuple(scope for scope in GOOGLE_SERVICE_SCOPES[service] if scope not in granted)
    if missing:
        raise IntegrationConnectionError(
            integration_id=service,
            reason="scope_required",
            detail="Google authorization is missing required permissions.",
            required_scopes=missing,
            retry_safe=True,
        )
    profile = build("oauth2", "v2", credentials=creds).userinfo().get().execute()
    email = str(profile.get("email") or "").strip() or None
    existing = next(
        (
            item
            for item in store.list_accounts()
            if email and item.email and item.email.casefold() == email.casefold()
        ),
        None,
    )
    if account_id is None and existing and not set(existing.scopes).issubset(granted):
        raise ValueError(
            "This Google account is already connected. Choose it explicitly so existing permissions are preserved."
        )
    return store.upsert_authorization(
        account_id=account_id,
        email=email,
        credential_json=creds.to_json(),
        scopes=granted,
        service=service,
    )


def revoke_google_account(account: GoogleAccount, store: GoogleAccountStore | None = None) -> None:
    store = store or google_account_store()
    creds = Credentials.from_authorized_user_file(str(store.token_path(account)))
    token = creds.refresh_token or creds.token
    if not token:
        return
    body = urlencode({"token": token}).encode()
    request = URLRequest(
        "https://oauth2.googleapis.com/revoke",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urlopen(request, timeout=15) as response:
        if response.status not in {200, 400}:
            raise RuntimeError(f"Google token revocation failed with HTTP {response.status}")


def add_google_account(service_choice: str = "all") -> dict:
    scopes = scopes_for_google_choice(service_choice)
    if not CREDENTIALS_PATH.exists():
        raise FileNotFoundError(
            f"Google credentials not found at {CREDENTIALS_PATH}. "
            "Download OAuth Desktop app credentials from Google Cloud Console."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), scopes)
    creds = flow.run_local_server(port=0)

    email = None
    if service_choice in ("email", "email_calendar", "all"):
        service = build("gmail", "v1", credentials=creds)
        profile = service.users().getProfile(userId="me").execute()
        email = profile.get("emailAddress", "unknown")
        token_path = gmail_token_path(email)
    else:
        token_path = _next_calendar_token_path()

    NTRP_DIR.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())

    return {
        "email": email,
        "status": "connected",
        "token_file": token_path.name,
        "scopes": scopes,
    }


def add_gmail_account() -> str:
    """Compatibility wrapper for the legacy Gmail add flow."""
    result = add_google_account("all")
    return result.get("email") or ""
