from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import arden.wiki.maintenance as maintenance_module
from arden.revisions import ChangeSet, Create, ManagedFileRepository, Update
from arden.wiki import (
    WikiMaintenance,
    WikiMaintenanceConcernDraft,
    WikiMaintenanceDecision,
    WikiMaintenancePageUpdate,
    WikiMaintenanceReviewAction,
    WikiMaintenanceReviewInput,
    WikiMaintenanceStore,
    WikiMaintenanceUpdateDraft,
    WikiService,
    create_page,
)

if TYPE_CHECKING:
    from pathlib import Path


class _Reviewer:
    def __init__(self, *decisions: WikiMaintenanceDecision) -> None:
        self.decisions = list(decisions)
        self.reports = []

    async def review(self, report) -> WikiMaintenanceDecision:
        self.reports.append(report)
        return self.decisions.pop(0)


def _repo(tmp_path: Path) -> ManagedFileRepository:
    return ManagedFileRepository(tmp_path / "wiki")


def _seed(repo: ManagedFileRepository, body: bytes = b"Original\n") -> str:
    page = create_page(page_id="page-one", title="One", body=body)
    return repo.commit(
        ChangeSet(
            operations=(Create("page-one", "one.md", page.to_bytes()),),
            actor="User",
            origin="desktop",
            reason="write page",
            idempotency_key="seed",
        )
    ).commit_id


def _update(repo: ManagedFileRepository, page_id: str, body: bytes, *, key: str) -> None:
    current = repo.get(page_id)
    repo.commit(
        ChangeSet(
            operations=(
                Update(page_id, current.version_id, create_page(page_id=page_id, title="One", body=body).to_bytes()),
            ),
            actor="User",
            origin="desktop",
            reason="change evidence",
            idempotency_key=key,
            expected_head=repo.head,
        )
    )


@pytest.mark.asyncio
async def test_runner_applies_only_token_addressed_updates_and_advances_prefix(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    source = _seed(repo)
    store = await WikiMaintenanceStore.open(tmp_path / "state.sqlite")
    reviewer = _Reviewer(
        WikiMaintenanceDecision(
            outcome="updates",
            updates=[WikiMaintenanceUpdateDraft(page_token="P001", title="One", aliases=[], body="Clarified\n")],
        )
    )
    try:
        result = await WikiMaintenance(store, WikiService(repo), reviewer).run()
        assert result.advanced and result.updated_pages == 1
        assert (await store.get_watermark()).revision == source
        page = WikiService(repo).read_page("page-one")
        assert page.page.body == b"Clarified\n"
        assert "P001" in reviewer.reports[0].markdown
        assert page.resource.version_id not in reviewer.reports[0].markdown
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_pending_identical_evidence_is_not_asked_again_then_rejection_advances(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    source = _seed(repo)
    store = await WikiMaintenanceStore.open(tmp_path / "state.sqlite")
    reviewer = _Reviewer(
        WikiMaintenanceDecision(
            outcome="needs_review",
            concern=WikiMaintenanceConcernDraft(
                key="ambiguous-note", summary="Need a choice", proposal="Keep wording."
            ),
        )
    )
    maintenance = WikiMaintenance(store, WikiService(repo), reviewer)
    try:
        first = await maintenance.run()
        assert first.blocked
        assert len(reviewer.reports) == 1
        again = await maintenance.run()
        assert again.blocked
        assert len(reviewer.reports) == 1
        pending = (await store.list_pending())[0]
        await store.resolve(
            pending.review_id,
            expected_generation=pending.generation,
            action=WikiMaintenanceReviewAction.REJECT,
        )
        resolved = await maintenance.run()
        assert resolved.advanced
        assert (await store.get_watermark()).revision == source
        assert len(reviewer.reports) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_runner_advances_backend_health_projection_without_model_review(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    service = WikiService(repo)
    commit = service.publish_health(body=b"Healthy.\n", base_head=None)
    assert commit is not None
    store = await WikiMaintenanceStore.open(tmp_path / "state.sqlite")
    reviewer = _Reviewer()
    try:
        result = await WikiMaintenance(store, service, reviewer).run()
        assert result.advanced and result.complete
        assert result.reviewed_commits == 1
        assert reviewer.reports == []
        assert (await store.get_watermark()).revision == commit.commit_id
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_changed_evidence_clears_the_old_pending_review_after_fresh_no_change(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    source = _seed(repo)
    store = await WikiMaintenanceStore.open(tmp_path / "state.sqlite")
    reviewer = _Reviewer(
        WikiMaintenanceDecision(
            outcome="needs_review",
            concern=WikiMaintenanceConcernDraft(key="question", summary="Question", proposal="Decide."),
        ),
        WikiMaintenanceDecision(outcome="no_change"),
        WikiMaintenanceDecision(outcome="no_change"),
    )
    maintenance = WikiMaintenance(store, WikiService(repo), reviewer)
    try:
        assert (await maintenance.run()).blocked
        _update(repo, "page-one", b"New\n", key="change")
        result = await maintenance.run()
        assert result.advanced
        assert len(reviewer.reports) == 3
        history = await store.list_history()
        assert history[0].status.value == "cleared"
        assert (await store.get_watermark()).revision != source
    finally:
        await store.close()


def test_prepared_report_is_pinned_and_includes_resolved_link_neighbors(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    one = create_page(page_id="page-one", title="One", body=b"Original [[Two]]\n")
    two = create_page(page_id="page-two", title="Two", body=b"Neighbor\n")
    repo.commit(
        ChangeSet(
            operations=(Create("page-one", "one.md", one.to_bytes()), Create("page-two", "two.md", two.to_bytes())),
            actor="User",
            origin="desktop",
            reason="seed linked pages",
            idempotency_key="seed-linked",
        )
    )
    service = WikiService(repo)
    feed = service.changes_since(None)
    _update(repo, "page-one", b"Later [[Two]]\n", key="later")
    maintenance = WikiMaintenance.__new__(WikiMaintenance)
    maintenance._wiki = service

    report = maintenance._prepare(feed, feed.commits[0])

    assert len(report.page_tokens) == 2
    assert "Original [[Two]]" in report.markdown
    assert "Later [[Two]]" not in report.markdown


@pytest.mark.asyncio
async def test_changed_evidence_replaces_stale_pending_before_the_next_blocked_run(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _seed(repo)
    store = await WikiMaintenanceStore.open(tmp_path / "state.sqlite")
    reviewer = _Reviewer(
        WikiMaintenanceDecision(
            outcome="needs_review",
            concern=WikiMaintenanceConcernDraft(key="old-question", summary="Old", proposal="Old choice."),
        ),
        WikiMaintenanceDecision(
            outcome="needs_review",
            concern=WikiMaintenanceConcernDraft(key="new-question", summary="New", proposal="New choice."),
        ),
    )
    maintenance = WikiMaintenance(store, WikiService(repo), reviewer)
    try:
        assert (await maintenance.run()).blocked
        _update(repo, "page-one", b"New\n", key="change")
        assert (await maintenance.run()).blocked
        pending = await store.list_pending()
        assert [review.evidence_key for review in pending] == ["new-question"]
        # The next run finds that exact durable ask and must not call the model.
        assert (await maintenance.run()).blocked
        assert len(reviewer.reports) == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_two_source_updates_stop_at_each_write_and_finish_after_reloading(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first_source = _seed(repo)
    second_page = create_page(page_id="page-two", title="Two", body=b"Second\n")
    second_source = repo.commit(
        ChangeSet(
            operations=(Create("page-two", "two.md", second_page.to_bytes()),),
            actor="User",
            origin="desktop",
            reason="write second page",
            idempotency_key="second",
            expected_head=repo.head,
        )
    ).commit_id
    store = await WikiMaintenanceStore.open(tmp_path / "state.sqlite")
    reviewer = _Reviewer(
        WikiMaintenanceDecision(
            outcome="updates",
            updates=[WikiMaintenanceUpdateDraft(page_token="P001", title="One", body="First fixed\n")],
        ),
        WikiMaintenanceDecision(
            outcome="updates",
            updates=[WikiMaintenanceUpdateDraft(page_token="P001", title="Two", body="Second fixed\n")],
        ),
        WikiMaintenanceDecision(outcome="no_change"),
        WikiMaintenanceDecision(outcome="no_change"),
    )
    maintenance = WikiMaintenance(store, WikiService(repo), reviewer)
    try:
        first = await maintenance.run()
        assert first.reload_required and not first.complete
        assert first.feed_target_revision == second_source
        assert first.processed_through_revision == first_source
        assert (await store.get_watermark()).revision == first_source

        second = await maintenance.run()
        assert second.reload_required and not second.complete
        assert second.processed_through_revision == second_source
        assert (await store.get_watermark()).revision == second_source

        final = await maintenance.run()
        assert final.complete and not final.reload_required
        assert final.processed_through_revision == final.feed_target_revision == repo.head
        assert WikiService(repo).read_page("page-one").page.body == b"First fixed\n"
        assert WikiService(repo).read_page("page-two").page.body == b"Second fixed\n"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_oversized_utf8_evidence_creates_one_durable_ask_without_calling_model(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    source = _seed(repo, ("😀" * 17_000).encode())
    store = await WikiMaintenanceStore.open(tmp_path / "state.sqlite")
    reviewer = _Reviewer()
    maintenance = WikiMaintenance(store, WikiService(repo), reviewer)
    try:
        first = await maintenance.run()
        assert first.blocked and not first.advanced and not first.complete
        assert first.feed_target_revision == source
        assert first.processed_through_revision is None
        assert reviewer.reports == []
        pending = await store.list_pending()
        assert len(pending) == 1
        assert pending[0].evidence_key == "evidence-too-large"
        assert "UTF-8 bytes" in pending[0].summary

        second = await maintenance.run()
        assert second.blocked and reviewer.reports == []
        assert (await store.list_pending())[0].review_id == pending[0].review_id
        assert await store.get_watermark() is None

        await store.resolve(
            pending[0].review_id,
            expected_generation=pending[0].generation,
            action=WikiMaintenanceReviewAction.ACCEPT,
        )
        accepted = await maintenance.run()
        assert accepted.complete and accepted.processed_through_revision == source
        assert (await store.get_watermark()).revision == source
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_hard_total_prompt_budget_creates_durable_ask(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    _seed(repo)
    monkeypatch.setattr(maintenance_module, "_MAX_PROMPT_BYTES", 256)
    store = await WikiMaintenanceStore.open(tmp_path / "state.sqlite")
    reviewer = _Reviewer()
    try:
        result = await WikiMaintenance(store, WikiService(repo), reviewer).run()
        assert result.blocked and reviewer.reports == []
        pending = (await store.list_pending())[0]
        assert "total prompt" in pending.summary
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_store_failure_is_reported_verbatim_with_actual_processed_watermark(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    source = _seed(repo)
    store = await WikiMaintenanceStore.open(tmp_path / "state.sqlite")
    reviewer = _Reviewer(WikiMaintenanceDecision(outcome="no_change"))

    async def fail_apply_run(**_kwargs):
        raise RuntimeError("maintenance state disk failure")

    monkeypatch.setattr(store, "apply_run", fail_apply_run)
    try:
        result = await WikiMaintenance(store, WikiService(repo), reviewer).run()
        assert result.error == "maintenance state disk failure"
        assert result.feed_target_revision == source
        assert result.processed_through_revision is None
        assert not result.advanced and not result.complete
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_trusted_replay_clears_stale_ask_before_verified_watermark_advance(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    source = _seed(repo)
    store = await WikiMaintenanceStore.open(tmp_path / "state.sqlite")
    reviewer = _Reviewer(
        WikiMaintenanceDecision(
            outcome="needs_review",
            concern=WikiMaintenanceConcernDraft(key="old-question", summary="Old", proposal="Old choice."),
        ),
        WikiMaintenanceDecision(outcome="no_change"),
        WikiMaintenanceDecision(outcome="no_change"),
    )
    service = WikiService(repo)
    maintenance = WikiMaintenance(store, service, reviewer)
    try:
        assert (await maintenance.run()).blocked
        _update(repo, "page-one", b"User changed\n", key="user-change")
        feed = service.changes_since(None)
        prepared = maintenance._prepare(feed, feed.commits[0])
        current = service.read_page("page-one")
        service.apply_maintenance_updates(
            (
                WikiMaintenancePageUpdate(
                    page_id="page-one",
                    expected_version=current.resource.version_id,
                    title="One",
                    aliases=(),
                    body=b"Applied\n",
                ),
            ),
            base_head=feed.through_revision,
            reason=maintenance._reason(prepared),
        )

        result = await maintenance.run()
        assert result.complete and result.replayed
        assert result.processed_through_revision == repo.head
        assert await store.list_pending() == []
        assert (await store.get_watermark()).revision == repo.head
        assert source != repo.head
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_trusted_replay_rejects_multiple_matching_acceptances_before_apply_or_advance(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    source = _seed(repo)
    service = WikiService(repo)
    store = await WikiMaintenanceStore.open(tmp_path / "state.sqlite")
    reviewer = _Reviewer()
    maintenance = WikiMaintenance(store, service, reviewer)
    try:
        initial_feed = service.changes_since(None)
        initial_prepared = maintenance._prepare(initial_feed, initial_feed.commits[0])
        current = service.read_page("page-one")
        service.apply_maintenance_updates(
            (
                WikiMaintenancePageUpdate(
                    page_id="page-one",
                    expected_version=current.resource.version_id,
                    title="One",
                    aliases=(),
                    body=b"Trusted replay\n",
                ),
            ),
            base_head=initial_feed.through_revision,
            reason=maintenance._reason(initial_prepared),
        )
        final_feed = service.changes_since(None)
        prepared = maintenance._prepare(final_feed, final_feed.commits[0])
        commit_ids = tuple(commit.commit_id for commit in final_feed.commits)

        accepted = []
        for key in ("first-acceptance", "second-acceptance"):
            applied = await store.apply_run(
                expected_revision=None,
                ordered_commit_ids=commit_ids,
                reviewed_through=source,
                reviews=(
                    WikiMaintenanceReviewInput(
                        blocking_commit_id=source,
                        evidence_key=key,
                        evidence_fingerprint=prepared.evidence_fingerprint,
                        summary="Duplicate accepted proposal.",
                        proposal_json={"kind": "test"},
                    ),
                ),
            )
            accepted.append(
                await store.resolve(
                    applied.reviews[0].review_id,
                    expected_generation=applied.reviews[0].generation,
                    action=WikiMaintenanceReviewAction.ACCEPT,
                )
            )
        assert len(accepted) == 2

        result = await maintenance.run()
        assert result.error == "multiple accepted reviews match one wiki commit"
        assert not result.replayed and reviewer.reports == []
        assert result.processed_through_revision is None
        assert await store.get_watermark() is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_concurrent_cas_failure_reports_the_reread_durable_watermark(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    source = _seed(repo)
    path = tmp_path / "state.sqlite"
    first = await WikiMaintenanceStore.open(path)
    second = await WikiMaintenanceStore.open(path)

    class _RacingReviewer:
        async def review(self, _report):
            await second.apply_run(
                expected_revision=None,
                ordered_commit_ids=(source,),
                reviewed_through=source,
            )
            return WikiMaintenanceDecision(outcome="no_change")

    try:
        result = await WikiMaintenance(first, WikiService(repo), _RacingReviewer()).run()
        assert result.error is not None and "watermark changed before the run began" in result.error
        assert result.processed_through_revision == source
        assert result.feed_target_revision == source
        assert result.advanced and not result.complete
    finally:
        await first.close()
        await second.close()
