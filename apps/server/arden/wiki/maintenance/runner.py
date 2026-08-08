"""Conservative, durable maintenance over the revision-backed wiki feed."""

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256

from arden.revisions.errors import RevisionConflictError
from arden.revisions.models import ResourceChange
from arden.wiki.constants import (
    WIKI_HEALTH_ACTOR,
    WIKI_HEALTH_ORIGIN,
    WIKI_HEALTH_REASON,
    WIKI_HEALTH_RESOURCE_ID,
    WIKI_MAINTENANCE_ACTOR,
    WIKI_MAINTENANCE_ORIGIN,
)
from arden.wiki.maintenance.proposals import (
    WikiMaintenanceDecision,
    WikiMaintenanceError,
    WikiMaintenanceUpdateDraft,
    fingerprint,
)
from arden.wiki.maintenance.store import WikiMaintenanceStore, WikiMaintenanceWatermarkConflictError
from arden.wiki.models import (
    WikiChangeCommit,
    WikiChangesReport,
    WikiMaintenanceCommit,
    WikiMaintenanceDetails,
    WikiMaintenanceFeed,
    WikiMaintenancePageUpdate,
    WikiPageRecord,
)
from arden.wiki.service import WikiMaintenanceEvidenceLimitError, WikiService, WikiSnapshotChangedError

__all__ = (
    "WikiMaintenance",
    "WikiMaintenanceDecision",
    "WikiMaintenanceError",
    "WikiMaintenancePreparedReport",
    "WikiMaintenanceResult",
    "WikiMaintenanceReviewer",
    "WikiMaintenanceUpdateDraft",
)

_MODEL_METADATA_LINE = re.compile(
    r"(?mi)^([+-]?\s*(?:-\s*)?(?:page_id|generated_from_revision|fact_citations|fact_id|version)\s*:\s*).*$"
)
_MAX_HEADER_BYTES = 16 * 1024
_MAX_PAGE_SECTION_BYTES = 64 * 1024
_MAX_DIFF_BYTES = 64 * 1024
_MAX_LINK_SECTION_BYTES = 32 * 1024
_MAX_WARNINGS_BYTES = 64 * 1024
_MAX_PROMPT_BYTES = 512 * 1024


class WikiMaintenanceEvidenceTooLarge(WikiMaintenanceError):
    """Essential evidence cannot fit in the deterministic completion budget."""

    def __init__(
        self,
        *,
        label: str,
        actual_bytes: int,
        limit_bytes: int,
        actual_bytes_at_least: bool = False,
    ) -> None:
        qualifier = "at least " if actual_bytes_at_least else ""
        super().__init__(f"{label} is {qualifier}{actual_bytes} UTF-8 bytes; limit is {limit_bytes}")
        self.label = label
        self.actual_bytes = actual_bytes
        self.limit_bytes = limit_bytes
        self.actual_bytes_at_least = actual_bytes_at_least


@dataclass(frozen=True, slots=True)
class WikiMaintenancePreparedReport:
    """The complete, intentionally narrow evidence handed to a reviewer."""

    commit_id: str
    base_head: str
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
    skipped_oversized_reports: int = 0
    empty: bool = False
    replayed: bool = False
    reload_required: bool = False


WikiMaintenanceReviewer = Callable[[WikiMaintenancePreparedReport], Awaitable[WikiMaintenanceDecision]]


class WikiMaintenance:
    """Review the wiki feed in order; advance only an accepted safe prefix.

    The model is a proposer, never an authority over identity or concurrency.
    It receives no fact ledger, index, raw storage, resource IDs, or versions.
    """

    def __init__(self, store: WikiMaintenanceStore, wiki: WikiService, reviewer: WikiMaintenanceReviewer) -> None:
        self._store = store
        self._wiki = wiki
        self._reviewer = reviewer

    def validate_prepared_decision(
        self, prepared: WikiMaintenancePreparedReport, decision: WikiMaintenanceDecision
    ) -> None:
        if decision.outcome == "updates":
            self._updates(prepared, decision.updates)

    async def run(self) -> WikiMaintenanceResult:
        watermark = await self._store.get_watermark()
        expected = None if watermark is None else watermark.revision
        initial = expected
        try:
            feed = await asyncio.to_thread(
                self._wiki.maintenance_feed,
                expected,
            )
        except WikiSnapshotChangedError:
            feed = await asyncio.to_thread(
                self._wiki.maintenance_feed,
                expected,
            )
        if not feed.commits:
            return self._result(feed.through_revision, expected, initial, empty=True)

        commit_ids = tuple(commit.commit_id for commit in feed.commits)
        if feed.through_revision is None:
            raise WikiMaintenanceError("nonempty wiki maintenance feed has no target revision")
        reviewed = 0
        updated = 0
        skipped_oversized = 0
        replayed = False
        for commit_metadata in feed.commits:
            try:
                # Metadata is sufficient for commits that can never enter model
                # review. Keep their detail path cold.
                if not self._has_markdown_changes(commit_metadata) or self._is_health_projection(commit_metadata):
                    await self._advance(expected, commit_ids, commit_metadata.commit_id)
                    expected = commit_metadata.commit_id
                    reviewed += 1
                    continue
                detail = await asyncio.to_thread(
                    self._wiki.maintenance_details,
                    commit_metadata,
                    through_revision=feed.through_revision,
                    diff_char_limit=_MAX_DIFF_BYTES + 1,
                    diff_byte_budget=_MAX_PROMPT_BYTES,
                )
                commit = detail.commit
                prepared = self._prepare(detail, commit)
                if self._has_trusted_replay(feed, prepared):
                    await self._advance(expected, commit_ids, commit.commit_id)
                    expected = commit.commit_id
                    reviewed += 1
                    replayed = True
                    continue

                decision = await self._reviewer(prepared)
                self.validate_prepared_decision(prepared, decision)
                if decision.outcome == "updates":
                    updated += await self._apply(prepared, decision.updates)
                await self._advance(expected, commit_ids, commit.commit_id)
            except (WikiSnapshotChangedError, RevisionConflictError):
                return self._result(
                    feed.through_revision,
                    expected,
                    initial,
                    reviewed=reviewed,
                    updated=updated,
                    skipped_oversized=skipped_oversized,
                    replayed=replayed,
                    reload_required=True,
                )
            except (WikiMaintenanceEvidenceLimitError, WikiMaintenanceEvidenceTooLarge):
                pass
            else:
                expected = commit.commit_id
                reviewed += 1
                if decision.outcome == "updates":
                    return self._result(
                        feed.through_revision,
                        expected,
                        initial,
                        reviewed=reviewed,
                        updated=updated,
                        skipped_oversized=skipped_oversized,
                        replayed=replayed,
                        reload_required=True,
                    )
                continue

            await self._advance(expected, commit_ids, commit_metadata.commit_id)
            expected = commit_metadata.commit_id
            reviewed += 1
            skipped_oversized += 1

        return self._result(
            feed.through_revision,
            expected,
            initial,
            reviewed=reviewed,
            updated=updated,
            skipped_oversized=skipped_oversized,
            replayed=replayed,
        )

    async def _advance(self, expected: str | None, commits: Sequence[str], through: str) -> None:
        if through not in self._remaining(commits, expected):
            raise WikiMaintenanceError("wiki maintenance checkpoint is outside the current feed")
        watermark = await self._store.advance(expected_revision=expected, revision=through)
        if watermark.revision != through:
            raise WikiMaintenanceWatermarkConflictError("wiki maintenance watermark did not advance")

    @staticmethod
    def _result(
        feed_target: str | None,
        processed_through: str | None,
        initial: str | None,
        *,
        reviewed: int = 0,
        updated: int = 0,
        skipped_oversized: int = 0,
        empty: bool = False,
        replayed: bool = False,
        reload_required: bool = False,
    ) -> WikiMaintenanceResult:
        return WikiMaintenanceResult(
            feed_target_revision=feed_target,
            processed_through_revision=processed_through,
            advanced=processed_through != initial,
            complete=processed_through == feed_target and not reload_required,
            reviewed_commits=reviewed,
            updated_pages=updated,
            skipped_oversized_reports=skipped_oversized,
            empty=empty,
            replayed=replayed,
            reload_required=reload_required,
        )

    @staticmethod
    def _remaining(commits: Sequence[str], expected: str | None) -> tuple[str, ...]:
        if expected is None:
            return tuple(commits)
        try:
            return tuple(commits[commits.index(expected) + 1 :])
        except ValueError:
            # A fresh feed starts *after* the durable checkpoint, so the
            # checkpoint itself is absent from this chronological sequence.
            return tuple(commits)

    def _prepare(
        self,
        feed: WikiChangesReport | WikiMaintenanceDetails,
        commit: WikiChangeCommit,
    ) -> WikiMaintenancePreparedReport:
        # ``maintenance_details`` has already pinned, bounded, and loaded just
        # these editable current records.  Re-reading here would make a report
        # disagree with its evidence budget (and could observe a new head).
        records = feed.current_records
        page_tokens = {f"P{index:03d}": record for index, record in enumerate(records, start=1)}
        token_by_id = {record.page.page_id: token for token, record in page_tokens.items()}
        header = "\n\n".join(
            (
                "# Wiki maintenance review",
                "Only the supplied Markdown evidence is available. User edits are authoritative: preserve their "
                "intent, make no speculative insights, and never edit a generated region. Do not rename, move, "
                "archive, or combine pages. If two pages may describe the same subject, choose no_change.",
                f"Source: {self._model_provenance_category(commit)}",
            )
        )
        pieces: list[tuple[str, str, int]] = [("header", header, _MAX_HEADER_BYTES)]
        for index, change in enumerate(commit.changes, start=1):
            if not change.unified_diff_complete:
                observed = len(change.unified_diff.encode("utf-8", errors="surrogateescape"))
                raise WikiMaintenanceEvidenceTooLarge(
                    label=f"change {index} diff",
                    actual_bytes=observed,
                    limit_bytes=_MAX_DIFF_BYTES,
                    actual_bytes_at_least=True,
                )
            before = self._revision_text(change.before)
            after = self._revision_text(change.after)
            diff, diff_lossy = self._display_text(change.unified_diff)
            token = token_by_id.get(change.resource_id, "not editable")
            prefix = f"change {index}"
            before_path = "(absent)" if change.before is None else change.before.resource.path
            after_path = "(absent)" if change.after is None else change.after.resource.path
            pieces.extend(
                [
                    (
                        f"{prefix} header",
                        f"## Change ({change.action}) — {token}\nBefore path: {before_path}\nAfter path: {after_path}",
                        _MAX_HEADER_BYTES,
                    ),
                    (f"{prefix} before", "### Before\n" + before, _MAX_PAGE_SECTION_BYTES),
                    (f"{prefix} after", "### After\n" + after, _MAX_PAGE_SECTION_BYTES),
                    (
                        f"{prefix} diff",
                        "### Diff\n"
                        + ("Lossy UTF-8 display: invalid source bytes appear as U+FFFD.\n" if diff_lossy else "")
                        + "```diff\n"
                        + self._model_diff(diff)
                        + "\n```",
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
                    + "\n".join(f"- {warning.code}: inspect the affected wiki content." for warning in feed.warnings),
                    _MAX_WARNINGS_BYTES,
                )
            )
        markdown = self._bounded_markdown(pieces)
        replay_fingerprint = fingerprint(
            {
                "commit": commit.commit_id,
                "changes": [(c.action, c.resource_id, self._identity_text(c.unified_diff)) for c in commit.changes],
            }
        )
        return WikiMaintenancePreparedReport(
            commit_id=commit.commit_id,
            base_head=feed.through_revision or commit.commit_id,
            replay_fingerprint=replay_fingerprint,
            markdown=markdown,
            page_tokens=page_tokens,
        )

    @staticmethod
    def _model_provenance_category(commit: WikiChangeCommit) -> str:
        if commit.origin.startswith("wiki.automation."):
            return "scheduled automation"
        if commit.actor == "User" or commit.actor.startswith("user:"):
            return "user"
        if commit.origin == "wiki.agent":
            return "agent"
        return "system"

    def _bounded_markdown(self, pieces: Sequence[tuple[str, str, int]]) -> str:
        total = 0
        oversized: tuple[str, int, int] | None = None
        for index, (label, text, limit) in enumerate(pieces):
            encoded = text.encode("utf-8")
            if index:
                total += 2
            total += len(encoded)
            if oversized is None and len(encoded) > limit:
                oversized = (label, len(encoded), limit)
        if oversized is not None:
            label, actual, limit = oversized
            raise self._oversized(label, actual, limit)
        if total > _MAX_PROMPT_BYTES:
            raise self._oversized("total prompt", total, _MAX_PROMPT_BYTES)
        return "\n\n".join(text for _label, text, _limit in pieces)

    @staticmethod
    def _oversized(label: str, actual: int, limit: int) -> WikiMaintenanceEvidenceTooLarge:
        return WikiMaintenanceEvidenceTooLarge(
            label=label,
            actual_bytes=actual,
            limit_bytes=limit,
        )

    @staticmethod
    def _revision_text(revision) -> str:
        if revision is None:
            return "(absent)"
        if revision.page is None:
            content, lossy = WikiMaintenance._display_bytes(revision.content)
            marker = "Lossy UTF-8 display: invalid source bytes appear as U+FFFD.\n" if lossy else ""
            return (
                "Invalid Markdown: validation failed; storage metadata is hidden.\n"
                f"{marker}```markdown\n{WikiMaintenance._model_diff(content)}\n```"
            )
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
    def _identity_text(value: str) -> str | dict[str, str]:
        """Keep valid text stable while hashing surrogateescaped raw bytes exactly."""

        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return {
                "encoding": "utf-8-surrogateescape",
                "sha256": sha256(value.encode("utf-8", errors="surrogateescape")).hexdigest(),
            }
        return value

    @staticmethod
    def _display_text(value: str) -> tuple[str, bool]:
        """Return JSON-safe display text without changing revision identity inputs."""

        display = value.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace")
        return display, display != value

    @staticmethod
    def _model_diff(value: str) -> str:
        """Hide storage metadata from the maintenance reviewer."""

        return _MODEL_METADATA_LINE.sub(r"\1[opaque]", value)

    @staticmethod
    def _display_bytes(value: bytes) -> tuple[str, bool]:
        """Decode raw historical content for display while marking replacement."""

        try:
            return value.decode("utf-8"), False
        except UnicodeDecodeError:
            return value.decode("utf-8", errors="replace"), True

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

    @staticmethod
    def _has_markdown_changes(commit: WikiMaintenanceCommit) -> bool:
        return any(
            (change.before is not None and change.before.path.endswith(".md"))
            or (change.after is not None and change.after.path.endswith(".md"))
            for change in commit.changes
        )

    @staticmethod
    def _is_health_projection(commit: WikiChangeCommit | WikiMaintenanceCommit) -> bool:
        return (
            commit.actor == WIKI_HEALTH_ACTOR
            and commit.origin == WIKI_HEALTH_ORIGIN
            and commit.reason == WIKI_HEALTH_REASON
            and bool(commit.changes)
            and all(
                (
                    (change.after or change.before).resource_id
                    if isinstance(change, ResourceChange)
                    else change.resource_id
                )
                == WIKI_HEALTH_RESOURCE_ID
                for change in commit.changes
            )
        )

    def _has_trusted_replay(
        self, feed: WikiChangesReport | WikiMaintenanceFeed, prepared: WikiMaintenancePreparedReport
    ) -> bool:
        reason = self._reason(prepared)
        for commit in feed.commits:
            if (
                commit.actor != WIKI_MAINTENANCE_ACTOR
                or commit.origin != WIKI_MAINTENANCE_ORIGIN
                or commit.reason != reason
            ):
                continue
            if commit.commit_id == prepared.commit_id:
                continue
            # The exact reason binds this source commit and immutable source
            # evidence. The service already binds the actual write to its base
            # head and resource versions.
            return True
        return False

    @staticmethod
    def _reason(prepared: WikiMaintenancePreparedReport) -> str:
        return f"wiki maintenance {prepared.commit_id} {prepared.replay_fingerprint}"
