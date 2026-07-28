"""Canonical fact-correction planning for explicit user wiki edits."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC
from types import MappingProxyType
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from arden.memory.facts.models import Fact
from arden.memory.facts.service import FactPlanPreview, FactPrincipal, FactScopeError, FactService
from arden.revisions import Commit, ResourceChange, ResourceState

from .models import WikiFactCitation
from .pages import WikiPage, parse_page
from .service import WikiService

USER_ACTOR = "user:desktop"
USER_ORIGIN = "desktop"
SYNTHESIS_ACTOR = "Synthesis"
SYNTHESIS_ORIGIN = "memory.synthesis"
CURATOR_ACTOR = "Curator"
CURATOR_ORIGIN = "memory.curator"
_MAX_REASON = 1_000
_MAX_CORRECTIONS = 20


class WikiEditCuratorError(RuntimeError):
    """A user-edit correction could not be planned from canonical evidence."""


class WikiFactCorrection(BaseModel):
    """One full-text correction addressed by a run-local opaque fact token."""

    model_config = ConfigDict(extra="forbid")

    target_token: str = Field(min_length=1, max_length=20)
    corrected_text: str = Field(min_length=1, max_length=100_000)
    reason: str = Field(min_length=1, max_length=_MAX_REASON)


class WikiEditCuratorDecision(BaseModel):
    """Constrained semantic classification of one committed user edit."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["no_change", "correct_facts"]
    reason: str = Field(min_length=1, max_length=_MAX_REASON)
    corrections: list[WikiFactCorrection] = Field(default_factory=list, max_length=_MAX_CORRECTIONS)

    @model_validator(mode="after")
    def _validate_outcome_shape(self) -> WikiEditCuratorDecision:
        if self.outcome == "no_change" and self.corrections:
            raise ValueError("no_change must not include corrections")
        if self.outcome == "correct_facts" and not self.corrections:
            raise ValueError("correct_facts requires at least one correction")
        tokens = [item.target_token for item in self.corrections]
        if len(tokens) != len(set(tokens)):
            raise ValueError("one decision cannot correct the same fact twice")
        return self


@dataclass(frozen=True, slots=True)
class WikiEditCuratorEvidence:
    """Exact baseline, pre-edit, edit, and cited facts shown to the reviewer."""

    commit_id: str
    baseline_commit_id: str
    source_revision: str
    page_id: str
    path: str
    generated_baseline: str
    before_user_edit: str
    committed_user_edit: str
    fact_tokens: Mapping[str, Fact]


class WikiEditCuratorReviewer(Protocol):
    async def review(self, evidence: WikiEditCuratorEvidence) -> WikiEditCuratorDecision: ...


@dataclass(frozen=True, slots=True)
class WikiEditCuratorResult:
    """One plan-only reconciliation result; no fact events are committed."""

    commit_id: str
    outcome: Literal["ignored", "no_change", "planned"]
    reason: str
    page_id: str | None = None
    baseline_commit_id: str | None = None
    plan: FactPlanPreview | None = None


@dataclass(frozen=True, slots=True)
class _PreparedEdit:
    commit: Commit
    change: ResourceChange
    baseline_commit: Commit
    baseline_change: ResourceChange
    source_revision: str
    before_content: bytes
    after_content: bytes
    baseline_content: bytes
    citations: tuple[WikiFactCitation, ...]


class WikiEditCurator:
    """Review eligible wiki commits and persist version-pinned fact plans."""

    def __init__(
        self,
        wiki: WikiService,
        facts: FactService,
        reviewer: WikiEditCuratorReviewer,
    ) -> None:
        self._wiki = wiki
        self._facts = facts
        self._reviewer = reviewer

    async def reconcile(self, commit_id: str, principal: FactPrincipal) -> WikiEditCuratorResult:
        """Plan canonical corrections for one exact user-originated wiki commit."""

        if not isinstance(commit_id, str) or not commit_id:
            raise ValueError("commit_id must be a nonempty string")
        if not isinstance(principal, FactPrincipal):
            raise TypeError("principal must be a FactPrincipal")

        commit = self._reachable_commit(commit_id)
        if (commit.actor, commit.origin) != (USER_ACTOR, USER_ORIGIN):
            return WikiEditCuratorResult(commit_id, "ignored", "commit is not an explicit user wiki edit")

        prepared = self._prepare(commit)
        if prepared is None:
            return WikiEditCuratorResult(commit_id, "ignored", "commit is not one ordinary page update")

        page_id = self._page_id(prepared.change)
        facts = await asyncio.to_thread(self._facts.ledger.facts_at, prepared.source_revision)
        token_facts = self._cited_facts(prepared.citations, facts, principal)
        if not token_facts:
            return WikiEditCuratorResult(
                commit_id,
                "ignored",
                "generated baseline cites no canonical facts",
                page_id=page_id,
                baseline_commit_id=prepared.baseline_commit.commit_id,
            )

        evidence = WikiEditCuratorEvidence(
            commit_id=commit_id,
            baseline_commit_id=prepared.baseline_commit.commit_id,
            source_revision=prepared.source_revision,
            page_id=page_id,
            path=prepared.change.after.path,
            generated_baseline=prepared.baseline_content.decode("utf-8", errors="strict"),
            before_user_edit=prepared.before_content.decode("utf-8", errors="strict"),
            committed_user_edit=prepared.after_content.decode("utf-8", errors="strict"),
            fact_tokens=MappingProxyType(token_facts),
        )
        returned = await self._reviewer.review(evidence)
        decision = (
            returned
            if isinstance(returned, WikiEditCuratorDecision)
            else WikiEditCuratorDecision.model_validate(returned)
        )
        if decision.outcome == "no_change":
            return WikiEditCuratorResult(
                commit_id,
                "no_change",
                decision.reason,
                page_id=page_id,
                baseline_commit_id=prepared.baseline_commit.commit_id,
            )

        changes = self._correction_changes(prepared, token_facts, decision)
        plan = await self._facts.plan(
            principal,
            changes,
            request_key=f"wiki-edit-curator:{commit_id}",
            actor=CURATOR_ACTOR,
            origin=CURATOR_ORIGIN,
            reason=decision.reason,
        )
        return WikiEditCuratorResult(
            commit_id,
            "planned",
            decision.reason,
            page_id=page_id,
            baseline_commit_id=prepared.baseline_commit.commit_id,
            plan=plan,
        )

    def _reachable_commit(self, commit_id: str) -> Commit:
        commits = self._wiki.repository.history(start=commit_id, limit=1)
        if not commits or commits[0].commit_id != commit_id:
            raise KeyError(f"unknown wiki commit: {commit_id}")
        return commits[0]

    def _prepare(self, commit: Commit) -> _PreparedEdit | None:
        if len(commit.changes) != 1:
            return None
        change = commit.changes[0]
        if (
            change.action != "update"
            or change.before is None
            or change.after is None
            or change.before.resource_id != change.after.resource_id
            or change.before.path != change.after.path
            or change.before.state is not ResourceState.ACTIVE
            or change.after.state is not ResourceState.ACTIVE
            or not change.after.path.endswith(".md")
        ):
            return None

        page_id = change.after.resource_id
        before_content = self._wiki.repository.read_version(change.before)
        after_content = self._wiki.repository.read_version(change.after)
        before_page = parse_page(before_content, expected_page_id=page_id)
        after_page = parse_page(after_content, expected_page_id=page_id)
        if before_page.lifecycle != "active" or after_page.lifecycle != "active":
            return None

        baseline = self._last_generated_baseline(page_id, commit.parent_id)
        if baseline is None:
            return None
        baseline_commit, baseline_change = baseline
        assert baseline_change.after is not None
        baseline_content = self._wiki.repository.read_version(baseline_change.after)
        baseline_page = parse_page(baseline_content, expected_page_id=page_id)
        source_revision = baseline_page.metadata.get("generated_from_revision")
        if not isinstance(source_revision, str) or not source_revision:
            raise WikiEditCuratorError("generated baseline has no canonical fact revision")
        citations = self._citations(baseline_page)
        return _PreparedEdit(
            commit=commit,
            change=change,
            baseline_commit=baseline_commit,
            baseline_change=baseline_change,
            source_revision=source_revision,
            before_content=before_content,
            after_content=after_content,
            baseline_content=baseline_content,
            citations=citations,
        )

    def _last_generated_baseline(
        self,
        page_id: str,
        start: str | None,
    ) -> tuple[Commit, ResourceChange] | None:
        if start is None:
            return None
        for commit in self._wiki.repository.history(resource_id=page_id, start=start):
            if (commit.actor, commit.origin) != (SYNTHESIS_ACTOR, SYNTHESIS_ORIGIN):
                continue
            change = next(
                (
                    item
                    for item in commit.changes
                    if item.after is not None
                    and item.after.resource_id == page_id
                    and item.after.state is ResourceState.ACTIVE
                ),
                None,
            )
            if change is not None:
                return commit, change
        return None

    @staticmethod
    def _citations(page: WikiPage) -> tuple[WikiFactCitation, ...]:
        raw = page.metadata.get("fact_citations")
        if raw is None:
            return ()
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise WikiEditCuratorError("generated baseline has invalid fact citations")
        result: list[WikiFactCitation] = []
        seen: dict[str, str] = {}
        for item in raw:
            if not isinstance(item, Mapping) or set(item) != {"fact_id", "version"}:
                raise WikiEditCuratorError("generated baseline has invalid fact citations")
            try:
                citation = WikiFactCitation(item["fact_id"], item["version"])
            except (TypeError, ValueError) as exc:
                raise WikiEditCuratorError("generated baseline has invalid fact citations") from exc
            prior = seen.setdefault(citation.fact_id, citation.version)
            if prior != citation.version:
                raise WikiEditCuratorError("generated baseline cites two versions of one fact")
            if citation not in result:
                result.append(citation)
        return tuple(result)

    @staticmethod
    def _cited_facts(
        citations: tuple[WikiFactCitation, ...],
        facts: Mapping[str, Fact],
        principal: FactPrincipal,
    ) -> dict[str, Fact]:
        result: dict[str, Fact] = {}
        for index, citation in enumerate(citations):
            fact = facts.get(citation.fact_id)
            if fact is None or fact.version != citation.version:
                raise WikiEditCuratorError("generated baseline citation does not match its fact revision")
            scope = str(fact.scope["kind"]), fact.scope["key"]
            if not principal.can_read(scope):
                raise FactScopeError("generated fact is outside the current readable scope")
            result[f"F{index:03d}"] = fact
        return result

    @staticmethod
    def _correction_changes(
        prepared: _PreparedEdit,
        facts: Mapping[str, Fact],
        decision: WikiEditCuratorDecision,
    ) -> list[dict[str, object]]:
        changes: list[dict[str, object]] = []
        for correction in decision.corrections:
            try:
                target = facts[correction.target_token]
            except KeyError as exc:
                raise WikiEditCuratorError("reviewer selected an unknown fact token") from exc
            if correction.corrected_text == target.text:
                raise WikiEditCuratorError("reviewer returned unchanged fact text as a correction")
            change: dict[str, object] = {
                "op": "create",
                "text": correction.corrected_text,
                "kind": target.kind,
                "labels": list(target.labels),
                "subjects": list(target.subjects),
                "scope": dict(target.scope),
                "lifecycle": target.lifecycle,
                "certainty": "confirmed",
                "evidence_class": "direct",
                "sources": [
                    {
                        "kind": "wiki_page",
                        "ref": f"{prepared.change.after.resource_id}@{prepared.commit.commit_id}",
                        "captured_at": prepared.commit.timestamp.astimezone(UTC).isoformat(),
                        "scope_kind": target.scope["kind"],
                        "scope_key": target.scope["key"],
                        "role": "user_correction",
                        "extra": {
                            "wiki_commit_id": prepared.commit.commit_id,
                            "page_version": prepared.change.after.version_id,
                            "baseline_commit_id": prepared.baseline_commit.commit_id,
                            "baseline_page_version": prepared.baseline_change.after.version_id,
                        },
                    },
                    {
                        "kind": "fact",
                        "ref": target.fact_id,
                        "scope_kind": target.scope["kind"],
                        "scope_key": target.scope["key"],
                        "role": "corrected_fact",
                        "extra": {"version": target.version},
                    },
                ],
                "supersedes": [target.fact_id],
                "reason": correction.reason,
            }
            if target.review_at is not None:
                change["review_at"] = target.review_at.astimezone(UTC).isoformat()
                change["review_basis"] = target.review_basis
            if target.expires_at is not None:
                change["expires_at"] = target.expires_at.astimezone(UTC).isoformat()
            changes.append(change)
        return changes

    @staticmethod
    def _page_id(change: ResourceChange) -> str:
        resource = change.after or change.before
        assert resource is not None
        return resource.resource_id
