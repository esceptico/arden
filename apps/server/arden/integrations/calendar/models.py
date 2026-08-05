import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from arden.integrations.google_auth.accounts import validate_google_account_email

_TITLE_MAX_CHARS = 256
_DESCRIPTION_MAX_CHARS = 8_000
_LOCATION_MAX_CHARS = 512
_STATUS_MAX_CHARS = 64
_URL_MAX_CHARS = 4_096
_PAGE_TOKEN_MAX_CHARS = 4_096
_PAGE_REF_MAX_CHARS = 12_000
_PAGE_REF_PREFIX = "calendar-page-v1:"
CALENDAR_ATTENDEE_LIMIT = 200
CalendarUpdateField = Literal["summary", "start", "end", "description", "location", "attendees", "all_day"]


def _account_ref(value: str) -> None:
    try:
        validate_google_account_email(value)
    except ValueError as exc:
        raise ValueError("calendar account reference must be an exact email address") from exc


def _exact_text(name: str, value: str, *, max_chars: int, allow_empty: bool = False) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if not allow_empty and not value:
        raise ValueError(f"{name} must not be empty")
    if value != value.strip():
        raise ValueError(f"{name} must not have surrounding whitespace")
    if "\r" in value or "\n" in value:
        raise ValueError(f"{name} must be one line")
    if len(value) > max_chars:
        raise ValueError(f"{name} exceeds {max_chars} characters")


def _description(value: str) -> None:
    if not isinstance(value, str):
        raise ValueError("calendar description must be a string")
    if value != value.strip():
        raise ValueError("calendar description must not have surrounding whitespace")
    if len(value) > _DESCRIPTION_MAX_CHARS:
        raise ValueError(f"calendar description exceeds {_DESCRIPTION_MAX_CHARS} characters")


def _http_url(name: str, value: str) -> None:
    _exact_text(name, value, max_chars=_URL_MAX_CHARS)
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


def _time_range(start: datetime, end: datetime) -> None:
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        raise ValueError("calendar event times must be datetimes")
    if start.tzinfo is None or start.utcoffset() is None or end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("calendar event times must include a timezone")
    if end <= start:
        raise ValueError("calendar event end must be after start")


def _attendees(attendees: tuple[str, ...]) -> None:
    if not isinstance(attendees, tuple):
        raise ValueError("calendar event attendees must be immutable")
    if len(attendees) > CALENDAR_ATTENDEE_LIMIT:
        raise ValueError(f"calendar event attendees exceed {CALENDAR_ATTENDEE_LIMIT}")
    identities: set[str] = set()
    for attendee in attendees:
        validate_google_account_email(attendee)
        identity = attendee.casefold()
        if identity in identities:
            raise ValueError(f"duplicate calendar attendee: {attendee}")
        identities.add(identity)


def qualify_event_ref(account_ref: str, provider_event_ref: str) -> str:
    _account_ref(account_ref)
    if not provider_event_ref or provider_event_ref != provider_event_ref.strip():
        raise ValueError("provider event reference must be exact and non-blank")
    existing_account, separator, existing_provider_ref = provider_event_ref.partition(":")
    if separator and existing_account.casefold() == account_ref.casefold():
        if not existing_provider_ref:
            raise ValueError("provider event reference must be exact and non-blank")
        return provider_event_ref
    return f"{account_ref}:{provider_event_ref}"


@dataclass(frozen=True, slots=True)
class CalendarAccount:
    account_ref: str

    def __post_init__(self) -> None:
        _account_ref(self.account_ref)


@dataclass(frozen=True, slots=True)
class CalendarPageRef:
    """Opaque continuation bound to one account and one exact provider query window."""

    account_ref: str
    provider_token: str
    query: str | None
    start_at: datetime
    end_at: datetime
    limit: int

    def __post_init__(self) -> None:
        _account_ref(self.account_ref)
        _exact_text("calendar page token", self.provider_token, max_chars=_PAGE_TOKEN_MAX_CHARS)
        if any(not 0x21 <= ord(character) <= 0x7E for character in self.provider_token):
            raise ValueError("calendar page token must contain printable ASCII")
        if self.query is not None:
            _exact_text("calendar page query", self.query, max_chars=500)
        _time_range(self.start_at, self.end_at)
        if type(self.limit) is not int or not 1 <= self.limit <= 50:
            raise ValueError("calendar page limit is invalid")

    def encode(self) -> str:
        payload = json.dumps(
            {
                "account_ref": self.account_ref,
                "end_at": self.end_at.isoformat(),
                "limit": self.limit,
                "provider_token": self.provider_token,
                "query": self.query,
                "start_at": self.start_at.isoformat(),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
        return f"{_PAGE_REF_PREFIX}{encoded}"

    @classmethod
    def decode(cls, value: str) -> "CalendarPageRef":
        if not isinstance(value, str) or len(value) > _PAGE_REF_MAX_CHARS:
            raise ValueError("page_ref must be an exact Calendar page reference")
        encoded = value.removeprefix(_PAGE_REF_PREFIX)
        if encoded == value or not encoded:
            raise ValueError("page_ref must be an exact Calendar page reference")
        try:
            raw = base64.b64decode(
                encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
            ).decode()
            payload = json.loads(raw)
            if not isinstance(payload, dict) or set(payload) != {
                "account_ref", "end_at", "limit", "provider_token", "query", "start_at"
            }:
                raise ValueError
            start_at = datetime.fromisoformat(payload["start_at"])
            end_at = datetime.fromisoformat(payload["end_at"])
        except (binascii.Error, json.JSONDecodeError, UnicodeError, TypeError, ValueError) as exc:
            raise ValueError("page_ref must be an exact Calendar page reference") from exc
        result = cls(
            account_ref=payload["account_ref"],
            provider_token=payload["provider_token"],
            query=payload["query"],
            start_at=start_at,
            end_at=end_at,
            limit=payload["limit"],
        )
        if result.encode() != value:
            raise ValueError("page_ref must be canonical")
        return result


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    event_ref: str
    account_ref: str
    title: str | None
    start: datetime
    end: datetime
    is_all_day: bool
    description: str | None = None
    location: str | None = None
    calendar_ref: str | None = None
    status: str | None = None
    attendees: tuple[str, ...] = ()
    url: str | None = None

    def __post_init__(self) -> None:
        _exact_text("calendar event reference", self.event_ref, max_chars=_URL_MAX_CHARS)
        _account_ref(self.account_ref)
        if self.event_ref != qualify_event_ref(self.account_ref, self.event_ref):
            raise ValueError("calendar event reference must be account-qualified")
        if self.title is not None:
            _exact_text("calendar event title", self.title, max_chars=_TITLE_MAX_CHARS)
        _time_range(self.start, self.end)
        if not isinstance(self.is_all_day, bool):
            raise ValueError("calendar event all-day state must be boolean")
        if self.description is not None:
            _description(self.description)
        if self.location is not None:
            _exact_text("calendar event location", self.location, max_chars=_LOCATION_MAX_CHARS)
        if self.calendar_ref is not None:
            _exact_text("calendar reference", self.calendar_ref, max_chars=_TITLE_MAX_CHARS)
        if self.status is not None:
            _exact_text("calendar event status", self.status, max_chars=_STATUS_MAX_CHARS)
        _attendees(self.attendees)
        if self.url is not None:
            _http_url("calendar event URL", self.url)


@dataclass(frozen=True, slots=True)
class CalendarEventPage:
    events: tuple[CalendarEvent, ...]
    next_page_ref: CalendarPageRef | None = None
    has_more: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.events, tuple):
            raise ValueError("calendar event page must be immutable")
        if not isinstance(self.has_more, bool):
            raise ValueError("calendar event page continuation state must be boolean")
        if self.next_page_ref is not None and not isinstance(self.next_page_ref, CalendarPageRef):
            raise ValueError("calendar event page continuation must be a CalendarPageRef")
        if self.next_page_ref is not None and not self.has_more:
            raise ValueError("calendar event page continuation must set has_more")


@dataclass(frozen=True, slots=True)
class CalendarMutationResult:
    event_ref: str
    summary: str | None = None
    url: str | None = None

    def __post_init__(self) -> None:
        _exact_text("calendar mutation reference", self.event_ref, max_chars=_URL_MAX_CHARS)
        if self.summary is not None:
            _exact_text("calendar mutation summary", self.summary, max_chars=_TITLE_MAX_CHARS)
        if self.url is not None:
            _http_url("calendar mutation URL", self.url)


@dataclass(frozen=True, slots=True)
class CalendarCreateDraft:
    account_ref: str
    summary: str
    start: datetime
    end: datetime
    description: str | None
    location: str | None
    attendees: tuple[str, ...]
    all_day: bool

    def __post_init__(self) -> None:
        _account_ref(self.account_ref)
        _exact_text("calendar event summary", self.summary, max_chars=_TITLE_MAX_CHARS)
        _time_range(self.start, self.end)
        if self.description is not None:
            _description(self.description)
        if self.location is not None:
            _exact_text("calendar event location", self.location, max_chars=_LOCATION_MAX_CHARS)
        _attendees(self.attendees)
        if not isinstance(self.all_day, bool):
            raise ValueError("calendar event all-day state must be boolean")
        if self.all_day and self.end.date() <= self.start.date():
            raise ValueError("all-day event end must be a later date")


@dataclass(frozen=True, slots=True)
class CalendarUpdateDraft:
    event_ref: str
    changed_fields: frozenset[CalendarUpdateField]
    summary: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    description: str | None = None
    location: str | None = None
    attendees: tuple[str, ...] | None = None
    all_day: bool | None = None

    def __post_init__(self) -> None:
        _exact_text("calendar event reference", self.event_ref, max_chars=_URL_MAX_CHARS)
        account_ref, separator, provider_ref = self.event_ref.partition(":")
        try:
            qualified_ref = qualify_event_ref(account_ref, provider_ref)
        except ValueError as exc:
            raise ValueError("calendar event reference must be account-qualified") from exc
        if not separator or not provider_ref or qualified_ref != self.event_ref:
            raise ValueError("calendar event reference must be account-qualified")
        allowed = {"summary", "start", "end", "description", "location", "attendees", "all_day"}
        if not self.changed_fields or not isinstance(self.changed_fields, frozenset):
            raise ValueError("calendar update must change at least one field")
        if unknown := self.changed_fields - allowed:
            raise ValueError(f"calendar update has unknown fields: {', '.join(sorted(unknown))}")
        for field in allowed - self.changed_fields:
            if getattr(self, field) is not None:
                raise ValueError(f"calendar update {field} has a value but is not marked changed")
        if "summary" in self.changed_fields and self.summary is None:
            raise ValueError("calendar update summary must be an exact string")
        if "summary" in self.changed_fields:
            _exact_text("calendar event summary", self.summary, max_chars=_TITLE_MAX_CHARS, allow_empty=True)
        if "start" in self.changed_fields and self.start is None:
            raise ValueError("calendar update start must be a timestamp")
        if "end" in self.changed_fields and self.end is None:
            raise ValueError("calendar update end must be a timestamp")
        for name, value in (("start", self.start), ("end", self.end)):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"calendar update {name} must include a timezone")
        if self.start is not None and self.end is not None:
            _time_range(self.start, self.end)
            if self.all_day is True and self.end.date() <= self.start.date():
                raise ValueError("all-day event end must be a later date")
        if "description" in self.changed_fields and self.description is None:
            raise ValueError("calendar update description must be an exact string")
        if "description" in self.changed_fields:
            _description(self.description)
        if "location" in self.changed_fields and self.location is None:
            raise ValueError("calendar update location must be an exact string")
        if "location" in self.changed_fields:
            _exact_text("calendar event location", self.location, max_chars=_LOCATION_MAX_CHARS, allow_empty=True)
        if "attendees" in self.changed_fields and self.attendees is None:
            raise ValueError("calendar update attendees must be an exact list")
        if self.attendees is not None:
            _attendees(self.attendees)
        if "all_day" in self.changed_fields and self.all_day is None:
            raise ValueError("calendar update all-day state must be boolean")
        temporal_fields = {"start", "end", "all_day"}
        changed_temporal_fields = self.changed_fields & temporal_fields
        if changed_temporal_fields and changed_temporal_fields != temporal_fields:
            raise ValueError("calendar temporal update requires start, end, and all_day together")


@dataclass(frozen=True, slots=True)
class CalendarDeleteDraft:
    event_ref: str

    def __post_init__(self) -> None:
        _exact_text("calendar event reference", self.event_ref, max_chars=_URL_MAX_CHARS)
        account_ref, separator, provider_ref = self.event_ref.partition(":")
        try:
            qualified_ref = qualify_event_ref(account_ref, provider_ref)
        except ValueError as exc:
            raise ValueError("calendar event reference must be account-qualified") from exc
        if not separator or not provider_ref or qualified_ref != self.event_ref:
            raise ValueError("calendar event reference must be account-qualified")
