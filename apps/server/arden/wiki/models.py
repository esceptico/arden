"""Typed values returned by the wiki domain service."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

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
class WikiLinkReport:
    head: str | None
    page: WikiPageRecord
    pages: tuple[WikiPageRecord, ...]
    outgoing: tuple[LinkReference, ...]
    backlinks: tuple[LinkReference, ...]


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


@dataclass(frozen=True, slots=True)
class GeneratedPageTarget:
    """One Synthesis-owned generated-region publication target."""

    page_id: str
    path: str
    title: str
    aliases: tuple[str, ...]
    generated: bytes
    metadata: Mapping[str, object]

    def __post_init__(self) -> None:
        for name in ("page_id", "path", "title"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a nonempty string")
        if not isinstance(self.aliases, tuple) or not all(isinstance(alias, str) and alias for alias in self.aliases):
            raise ValueError("aliases must be a tuple of nonempty strings")
        if not isinstance(self.generated, bytes):
            raise TypeError("generated must be bytes")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
