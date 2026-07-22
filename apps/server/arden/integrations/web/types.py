from dataclasses import dataclass
from typing import Protocol, runtime_checkable


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
