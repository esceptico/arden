import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime

_PAGE_REF_PREFIX = "gmail-page-v1:"
_MAX_ACCOUNT_REF_CHARS = 320
_MAX_PROVIDER_REF_CHARS = 4096
_MAX_QUERY_CHARS = 4096
_MAX_PAGE_REF_CHARS = 12_000


@dataclass(frozen=True, slots=True)
class GmailPageRef:
    account_ref: str
    provider_token: str
    query: str | None
    days: int | None
    limit: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.account_ref, str)
            or not self.account_ref
            or self.account_ref != self.account_ref.strip()
            or ":" in self.account_ref
            or len(self.account_ref) > _MAX_ACCOUNT_REF_CHARS
            or not isinstance(self.provider_token, str)
            or not self.provider_token
            or self.provider_token != self.provider_token.strip()
            or len(self.provider_token) > _MAX_PROVIDER_REF_CHARS
            or any(not 0x21 <= ord(character) <= 0x7E for character in self.provider_token)
        ):
            raise ValueError("Gmail page reference fields are invalid")
        if type(self.limit) is not int or not 1 <= self.limit <= 100:
            raise ValueError("Gmail page reference limit is invalid")
        if (self.query is None) == (self.days is None):
            raise ValueError("Gmail page reference must identify search or recent mail")
        if self.query is not None and (
            not isinstance(self.query, str)
            or not self.query.strip()
            or self.query != self.query.strip()
            or len(self.query) > _MAX_QUERY_CHARS
        ):
            raise ValueError("Gmail page reference query is invalid")
        if self.days is not None and (type(self.days) is not int or not 1 <= self.days <= 3650):
            raise ValueError("Gmail page reference days is invalid")

    def encode(self) -> str:
        payload = json.dumps(
            {
                "account_ref": self.account_ref,
                "days": self.days,
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
    def decode(cls, value: str) -> "GmailPageRef":
        if not isinstance(value, str) or len(value) > _MAX_PAGE_REF_CHARS:
            raise ValueError("page_ref must be an exact Gmail page reference")
        encoded = value.removeprefix(_PAGE_REF_PREFIX)
        if encoded == value or not encoded:
            raise ValueError("page_ref must be an exact Gmail page reference")
        try:
            raw = base64.b64decode(
                encoded + "=" * (-len(encoded) % 4),
                altchars=b"-_",
                validate=True,
            ).decode()
            payload = json.loads(raw)
        except (binascii.Error, json.JSONDecodeError, UnicodeError) as exc:
            raise ValueError("page_ref must be an exact Gmail page reference") from exc
        if not isinstance(payload, dict) or set(payload) != {
            "account_ref",
            "days",
            "limit",
            "provider_token",
            "query",
        }:
            raise ValueError("page_ref must be an exact Gmail page reference")
        result = cls(
            account_ref=payload["account_ref"],
            provider_token=payload["provider_token"],
            query=payload["query"],
            days=payload["days"],
            limit=payload["limit"],
        )
        if result.encode() != value:
            raise ValueError("page_ref must be canonical")
        return result


@dataclass(frozen=True, slots=True)
class GmailMessageSummary:
    ref: str
    account_ref: str
    thread_ref: str
    subject: str | None
    sender: str
    recipient: str
    reply_to: str
    snippet: str
    received_at: datetime
    labels: tuple[str, ...]

    @property
    def title(self) -> str:
        return self.subject or "(no subject)"


@dataclass(frozen=True, slots=True)
class GmailMessage:
    summary: GmailMessageSummary
    content: str


@dataclass(frozen=True, slots=True)
class GmailMessagePage:
    account_ref: str
    messages: tuple[GmailMessageSummary, ...]
    next_page_ref: GmailPageRef | None


@dataclass(frozen=True, slots=True)
class GmailMessageRefPage:
    provider_message_ids: tuple[str, ...]
    next_page_token: str | None


@dataclass(frozen=True, slots=True)
class GmailAttachmentBody:
    data: bytes
    size_bytes: int


@dataclass(frozen=True, slots=True)
class GmailMutationReceipt:
    message_ref: str
    thread_ref: str
    recipient: str


@dataclass(frozen=True, slots=True)
class GmailReplySource:
    provider_message_id: str
    provider_thread_id: str
    recipient: str
    subject_header: str | None
    subject: str | None
    message_id_header: str
    references_header: str
