from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

PUBLIC_WEB_URL_MAX_CHARS = 4_096


class InvalidPublicWebUrl(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PublicWebUrl:
    value: str

    def __post_init__(self) -> None:
        value = self.value
        if not value:
            raise InvalidPublicWebUrl("URL is empty.")
        if len(value) > PUBLIC_WEB_URL_MAX_CHARS:
            raise InvalidPublicWebUrl(f"URL exceeds {PUBLIC_WEB_URL_MAX_CHARS} characters.")
        if any(char.isspace() or char in {'"', "\\"} or ord(char) < 32 or ord(char) == 127 for char in value):
            raise InvalidPublicWebUrl("URL contains unsafe characters.")
        try:
            parsed = urlsplit(value)
            hostname = parsed.hostname
        except ValueError as error:
            raise InvalidPublicWebUrl("URL is invalid.") from error
        if parsed.scheme.lower() not in {"http", "https"} or not hostname:
            raise InvalidPublicWebUrl("URL must be an absolute HTTP(S) URL.")
        if parsed.username is not None or parsed.password is not None:
            raise InvalidPublicWebUrl("URL must not contain credentials.")

    @classmethod
    def parse(cls, raw_url: str) -> "PublicWebUrl":
        return cls(value=raw_url)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class WebSearchResult:
    title: str
    url: str
    published_date: str | None = None
    summary: str | None = None
    highlights: list[str] | None = None


@dataclass(frozen=True)
class WebContentResult:
    title: str | None
    url: str
    text: str | None = None
    published_date: str | None = None
    author: str | None = None


@runtime_checkable
class WebClient(Protocol):
    name: str
    provider: str

    def search_with_details(self, query: str, limit: int, category: str | None) -> list[WebSearchResult]: ...

    def get_contents(self, urls: list[str]) -> list[WebContentResult]: ...
