"""Conservative, durable maintenance over the revision-backed wiki feed."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, Field

from arden.revisions import ResourceState

from .maintenance_store import (
    WikiMaintenanceReview,
    WikiMaintenanceReviewInput,
    WikiMaintenanceReviewStatus,
    WikiMaintenanceStore,
    WikiMaintenanceWatermarkConflictError,
)
from .models import WikiChangeCommit, WikiChangesReport, WikiMaintenancePageUpdate, WikiPageRecord
from .pages import PageValidationError, parse_page
from .service import (
    WIKI_HEALTH_ACTOR,
    WIKI_HEALTH_ORIGIN,
    WIKI_HEALTH_REASON,
    WIKI_HEALTH_RESOURCE_ID,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .service import WikiService


_CONCERN_KEY = re.compile(r"[a-z0-9][a-z0-9._:-]{0,120}")
_ORIGIN = "wiki.maintenance"
_PAGE_ID_LINE = re.compile(r"(?m)^([+-]?\s*page_id:\s*).*$")
_MAX_HEADER_BYTES = 16 * 1024
_MAX_PAGE_SECTION_BYTES = 64 * 1024
_MAX_DIFF_BYTES = 64 * 1024
_MAX_LINK_SECTION_BYTES = 32 * 1024
_MAX_WARNINGS_BYTES = 64 * 1024
_MAX_PROMPT_BYTES = 256 * 1024


class WikiMaintenanceError(RuntimeError):
    """Maintenance could not safely review or apply one contiguous commit."""


class WikiMaintenanceEvidenceTooLarge(WikiMaintenanceError):
    """Essential evidence cannot fit in the deterministic completion budget."""

    def __init__(self, *, label: str, actual_bytes: int, limit_bytes: int, fingerprint: str) -> None:
        super().__init__(f"{label} is {actual_bytes} UTF-8 bytes; limit is {limit_bytes}")
        self.label = label
        self.actual_bytes = actual_bytes
        self.limit_bytes = limit_bytes
        self.fingerprint = fingerprint


class WikiMaintenanceUpdateDraft(BaseModel):
    """A model proposal addressed only by a run-local opaque page token."""

    page_token: str
    title: str
    aliases: list[str] = Field(default_factory=list)
    body: str


class WikiMaintenanceConcernDraft(BaseModel):
    """A stable human-review concern returned by the completion model."""

    key: str
    summary: str
    proposal: str


class WikiMaintenanceDecision(BaseModel):
    outcome: Literal["no_change", "updates", "needs_review"]
    updates: list[WikiMaintenanceUpdateDraft] = Field(default_factory=list)
    concern: WikiMaintenanceConcernDraft | None = None


@dataclass(frozen=True, slots=True)
class WikiMaintenancePreparedReport:
    """The complete, intentionally narrow evidence handed to a reviewer."""

    commit_id: str
    base_head: str
    evidence_fingerprint: str
    replay_fingerprint: str
    markdown: str
    page_tokens: Mapping[str, WikiPageRecord]


@dataclass(frozen=True, slots=True)
class WikiMaintenanceResult:
    feed_target_revision: str | None
    processed_through_revision: str | None
    advanced: bool = False
    complete: bool = False
    reviewed_commits: int = 0
    updated_pages: int = 0
    blocked: bool = False
    empty: bool = False
    replayed: bool = False
    reload_required: bool = False
    error: str | None = None


class WikiMaintenanceReviewer(Protocol):
    """Reviews only one already-prepared wiki evidence bundle."""

    async def review(self, report: WikiMaintenancePreparedReport) -> WikiMaintenanceDecision: ...


class WikiMaintenance:
    """Review the wiki feed in order; advance only an accepted safe prefix.

    The model is a proposer, never an authority over identity or concurrency.
    It receives no fact ledger, index, raw storage, resource IDs, or versions.
    """

    def __init__(self, store: WikiMaintenanceStore, wiki: WikiService, reviewer: WikiMaintenanceReviewer) -> None:
        self._store = store
        self._wiki = wiki
        self._reviewer = reviewer

    async def run(self) -> WikiMaintenanceResult:
        watermark = await self._store.get_watermark()
        expected = None if watermark is None else watermark.revision
        initial = expected
        try:
            feed = await asyncio.to_thread(self._wiki.changes_since, expected)
        except Exception as exc:
            return await self._error_result(None, expected, initial, error=exc)
        if not feed.commits:
            return self._result(feed.through_revision, expected, initial, empty=True)

        commit_ids = tuple(commit.commit_id for commit in feed.commits)
        reviewed = 0
        updated = 0
        replayed = False
        history = await self._store.list_history()
        for commit in feed.commits:
            # Non-Markdown commits and the exact backend-owned health projection
            # remain in the contiguous chain without entering model review.
            if not commit.changes or self._is_health_projection(commit):
                try:
                    await self._advance(expected, commit_ids, commit.commit_id)
                except Exception as exc:
                    return await self._error_result(
                        feed.through_revision,
                        expected,
                        initial,
                        reviewed=reviewed,
                        updated=updated,
                        replayed=replayed,
                        error=exc,
                    )
                expected = commit.commit_id
                reviewed += 1
                continue

            rows = [row for row in history if row.blocking_commit_id == commit.commit_id]
            try:
                prepared = self._prepare(feed, commit)
            except WikiMaintenanceEvidenceTooLarge as exc:
                try:
                    existing = await self._store.get_by_evidence(commit.commit_id, "evidence-too-large")
                except Exception as store_exc:
                    return await self._error_result(
                        feed.through_revision,
                        expected,
                        initial,
                        reviewed=reviewed,
                        updated=updated,
                        replayed=replayed,
                        error=store_exc,
                    )
                stale_oversized = [
                    row
                    for row in rows
                    if row.status is WikiMaintenanceReviewStatus.NEEDS_REVIEW
                    and (
                        existing is None
                        or row.review_id != existing.review_id
                        or row.evidence_fingerprint != exc.fingerprint
                    )
                ]
                try:
                    await self._clear_stale(stale_oversized)
                except Exception as store_exc:
                    return await self._error_result(
                        feed.through_revision,
                        expected,
                        initial,
                        reviewed=reviewed,
                        updated=updated,
                        replayed=replayed,
                        error=store_exc,
                    )
                if existing is not None and existing.evidence_fingerprint == exc.fingerprint:
                    if existing.status is WikiMaintenanceReviewStatus.NEEDS_REVIEW:
                        return self._result(
                            feed.through_revision,
                            expected,
                            initial,
                            reviewed=reviewed,
                            updated=updated,
                            replayed=replayed,
                            blocked=True,
                        )
                    # Any explicit terminal decision authorizes leaving this
                    # exact manual-only evidence unchanged. This gives ACCEPT
                    # a recoverable meaning without pretending an edit exists.
                    try:
                        await self._advance(expected, commit_ids, commit.commit_id)
                    except Exception as store_exc:
                        return await self._error_result(
                            feed.through_revision,
                            expected,
                            initial,
                            reviewed=reviewed,
                            updated=updated,
                            replayed=replayed,
                            error=store_exc,
                        )
                    expected = commit.commit_id
                    reviewed += 1
                    continue
                try:
                    await self._store.apply_run(
                        expected_revision=expected,
                        ordered_commit_ids=self._remaining(commit_ids, expected),
                        reviewed_through=commit.commit_id,
                        reviews=(
                            WikiMaintenanceReviewInput(
                                blocking_commit_id=commit.commit_id,
                                evidence_key="evidence-too-large",
                                evidence_fingerprint=exc.fingerprint,
                                summary=str(exc),
                                proposal_json={
                                    "kind": "manual_evidence_review",
                                    "section": exc.label,
                                    "actual_bytes": exc.actual_bytes,
                                    "limit_bytes": exc.limit_bytes,
                                },
                            ),
                        ),
                    )
                except Exception as store_exc:
                    return await self._error_result(
                        feed.through_revision,
                        expected,
                        initial,
                        reviewed=reviewed,
                        updated=updated,
                        replayed=replayed,
                        error=store_exc,
                    )
                return self._result(
                    feed.through_revision,
                    expected,
                    initial,
                    reviewed=reviewed,
                    updated=updated,
                    replayed=replayed,
                    blocked=True,
                )
            matching = [row for row in rows if row.evidence_fingerprint == prepared.evidence_fingerprint]
            open_rows = [row for row in rows if row.status is WikiMaintenanceReviewStatus.NEEDS_REVIEW]
            if len(open_rows) > 1:
                return await self._error_result(
                    feed.through_revision,
                    expected,
                    initial,
                    reviewed=reviewed,
                    updated=updated,
                    replayed=replayed,
                    error=WikiMaintenanceError("multiple open reviews block one wiki commit"),
                )
            accepted = [row for row in matching if row.status is WikiMaintenanceReviewStatus.ACCEPTED]
            if len(accepted) > 1:
                return await self._error_result(
                    feed.through_revision,
                    expected,
                    initial,
                    reviewed=reviewed,
                    updated=updated,
                    replayed=replayed,
                    error=WikiMaintenanceError("multiple accepted reviews match one wiki commit"),
                )
            if self._has_trusted_replay(feed, prepared):
                try:
                    await self._clear_stale(open_rows)
                    await self._advance(expected, commit_ids, commit.commit_id)
                except Exception as exc:
                    return await self._error_result(
                        feed.through_revision,
                        expected,
                        initial,
                        reviewed=reviewed,
                        updated=updated,
                        replayed=replayed,
                        error=exc,
                    )
                expected = commit.commit_id
                reviewed += 1
                replayed = True
                continue

            stale_open = [
                row
                for row in rows
                if row.status is WikiMaintenanceReviewStatus.NEEDS_REVIEW
                and row.evidence_fingerprint != prepared.evidence_fingerprint
            ]
            if matching and stale_open:
                try:
                    await self._clear_stale(stale_open)
                except Exception as exc:
                    return await self._error_result(
                        feed.through_revision,
                        expected,
                        initial,
                        reviewed=reviewed,
                        updated=updated,
                        replayed=replayed,
                        error=exc,
                    )
            pending = [row for row in matching if row.status is WikiMaintenanceReviewStatus.NEEDS_REVIEW]
            if pending:
                # Exact evidence already has a visible, durable question. Do not
                # spend another completion or silently replace it.
                return self._result(
                    feed.through_revision,
                    expected,
                    initial,
                    reviewed=reviewed,
                    updated=updated,
                    replayed=replayed,
                    blocked=True,
                )

            if accepted:
                try:
                    for row in accepted:
                        updated += await self._apply_accepted(prepared, row)
                except Exception as exc:
                    return await self._error_result(
                        feed.through_revision,
                        expected,
                        initial,
                        reviewed=reviewed,
                        updated=updated,
                        replayed=replayed,
                        error=exc,
                    )
                try:
                    await self._advance(expected, commit_ids, commit.commit_id)
                except Exception as exc:
                    return await self._error_result(
                        feed.through_revision,
                        expected,
                        initial,
                        reviewed=reviewed,
                        updated=updated,
                        replayed=replayed,
                        error=exc,
                    )
                expected = commit.commit_id
                reviewed += 1
                return self._result(
                    feed.through_revision,
                    expected,
                    initial,
                    reviewed=reviewed,
                    updated=updated,
                    replayed=replayed,
                    reload_required=True,
                )

            # Rejected/manual rows are an explicit decision for this exact
            # evidence. A changed fingerprint falls through to a new review.
            if matching:
                try:
                    await self._advance(expected, commit_ids, commit.commit_id)
                except Exception as exc:
                    return await self._error_result(
                        feed.through_revision,
                        expected,
                        initial,
                        reviewed=reviewed,
                        updated=updated,
                        replayed=replayed,
                        error=exc,
                    )
                expected = commit.commit_id
                reviewed += 1
                continue

            try:
                decision = await self._reviewer.review(prepared)
                self._validate_decision(decision, prepared)
            except Exception as exc:
                return await self._error_result(
                    feed.through_revision,
                    expected,
                    initial,
                    reviewed=reviewed,
                    updated=updated,
                    replayed=replayed,
                    error=exc,
                )

            try:
                if decision.outcome == "needs_review":
                    assert decision.concern is not None
                    proposal = self._proposal(prepared, decision)
                    await self._clear_stale(stale_open)
                    await self._store.apply_run(
                        expected_revision=expected,
                        ordered_commit_ids=self._remaining(commit_ids, expected),
                        # The newly stored open review itself keeps this commit
                        # out of the checkpoint; apply_run makes both durable.
                        reviewed_through=commit.commit_id,
                        reviews=(
                            WikiMaintenanceReviewInput(
                                blocking_commit_id=commit.commit_id,
                                evidence_key=decision.concern.key,
                                evidence_fingerprint=prepared.evidence_fingerprint,
                                summary=decision.concern.summary,
                                proposal_json=proposal,
                            ),
                        ),
                    )
                    return self._result(
                        feed.through_revision,
                        expected,
                        initial,
                        reviewed=reviewed,
                        updated=updated,
                        replayed=replayed,
                        blocked=True,
                    )

                if decision.outcome == "updates":
                    updated += await self._apply(prepared, decision.updates)
                await self._clear_stale(stale_open)
                await self._advance(expected, commit_ids, commit.commit_id)
            except Exception as exc:
                return await self._error_result(
                    feed.through_revision,
                    expected,
                    initial,
                    reviewed=reviewed,
                    updated=updated,
                    replayed=replayed,
                    error=exc,
                )
            expected = commit.commit_id
            reviewed += 1
            if decision.outcome == "updates":
                return self._result(
                    feed.through_revision,
                    expected,
                    initial,
                    reviewed=reviewed,
                    updated=updated,
                    replayed=replayed,
                    reload_required=True,
                )

        return self._result(
            feed.through_revision,
            expected,
            initial,
            reviewed=reviewed,
            updated=updated,
            replayed=replayed,
        )

    async def _advance(self, expected: str | None, commits: Sequence[str], through: str) -> None:
        result = await self._store.apply_run(
            expected_revision=expected,
            ordered_commit_ids=self._remaining(commits, expected),
            reviewed_through=through,
        )
        actual = None if result.watermark is None else result.watermark.revision
        if actual != through:
            raise WikiMaintenanceWatermarkConflictError(
                f"wiki maintenance watermark did not advance: expected {through}, found {actual}"
            )

    @staticmethod
    def _result(
        feed_target: str | None,
        processed_through: str | None,
        initial: str | None,
        *,
        reviewed: int = 0,
        updated: int = 0,
        blocked: bool = False,
        empty: bool = False,
        replayed: bool = False,
        reload_required: bool = False,
        error: str | None = None,
    ) -> WikiMaintenanceResult:
        return WikiMaintenanceResult(
            feed_target_revision=feed_target,
            processed_through_revision=processed_through,
            advanced=processed_through != initial,
            complete=(processed_through == feed_target and not blocked and not reload_required and error is None),
            reviewed_commits=reviewed,
            updated_pages=updated,
            blocked=blocked,
            empty=empty,
            replayed=replayed,
            reload_required=reload_required,
            error=error,
        )

    async def _error_result(
        self,
        feed_target: str | None,
        processed_fallback: str | None,
        initial: str | None,
        *,
        error: BaseException,
        reviewed: int = 0,
        updated: int = 0,
        replayed: bool = False,
    ) -> WikiMaintenanceResult:
        message = str(error)
        try:
            watermark = await self._store.get_watermark()
            processed = None if watermark is None else watermark.revision
        except Exception as reread_error:
            processed = processed_fallback
            message = f"{message}; durable watermark reread failed: {reread_error}"
        return self._result(
            feed_target,
            processed,
            initial,
            reviewed=reviewed,
            updated=updated,
            replayed=replayed,
            error=message,
        )

    @staticmethod
    def _remaining(commits: Sequence[str], expected: str | None) -> tuple[str, ...]:
        if expected is None:
            return tuple(commits)
        try:
            return tuple(commits[commits.index(expected) + 1 :])
        except ValueError:
            # A fresh feed is always expected to start after the durable
            # checkpoint. Treat divergence as a safe no-advance failure later.
            return ()

    def _prepare(self, feed: WikiChangesReport, commit: WikiChangeCommit) -> WikiMaintenancePreparedReport:
        records = self._current_records(feed, commit)
        page_tokens = {f"P{index:03d}": record for index, record in enumerate(records, start=1)}
        token_by_id = {record.page.page_id: token for token, record in page_tokens.items()}
        header = "\n\n".join(
            (
                "# Wiki maintenance review",
                "Only the supplied Markdown evidence is available. User edits are authoritative: preserve their "
                "intent, make no speculative insights, and propose no rename, move, archive, merge, or "
                "generated-region edit.",
                f"Commit: {commit.commit_id[:12]}",
                f"Actor: {commit.actor}; origin: {commit.origin}; reason: {commit.reason}",
            )
        )
        pieces: list[tuple[str, str, int]] = [("header", header, _MAX_HEADER_BYTES)]
        for index, change in enumerate(commit.changes, start=1):
            before = self._revision_text(change.before)
            after = self._revision_text(change.after)
            token = token_by_id.get(change.resource_id, "not editable")
            prefix = f"change {index}"
            pieces.extend(
                [
                    (f"{prefix} header", f"## Change ({change.action}) — {token}", _MAX_HEADER_BYTES),
                    (f"{prefix} before", "### Before\n" + before, _MAX_PAGE_SECTION_BYTES),
                    (f"{prefix} after", "### After\n" + after, _MAX_PAGE_SECTION_BYTES),
                    (
                        f"{prefix} diff",
                        "### Diff\n```diff\n" + self._redact_page_ids(change.unified_diff) + "\n```",
                        _MAX_DIFF_BYTES,
                    ),
                    (
                        f"{prefix} links",
                        "### Current links\n" + self._links(change.current_outgoing, token_by_id),
                        _MAX_LINK_SECTION_BYTES,
                    ),
                    (
                        f"{prefix} backlinks",
                        "### Current backlinks\n" + self._links(change.current_backlinks, token_by_id),
                        _MAX_LINK_SECTION_BYTES,
                    ),
                ]
            )
        if page_tokens:
            pieces.append(("current pages header", "## Current editable pages", _MAX_HEADER_BYTES))
            for index, (token, record) in enumerate(page_tokens.items(), start=1):
                pieces.append(
                    (
                        f"current page {index}",
                        f"### {token}\nTitle: {record.page.title}\n"
                        f"Aliases: {', '.join(record.page.aliases) or '(none)'}\n"
                        f"Body:\n```markdown\n{record.page.body.decode('utf-8', errors='replace')}\n```",
                        _MAX_PAGE_SECTION_BYTES,
                    )
                )
        if feed.warnings:
            pieces.append(
                (
                    "warnings",
                    "## Mechanical warnings\n"
                    + "\n".join(f"- {warning.code}: {warning.target}: {warning.evidence}" for warning in feed.warnings),
                    _MAX_WARNINGS_BYTES,
                )
            )
        markdown = self._bounded_markdown(commit, feed.through_revision, pieces)
        replay_fingerprint = self._fingerprint(
            {"commit": commit.commit_id, "changes": [(c.action, c.resource_id, c.unified_diff) for c in commit.changes]}
        )
        return WikiMaintenancePreparedReport(
            commit_id=commit.commit_id,
            base_head=feed.through_revision or commit.commit_id,
            evidence_fingerprint=self._fingerprint({"replay": replay_fingerprint, "report": markdown}),
            replay_fingerprint=replay_fingerprint,
            markdown=markdown,
            page_tokens=page_tokens,
        )

    def _bounded_markdown(
        self,
        commit: WikiChangeCommit,
        base_head: str | None,
        pieces: Sequence[tuple[str, str, int]],
    ) -> str:
        digest = sha256()
        total = 0
        oversized: tuple[str, int, int] | None = None
        for index, (label, text, limit) in enumerate(pieces):
            encoded = text.encode("utf-8")
            if index:
                digest.update(b"\n\n")
                total += 2
            digest.update(encoded)
            total += len(encoded)
            if oversized is None and len(encoded) > limit:
                oversized = (label, len(encoded), limit)
        report_hash = digest.hexdigest()
        if oversized is not None:
            label, actual, limit = oversized
            raise self._oversized(commit, base_head, report_hash, label, actual, limit)
        if total > _MAX_PROMPT_BYTES:
            raise self._oversized(
                commit,
                base_head,
                report_hash,
                "total prompt",
                total,
                _MAX_PROMPT_BYTES,
            )
        return "\n\n".join(text for _label, text, _limit in pieces)

    def _oversized(
        self,
        commit: WikiChangeCommit,
        base_head: str | None,
        report_hash: str,
        label: str,
        actual: int,
        limit: int,
    ) -> WikiMaintenanceEvidenceTooLarge:
        fingerprint = self._fingerprint(
            {
                "commit": commit.commit_id,
                "base_head": base_head,
                "report_hash": report_hash,
                "section": label,
                "actual_bytes": actual,
                "limit_bytes": limit,
            }
        )
        return WikiMaintenanceEvidenceTooLarge(
            label=label,
            actual_bytes=actual,
            limit_bytes=limit,
            fingerprint=fingerprint,
        )

    def _current_records(self, feed: WikiChangesReport, commit: WikiChangeCommit) -> tuple[WikiPageRecord, ...]:
        page_ids = {change.resource_id for change in commit.changes}
        for change in commit.changes:
            page_ids.update(
                reference.target_page_id for reference in change.current_outgoing if reference.target_page_id
            )
            page_ids.update(reference.source_page_id for reference in change.current_backlinks)
        records: list[WikiPageRecord] = []
        for page_id in page_ids:
            try:
                resource = self._wiki.repository.get(page_id, at=feed.through_revision)
                if resource.state is not ResourceState.ACTIVE or not resource.path.endswith(".md"):
                    continue
                content = self._wiki.repository.read(page_id, at=feed.through_revision)
                page = parse_page(content, expected_page_id=page_id)
                records.append(WikiPageRecord(resource, page, content))
            except (KeyError, PageValidationError):
                continue
        return tuple(sorted(records, key=lambda item: item.page.page_id))

    @staticmethod
    def _revision_text(revision) -> str:
        if revision is None:
            return "(absent)"
        if revision.page is None:
            content = WikiMaintenance._redact_page_ids(revision.content.decode("utf-8", errors="replace"))
            return f"Invalid Markdown: {revision.validation_error}\n```markdown\n{content}\n```"
        page = revision.page
        return (
            f"Title: {page.title}\nAliases: {', '.join(page.aliases) or '(none)'}\n"
            f"Body:\n```markdown\n{page.body.decode('utf-8', errors='replace')}\n```"
        )

    @staticmethod
    def _links(references, tokens: Mapping[str, str]) -> str:
        if not references:
            return "(none)"
        result = []
        for reference in references:
            target = (
                "unresolved"
                if reference.target_page_id is None
                else tokens.get(reference.target_page_id, "current page")
            )
            label = reference.node.page or "#fragment"
            result.append(f"- {label} ({reference.status.value}; {target})")
        return "\n".join(result)

    @staticmethod
    def _fingerprint(value: object) -> str:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        return sha256(encoded).hexdigest()

    @staticmethod
    def _redact_page_ids(markdown: str) -> str:
        return _PAGE_ID_LINE.sub(r"\1[opaque]", markdown)

    def _validate_decision(self, decision: object, prepared: WikiMaintenancePreparedReport) -> None:
        if not isinstance(decision, WikiMaintenanceDecision):
            raise WikiMaintenanceError("reviewer returned an invalid decision")
        if decision.outcome == "no_change":
            if decision.updates or decision.concern is not None:
                raise WikiMaintenanceError("no_change must not contain updates or a concern")
            return
        if decision.outcome == "updates":
            if not decision.updates or decision.concern is not None:
                raise WikiMaintenanceError("updates requires one or more updates and no concern")
            self._updates(prepared, decision.updates)
            return
        if decision.concern is None or not _CONCERN_KEY.fullmatch(decision.concern.key):
            raise WikiMaintenanceError("needs_review requires a stable concern key")
        if not decision.concern.summary.strip() or not decision.concern.proposal.strip():
            raise WikiMaintenanceError("needs_review requires a summary and proposal")
        self._updates(prepared, decision.updates)

    def _updates(
        self, prepared: WikiMaintenancePreparedReport, drafts: Sequence[WikiMaintenanceUpdateDraft]
    ) -> tuple[WikiMaintenancePageUpdate, ...]:
        tokens = [draft.page_token for draft in drafts]
        if len(tokens) != len(set(tokens)):
            raise WikiMaintenanceError("maintenance output repeated a page token")
        result: list[WikiMaintenancePageUpdate] = []
        for draft in drafts:
            record = prepared.page_tokens.get(draft.page_token)
            if record is None:
                raise WikiMaintenanceError("maintenance output named an unknown page token")
            if not draft.title.strip() or any(not alias.strip() for alias in draft.aliases):
                raise WikiMaintenanceError("maintenance output used an empty title or alias")
            result.append(
                WikiMaintenancePageUpdate(
                    page_id=record.page.page_id,
                    expected_version=record.resource.version_id,
                    title=draft.title,
                    aliases=tuple(draft.aliases),
                    body=draft.body.encode(),
                )
            )
        return tuple(result)

    async def _apply(
        self, prepared: WikiMaintenancePreparedReport, drafts: Sequence[WikiMaintenanceUpdateDraft]
    ) -> int:
        updates = self._updates(prepared, drafts)
        await asyncio.to_thread(
            self._wiki.apply_maintenance_updates,
            updates,
            base_head=prepared.base_head,
            reason=self._reason(prepared),
        )
        return len(updates)

    async def _clear_stale(self, reviews: Sequence[WikiMaintenanceReview]) -> None:
        for row in reviews:
            if row.status is WikiMaintenanceReviewStatus.NEEDS_REVIEW:
                await self._store.clear(row.review_id, expected_generation=row.generation)

    @staticmethod
    def _is_health_projection(commit: WikiChangeCommit) -> bool:
        return (
            commit.actor == WIKI_HEALTH_ACTOR
            and commit.origin == WIKI_HEALTH_ORIGIN
            and commit.reason == WIKI_HEALTH_REASON
            and bool(commit.changes)
            and all(change.resource_id == WIKI_HEALTH_RESOURCE_ID for change in commit.changes)
        )

    def _proposal(
        self, prepared: WikiMaintenancePreparedReport, decision: WikiMaintenanceDecision
    ) -> dict[str, object]:
        concern = decision.concern
        assert concern is not None
        return {
            "kind": "maintenance_updates",
            "reason": self._reason(prepared),
            "replay_fingerprint": prepared.replay_fingerprint,
            "summary": concern.proposal,
            "updates": [
                {
                    "page_id": update.page_id,
                    "expected_version": update.expected_version,
                    "title": update.title,
                    "aliases": list(update.aliases),
                    "body": update.body.decode(),
                }
                for update in self._updates(prepared, decision.updates)
            ],
        }

    async def _apply_accepted(self, prepared: WikiMaintenancePreparedReport, row: WikiMaintenanceReview) -> int:
        if row.proposal_json is None:
            raise WikiMaintenanceError("accepted maintenance review has no executable proposal")
        try:
            proposal = json.loads(row.proposal_json)
            if proposal.get("kind") != "maintenance_updates" or proposal.get("reason") != self._reason(prepared):
                raise ValueError("proposal does not match current evidence")
            drafts = tuple(
                WikiMaintenancePageUpdate(
                    page_id=item["page_id"],
                    expected_version=item["expected_version"],
                    title=item["title"],
                    aliases=tuple(item["aliases"]),
                    body=item["body"].encode(),
                )
                for item in proposal["updates"]
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise WikiMaintenanceError("accepted maintenance proposal is malformed") from exc
        if not drafts:
            raise WikiMaintenanceError("accepted maintenance proposal has no updates")
        await asyncio.to_thread(
            self._wiki.apply_maintenance_updates,
            drafts,
            base_head=prepared.base_head,
            reason=self._reason(prepared),
        )
        return len(drafts)

    def _has_trusted_replay(self, feed: WikiChangesReport, prepared: WikiMaintenancePreparedReport) -> bool:
        reason = self._reason(prepared)
        for commit in feed.commits:
            if commit.actor != "Wiki Maintenance" or commit.origin != _ORIGIN or commit.reason != reason:
                continue
            if commit.commit_id == prepared.commit_id or not commit.changes:
                continue
            # The exact reason binds this source commit and immutable source
            # evidence. The service already binds the actual write to its base
            # head and resource versions.
            return True
        return False

    @staticmethod
    def _reason(prepared: WikiMaintenancePreparedReport) -> str:
        return f"wiki maintenance {prepared.commit_id} {prepared.replay_fingerprint}"
