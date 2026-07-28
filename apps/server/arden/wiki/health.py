"""Deterministic, backend-owned projection of wiki maintenance health."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from arden.revisions.models import Commit

from .models import WikiChangesReport
from .service import WikiService


class WikiHealthIssueCode(StrEnum):
    UNRESOLVED_LINK = "unresolved_link"
    STALE_PAGE = "stale_page"
    DANGLING_CITATION = "dangling_citation"
    INDEX_BEHIND = "index_behind"
    VALIDATION_ERROR = "validation_error"
    FACT_REVIEW_DUE = "fact_review_due"


class WikiHealthIssueOwner(StrEnum):
    BACKEND = "Backend"
    SYNTHESIS = "Synthesis"
    RETENTION = "Memory Retention"
    MEMORY_MAINTENANCE = "Memory Maintenance"
    WIKI_MAINTENANCE = "Wiki Maintenance"


@dataclass(frozen=True, slots=True)
class WikiHealthIssue:
    code: WikiHealthIssueCode
    target: str
    evidence: str
    owner: WikiHealthIssueOwner

    def __post_init__(self) -> None:
        if not isinstance(self.code, WikiHealthIssueCode):
            raise TypeError("code must be a WikiHealthIssueCode")
        if not isinstance(self.owner, WikiHealthIssueOwner):
            raise TypeError("owner must be a WikiHealthIssueOwner")
        for field in ("target", "evidence"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field} must be a nonempty string")


@dataclass(frozen=True, slots=True)
class WikiHealthWorker:
    """Typed state supplied by the fact and automation owners."""

    name: str
    last_success: datetime | None
    processed_through: str | None
    current_revision: str | None
    current: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("name must be a nonempty string")
        if self.last_success is not None and not isinstance(self.last_success, datetime):
            raise TypeError("last_success must be a datetime or None")
        if self.current is not None and not isinstance(self.current, bool):
            raise TypeError("current must be a bool or None")
        for field in ("processed_through", "current_revision"):
            value = getattr(self, field)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{field} must be a nonempty string or None")

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

    def __post_init__(self) -> None:
        if self.revision is not None and (not isinstance(self.revision, str) or not self.revision):
            raise ValueError("revision must be a nonempty string or None")
        if not isinstance(self.status, str) or not self.status:
            raise ValueError("status must be a nonempty string")
        if self.detail is not None and (not isinstance(self.detail, str) or not self.detail):
            raise ValueError("detail must be a nonempty string or None")

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

    def __post_init__(self) -> None:
        for field in ("review_id", "summary"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field} must be a nonempty string")


@dataclass(frozen=True, slots=True)
class WikiHealthInput:
    fact_ledger_revision: str | None
    wiki: WikiChangesReport
    workers: tuple[WikiHealthWorker, ...]
    index: WikiHealthIndex
    issues: tuple[WikiHealthIssue, ...] = ()
    pending_reviews: tuple[WikiHealthPendingReview, ...] = ()

    def __post_init__(self) -> None:
        if self.fact_ledger_revision is not None and (
            not isinstance(self.fact_ledger_revision, str) or not self.fact_ledger_revision
        ):
            raise ValueError("fact_ledger_revision must be a nonempty string or None")
        if not isinstance(self.wiki, WikiChangesReport):
            raise TypeError("wiki must be a WikiChangesReport")
        if self.wiki.watermark is not None:
            raise ValueError("health requires a whole-wiki report so it can ignore its own commits")
        if (
            not isinstance(self.workers, tuple)
            or not self.workers
            or not all(isinstance(item, WikiHealthWorker) for item in self.workers)
        ):
            raise TypeError("workers must be a nonempty tuple of WikiHealthWorker values")
        if not isinstance(self.index, WikiHealthIndex):
            raise TypeError("index must be a WikiHealthIndex")
        if not isinstance(self.issues, tuple) or not all(isinstance(item, WikiHealthIssue) for item in self.issues):
            raise TypeError("issues must be a tuple of WikiHealthIssue values")
        if not isinstance(self.pending_reviews, tuple) or not all(
            isinstance(item, WikiHealthPendingReview) for item in self.pending_reviews
        ):
            raise TypeError("pending_reviews must be a tuple of WikiHealthPendingReview values")


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
        if not isinstance(value, WikiHealthInput):
            raise TypeError("value must be a WikiHealthInput")
        observed = _last_non_health_revision(value.wiki)
        issues = _dedupe((*_mechanical_issues(value.wiki), *value.issues, *_index_issues(value.index, observed)))
        body = _render(value, observed, issues)
        commit = self._wiki.publish_health(body=body, base_head=value.wiki.through_revision)
        return WikiHealthResult(observed, commit, issues)


def _last_non_health_revision(report: WikiChangesReport) -> str | None:
    for commit in reversed(report.commits):
        if commit.origin != "wiki.health":
            return commit.commit_id
    return None


def _mechanical_issues(report: WikiChangesReport) -> tuple[WikiHealthIssue, ...]:
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
    return tuple(issues)


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
