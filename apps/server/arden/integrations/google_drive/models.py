"""Provider-neutral immutable values used by the Google Drive integration."""

import base64
import binascii
import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from arden.integrations.google_auth.accounts import validate_google_account_email

DriveKind = Literal["all", "doc", "sheet"]
DriveFileKind = Literal["document", "spreadsheet"]
DocEditOperation = Literal["append", "replace_all"]
ValueInputOption = Literal["RAW", "USER_ENTERED"]
type DriveCell = str | int | float | bool | None

_MAX_REF_CHARS = 512
_MAX_NAME_CHARS = 1_024
_MAX_URL_CHARS = 4_096
_MAX_RANGE_CHARS = 512
_MAX_PAGE_TOKEN_CHARS = 4_096
_PAGE_REF_PREFIX = "drive-page-v1:"
_MAX_PAGE_REF_CHARS = 8_000


def _exact_text(name: str, value: str, *, max_chars: int) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be exact non-blank text")
    if "\r" in value or "\n" in value:
        raise ValueError(f"{name} must be one line")
    if len(value) > max_chars:
        raise ValueError(f"{name} exceeds {max_chars} characters")


def _account_ref(value: str) -> None:
    try:
        validate_google_account_email(value)
    except ValueError as exc:
        raise ValueError("Google Drive account reference must be an exact email address") from exc


def _http_url(name: str, value: str) -> None:
    _exact_text(name, value, max_chars=_MAX_URL_CHARS)
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid HTTP(S) URL") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(f"{name} must be an HTTP(S) URL without credentials")


def qualify_file_ref(account_ref: str, provider_ref: str) -> str:
    _account_ref(account_ref)
    _exact_text("Google Drive provider reference", provider_ref, max_chars=_MAX_REF_CHARS)
    return f"{account_ref}:{provider_ref}"


def split_file_ref(file_ref: str) -> tuple[str, str]:
    _exact_text("Google Drive file reference", file_ref, max_chars=_MAX_URL_CHARS)
    account_ref, separator, provider_ref = file_ref.partition(":")
    if not separator:
        raise ValueError("Google Drive file reference must be account-qualified")
    if qualify_file_ref(account_ref, provider_ref) != file_ref:
        raise ValueError("Google Drive file reference must be canonical")
    return account_ref, provider_ref


@dataclass(frozen=True, slots=True)
class DrivePageRef:
    account_ref: str
    provider_token: str
    query: str
    kind: DriveKind
    limit: int

    def __post_init__(self) -> None:
        _account_ref(self.account_ref)
        _exact_text(
            "Google Drive page token",
            self.provider_token,
            max_chars=_MAX_PAGE_TOKEN_CHARS,
        )
        if any(not 0x21 <= ord(character) <= 0x7E for character in self.provider_token):
            raise ValueError("Google Drive page token must contain printable ASCII")
        if not isinstance(self.query, str) or len(self.query) > 500:
            raise ValueError("Google Drive page query is invalid")
        if self.query and self.query != self.query.strip():
            raise ValueError("Google Drive page query must not have surrounding whitespace")
        if not isinstance(self.kind, str) or self.kind not in {"all", "doc", "sheet"}:
            raise ValueError("Google Drive page kind is invalid")
        if type(self.limit) is not int or not 1 <= self.limit <= 100:
            raise ValueError("Google Drive page limit is invalid")

    def encode(self) -> str:
        payload = json.dumps(
            {
                "account_ref": self.account_ref,
                "kind": self.kind,
                "limit": self.limit,
                "provider_token": self.provider_token,
                "query": self.query,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
        return f"{_PAGE_REF_PREFIX}{encoded}"

    @classmethod
    def decode(cls, value: str) -> "DrivePageRef":
        if not isinstance(value, str) or len(value) > _MAX_PAGE_REF_CHARS:
            raise ValueError("page_ref must be an exact Google Drive page reference")
        encoded = value.removeprefix(_PAGE_REF_PREFIX)
        if encoded == value or not encoded:
            raise ValueError("page_ref must be an exact Google Drive page reference")
        try:
            raw = base64.b64decode(
                encoded + "=" * (-len(encoded) % 4),
                altchars=b"-_",
                validate=True,
            ).decode()
            payload = json.loads(raw)
        except (binascii.Error, json.JSONDecodeError, UnicodeError) as exc:
            raise ValueError("page_ref must be an exact Google Drive page reference") from exc
        if not isinstance(payload, dict) or set(payload) != {
            "account_ref",
            "kind",
            "limit",
            "provider_token",
            "query",
        }:
            raise ValueError("page_ref must be an exact Google Drive page reference")
        result = cls(
            account_ref=payload["account_ref"],
            provider_token=payload["provider_token"],
            query=payload["query"],
            kind=payload["kind"],
            limit=payload["limit"],
        )
        if result.encode() != value:
            raise ValueError("page_ref must be canonical")
        return result


@dataclass(frozen=True, slots=True)
class DriveFile:
    ref: str
    account_ref: str
    file_id: str
    name: str
    kind: DriveFileKind
    modified_at: datetime | None
    url: str

    def __post_init__(self) -> None:
        if self.ref != qualify_file_ref(self.account_ref, self.file_id):
            raise ValueError("Google Drive file reference does not match its account and provider reference")
        _exact_text("Google Drive file name", self.name, max_chars=_MAX_NAME_CHARS)
        if self.kind not in {"document", "spreadsheet"}:
            raise ValueError("Google Drive file kind is invalid")
        if self.modified_at is not None and (
            not isinstance(self.modified_at, datetime)
            or self.modified_at.tzinfo is None
            or self.modified_at.utcoffset() is None
        ):
            raise ValueError("Google Drive modified time must include a timezone")
        _http_url("Google Drive file URL", self.url)


@dataclass(frozen=True, slots=True)
class DriveFilePage:
    files: tuple[DriveFile, ...]
    next_page_ref: DrivePageRef | None
    incomplete_search: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.files, tuple):
            raise ValueError("Google Drive file page must be immutable")
        if any(not isinstance(file, DriveFile) for file in self.files):
            raise ValueError("Google Drive file page must contain DriveFile values")
        if len(self.files) > 100:
            raise ValueError("Google Drive file page exceeds 100 files")
        if any(file.modified_at is None for file in self.files):
            raise ValueError("Google Drive search results require modified times")
        refs = [file.ref for file in self.files]
        if len(refs) != len(set(refs)):
            raise ValueError("Google Drive file page contains duplicate references")
        if self.next_page_ref is not None and not isinstance(self.next_page_ref, DrivePageRef):
            raise ValueError("Google Drive page continuation must be a DrivePageRef")
        if not isinstance(self.incomplete_search, bool):
            raise ValueError("Google Drive search completeness state must be boolean")

    @property
    def has_more(self) -> bool:
        return self.next_page_ref is not None


@dataclass(frozen=True, slots=True)
class DriveDocument:
    file: DriveFile
    text: str
    revision_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.file, DriveFile):
            raise ValueError("Google Drive document envelope requires a DriveFile")
        if self.file.kind != "document":
            raise ValueError("Google Drive document envelope requires a document file")
        if not isinstance(self.text, str):
            raise ValueError("Google Drive document text must be a string")
        _exact_text("Google Drive document revision", self.revision_id, max_chars=_MAX_REF_CHARS)


@dataclass(frozen=True, slots=True)
class DriveDocumentState:
    file: DriveFile
    revision_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.file, DriveFile):
            raise ValueError("Google Drive document state requires a DriveFile")
        if self.file.kind != "document":
            raise ValueError("Google Drive document state requires a document file")
        _exact_text("Google Drive document revision", self.revision_id, max_chars=_MAX_REF_CHARS)


@dataclass(frozen=True, slots=True)
class DriveSheetRange:
    file: DriveFile
    range_name: str
    values: tuple[tuple[DriveCell, ...], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.file, DriveFile):
            raise ValueError("Google Drive sheet range requires a DriveFile")
        if self.file.kind != "spreadsheet":
            raise ValueError("Google Drive sheet range requires a spreadsheet file")
        _exact_text("Google Drive sheet range", self.range_name, max_chars=_MAX_RANGE_CHARS)
        if not isinstance(self.values, tuple) or any(not isinstance(row, tuple) for row in self.values):
            raise ValueError("Google Drive sheet values must be immutable")
        if len(self.values) > 10_000 or sum(len(row) for row in self.values) > 100_000:
            raise ValueError("Google Drive sheet values exceed the supported bounds")
        for row in self.values:
            for cell in row:
                if cell is not None and type(cell) not in {str, int, float, bool}:
                    raise ValueError("Google Drive sheet values contain an invalid cell")
                if isinstance(cell, float) and not math.isfinite(cell):
                    raise ValueError("Google Drive sheet values contain a non-finite number")


@dataclass(frozen=True, slots=True)
class DriveSheetMutationReceipt:
    file_ref: str
    acknowledged_range: str

    def __post_init__(self) -> None:
        split_file_ref(self.file_ref)
        _exact_text("Google Drive acknowledged range", self.acknowledged_range, max_chars=_MAX_RANGE_CHARS)
