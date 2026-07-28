"""Typed values returned by the wiki domain service."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

from arden.revisions import IntegrityReport, ResourceChange, ResourceVersion, StorageReport

from .pages import WikiPage
from .wikilinks import WikilinkNode


class LinkStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


class WikiInfrastructureRole(StrEnum):
    """Explicit operational roles; all unregistered pages are common pages."""

    COMMON = "common"
    README = "readme"
    ME = "me"
    ACTIVE_WORK = "active_work"
    DAILY = "daily"
    INSIGHT = "insight"


@dataclass(frozen=True, slots=True)
class WikiPageRecord:
    resource: ResourceVersion
    page: WikiPage
    content: bytes

    def __post_init__(self) -> None:
        if self.resource.resource_id != self.page.page_id:
            raise ValueError("page_id must match resource_id")


@dataclass(frozen=True, slots=True)
class WikiMaintenancePageUpdate:
    """One ordinary, identity-preserving page edit proposed by maintenance."""

    page_id: str
    expected_version: str
    title: str
    aliases: tuple[str, ...]
    body: bytes

    def __post_init__(self) -> None:
        for name in ("page_id", "expected_version", "title"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a nonempty string")
        if not isinstance(self.aliases, tuple) or not all(isinstance(alias, str) for alias in self.aliases):
            raise TypeError("aliases must be a tuple of strings")
        if not isinstance(self.body, bytes):
            raise TypeError("body must be bytes")


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


@dataclass(frozen=True, slots=True)
class WikiChangeWarning:
    """A mechanical maintenance finding with stable, displayable evidence."""

    code: str
    target: str
    evidence: str

    def __post_init__(self) -> None:
        for name in ("code", "target", "evidence"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a nonempty string")


@dataclass(frozen=True, slots=True)
class WikiFactCitation:
    """One well-formed canonical fact citation recorded in page frontmatter."""

    fact_id: str
    version: str

    def __post_init__(self) -> None:
        if not isinstance(self.fact_id, str) or not self.fact_id.strip():
            raise ValueError("fact_id must be a nonempty string")
        if not _revision_id(self.version):
            raise ValueError("version must be a lowercase SHA-256 revision")


@dataclass(frozen=True, slots=True)
class WikiPageRevision:
    """One Markdown resource version, kept inspectable even when invalid."""

    resource: ResourceVersion
    content: bytes
    page: WikiPage | None
    validation_error: str | None
    role: WikiInfrastructureRole
    generated_from_revision: str | None
    fact_citations: tuple[WikiFactCitation, ...]
    provenance_warnings: tuple[WikiChangeWarning, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.resource, ResourceVersion):
            raise TypeError("resource must be a ResourceVersion")
        if not isinstance(self.content, bytes):
            raise TypeError("content must be bytes")
        if self.page is not None and not isinstance(self.page, WikiPage):
            raise TypeError("page must be a WikiPage or None")
        if self.page is None and (not isinstance(self.validation_error, str) or not self.validation_error):
            raise ValueError("invalid pages require a validation_error")
        if self.page is not None and self.validation_error is not None:
            raise ValueError("valid pages must not have a validation_error")
        if self.page is not None and self.page.page_id != self.resource.resource_id:
            raise ValueError("page_id must match resource_id")
        if not isinstance(self.role, WikiInfrastructureRole):
            raise TypeError("role must be a WikiInfrastructureRole")
        if self.generated_from_revision is not None and not _revision_id(self.generated_from_revision):
            raise ValueError("generated_from_revision must be a lowercase SHA-256 revision")
        object.__setattr__(self, "fact_citations", _freeze(self.fact_citations, WikiFactCitation, "fact_citations"))
        object.__setattr__(
            self,
            "provenance_warnings",
            _freeze(self.provenance_warnings, WikiChangeWarning, "provenance_warnings"),
        )


@dataclass(frozen=True, slots=True)
class WikiResourceChange:
    """One changed managed Markdown resource within a revision commit."""

    action: str
    resource_id: str
    before: WikiPageRevision | None
    after: WikiPageRevision | None
    unified_diff: str
    current_outgoing: tuple[LinkReference, ...]
    current_backlinks: tuple[LinkReference, ...]
    unified_diff_complete: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.action, str) or not self.action:
            raise ValueError("action must be a nonempty string")
        if not isinstance(self.resource_id, str) or not self.resource_id:
            raise ValueError("resource_id must be a nonempty string")
        for name in ("before", "after"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, WikiPageRevision):
                raise TypeError(f"{name} must be a WikiPageRevision or None")
            if value is not None and value.resource.resource_id != self.resource_id:
                raise ValueError(f"{name}.resource_id must match resource_id")
        if self.before is None and self.after is None:
            raise ValueError("a resource change requires before or after")
        if not isinstance(self.unified_diff, str):
            raise TypeError("unified_diff must be a string")
        if not isinstance(self.unified_diff_complete, bool):
            raise TypeError("unified_diff_complete must be a bool")
        object.__setattr__(self, "current_outgoing", _freeze(self.current_outgoing, LinkReference, "current_outgoing"))
        object.__setattr__(
            self, "current_backlinks", _freeze(self.current_backlinks, LinkReference, "current_backlinks")
        )
        for reference in (*self.current_outgoing, *self.current_backlinks):
            if not isinstance(reference.candidates, tuple):
                raise TypeError("link reference candidates must be tuples")


@dataclass(frozen=True, slots=True)
class WikiChangeCommit:
    """One chronological commit in a pinned wiki maintenance feed."""

    commit_id: str
    parent_id: str | None
    actor: str
    origin: str
    reason: str
    timestamp: datetime
    changes: tuple[WikiResourceChange, ...]

    def __post_init__(self) -> None:
        for name in ("commit_id", "actor", "origin", "reason"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a nonempty string")
        if self.parent_id is not None and (not isinstance(self.parent_id, str) or not self.parent_id):
            raise ValueError("parent_id must be a nonempty string or None")
        if not isinstance(self.timestamp, datetime):
            raise TypeError("timestamp must be a datetime")
        object.__setattr__(self, "changes", _freeze(self.changes, WikiResourceChange, "changes"))


@dataclass(frozen=True, slots=True)
class WikiMaintenanceCommit:
    """Pinned commit metadata retained until maintenance reaches it.

    This deliberately carries revision metadata only.  In particular, creating
    the feed does not read page blobs or derive current wiki health.
    """

    commit_id: str
    parent_id: str | None
    actor: str
    origin: str
    reason: str
    timestamp: datetime
    changes: tuple[ResourceChange, ...]

    def __post_init__(self) -> None:
        for name in ("commit_id", "actor", "origin", "reason"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a nonempty string")
        if self.parent_id is not None and (not isinstance(self.parent_id, str) or not self.parent_id):
            raise ValueError("parent_id must be a nonempty string or None")
        if not isinstance(self.timestamp, datetime):
            raise TypeError("timestamp must be a datetime")
        object.__setattr__(self, "changes", _freeze(self.changes, ResourceChange, "changes"))


@dataclass(frozen=True, slots=True)
class WikiMaintenanceFeed:
    """Chronological, head-pinned metadata for scheduled maintenance."""

    watermark: str | None
    through_revision: str | None
    commits: tuple[WikiMaintenanceCommit, ...]

    def __post_init__(self) -> None:
        for name in ("watermark", "through_revision"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{name} must be a nonempty string or None")
        object.__setattr__(self, "commits", _freeze(self.commits, WikiMaintenanceCommit, "commits"))


@dataclass(frozen=True, slots=True)
class WikiMaintenanceDetails:
    """Bounded current evidence for one scheduled maintenance commit."""

    through_revision: str
    commit: WikiChangeCommit
    warnings: tuple[WikiChangeWarning, ...]
    current_records: tuple[WikiPageRecord, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.through_revision, str) or not self.through_revision:
            raise ValueError("through_revision must be a nonempty string")
        if not isinstance(self.commit, WikiChangeCommit):
            raise TypeError("commit must be a WikiChangeCommit")
        object.__setattr__(self, "warnings", _freeze(self.warnings, WikiChangeWarning, "warnings"))
        object.__setattr__(self, "current_records", _freeze(self.current_records, WikiPageRecord, "current_records"))


@dataclass(frozen=True, slots=True)
class WikiChangesReport:
    """Immutable, head-pinned managed Markdown history plus current findings."""

    watermark: str | None
    through_revision: str | None
    commits: tuple[WikiChangeCommit, ...]
    warnings: tuple[WikiChangeWarning, ...]
    integrity: IntegrityReport
    storage: StorageReport
    # Scheduled maintenance preloads only the changed/link-neighbor records it
    # will hand to a reviewer.  The ordinary history feed may carry the full
    # current snapshot for the same internal prepared-report contract.
    current_records: tuple[WikiPageRecord, ...] = ()

    def __post_init__(self) -> None:
        for name in ("watermark", "through_revision"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{name} must be a nonempty string or None")
        if not isinstance(self.integrity, IntegrityReport):
            raise TypeError("integrity must be an IntegrityReport")
        if not isinstance(self.storage, StorageReport):
            raise TypeError("storage must be a StorageReport")
        object.__setattr__(self, "commits", _freeze(self.commits, WikiChangeCommit, "commits"))
        object.__setattr__(self, "warnings", _freeze(self.warnings, WikiChangeWarning, "warnings"))
        object.__setattr__(self, "current_records", _freeze(self.current_records, WikiPageRecord, "current_records"))


def _freeze(value: object, expected: type, name: str) -> tuple:
    if isinstance(value, (str, bytes)):
        raise TypeError(f"{name} must be an iterable of {expected.__name__}")
    try:
        result = tuple(value)  # type: ignore[arg-type]
    except TypeError as error:
        raise TypeError(f"{name} must be an iterable of {expected.__name__}") from error
    if not all(isinstance(item, expected) for item in result):
        raise TypeError(f"{name} must contain only {expected.__name__}")
    return result


def _revision_id(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)
