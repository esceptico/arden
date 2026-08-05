import json
from pathlib import Path
from typing import Literal

from pydantic import AnyUrl, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from arden.integrations.google_auth.storage import write_private_text
from arden.settings import ARDEN_DIR

CREDENTIALS_PATH = ARDEN_DIR / "gmail_credentials.json"

_URL = TypeAdapter(AnyUrl)
_GOOGLE_AUTH_URIS = frozenset(
    {
        "https://accounts.google.com/o/oauth2/auth",
        "https://accounts.google.com/o/oauth2/v2/auth",
    }
)
_GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
_GOOGLE_CERT_URI = "https://www.googleapis.com/oauth2/v1/certs"
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})


class _DuplicateJsonKey(ValueError):
    pass


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _load_document_json(raw: str) -> object:
    return json.loads(raw, object_pairs_hook=_reject_duplicate_json_keys)


class GoogleInstalledOAuthClient(BaseModel):
    """Fields emitted by Google's installed Desktop OAuth client download."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    client_id: str = Field(min_length=1, max_length=512)
    client_secret: str = Field(min_length=1, max_length=512)
    auth_uri: str = Field(min_length=1, max_length=256)
    token_uri: str = Field(min_length=1, max_length=256)
    redirect_uris: list[str] = Field(min_length=1, max_length=8)
    project_id: str | None = Field(default=None, min_length=1, max_length=256)
    auth_provider_x509_cert_url: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("client_id", "client_secret", "project_id")
    @classmethod
    def _nonblank_string(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or value != value.strip()):
            raise ValueError("must be an exact non-blank string")
        return value

    @field_validator("auth_uri")
    @classmethod
    def _auth_endpoint(cls, value: str) -> str:
        if value not in _GOOGLE_AUTH_URIS:
            raise ValueError("auth_uri must be a canonical Google OAuth endpoint")
        return value

    @field_validator("token_uri")
    @classmethod
    def _token_endpoint(cls, value: str) -> str:
        if value != _GOOGLE_TOKEN_URI:
            raise ValueError("token_uri must be the canonical Google OAuth token endpoint")
        return value

    @field_validator("auth_provider_x509_cert_url")
    @classmethod
    def _certificate_endpoint(cls, value: str | None) -> str | None:
        if value is not None and value != _GOOGLE_CERT_URI:
            raise ValueError("certificate URI must be the canonical Google endpoint")
        return value

    @field_validator("redirect_uris")
    @classmethod
    def _valid_redirect_uris(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value.strip() or value != value.strip():
                raise ValueError("redirect URI must be exact and non-blank")
            uri = _URL.validate_python(value, strict=True)
            if (
                uri.scheme != "http"
                or uri.host not in _LOOPBACK_HOSTS
                or uri.username is not None
                or uri.password is not None
                or uri.query is not None
                or uri.fragment is not None
                or uri.path not in {"", "/"}
            ):
                raise ValueError("redirect URI must be a loopback HTTP endpoint")
        if len(values) != len(set(values)):
            raise ValueError("redirect URIs must be unique")
        return values


class GoogleOAuthClientDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    installed: GoogleInstalledOAuthClient


class GoogleCredentialsStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    exists: bool
    valid: bool
    client_id: str | None = None
    client_type: Literal["installed"] | None = None
    error: str | None = None


class GoogleCredentialsFileError(ValueError):
    pass


def save_google_credentials_json(document: GoogleOAuthClientDocument) -> Path:
    write_private_text(
        CREDENTIALS_PATH,
        json.dumps(document.model_dump(mode="json", exclude_none=True), indent=2),
    )
    return CREDENTIALS_PATH


def import_google_credentials_file(path: str | Path) -> Path:
    source = Path(path).expanduser()
    if not source.is_file():
        raise GoogleCredentialsFileError("Google credentials file was not found.")
    try:
        data = _load_document_json(source.read_text())
    except (OSError, UnicodeError) as exc:
        raise GoogleCredentialsFileError("Google credentials file could not be read.") from exc
    except json.JSONDecodeError as exc:
        raise GoogleCredentialsFileError("Google credentials file is not valid JSON.") from exc
    except _DuplicateJsonKey as exc:
        raise GoogleCredentialsFileError("Google credentials file contains duplicate fields.") from exc
    try:
        document = GoogleOAuthClientDocument.model_validate(data, strict=True)
    except ValidationError as exc:
        raise GoogleCredentialsFileError(
            "Google credentials must be an installed Desktop OAuth client document."
        ) from exc
    return save_google_credentials_json(document)


def google_credentials_status() -> GoogleCredentialsStatus:
    if not CREDENTIALS_PATH.exists():
        return GoogleCredentialsStatus(
            path=str(CREDENTIALS_PATH),
            exists=False,
            valid=False,
            error=(
                f"Google credentials not found at {CREDENTIALS_PATH}. "
                "Download OAuth Desktop app credentials from Google Cloud Console."
            ),
        )
    try:
        data = _load_document_json(CREDENTIALS_PATH.read_text())
        document = GoogleOAuthClientDocument.model_validate(data, strict=True)
    except (OSError, UnicodeError):
        error = "Google credentials file could not be read."
    except json.JSONDecodeError:
        error = "Google credentials file is not valid JSON."
    except _DuplicateJsonKey:
        error = "Google credentials file contains duplicate fields."
    except ValidationError:
        error = "Google credentials must be an installed Desktop OAuth client document."
    else:
        return GoogleCredentialsStatus(
            path=str(CREDENTIALS_PATH),
            exists=True,
            valid=True,
            client_id=document.installed.client_id,
            client_type="installed",
        )
    return GoogleCredentialsStatus(
        path=str(CREDENTIALS_PATH),
        exists=True,
        valid=False,
        error=error,
    )
