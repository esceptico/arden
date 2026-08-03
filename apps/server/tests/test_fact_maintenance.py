from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from arden.database import connect
from arden.memory.facts.consumer_store import FactConsumerStore
from arden.memory.facts.ledger import FactLedger
from arden.memory.facts.maintenance.runner import (
    CONSUMER_ID,
    ORIGIN,
    FactMaintenance,
    FactMaintenanceDecision,
    FactMaintenanceError,
    FactMaintenancePreparedCluster,
)
from arden.memory.facts.models import Fact, FactConflictError, FactValidationError
from arden.memory.facts.plan_store import FactPlanStore
from arden.memory.facts.service import FactService
from arden.revisions import Archive, ChangeSet, Create, ManagedFileRepository, RevisionConflictError, Update
from arden.wiki.pages import create_page as wiki_create_page
from arden.wiki.service import WikiService

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 7, 28, 12, tzinfo=UTC)


def _create(
    fact_id: str,
    text: str,
    *,
    labels: list[str] | None = None,
    subjects: list[str] | None = None,
    scope: dict[str, str | None] | None = None,
    evidence_class: str = "direct",
) -> dict[str, object]:
    return {
        "op": "create",
        "fact_id": fact_id,
        "text": text,
        "kind": "fact",
        "labels": labels or [],
        "subjects": subjects or ["Alpha"],
        "scope": scope or {"kind": "user", "key": None},
        "sources": [{"kind": "test", "ref": fact_id}],
        "evidence_class": evidence_class,
    }


def _commit(ledger: FactLedger, *changes: dict[str, object]) -> str:
    plan = ledger.plan(changes, actor="test", origin="test", reason="test facts")
    ledger.commit(plan)
    assert ledger.revision is not None
    return ledger.revision


class _Reviewer:
    def __init__(
        self,
        decide: Callable[[FactMaintenancePreparedCluster], FactMaintenanceDecision] | None = None,
    ) -> None:
        self.decide = decide or (lambda _cluster: FactMaintenanceDecision(outcome="no_change", reason="No change."))
        self.calls: list[FactMaintenancePreparedCluster] = []

    async def __call__(self, cluster: FactMaintenancePreparedCluster) -> FactMaintenanceDecision:
        self.calls.append(cluster)
        return self.decide(cluster)


class _FailingReviewer(_Reviewer):
    async def __call__(self, cluster: FactMaintenancePreparedCluster) -> FactMaintenanceDecision:
        self.calls.append(cluster)
        raise RuntimeError("review unavailable")


class _Provider:
    def __init__(self, ids: list[str]) -> None:
        self.ids = ids
        self.calls: list[tuple[str, int]] = []

    async def semantic_candidates(self, fact: Fact, *, limit: int) -> list[str]:
        self.calls.append((fact.fact_id, limit))
        return self.ids


async def _service(
    tmp_path: Path,
    reviewer: _Reviewer,
    *,
    provider: _Provider | None = None,
    post_commit=None,
    wiki: WikiService | None = None,
) -> tuple[FactLedger, FactConsumerStore, object, FactMaintenance]:
    ledger = FactLedger(tmp_path / "facts", clock=lambda: NOW)
    connection = await connect(tmp_path / "facts.sqlite")
    plans = FactPlanStore(connection)
    await plans.init_schema()
    consumers = await FactConsumerStore.open(tmp_path / "facts.sqlite")
    service = FactService(ledger, plans, post_commit=post_commit)
    maintenance = FactMaintenance(
        service,
        consumers,
        reviewer,
        wiki=wiki or WikiService(ManagedFileRepository(tmp_path / "wiki")),
        candidate_provider=provider,
        candidate_limit=3,
    )
    return ledger, consumers, connection, maintenance


async def _baseline(ledger: FactLedger, consumers: FactConsumerStore) -> str:
    feed = ledger.changes_since(None)
    assert feed.events
    return (await consumers.advance(CONSUMER_ID, feed=feed, ledger=ledger)).revision


def _group_facts(cluster: FactMaintenancePreparedCluster, tokens: tuple[str, ...]) -> list[str]:
    return [cluster.fact_tokens[token].fact_id for token in tokens]


async def test_candidate_clusters_are_bounded_and_ordered_before_optional_semantics(tmp_path: Path) -> None:
    reviewer = _Reviewer()
    provider = _Provider(["semantic", "shared", "cross-scope", "missing", "target"])
    ledger, consumers, connection, maintenance = await _service(tmp_path, reviewer, provider=provider)
    try:
        _commit(
            ledger,
            _create("shared", "Different claim", subjects=["Alpha", "Shared"]),
            _create("exact", "Target wording", subjects=["Other"]),
            _create("semantic", "Paraphrased claim", subjects=["Elsewhere"]),
            _create(
                "cross-scope",
                "Cross scope",
                subjects=["Elsewhere"],
                scope={"kind": "project", "key": "other"},
            ),
        )
        await _baseline(ledger, consumers)
        target_revision = _commit(ledger, _create("target", "Target wording", subjects=["Alpha"]))

        result = await maintenance.run()

        assert result.advanced and result.reviewed_clusters == 1
        assert (await consumers.get(CONSUMER_ID)).revision == target_revision
        assert provider.calls == [("target", 3)]
        cluster = reviewer.calls[0]
        assert cluster.fact_tokens[cluster.target_token].fact_id == "target"
        assert _group_facts(cluster, cluster.shared_subject_tokens) == ["shared"]
        assert _group_facts(cluster, cluster.exact_text_tokens) == ["exact"]
        assert _group_facts(cluster, cluster.semantic_tokens) == ["semantic"]
        assert cluster.markdown.index("Shared-subject candidates") < cluster.markdown.index("Exact-text candidates")
        assert cluster.markdown.index("Exact-text candidates") < cluster.markdown.index("Semantic candidates")
    finally:
        await consumers.close()
        await connection.close()


async def test_one_maintenance_plan_amends_and_merges_then_advances_before_its_own_events(
    tmp_path: Path,
) -> None:
    notifications = 0

    async def post_commit() -> None:
        nonlocal notifications
        notifications += 1

    def decide(cluster: FactMaintenancePreparedCluster) -> FactMaintenanceDecision:
        target = cluster.fact_tokens[cluster.target_token]
        if target.fact_id == "metadata":
            return FactMaintenanceDecision(
                outcome="amend_metadata",
                reason="Correct classification.",
                target_token=cluster.target_token,
                kind="note",
                labels=[],
                subjects=["Corrected"],
                lifecycle="temporary",
                evidence_class="inferred",
            )
        survivor_token = next(token for token, fact in cluster.fact_tokens.items() if fact.fact_id == "survivor")
        return FactMaintenanceDecision(
            outcome="merge_duplicate",
            reason="Same underlying claim.",
            discard_token=cluster.target_token,
            survivor_token=survivor_token,
        )

    reviewer = _Reviewer(decide)
    ledger, consumers, connection, maintenance = await _service(
        tmp_path,
        reviewer,
        post_commit=post_commit,
    )
    try:
        _commit(ledger, _create("survivor", "Duplicate claim", subjects=["Duplicate"]))
        await _baseline(ledger, consumers)
        input_revision = _commit(
            ledger,
            _create(
                "metadata",
                "Metadata correction",
                labels=["Stale"],
                subjects=["Wrong"],
            ),
            _create("duplicate", "Duplicate claim", subjects=["Duplicate"]),
        )

        result = await maintenance.run()

        assert result.advanced
        assert (result.reviewed_clusters, result.amended_facts, result.merged_facts) == (2, 1, 1)
        assert notifications == 1
        assert (await consumers.get(CONSUMER_ID)).revision == input_revision

        metadata = ledger.get("metadata")
        assert metadata.kind == "note"
        assert metadata.labels == ()
        assert metadata.subjects == ("Corrected",)
        assert metadata.lifecycle == "temporary"
        assert metadata.evidence_class == "inferred"
        assert metadata.sources == ({"kind": "test", "ref": "metadata"},)

        duplicate = ledger.get("duplicate")
        assert duplicate.status == "superseded"
        assert duplicate.successor_id == "survivor"
        maintenance_events = [
            event
            for fact_id in ("metadata", "duplicate")
            for event in ledger.history(fact_id)
            if event.origin == ORIGIN
        ]
        assert {event.actor for event in maintenance_events} == {"automation:builtin-memory-consolidate"}
        merge = next(event for event in maintenance_events if event.op == "supersede")
        assert merge.record["payload"]["maintenance_merge"] is True

        reviewed_calls = len(reviewer.calls)
        own_event_result = await maintenance.run()
        assert own_event_result.advanced and own_event_result.reviewed_clusters == 0
        assert len(reviewer.calls) == reviewed_calls
        assert notifications == 1
        assert (await consumers.get(CONSUMER_ID)).revision == ledger.revision
    finally:
        await consumers.close()
        await connection.close()


async def test_invalid_reviewer_target_and_review_failure_do_not_advance(tmp_path: Path) -> None:
    def wrong_target(cluster: FactMaintenancePreparedCluster) -> FactMaintenanceDecision:
        candidate = next(token for token in cluster.fact_tokens if token != cluster.target_token)
        return FactMaintenanceDecision(
            outcome="amend_metadata",
            reason="Wrong target.",
            target_token=candidate,
            labels=["Invalid"],
        )

    reviewer = _Reviewer(wrong_target)
    ledger, consumers, connection, maintenance = await _service(tmp_path, reviewer)
    try:
        _commit(ledger, _create("candidate", "Candidate"))
        baseline = await _baseline(ledger, consumers)
        _commit(ledger, _create("target", "Target"))

        with pytest.raises(FactMaintenanceError, match="changed fact"):
            await maintenance.run()
        assert (await consumers.get(CONSUMER_ID)).revision == baseline
        assert ledger.get("target").labels == ()
    finally:
        await consumers.close()
        await connection.close()

    failing = _FailingReviewer()
    ledger, consumers, connection, maintenance = await _service(tmp_path / "failure", failing)
    try:
        _commit(ledger, _create("baseline", "Baseline"))
        baseline = await _baseline(ledger, consumers)
        _commit(ledger, _create("later", "Later"))

        with pytest.raises(RuntimeError, match="review unavailable"):
            await maintenance.run()
        assert (await consumers.get(CONSUMER_ID)).revision == baseline
    finally:
        await consumers.close()
        await connection.close()


async def test_memory_maintenance_origin_is_hard_limited_below_the_reviewer(tmp_path: Path) -> None:
    ledger = FactLedger(tmp_path / "facts", clock=lambda: NOW)
    _commit(
        ledger,
        _create("old", "Old"),
        _create("successor", "Successor"),
        _create(
            "other-scope",
            "Other",
            scope={"kind": "project", "key": "other"},
        ),
    )

    with pytest.raises(FactValidationError, match="only amend metadata or merge duplicates"):
        ledger.plan(
            [_create("forbidden", "Forbidden")],
            actor="maintenance",
            origin=ORIGIN,
            reason="must fail",
        )
    with pytest.raises(FactValidationError, match="only classification metadata"):
        ledger.plan(
            [
                {
                    "op": "amend",
                    "fact_id": "old",
                    "scope": {"kind": "project", "key": "other"},
                }
            ],
            actor="maintenance",
            origin=ORIGIN,
            reason="must fail",
        )
    with pytest.raises(FactValidationError, match="only classification metadata"):
        ledger.plan(
            [
                {
                    "op": "amend",
                    "fact_id": "old",
                    "sources": [{"kind": "test", "ref": "replacement"}],
                }
            ],
            actor="maintenance",
            origin=ORIGIN,
            reason="must fail",
        )
    with pytest.raises(FactValidationError, match="only amend metadata or merge duplicates"):
        ledger.plan(
            [{"op": "supersede", "fact_id": "old", "successor_id": "successor", "reason": "not marked"}],
            actor="maintenance",
            origin=ORIGIN,
            reason="must fail",
        )
    with pytest.raises(FactValidationError, match="reserved"):
        ledger.plan(
            [
                {
                    "op": "supersede",
                    "fact_id": "old",
                    "successor_id": "successor",
                    "reason": "spoofed",
                    "maintenance_merge": True,
                }
            ],
            actor="ordinary",
            origin="ordinary",
            reason="must fail",
        )
    with pytest.raises(FactValidationError, match="cannot cross fact scopes"):
        ledger.plan(
            [
                {
                    "op": "supersede",
                    "fact_id": "old",
                    "successor_id": "other-scope",
                    "reason": "cross scope",
                    "maintenance_merge": True,
                }
            ],
            actor="maintenance",
            origin=ORIGIN,
            reason="must fail",
        )


async def test_normalize_topic_adds_alias_then_relabels_every_active_exact_subject(tmp_path: Path) -> None:
    wiki = WikiService(ManagedFileRepository(tmp_path / "wiki"))
    original_page = wiki.create_page(
        path="topics/bicycle.md",
        title="Bicycle",
        page_id="page-bicycle-secret",
        body=b"User notes stay exact.\n",
    )

    def decide(cluster: FactMaintenancePreparedCluster) -> FactMaintenanceDecision:
        assert "page-bicycle-secret" not in cluster.markdown
        assert "topics/bicycle.md" not in cluster.markdown
        page_token = next(token for token, title in cluster.wiki_page_tokens.items() if title == "Bicycle")
        return FactMaintenanceDecision(
            outcome="normalize_topic",
            reason="Bike and Bicycle name the same topic.",
            old_topic="Bike",
            canonical_page_token=page_token,
        )

    reviewer = _Reviewer(decide)
    ledger, consumers, connection, maintenance = await _service(tmp_path, reviewer, wiki=wiki)
    try:
        _commit(
            ledger,
            _create("existing", "Existing claim", subjects=["Bike", "Bicycle", "Transport"]),
            _create("inactive", "Inactive claim", subjects=["Bike"]),
            _create("successor", "Replacement", subjects=["Elsewhere"]),
        )
        _commit(
            ledger,
            {
                "op": "supersede",
                "fact_id": "inactive",
                "successor_id": "successor",
                "reason": "fixture",
            },
        )
        await _baseline(ledger, consumers)
        input_revision = _commit(ledger, _create("target", "New claim", subjects=["Bike"]))

        result = await maintenance.run()

        assert result.advanced
        assert (result.reviewed_clusters, result.amended_facts, result.merged_facts) == (1, 2, 0)
        assert (await consumers.get(CONSUMER_ID)).revision == input_revision
        assert ledger.get("target").subjects == ("Bicycle",)
        assert ledger.get("existing").subjects == ("Bicycle", "Transport")
        assert ledger.get("inactive").subjects == ("Bike",)

        page = wiki.read_page("page-bicycle-secret")
        assert page.page.aliases == ("Bike",)
        assert page.page.body == original_page.page.body
        commit = wiki.repository.history(resource_id="page-bicycle-secret", limit=1)[0]
        assert (commit.actor, commit.origin) == ("automation:builtin-memory-consolidate", ORIGIN)
    finally:
        await consumers.close()
        await connection.close()


async def test_normalize_topic_rejects_unknown_page_token_without_advancing(tmp_path: Path) -> None:
    wiki = WikiService(ManagedFileRepository(tmp_path / "wiki"))
    wiki.create_page(path="bicycle.md", title="Bicycle", page_id="bicycle-page")
    reviewer = _Reviewer(
        lambda _cluster: FactMaintenanceDecision(
            outcome="normalize_topic",
            reason="Invalid raw page identifier.",
            old_topic="Bike",
            canonical_page_token="bicycle-page",
        )
    )
    ledger, consumers, connection, maintenance = await _service(tmp_path, reviewer, wiki=wiki)
    try:
        _commit(ledger, _create("baseline", "Baseline"))
        baseline = await _baseline(ledger, consumers)
        _commit(ledger, _create("target", "Target", subjects=["Bike"]))

        with pytest.raises(FactMaintenanceError, match="unknown wiki page token"):
            await maintenance.run()

        assert (await consumers.get(CONSUMER_ID)).revision == baseline
        assert ledger.get("target").subjects == ("Bike",)
        assert wiki.read_page("bicycle-page").page.aliases == ()
    finally:
        await consumers.close()
        await connection.close()


async def test_normalize_topic_wiki_conflict_preserves_intent_and_retries_without_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wiki = WikiService(ManagedFileRepository(tmp_path / "wiki"))
    wiki.create_page(path="bicycle.md", title="Bicycle", page_id="bicycle-page")

    def decide(cluster: FactMaintenancePreparedCluster) -> FactMaintenanceDecision:
        token = next(token for token, title in cluster.wiki_page_tokens.items() if title == "Bicycle")
        return FactMaintenanceDecision(
            outcome="normalize_topic",
            reason="Normalize after a conflicting wiki edit.",
            old_topic="Bike",
            canonical_page_token=token,
        )

    reviewer = _Reviewer(decide)
    ledger, consumers, connection, maintenance = await _service(tmp_path, reviewer, wiki=wiki)
    original_apply = wiki.apply_maintenance_updates
    conflicted = False

    def conflict_once(*args, **kwargs):
        nonlocal conflicted
        if not conflicted:
            conflicted = True
            wiki.create_page(path="concurrent.md", title="Concurrent", page_id="concurrent-page")
        return original_apply(*args, **kwargs)

    monkeypatch.setattr(wiki, "apply_maintenance_updates", conflict_once)
    try:
        _commit(ledger, _create("baseline", "Baseline"))
        baseline = await _baseline(ledger, consumers)
        _commit(ledger, _create("target", "Target", subjects=["Bike"]))

        with pytest.raises(RevisionConflictError):
            await maintenance.run()

        assert (await consumers.get(CONSUMER_ID)).revision == baseline
        assert ledger.get("target").subjects == ("Bike",)
        assert wiki.read_page("bicycle-page").page.aliases == ()

        result = await maintenance.run()
        assert result.advanced and ledger.get("target").subjects == ("Bicycle",)
        assert wiki.read_page("bicycle-page").page.aliases == ("Bike",)
        assert len(reviewer.calls) == 1
    finally:
        await consumers.close()
        await connection.close()


async def test_normalize_topic_fact_conflict_keeps_alias_and_retry_converges(tmp_path: Path) -> None:
    wiki = WikiService(ManagedFileRepository(tmp_path / "wiki"))
    wiki.create_page(path="bicycle.md", title="Bicycle", page_id="bicycle-page")
    mutated = False
    ledger: FactLedger

    def decide(cluster: FactMaintenancePreparedCluster) -> FactMaintenanceDecision:
        nonlocal mutated
        if not mutated:
            mutated = True
            current = ledger.get("target")
            _commit(
                ledger,
                {
                    "op": "amend",
                    "fact_id": "target",
                    "expected_version": current.version,
                    "labels": ["concurrent"],
                },
            )
        token = next(token for token, title in cluster.wiki_page_tokens.items() if title == "Bicycle")
        return FactMaintenanceDecision(
            outcome="normalize_topic",
            reason="Normalize with version-pinned facts.",
            old_topic="Bike",
            canonical_page_token=token,
        )

    reviewer = _Reviewer(decide)
    ledger, consumers, connection, maintenance = await _service(tmp_path, reviewer, wiki=wiki)
    try:
        _commit(ledger, _create("baseline", "Baseline"))
        baseline = await _baseline(ledger, consumers)
        _commit(ledger, _create("target", "Target", subjects=["Bike"]))

        with pytest.raises(FactConflictError):
            await maintenance.run()

        assert (await consumers.get(CONSUMER_ID)).revision == baseline
        assert ledger.get("target").subjects == ("Bike",)
        assert wiki.read_page("bicycle-page").page.aliases == ()

        result = await maintenance.run()
        assert result.advanced and result.amended_facts == 1
        assert ledger.get("target").subjects == ("Bicycle",)
        assert ledger.get("target").labels == ("concurrent",)
        assert (
            len([commit for commit in wiki.repository.history(resource_id="bicycle-page") if commit.origin == ORIGIN])
            == 1
        )
    finally:
        await consumers.close()
        await connection.close()


async def test_normalize_topic_global_fact_revision_guard_retries_all_exact_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wiki = WikiService(ManagedFileRepository(tmp_path / "wiki"))
    wiki.create_page(path="bicycle.md", title="Bicycle", page_id="bicycle-page")

    def decide(cluster: FactMaintenancePreparedCluster) -> FactMaintenanceDecision:
        token = next(token for token, title in cluster.wiki_page_tokens.items() if title == "Bicycle")
        return FactMaintenanceDecision(
            outcome="normalize_topic",
            reason="Normalize every exact old topic.",
            old_topic="Bike",
            canonical_page_token=token,
        )

    reviewer = _Reviewer(decide)
    ledger, consumers, connection, maintenance = await _service(tmp_path, reviewer, wiki=wiki)
    original_apply = wiki.apply_maintenance_updates
    added_concurrently = False

    def apply_then_add_fact(*args, **kwargs):
        nonlocal added_concurrently
        result = original_apply(*args, **kwargs)
        if not added_concurrently:
            added_concurrently = True
            _commit(ledger, _create("concurrent", "Concurrent claim", subjects=["Bike"]))
        return result

    monkeypatch.setattr(wiki, "apply_maintenance_updates", apply_then_add_fact)
    try:
        _commit(ledger, _create("baseline", "Baseline"))
        baseline = await _baseline(ledger, consumers)
        _commit(ledger, _create("target", "Target", subjects=["Bike"]))

        with pytest.raises(FactConflictError, match="guarded publication"):
            await maintenance.run()
        assert (await consumers.get(CONSUMER_ID)).revision == baseline
        assert ledger.get("target").subjects == ("Bike",)
        assert ledger.get("concurrent").subjects == ("Bike",)
        assert wiki.read_page("bicycle-page").page.aliases == ("Bike",)

        result = await maintenance.run()
        assert result.advanced and result.amended_facts == 2
        assert ledger.get("target").subjects == ("Bicycle",)
        assert ledger.get("concurrent").subjects == ("Bicycle",)
    finally:
        await consumers.close()
        await connection.close()


async def test_normalize_topic_redirect_owner_blocks_relabel(tmp_path: Path) -> None:
    repository = ManagedFileRepository(tmp_path / "wiki")
    repository.commit(
        ChangeSet(
            operations=(
                Create(
                    "bicycle-page",
                    "bicycle.md",
                    wiki_create_page(page_id="bicycle-page", title="Bicycle").to_bytes(),
                ),
                Create(
                    "bike-redirect",
                    "bike.md",
                    wiki_create_page(
                        page_id="bike-redirect",
                        title="Bike",
                        lifecycle="redirect",
                        redirect_to="bicycle-page",
                    ).to_bytes(),
                ),
            ),
            actor="test",
            origin="test",
            reason="seed redirect owner",
            idempotency_key="seed-redirect-owner",
        )
    )
    wiki = WikiService(repository)

    def decide(cluster: FactMaintenancePreparedCluster) -> FactMaintenanceDecision:
        token = next(token for token, title in cluster.wiki_page_tokens.items() if title == "Bicycle")
        return FactMaintenanceDecision(
            outcome="normalize_topic",
            reason="A redirect still owns the old page name.",
            old_topic="Bike",
            canonical_page_token=token,
        )

    reviewer = _Reviewer(decide)
    ledger, consumers, connection, maintenance = await _service(tmp_path, reviewer, wiki=wiki)
    try:
        _commit(ledger, _create("target", "Target", subjects=["Bike"]))
        head = repository.head

        result = await maintenance.run()

        assert result.advanced and result.amended_facts == 0
        assert ledger.get("target").subjects == ("Bike",)
        assert repository.head == head
        assert wiki.resolve_topic_name("Bike").page.page_id == "bike-redirect"
        assert wiki.read_page("bicycle-page").page.aliases == ()
    finally:
        await consumers.close()
        await connection.close()


async def test_committed_normalization_is_corrected_when_topic_owner_changes(
    tmp_path: Path,
) -> None:
    repository = ManagedFileRepository(tmp_path / "wiki")
    wiki = WikiService(repository)
    wiki.create_page(path="bicycle.md", title="Bicycle", page_id="bicycle-page")
    diverged = False

    async def diverge_after_fact_commit() -> None:
        nonlocal diverged
        if diverged:
            return
        diverged = True
        record = wiki.read_page("bicycle-page")
        replacement = wiki_create_page(
            page_id=record.page.page_id,
            title=record.page.title,
            aliases=(),
            lifecycle=record.page.lifecycle,
            metadata=dict(record.page.metadata),
            body=record.page.body,
        )
        repository.commit(
            ChangeSet(
                operations=(
                    Update(record.page.page_id, record.resource.version_id, replacement.to_bytes()),
                    Create(
                        "bike-page",
                        "bike.md",
                        wiki_create_page(page_id="bike-page", title="Bike").to_bytes(),
                    ),
                ),
                actor="user:desktop",
                origin="desktop",
                reason="create distinct old-topic page",
                idempotency_key="diverge-topic-owner",
                expected_head=repository.head,
            )
        )

    def decide(cluster: FactMaintenancePreparedCluster) -> FactMaintenanceDecision:
        token = next(token for token, title in cluster.wiki_page_tokens.items() if title == "Bicycle")
        return FactMaintenanceDecision(
            outcome="normalize_topic",
            reason="Initially safe normalization.",
            old_topic="Bike",
            canonical_page_token=token,
        )

    reviewer = _Reviewer(decide)
    ledger, consumers, connection, maintenance = await _service(
        tmp_path,
        reviewer,
        wiki=wiki,
        post_commit=diverge_after_fact_commit,
    )
    try:
        _commit(ledger, _create("target", "Target", subjects=["Bike"]))

        result = await maintenance.run()

        assert result.advanced
        assert ledger.get("target").subjects == ("Bike",)
        history = ledger.history("target")
        assert [event.op for event in history] == ["create", "amend", "amend"]
        assert {event.origin for event in history[1:]} == {ORIGIN}
        assert wiki.read_page("bicycle-page").page.aliases == ()
        assert wiki.read_page("bike-page").page.lifecycle == "active"
    finally:
        await consumers.close()
        await connection.close()


async def test_normalize_topic_revalidates_binding_after_corrective_fact_commit(
    tmp_path: Path,
) -> None:
    repository = ManagedFileRepository(tmp_path / "wiki")
    wiki = WikiService(repository)
    wiki.create_page(path="bicycle.md", title="Bicycle", page_id="bicycle-page")
    notifications = 0

    async def change_binding_after_each_fact_commit() -> None:
        nonlocal notifications
        notifications += 1
        bicycle = wiki.read_page("bicycle-page")
        if notifications == 1:
            without_alias = wiki_create_page(
                page_id=bicycle.page.page_id,
                title=bicycle.page.title,
                aliases=(),
                metadata=dict(bicycle.page.metadata),
                body=bicycle.page.body,
            )
            repository.commit(
                ChangeSet(
                    operations=(
                        Update(bicycle.page.page_id, bicycle.resource.version_id, without_alias.to_bytes()),
                        Create(
                            "bike-page",
                            "bike.md",
                            wiki_create_page(page_id="bike-page", title="Bike").to_bytes(),
                        ),
                    ),
                    actor="user:desktop",
                    origin="desktop",
                    reason="temporarily claim old topic",
                    idempotency_key="temporarily-claim-old-topic",
                    expected_head=repository.head,
                )
            )
        elif notifications == 2:
            bike = repository.get("bike-page")
            with_alias = wiki_create_page(
                page_id=bicycle.page.page_id,
                title=bicycle.page.title,
                aliases=("Bike",),
                metadata=dict(bicycle.page.metadata),
                body=bicycle.page.body,
            )
            repository.commit(
                ChangeSet(
                    operations=(
                        Archive("bike-page", bike.version_id),
                        Update(bicycle.page.page_id, bicycle.resource.version_id, with_alias.to_bytes()),
                    ),
                    actor="user:desktop",
                    origin="desktop",
                    reason="restore canonical topic binding",
                    idempotency_key="restore-canonical-topic-binding",
                    expected_head=repository.head,
                )
            )

    def decide(cluster: FactMaintenancePreparedCluster) -> FactMaintenanceDecision:
        token = next(token for token, title in cluster.wiki_page_tokens.items() if title == "Bicycle")
        return FactMaintenanceDecision(
            outcome="normalize_topic",
            reason="Normalize only while the wiki binding stays current.",
            old_topic="Bike",
            canonical_page_token=token,
        )

    reviewer = _Reviewer(decide)
    ledger, consumers, connection, maintenance = await _service(
        tmp_path,
        reviewer,
        wiki=wiki,
        post_commit=change_binding_after_each_fact_commit,
    )
    try:
        input_revision = _commit(ledger, _create("target", "Target", subjects=["Bike"]))

        result = await maintenance.run()

        assert result.advanced
        assert (await consumers.get(CONSUMER_ID)).revision == input_revision
        assert ledger.get("target").subjects == ("Bicycle",)
        assert [event.op for event in ledger.history("target")] == ["create", "amend", "amend", "amend"]
        assert wiki.read_page("bicycle-page").page.aliases == ("Bike",)
        assert notifications == 3
        assert len(reviewer.calls) == 1
    finally:
        await consumers.close()
        await connection.close()


async def test_normalize_topic_retry_after_lost_alias_commit_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wiki = WikiService(ManagedFileRepository(tmp_path / "wiki"))
    wiki.create_page(path="bicycle.md", title="Bicycle", page_id="bicycle-page")

    def decide(cluster: FactMaintenancePreparedCluster) -> FactMaintenanceDecision:
        token = next(token for token, title in cluster.wiki_page_tokens.items() if title == "Bicycle")
        return FactMaintenanceDecision(
            outcome="normalize_topic",
            reason="Retry the convergent alias-first operation.",
            old_topic="Bike",
            canonical_page_token=token,
        )

    reviewer = _Reviewer(decide)
    ledger, consumers, connection, maintenance = await _service(tmp_path, reviewer, wiki=wiki)
    original_apply = wiki.apply_maintenance_updates
    lost_response = False

    def apply_then_lose_response(*args, **kwargs):
        nonlocal lost_response
        result = original_apply(*args, **kwargs)
        if not lost_response:
            lost_response = True
            raise RuntimeError("lost wiki commit response")
        return result

    monkeypatch.setattr(wiki, "apply_maintenance_updates", apply_then_lose_response)
    try:
        _commit(ledger, _create("baseline", "Baseline"))
        baseline = await _baseline(ledger, consumers)
        _commit(ledger, _create("target", "Target", subjects=["Bike"]))

        with pytest.raises(RuntimeError, match="lost wiki commit response"):
            await maintenance.run()
        assert (await consumers.get(CONSUMER_ID)).revision == baseline
        assert wiki.read_page("bicycle-page").page.aliases == ("Bike",)
        assert ledger.get("target").subjects == ("Bike",)

        result = await maintenance.run()
        assert result.advanced and result.amended_facts == 1
        assert ledger.get("target").subjects == ("Bicycle",)
        assert len(reviewer.calls) == 1
        assert (
            len([commit for commit in wiki.repository.history(resource_id="bicycle-page") if commit.origin == ORIGIN])
            == 1
        )
    finally:
        await consumers.close()
        await connection.close()


async def test_normalize_topic_retry_after_fact_commit_does_not_reask_reviewer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wiki = WikiService(ManagedFileRepository(tmp_path / "wiki"))
    wiki.create_page(path="bicycle.md", title="Bicycle", page_id="bicycle-page")

    def decide(cluster: FactMaintenancePreparedCluster) -> FactMaintenanceDecision:
        token = next(token for token, title in cluster.wiki_page_tokens.items() if title == "Bicycle")
        return FactMaintenanceDecision(
            outcome="normalize_topic",
            reason="Persist the accepted intent before publication.",
            old_topic="Bike",
            canonical_page_token=token,
        )

    reviewer = _Reviewer(decide)
    ledger, consumers, connection, maintenance = await _service(tmp_path, reviewer, wiki=wiki)
    original_validate = maintenance._topic_bindings_valid
    checks = 0

    def crash_after_fact_commit(bindings):
        nonlocal checks
        checks += 1
        if checks == 2:
            raise RuntimeError("crash after fact commit")
        return original_validate(bindings)

    monkeypatch.setattr(maintenance, "_topic_bindings_valid", crash_after_fact_commit)
    try:
        _commit(ledger, _create("baseline", "Baseline"))
        baseline = await _baseline(ledger, consumers)
        _commit(ledger, _create("target", "Target", subjects=["Bike"]))

        with pytest.raises(RuntimeError, match="crash after fact commit"):
            await maintenance.run()
        assert (await consumers.get(CONSUMER_ID)).revision == baseline
        assert ledger.get("target").subjects == ("Bicycle",)

        resume_commits = 0
        original_commit = maintenance._service.commit

        async def count_resume_commit(*args, **kwargs):
            nonlocal resume_commits
            resume_commits += 1
            return await original_commit(*args, **kwargs)

        monkeypatch.setattr(maintenance._service, "commit", count_resume_commit)
        result = await maintenance.run()
        assert result.advanced
        assert ledger.get("target").subjects == ("Bicycle",)
        assert len(reviewer.calls) == 1
        assert resume_commits == 0
        assert len([event for event in ledger.history("target") if event.origin == ORIGIN]) == 1
    finally:
        await consumers.close()
        await connection.close()


async def test_normalize_topic_is_noop_for_canonical_title(
    tmp_path: Path,
) -> None:
    canonical_wiki = WikiService(ManagedFileRepository(tmp_path / "canonical-wiki"))
    canonical_wiki.create_page(path="bicycle.md", title="Bicycle", page_id="bicycle-page")

    def canonical_decision(cluster: FactMaintenancePreparedCluster) -> FactMaintenanceDecision:
        token = next(token for token, title in cluster.wiki_page_tokens.items() if title == "Bicycle")
        return FactMaintenanceDecision(
            outcome="normalize_topic",
            reason="Already canonical.",
            old_topic="Bicycle",
            canonical_page_token=token,
        )

    reviewer = _Reviewer(canonical_decision)
    ledger, consumers, connection, maintenance = await _service(
        tmp_path / "canonical",
        reviewer,
        wiki=canonical_wiki,
    )
    try:
        _commit(ledger, _create("target", "Target", subjects=["Bicycle"]))
        head = canonical_wiki.repository.head
        result = await maintenance.run()
        assert result.advanced and result.amended_facts == 0
        assert canonical_wiki.repository.head == head
        assert ledger.get("target").subjects == ("Bicycle",)
    finally:
        await consumers.close()
        await connection.close()


async def test_normalize_topic_collision_skips_only_that_normalization(tmp_path: Path) -> None:
    wiki = WikiService(ManagedFileRepository(tmp_path / "wiki"))
    wiki.create_page(path="bicycle.md", title="Bicycle", page_id="bicycle-page")
    wiki.create_page(path="bike.md", title="Bike", page_id="bike-page")

    def decide(cluster: FactMaintenancePreparedCluster) -> FactMaintenanceDecision:
        token = next(token for token, title in cluster.wiki_page_tokens.items() if title == "Bicycle")
        return FactMaintenanceDecision(
            outcome="normalize_topic",
            reason="Use the canonical topic.",
            old_topic="Bike",
            canonical_page_token=token,
        )

    reviewer = _Reviewer(decide)
    ledger, consumers, connection, maintenance = await _service(tmp_path, reviewer, wiki=wiki)
    try:
        _commit(ledger, _create("target", "Target", subjects=["Bike"]))
        result = await maintenance.run()

        assert result.advanced and result.amended_facts == 0
        assert ledger.get("target").subjects == ("Bike",)
        assert wiki.read_page("bicycle-page").page.aliases == ()
    finally:
        await consumers.close()
        await connection.close()


async def test_empty_feed_is_an_idle_noop(tmp_path: Path) -> None:
    reviewer = _Reviewer()
    ledger, consumers, connection, maintenance = await _service(tmp_path, reviewer)
    try:
        result = await maintenance.run()
        assert result.empty and not result.advanced
        assert reviewer.calls == []
        assert await consumers.get(CONSUMER_ID) is None
        assert ledger.revision is None
    finally:
        await consumers.close()
        await connection.close()
