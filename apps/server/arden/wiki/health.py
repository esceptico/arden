"""Deterministic, backend-owned projection of wiki maintenance health."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from arden.constants import BUILTIN_MEMORY_DREAM_ID
from arden.revisions.models import Commit
from arden.wiki.constants import WIKI_HEALTH_ORIGIN, WIKI_MAINTENANCE_ACTOR
from arden.wiki.models import WikiChangesReport
from arden.wiki.provenance import FactPageFreshness, fact_page_freshness, parse_fact_provenance
from arden.wiki.service import WikiService


class WikiHealthIssueCode(StrEnum):
    UNRESOLVED_LINK = "unresolved_link"
    STALE_PAGE = "stale_page"
    DANGLING_CITATION = "dangling_citation"
    INDEX_BEHIND = "index_behind"
    VALIDATION_ERROR = "validation_error"
    FACT_REVIEW_DUE = "fact_review_due"


class WikiHealthIssueOwner(StrEnum):
    BACKEND = "Backend"
    DREAM = "Memory Dream"
    SYNTHESIS = "Synthesis"
    RETENTION = "Memory Retention"
    MEMORY_MAINTENANCE = "Memory Maintenance"
    WIKI_MAINTENANCE = WIKI_MAINTENANCE_ACTOR


@dataclass(frozen=True, slots=True)
class WikiHealthIssue:
    code: WikiHealthIssueCode
    target: str
    evidence: str
    owner: WikiHealthIssueOwner


@dataclass(frozen=True, slots=True)
class WikiHealthWorker:
    """Typed state supplied by the fact and automation owners."""

    name: str
    last_success: datetime | None
    processed_through: str | None
    current_revision: str | None
    current: bool | None = None

    @property
    def status(self) -> str:
        if self.current is not None:
            return "current" if self.current else "behind"
        if self.current_revision is None:
            return "not available"
        if self.processed_through == self.current_revision:
            return "current"
        return "behind"


@dataclass(frozen=True, slots=True)
class WikiHealthIndex:
    """Typed index checkpoint; the index implementation remains its owner."""

    revision: str | None
    status: str
    detail: str | None = None

    def state_for(self, wiki_revision: str | None) -> str:
        if self.status != "ready":
            return self.status
        if self.revision == wiki_revision:
            return "current"
        return "behind"


@dataclass(frozen=True, slots=True)
class WikiHealthPendingReview:
    """Visible pending maintenance review, intentionally not an issue type."""

    review_id: str
    summary: str


@dataclass(frozen=True, slots=True)
class WikiHealthInput:
    fact_ledger_revision: str | None
    wiki: WikiChangesReport
    workers: tuple[WikiHealthWorker, ...]
    index: WikiHealthIndex
    issues: tuple[WikiHealthIssue, ...] = ()
    pending_reviews: tuple[WikiHealthPendingReview, ...] = ()


@dataclass(frozen=True, slots=True)
class WikiHealthResult:
    observed_wiki_revision: str | None
    commit: Commit | None
    issues: tuple[WikiHealthIssue, ...]


class WikiHealthProjector:
    """Renders health from typed projections and publishes only ``health.md``."""

    def __init__(self, wiki: WikiService) -> None:
        self._wiki = wiki

    def project(self, value: WikiHealthInput) -> WikiHealthResult:
        observed = _last_non_health_revision(value.wiki)
        issues = _dedupe(
            (
                *_mechanical_issues(value.wiki, value.fact_ledger_revision),
                *value.issues,
                *_index_issues(value.index, observed),
            )
        )
        body = _render(value, observed, issues)
        commit = self._wiki.publish_health(body=body, base_head=value.wiki.through_revision)
        return WikiHealthResult(observed, commit, issues)


def _last_non_health_revision(report: WikiChangesReport) -> str | None:
    for commit in reversed(report.commits):
        if commit.origin != WIKI_HEALTH_ORIGIN:
            return commit.commit_id
    return None


def _mechanical_issues(
    report: WikiChangesReport,
    fact_revision: str | None,
) -> tuple[WikiHealthIssue, ...]:
    issues: list[WikiHealthIssue] = []
    for warning in report.warnings:
        if warning.code in {"unresolved_link", "ambiguous_link"}:
            evidence = warning.evidence if warning.code == "unresolved_link" else f"ambiguous: {warning.evidence}"
            issues.append(
                WikiHealthIssue(
                    WikiHealthIssueCode.UNRESOLVED_LINK,
                    warning.target,
                    evidence,
                    WikiHealthIssueOwner.WIKI_MAINTENANCE,
                )
            )
        elif warning.code in {"invalid_page", "invalid_fact_citations", "invalid_generated_from_revision"}:
            owner = (
                WikiHealthIssueOwner.SYNTHESIS
                if warning.code in {"invalid_fact_citations", "invalid_generated_from_revision"}
                else WikiHealthIssueOwner.WIKI_MAINTENANCE
            )
            issues.append(
                WikiHealthIssue(WikiHealthIssueCode.VALIDATION_ERROR, warning.target, warning.evidence, owner)
            )
    issues.extend(
        WikiHealthIssue(
            WikiHealthIssueCode.VALIDATION_ERROR,
            issue.target,
            issue.detail,
            WikiHealthIssueOwner.BACKEND,
        )
        for issue in report.integrity.issues
    )
    for record in report.current_records:
        if fact_page_freshness(record.page.metadata, fact_revision) is not FactPageFreshness.STALE:
            continue
        generated = parse_fact_provenance(record.page.metadata, record.resource.path).generated_from_revision
        assert generated is not None
        issues.append(
            WikiHealthIssue(
                WikiHealthIssueCode.STALE_PAGE,
                record.resource.path,
                f"generated from {generated}; current fact revision is {fact_revision}",
                fact_page_owner(record.page.metadata),
            )
        )
    return tuple(issues)


def fact_page_owner(metadata: Mapping[str, object]) -> WikiHealthIssueOwner:
    if metadata.get("producer_automation_id") == BUILTIN_MEMORY_DREAM_ID:
        return WikiHealthIssueOwner.DREAM
    return WikiHealthIssueOwner.SYNTHESIS


def _index_issues(index: WikiHealthIndex, observed_wiki_revision: str | None) -> tuple[WikiHealthIssue, ...]:
    if observed_wiki_revision is None or index.revision == observed_wiki_revision:
        return ()
    detail = index.detail or f"indexed {index.revision or 'nothing'}, observed {observed_wiki_revision}"
    return (WikiHealthIssue(WikiHealthIssueCode.INDEX_BEHIND, "wiki_page", detail, WikiHealthIssueOwner.BACKEND),)


def _dedupe(items: tuple[WikiHealthIssue, ...]) -> tuple[WikiHealthIssue, ...]:
    result: list[WikiHealthIssue] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        key = (item.code.value, item.target, item.evidence)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return tuple(result)


def _render(value: WikiHealthInput, observed: str | None, issues: tuple[WikiHealthIssue, ...]) -> bytes:
    index_status = value.index.state_for(observed)
    storage_status = _storage_status(value.wiki)
    workers = value.workers
    state = "healthy"
    if (
        issues
        or value.pending_reviews
        or index_status != "current"
        or storage_status != "healthy"
        or any(worker.status != "current" for worker in workers)
    ):
        state = "attention needed"

    lines = ["# Wiki health", "", f"Overall status: **{state}**", "", "## Observed revisions"]
    lines.extend(
        (
            f"- Fact ledger: `{_revision(value.fact_ledger_revision)}`",
            f"- Wiki: `{_revision(observed)}`",
            "",
            "## Maintenance",
            "",
            "| Worker | Status | Processed through | Last success |",
            "| --- | --- | --- | --- |",
        )
    )
    for worker in workers:
        lines.append(
            f"| {worker.name} | {worker.status} | `{_revision(worker.processed_through)}` | {_time(worker.last_success)} |"
        )
    lines.extend(
        (
            "",
            "## Search and link index",
            "",
            f"- Status: {index_status}",
            f"- Revision: `{_revision(value.index.revision)}`",
        )
    )
    if value.index.detail:
        lines.append(f"- Detail: {value.index.detail}")
    lines.extend(
        (
            "",
            "## Revision history storage",
            "",
            f"- Status: {storage_status}",
            "- Thresholds: 50 MiB inspection; 100 MiB attention",
        )
    )
    lines.extend(("", "## Pending maintenance reviews", ""))
    if value.pending_reviews:
        lines.extend(f"- `{item.review_id}` — {item.summary}" for item in value.pending_reviews)
    else:
        lines.append("- None")
    lines.extend(("", "## Actionable issues", ""))
    if issues:
        lines.extend(
            f"- **{issue.code.value}** — owner: {issue.owner.value}; `{issue.target}`: {issue.evidence}"
            for issue in issues
        )
    else:
        lines.append("- None")
    return ("\n".join(lines) + "\n").encode()


def _revision(value: str | None) -> str:
    return value or "none"


def _storage_status(report: WikiChangesReport) -> str:
    warning_codes = {warning.code for warning in report.warnings}
    if "storage_needs_attention" in warning_codes:
        return "attention needed"
    if "storage_inspection" in warning_codes:
        return "inspection recommended"
    return "healthy"


def _time(value: datetime | None) -> str:
    return "never" if value is None else value.isoformat()
