from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from arden.database import connect
from arden.memory.facts.ledger import FactLedger
from arden.memory.facts.models import FactConflictError
from arden.memory.facts.plan_store import FactPlanStore
from arden.memory.facts.service import FactPrincipal, FactService
from arden.revisions import ManagedFileRepository
from arden.wiki.curator import (
    CURATOR_ACTOR,
    CURATOR_ORIGIN,
    WikiEditCurator,
    WikiEditCuratorDecision,
    WikiFactCorrection,
)
from arden.wiki.models import GeneratedPageTarget, WikiMaintenancePageUpdate
from arden.wiki.service import WikiService

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from arden.wiki.curator import WikiEditCuratorEvidence

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 7, 28, 12, tzinfo=UTC)
SCOPE = ("area", "general")


class _Reviewer:
    def __init__(self, decide: Callable[[WikiEditCuratorEvidence], WikiEditCuratorDecision]) -> None:
        self._decide = decide
        self.calls: list[WikiEditCuratorEvidence] = []

    async def review(self, evidence: WikiEditCuratorEvidence) -> WikiEditCuratorDecision:
        self.calls.append(evidence)
        return self._decide(evidence)


def _create_fact(fact_id: str = "preference") -> dict[str, object]:
    return {
        "op": "create",
        "fact_id": fact_id,
        "text": "The user prefers tea.",
        "kind": "preference",
        "labels": ["beverage"],
        "subjects": ["User"],
        "scope": {"kind": SCOPE[0], "key": SCOPE[1]},
        "sources": [{"kind": "chat_message", "ref": "session:one"}],
    }


async def _fixture(
    tmp_path: Path,
    reviewer: _Reviewer,
) -> tuple[FactLedger, WikiService, FactService, FactPrincipal, WikiEditCurator, object]:
    ledger = FactLedger(tmp_path / "facts", clock=lambda: NOW)
    ledger.commit(ledger.plan([_create_fact()], actor="test", origin="test", reason="seed"))
    connection = await connect(tmp_path / "facts.sqlite")
    plans = FactPlanStore(connection)
    await plans.init_schema()
    fact_service = FactService(ledger, plans)
    wiki = WikiService(ManagedFileRepository(tmp_path / "wiki", clock=lambda: NOW))
    principal = FactPrincipal("user:one", frozenset({SCOPE}), frozenset({SCOPE}))
    curator = WikiEditCurator(wiki, fact_service, reviewer)
    return ledger, wiki, fact_service, principal, curator, connection


def _publish_baseline(ledger: FactLedger, wiki: WikiService) -> str:
    fact = ledger.get("preference")
    revision = ledger.revision
    assert revision is not None
    commit = wiki.publish_generated(
        (
            GeneratedPageTarget(
                page_id="preferences",
                path="preferences.md",
                title="Preferences",
                aliases=(),
                generated=b"The user prefers tea. (fact:preference)\n",
                metadata={"fact_citations": [{"fact_id": fact.fact_id, "version": fact.version}]},
            ),
        ),
        source_revision=revision,
        base_head=None,
    )
    assert commit is not None
    return commit.commit_id


def _user_edit(wiki: WikiService, old: bytes, new: bytes) -> str:
    page = wiki.read_page("preferences")
    head = wiki.repository.head
    assert head is not None
    wiki.update_page(
        "preferences",
        content=page.content.replace(old, new),
        expected_version=page.resource.version_id,
        expected_head=head,
    )
    commit_id = wiki.repository.head
    assert commit_id is not None
    return commit_id


async def test_meaningful_user_edit_produces_only_an_idempotent_pinned_plan(tmp_path: Path) -> None:
    reviewer = _Reviewer(
        lambda evidence: WikiEditCuratorDecision(
            outcome="correct_facts",
            reason="The explicit user edit corrects the beverage preference.",
            corrections=[
                WikiFactCorrection(
                    target_token=next(iter(evidence.fact_tokens)),
                    corrected_text="The user prefers coffee.",
                    reason="The user replaced tea with coffee.",
                )
            ],
        )
    )
    ledger, wiki, _facts, principal, curator, connection = await _fixture(tmp_path, reviewer)
    try:
        baseline_commit = _publish_baseline(ledger, wiki)
        original = ledger.get("preference")
        user_commit = _user_edit(wiki, b"prefers tea", b"prefers coffee")

        first = await curator.reconcile(user_commit, principal)
        second = await curator.reconcile(user_commit, principal)

        assert first.outcome == "planned"
        assert first.baseline_commit_id == baseline_commit
        assert first.plan is not None and second.plan is not None
        assert first.plan.plan_id == second.plan.plan_id
        assert first.plan.dependencies == {"preference": original.version}
        assert [event.op for event in first.plan.events] == ["create", "supersede"]
        assert {(event.actor, event.origin) for event in first.plan.events} == {(CURATOR_ACTOR, CURATOR_ORIGIN)}
        created = first.plan.events[0]
        assert created.record["payload"]["text"] == "The user prefers coffee."
        assert any(
            source["kind"] == "fact"
            and source["ref"] == original.fact_id
            and source["extra"]["version"] == original.version
            for source in created.record["payload"]["sources"]
        )
        assert any(
            source["kind"] == "wiki_page" and source["extra"]["wiki_commit_id"] == user_commit
            for source in created.record["payload"]["sources"]
        )
        assert ledger.get("preference") == original
        assert len(reviewer.calls) == 2
        assert reviewer.calls[0].generated_baseline != reviewer.calls[0].committed_user_edit
        assert reviewer.calls[0].before_user_edit == reviewer.calls[0].generated_baseline
    finally:
        await connection.close()


async def test_formatting_only_decision_creates_no_plan(tmp_path: Path) -> None:
    reviewer = _Reviewer(
        lambda _evidence: WikiEditCuratorDecision(
            outcome="no_change",
            reason="Only formatting changed.",
        )
    )
    ledger, wiki, _facts, principal, curator, connection = await _fixture(tmp_path, reviewer)
    try:
        _publish_baseline(ledger, wiki)
        user_commit = _user_edit(wiki, b"prefers tea", b"prefers  tea")

        result = await curator.reconcile(user_commit, principal)

        assert result.outcome == "no_change"
        assert result.plan is None
        assert len(reviewer.calls) == 1
    finally:
        await connection.close()


async def test_synthesis_and_maintenance_commits_never_reach_reviewer(tmp_path: Path) -> None:
    reviewer = _Reviewer(lambda _evidence: WikiEditCuratorDecision(outcome="no_change", reason="Should not be called."))
    ledger, wiki, _facts, principal, curator, connection = await _fixture(tmp_path, reviewer)
    try:
        synthesis_commit = _publish_baseline(ledger, wiki)
        synthesis_result = await curator.reconcile(synthesis_commit, principal)

        page = wiki.read_page("preferences")
        head = wiki.repository.head
        assert head is not None
        maintenance_commit = wiki.apply_maintenance_updates(
            (
                WikiMaintenancePageUpdate(
                    page_id=page.page.page_id,
                    expected_version=page.resource.version_id,
                    title=page.page.title,
                    aliases=page.page.aliases,
                    body=page.page.body + b"\nUser-owned note.\n",
                ),
            ),
            base_head=head,
        )
        maintenance_result = await curator.reconcile(maintenance_commit, principal)

        assert synthesis_result.outcome == maintenance_result.outcome == "ignored"
        assert reviewer.calls == []
    finally:
        await connection.close()


async def test_changed_fact_after_baseline_rejects_stale_correction_plan(tmp_path: Path) -> None:
    reviewer = _Reviewer(
        lambda evidence: WikiEditCuratorDecision(
            outcome="correct_facts",
            reason="Meaningful correction.",
            corrections=[
                WikiFactCorrection(
                    target_token=next(iter(evidence.fact_tokens)),
                    corrected_text="The user prefers coffee.",
                    reason="Explicit correction.",
                )
            ],
        )
    )
    ledger, wiki, _facts, principal, curator, connection = await _fixture(tmp_path, reviewer)
    try:
        _publish_baseline(ledger, wiki)
        stale_version = ledger.get("preference").version
        ledger.commit(
            ledger.plan(
                [
                    {
                        "op": "amend",
                        "fact_id": "preference",
                        "expected_version": stale_version,
                        "labels": ["beverage", "updated"],
                    }
                ],
                actor="test",
                origin="test",
                reason="concurrent fact update",
            )
        )
        user_commit = _user_edit(wiki, b"prefers tea", b"prefers coffee")

        with pytest.raises(FactConflictError, match="evidence changed"):
            await curator.reconcile(user_commit, principal)
    finally:
        await connection.close()


async def test_reviewer_cannot_select_a_fact_outside_baseline_tokens(tmp_path: Path) -> None:
    reviewer = _Reviewer(
        lambda _evidence: WikiEditCuratorDecision(
            outcome="correct_facts",
            reason="Invalid target.",
            corrections=[
                WikiFactCorrection(
                    target_token="F999",
                    corrected_text="Unrelated correction.",
                    reason="Invalid target.",
                )
            ],
        )
    )
    ledger, wiki, _facts, principal, curator, connection = await _fixture(tmp_path, reviewer)
    try:
        _publish_baseline(ledger, wiki)
        user_commit = _user_edit(wiki, b"prefers tea", b"prefers coffee")

        with pytest.raises(RuntimeError, match="unknown fact token"):
            await curator.reconcile(user_commit, principal)
    finally:
        await connection.close()
