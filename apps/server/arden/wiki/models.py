"""Typed values returned by the wiki domain service."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from arden.revisions import ResourceVersion

from .pages import WikiPage
from .wikilinks import WikilinkNode


class LinkStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class WikiPageRecord:
    resource: ResourceVersion
    page: WikiPage
    content: bytes

    def __post_init__(self) -> None:
        if self.resource.resource_id != self.page.page_id:
            raise ValueError("page_id must match resource_id")


@dataclass(frozen=True, slots=True)
class WikiSnapshot:
    head: str | None
    pages: tuple[WikiPageRecord, ...]


@dataclass(frozen=True, slots=True)
class LinkReference:
    source_page_id: str
    node: WikilinkNode
    status: LinkStatus
    target_page_id: str | None = None
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RenameRewrite:
    resource_id: str
    expected_version: str
    content: bytes
    replacements: tuple[LinkReference, ...]


@dataclass(frozen=True, slots=True)
class RenamePlan:
    base_head: str | None
    page_id: str
    expected_version: str
    old_path: str
    new_path: str
    old_title: str
    new_title: str
    moved_content: bytes
    redirect_page_id: str
    rewrite_links: bool
    link_count: int
    page_count: int
    rewrites: tuple[RenameRewrite, ...]
    idempotency_key: str
