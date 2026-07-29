"""Typed values returned by the wiki domain service."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from arden.revisions.models import IntegrityReport, ResourceChange, ResourceVersion, StorageReport
from arden.wiki.pages import WikiPage
from arden.wiki.wikilinks import WikilinkNode


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


@dataclass(frozen=True, slots=True)
class WikiMaintenancePageUpdate:
    """One ordinary, identity-preserving page edit proposed by maintenance."""

    page_id: str
    expected_version: str
    title: str
    aliases: tuple[str, ...]
    body: bytes


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
class WikiLinkReport:
    head: str | None
    page: WikiPageRecord
    pages: tuple[WikiPageRecord, ...]
    outgoing: tuple[LinkReference, ...]
    backlinks: tuple[LinkReference, ...]


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
class PageMergeRewrite:
    """One parsed-link rewrite performed as part of a page merge."""

    resource_id: str
    expected_version: str
    content: bytes
    replacements: tuple[LinkReference, ...]


@dataclass(frozen=True, slots=True)
class PageMergePlan:
    """A snapshot-pinned merge of one duplicate page into its canonical peer."""

    base_head: str
    canonical_page_id: str
    canonical_expected_version: str
    canonical_title: str
    loser_page_id: str
    loser_expected_version: str
    loser_title: str
    canonical_content: bytes
    rewrites: tuple[PageMergeRewrite, ...]
    link_count: int
    page_count: int
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


@dataclass(frozen=True, slots=True)
class WikiChangeWarning:
    """A mechanical maintenance finding with stable, displayable evidence."""

    code: str
    target: str
    evidence: str


@dataclass(frozen=True, slots=True)
class WikiFactCitation:
    """One well-formed canonical fact citation recorded in page frontmatter."""

    fact_id: str
    version: str


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


@dataclass(frozen=True, slots=True)
class WikiMaintenanceFeed:
    """Chronological, head-pinned metadata for scheduled maintenance."""

    watermark: str | None
    through_revision: str | None
    commits: tuple[WikiMaintenanceCommit, ...]


@dataclass(frozen=True, slots=True)
class WikiMaintenanceDetails:
    """Bounded current evidence for one scheduled maintenance commit."""

    through_revision: str
    commit: WikiChangeCommit
    warnings: tuple[WikiChangeWarning, ...]
    current_records: tuple[WikiPageRecord, ...]


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
