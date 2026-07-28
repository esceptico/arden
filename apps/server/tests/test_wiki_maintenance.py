import json
from pathlib import Path

import pytest

import arden.wiki.maintenance.runner as maintenance_module
import arden.wiki.service as wiki_service_module
from arden.revisions import ChangeSet, Create, ManagedFileRepository, Move, Update
from arden.wiki.maintenance.runner import (
    WikiMaintenance,
    WikiMaintenanceConcernDraft,
    WikiMaintenanceDecision,
    WikiMaintenanceUpdateDraft,
)
from arden.wiki.maintenance.store import WikiMaintenanceReviewAction, WikiMaintenanceReviewInput, WikiMaintenanceStore
from arden.wiki.models import WikiMaintenancePageUpdate
from arden.wiki.navigation.projection import (
    WIKI_NAVIGATION_ACTOR,
    WIKI_NAVIGATION_ORIGIN,
    WIKI_NAVIGATION_REASON,
)
from arden.wiki.pages import create_page
from arden.wiki.service import WikiMaintenanceEvidenceLimitError, WikiService


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
async def test_system_cleared_identical_evidence_is_asked_again(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    source = _seed(repo)
    store = await WikiMaintenanceStore.open(tmp_path / "state.sqlite")
    concern = WikiMaintenanceConcernDraft(
        key="ambiguous-note",
        summary="Need a choice",
        proposal="Keep wording.",
    )
    reviewer = _Reviewer(
        WikiMaintenanceDecision(outcome="needs_review", concern=concern),
        WikiMaintenanceDecision(outcome="needs_review", concern=concern),
    )
    maintenance = WikiMaintenance(store, WikiService(repo), reviewer)
    try:
        assert (await maintenance.run()).blocked
        original = (await store.list_pending())[0]
        await store.clear(original.review_id, expected_generation=original.generation)

        rerun = await maintenance.run()

        assert rerun.blocked and not rerun.advanced
        assert await store.get_watermark() is None
        pending = (await store.list_pending())[0]
        assert pending.review_id == original.review_id
        assert pending.generation == original.generation + 1
        assert pending.blocking_commit_id == source
        assert len(reviewer.reports) == 2
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
async def test_runner_advances_exact_navigation_projection_without_loading_details(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    page = create_page(page_id="navigation-readme", title="Home", body=b"## Navigation\n")
    commit = repo.commit(
        ChangeSet(
            operations=(Create("navigation-readme", "README.md", page.to_bytes()),),
            actor=WIKI_NAVIGATION_ACTOR,
            origin=WIKI_NAVIGATION_ORIGIN,
            reason=WIKI_NAVIGATION_REASON,
            idempotency_key="navigation-projection",
        )
    )
    service = WikiService(repo)
    store = await WikiMaintenanceStore.open(tmp_path / "state.sqlite")
    reviewer = _Reviewer()

    def fail_details(*_args, **_kwargs):
        raise AssertionError("navigation projection must not load maintenance details")

    monkeypatch.setattr(service, "maintenance_details", fail_details)
    try:
        result = await WikiMaintenance(store, service, reviewer).run()

        assert result.advanced and result.complete
        assert result.reviewed_commits == 1
        assert reviewer.reports == []
        assert (await store.get_watermark()).revision == commit.commit_id
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reason", "path"),
    [
        ("different navigation reason", "README.md"),
        (WIKI_NAVIGATION_REASON, "near-navigation.md"),
    ],
)
async def test_navigation_contract_mismatch_is_loaded_for_review(
    tmp_path: Path,
    reason: str,
    path: str,
) -> None:
    repo = _repo(tmp_path)
    page = create_page(page_id="near-navigation", title="Near navigation", body=b"Ordinary page.\n")
    repo.commit(
        ChangeSet(
            operations=(Create("near-navigation", path, page.to_bytes()),),
            actor=WIKI_NAVIGATION_ACTOR,
            origin=WIKI_NAVIGATION_ORIGIN,
            reason=reason,
            idempotency_key="near-navigation",
        )
    )
    store = await WikiMaintenanceStore.open(tmp_path / "state.sqlite")
    reviewer = _Reviewer(WikiMaintenanceDecision(outcome="no_change"))
    try:
        result = await WikiMaintenance(store, WikiService(repo), reviewer).run()

        assert result.advanced and result.complete
        assert len(reviewer.reports) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_navigation_commit_with_non_markdown_change_is_loaded_for_review(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    page = create_page(page_id="navigation-page", title="Navigation page", body=b"Ordinary page.\n")
    repo.commit(
        ChangeSet(
            operations=(
                Create("navigation-page", "navigation-page.md", page.to_bytes()),
                Create("navigation-asset", "navigation-asset.bin", b"asset"),
            ),
            actor=WIKI_NAVIGATION_ACTOR,
            origin=WIKI_NAVIGATION_ORIGIN,
            reason=WIKI_NAVIGATION_REASON,
            idempotency_key="navigation-with-asset",
        )
    )
    store = await WikiMaintenanceStore.open(tmp_path / "state.sqlite")
    reviewer = _Reviewer(WikiMaintenanceDecision(outcome="no_change"))
    try:
        result = await WikiMaintenance(store, WikiService(repo), reviewer).run()

        assert result.advanced and result.complete
        assert len(reviewer.reports) == 1
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
async def test_system_cleared_oversized_evidence_is_asked_again(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    source = _seed(repo, ("😀" * 17_000).encode())
    store = await WikiMaintenanceStore.open(tmp_path / "state.sqlite")
    reviewer = _Reviewer()
    maintenance = WikiMaintenance(store, WikiService(repo), reviewer)
    try:
        assert (await maintenance.run()).blocked
        original = (await store.list_pending())[0]
        await store.clear(original.review_id, expected_generation=original.generation)

        rerun = await maintenance.run()

        assert rerun.blocked and not rerun.advanced
        assert await store.get_watermark() is None
        pending = (await store.list_pending())[0]
        assert pending.review_id == original.review_id
        assert pending.generation == original.generation + 1
        assert pending.blocking_commit_id == source
        assert reviewer.reports == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_huge_single_line_diff_is_bounded_before_model_review(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    source = _seed(repo, b"x" * (maintenance_module._MAX_DIFF_BYTES + 1_000) + b"\n")
    store = await WikiMaintenanceStore.open(tmp_path / "state.sqlite")
    reviewer = _Reviewer()

    def reject_full_diff(*_args, **_kwargs):
        raise AssertionError("scheduled maintenance must use bounded diff pages")

    monkeypatch.setattr(repo, "diff", reject_full_diff)
    try:
        result = await WikiMaintenance(store, WikiService(repo), reviewer).run()

        assert result.blocked and not result.advanced
        assert result.feed_target_revision == source
        assert reviewer.reports == []
        pending = await store.list_pending()
        assert len(pending) == 1
        assert "at least" in pending[0].summary
        proposal = json.loads(pending[0].proposal_json)
        assert proposal["kind"] == "manual_evidence_review"
        assert proposal["actual_bytes_at_least"] is True
        assert proposal["actual_bytes"] > proposal["limit_bytes"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_blocked_oldest_commit_does_not_load_later_backlog_diffs(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    first = _seed(repo, b"x" * (maintenance_module._MAX_DIFF_BYTES + 1_000) + b"\n")
    page_two = create_page(page_id="page-two", title="Two", body=b"Later\n")
    later = repo.commit(
        ChangeSet(
            operations=(Create("page-two", "two.md", page_two.to_bytes()),),
            actor="User",
            origin="desktop",
            reason="later page",
            idempotency_key="later-page",
            expected_head=repo.head,
        )
    ).commit_id
    store = await WikiMaintenanceStore.open(tmp_path / "state.sqlite")
    reviewer = _Reviewer(WikiMaintenanceDecision(outcome="no_change"))
    original_page = repo.diff_versions_page
    loaded: list[str] = []

    def reject_full_diff(*_args, **_kwargs):
        raise AssertionError("maintenance backlog metadata must not build full diffs")

    def capture_page(before, after, **kwargs):
        version = after or before
        assert version is not None
        loaded.append(version.resource_id)
        return original_page(before, after, **kwargs)

    monkeypatch.setattr(repo, "diff", reject_full_diff)
    monkeypatch.setattr(repo, "diff_versions_page", capture_page)
    try:
        result = await WikiMaintenance(store, WikiService(repo), reviewer).run()

        assert result.blocked and not result.advanced
        assert reviewer.reports == []
        assert loaded == ["page-one"]

        pending = (await store.list_pending())[0]
        await store.resolve(
            pending.review_id,
            expected_generation=pending.generation,
            action=WikiMaintenanceReviewAction.ACCEPT,
        )
        resumed = await WikiMaintenance(store, WikiService(repo), reviewer).run()
        assert resumed.processed_through_revision == first
        assert resumed.reload_required
        completed = await WikiMaintenance(store, WikiService(repo), reviewer).run()
        assert completed.complete
        # The accepted oldest review is revalidated before its watermark moves;
        # only then can the next commit reach its bounded diff.
        assert loaded == ["page-one", "page-one", "page-two"]
        assert completed.processed_through_revision == later
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_unrelated_oversized_current_page_is_never_read_before_manual_ask(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    tiny = create_page(page_id="tiny", title="Tiny", body=b"Before\n")
    large = create_page(page_id="large", title="Large", body=b"x" * (2 * 1024 * 1024))
    baseline = repo.commit(
        ChangeSet(
            operations=(
                Create("tiny", "tiny.md", tiny.to_bytes()),
                Create("large", "large.md", large.to_bytes()),
            ),
            actor="User",
            origin="desktop",
            reason="baseline",
            idempotency_key="baseline",
        )
    ).commit_id
    _update(repo, "tiny", b"After\n", key="tiny-change")
    large_blob = repo.get("large").blob_id
    original_read_blob = repo._storage.read_blob
    reads: list[str] = []

    def reject_large_blob(blob_id: str) -> bytes:
        reads.append(blob_id)
        if blob_id == large_blob:
            raise AssertionError("unrelated oversized current page was read")
        return original_read_blob(blob_id)

    monkeypatch.setattr(repo._storage, "read_blob", reject_large_blob)
    store = await WikiMaintenanceStore.open(tmp_path / "state.sqlite")
    try:
        await store.apply_run(
            expected_revision=None,
            ordered_commit_ids=(baseline,),
            reviewed_through=baseline,
        )
        result = await WikiMaintenance(store, WikiService(repo), _Reviewer()).run()

        assert result.blocked and not result.advanced
        assert large_blob not in reads
        pending = (await store.list_pending())[0]
        assert "current page" in pending.summary
        assert "manual_evidence_review" in pending.proposal_json
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_oversized_commit_loads_only_its_own_durable_review_rows(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    source = _seed(repo)
    store = await WikiMaintenanceStore.open(tmp_path / "state.sqlite")
    try:
        applied = await store.apply_run(
            expected_revision=None,
            ordered_commit_ids=(source,),
            reviewed_through=source,
            reviews=(
                WikiMaintenanceReviewInput(
                    blocking_commit_id=source,
                    evidence_key="old-review",
                    evidence_fingerprint="a" * 64,
                    summary="Historical review.",
                ),
            ),
        )
        await store.resolve(
            applied.reviews[0].review_id,
            expected_generation=applied.reviews[0].generation,
            action=WikiMaintenanceReviewAction.REJECT,
        )
        await store.apply_run(
            expected_revision=None,
            ordered_commit_ids=(source,),
            reviewed_through=source,
        )
        _update(repo, "page-one", b"x" * (2 * 1024 * 1024), key="oversized-current")
        current = repo.head
        assert current is not None
        requested: list[str] = []
        original_for_commit = store.list_for_commit

        async def reject_history():
            raise AssertionError("maintenance must not load lifetime review history")

        async def capture_for_commit(commit_id: str):
            requested.append(commit_id)
            return await original_for_commit(commit_id)

        monkeypatch.setattr(store, "list_history", reject_history)
        monkeypatch.setattr(store, "list_for_commit", capture_for_commit)
        result = await WikiMaintenance(store, WikiService(repo), _Reviewer()).run()

        assert result.blocked and not result.advanced
        assert requested == [current]
    finally:
        await store.close()


def test_prepared_report_reuses_preloaded_current_records_without_reads(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    source = _seed(repo, b"Current [[One]]\n")
    service = WikiService(repo)
    metadata = service.maintenance_feed(None).commits[0]

    def reject_integrity():
        raise AssertionError("scheduled detail must not traverse repository-wide health")

    monkeypatch.setattr(repo, "integrity_report", reject_integrity)
    monkeypatch.setattr(repo, "storage_report", reject_integrity)
    detail = service.maintenance_details(
        metadata,
        through_revision=source,
        diff_char_limit=maintenance_module._MAX_DIFF_BYTES + 1,
        diff_byte_budget=maintenance_module._MAX_PROMPT_BYTES,
    )
    maintenance = WikiMaintenance.__new__(WikiMaintenance)

    def reject(*_args, **_kwargs):
        raise AssertionError("prepared report must use preloaded current records")

    monkeypatch.setattr(repo, "read", reject)
    prepared = maintenance._prepare(detail, detail.commit)

    assert "Current [[One]]" in prepared.markdown


def test_old_commit_details_do_not_walk_intervening_history(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    first = _seed(repo)
    for index in range(20):
        _update(repo, "page-one", f"Revision {index}\n".encode(), key=f"revision-{index}")
    service = WikiService(repo)
    oldest = service.maintenance_feed(None).commits[0]
    assert oldest.commit_id == first
    head = repo.current_revision
    assert head is not None
    parent = repo.inspect_commit(head).parent_id
    loaded: list[str] = []
    original_load = repo._load_commit

    def capture_load(commit_id):
        loaded.append(str(commit_id))
        return original_load(commit_id)

    monkeypatch.setattr(repo, "_load_commit", capture_load)

    service.maintenance_details(
        oldest,
        through_revision=head,
        diff_char_limit=1_000_000,
        diff_byte_budget=1_000_000,
    )

    assert set(loaded) <= {head, parent}
    assert first not in loaded


def test_maintenance_feed_is_metadata_only_and_chronological(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    first = _seed(repo)
    _update(repo, "page-one", b"Second\n", key="second")
    second = repo.head
    assert second is not None

    def reject(*_args, **_kwargs):
        raise AssertionError("maintenance backlog metadata must not inspect page content or repository health")

    monkeypatch.setattr(repo, "read", reject)
    monkeypatch.setattr(repo, "diff", reject)
    monkeypatch.setattr(repo, "diff_page", reject)
    monkeypatch.setattr(repo, "integrity_report", reject)
    monkeypatch.setattr(repo, "storage_report", reject)
    monkeypatch.setattr(repo._storage, "read_blob", reject)

    service = WikiService(repo)
    feed = service.maintenance_feed(None)

    assert feed.through_revision == second
    assert [commit.commit_id for commit in feed.commits] == [first, second]
    assert feed.commits[0].changes[0].after is not None

    loaded_commits: list[str] = []
    original_load = repo._load_commit

    def track_load(commit_id):
        loaded_commits.append(str(commit_id))
        return original_load(commit_id)

    monkeypatch.setattr(repo, "_load_commit", track_load)
    assert service.maintenance_feed(second).commits == ()
    assert loaded_commits == []


def test_shared_evidence_exact_exhaustion_reports_a_strict_lower_bound(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    commit_id = repo.commit(
        ChangeSet(
            operations=(
                Create("a", "a.md", create_page(page_id="a", title="A", body=b"A\n").to_bytes()),
                Create("b", "b.md", create_page(page_id="b", title="B", body=b"B\n").to_bytes()),
            ),
            actor="User",
            origin="desktop",
            reason="two pages",
            idempotency_key="two-pages",
        )
    ).commit_id
    service = WikiService(repo)
    commit = service.maintenance_feed(None).commits[0]
    first_diff = repo.diff_page(None, commit_id, "a", limit=1_000_000).unified_diff
    exact_first_cost = len(first_diff.encode()) + len(repo.read("a", at=commit_id))

    with pytest.raises(WikiMaintenanceEvidenceLimitError) as raised:
        service.maintenance_details(
            commit,
            through_revision=commit_id,
            diff_char_limit=1_000_000,
            diff_byte_budget=exact_first_cost,
        )

    assert raised.value.actual_bytes == exact_first_cost + 1
    assert raised.value.limit_bytes == exact_first_cost
    assert raised.value.actual_bytes_at_least


def test_reserved_changed_body_counts_toward_truncated_diff_lower_bound(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    content = create_page(page_id="page-one", title="One", body=b"x" * 850 + b"\n").to_bytes()
    commit_id = repo.commit(
        ChangeSet(
            operations=(Create("page-one", "one.md", content),),
            actor="User",
            origin="desktop",
            reason="near-budget page",
            idempotency_key="near-budget-page",
        )
    ).commit_id
    service = WikiService(repo)
    commit = service.maintenance_feed(None).commits[0]
    budget = len(content) + 32

    with pytest.raises(WikiMaintenanceEvidenceLimitError) as raised:
        service.maintenance_details(
            commit,
            through_revision=commit_id,
            diff_char_limit=1_000_000,
            diff_byte_budget=budget,
        )

    assert raised.value.section == "diff"
    assert raised.value.actual_bytes == budget + 1
    assert raised.value.limit_bytes == budget
    assert raised.value.actual_bytes_at_least


def test_current_link_context_has_a_total_scan_bound(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    commit_id = _seed(repo)
    commit = WikiService(repo).maintenance_feed(None).commits[0]
    monkeypatch.setattr(wiki_service_module, "_MAINTENANCE_CURRENT_SCAN_TOTAL_BYTES", 1)

    with pytest.raises(WikiMaintenanceEvidenceLimitError) as raised:
        WikiService(repo).maintenance_details(
            commit,
            through_revision=commit_id,
            diff_char_limit=1_000_000,
            diff_byte_budget=1_000_000,
        )

    assert raised.value.section == "current wiki link context"
    assert raised.value.actual_bytes > raised.value.limit_bytes


@pytest.mark.asyncio
async def test_markdown_move_to_non_markdown_is_still_reviewed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _seed(repo)
    current = repo.get("page-one")
    moved = repo.commit(
        ChangeSet(
            operations=(Move("page-one", current.version_id, "one.txt"),),
            actor="User",
            origin="desktop",
            reason="change file type",
            idempotency_key="move-to-text",
            expected_head=repo.head,
        )
    ).commit_id
    store = await WikiMaintenanceStore.open(tmp_path / "state.sqlite")
    reviewer = _Reviewer(
        WikiMaintenanceDecision(outcome="no_change"),
        WikiMaintenanceDecision(outcome="no_change"),
    )
    try:
        result = await WikiMaintenance(store, WikiService(repo), reviewer).run()

        assert result.complete
        assert result.processed_through_revision == moved
        assert len(reviewer.reports) == 2
        assert reviewer.reports[-1].markdown.find("one.md") >= 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_atomic_commit_stops_before_later_diff_or_body_when_shared_budget_is_exhausted(
    tmp_path: Path, monkeypatch
) -> None:
    repo = _repo(tmp_path)
    large_body = b"".join(f"ordinary line {index:05d}\n".encode() for index in range(15_000))
    seed = ChangeSet(
        operations=(
            Create("a-small", "a.md", create_page(page_id="a-small", title="A", body=b"A\n").to_bytes()),
            Create("b-large", "b.md", create_page(page_id="b-large", title="B", body=large_body).to_bytes()),
            Create("c-later", "c.md", create_page(page_id="c-later", title="C", body=b"C\n").to_bytes()),
        ),
        actor="User",
        origin="desktop",
        reason="seed pages",
        idempotency_key="atomic-seed",
    )
    seed_commit = repo.commit(seed).commit_id
    versions = {resource_id: repo.get(resource_id) for resource_id in ("a-small", "b-large", "c-later")}
    commit = repo.commit(
        ChangeSet(
            operations=(
                Update(
                    "a-small",
                    versions["a-small"].version_id,
                    create_page(page_id="a-small", title="A", body=b"A2\n").to_bytes(),
                ),
                Update(
                    "b-large",
                    versions["b-large"].version_id,
                    create_page(
                        page_id="b-large",
                        title="B",
                        body=large_body + b"new tail line\n",
                    ).to_bytes(),
                ),
                Update(
                    "c-later",
                    versions["c-later"].version_id,
                    create_page(page_id="c-later", title="C", body=b"C2\n").to_bytes(),
                ),
            ),
            actor="User",
            origin="desktop",
            reason="atomic edits",
            idempotency_key="atomic-edits",
            expected_head=repo.head,
        )
    ).commit_id
    diff_calls: list[str] = []
    body_reads: list[str] = []
    original_diff_page = repo.diff_versions_page
    original_read = repo.read_version

    def capture_diff_page(before, after, **kwargs):
        version = after or before
        assert version is not None
        diff_calls.append(version.resource_id)
        return original_diff_page(before, after, **kwargs)

    def capture_read(resource):
        body_reads.append(resource.resource_id)
        return original_read(resource)

    monkeypatch.setattr(repo, "diff_versions_page", capture_diff_page)
    monkeypatch.setattr(repo, "read_version", capture_read)
    store = await WikiMaintenanceStore.open(tmp_path / "state.sqlite")
    try:
        await store.apply_run(
            expected_revision=None,
            ordered_commit_ids=(seed_commit,),
            reviewed_through=seed_commit,
        )
        result = await WikiMaintenance(store, WikiService(repo), _Reviewer()).run()

        assert result.blocked and not result.advanced
        # The large changed source is sized before its diff can read it.
        assert diff_calls == ["a-small"]
        assert body_reads == ["a-small", "a-small"]
        pending = await store.list_pending()
        assert len(pending) == 1
        assert pending[0].blocking_commit_id == commit
        assert pending[0].evidence_key == "evidence-too-large"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_invalid_utf8_history_uses_lossy_display_without_wedging(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    invalid = create_page(page_id="invalid", title="Invalid").to_bytes() + b"\xff\n"
    source = repo.commit(
        ChangeSet(
            operations=(Create("invalid", "invalid.md", invalid),),
            actor="User",
            origin="desktop",
            reason="historical invalid bytes",
            idempotency_key="invalid-utf8",
        )
    ).commit_id
    store = await WikiMaintenanceStore.open(tmp_path / "state.sqlite")
    reviewer = _Reviewer(
        WikiMaintenanceDecision(
            outcome="needs_review",
            concern=WikiMaintenanceConcernDraft(
                key="invalid-history", summary="Review invalid bytes", proposal="Review the original source manually."
            ),
        )
    )
    try:
        result = await WikiMaintenance(store, WikiService(repo), reviewer).run()

        assert result.blocked and result.error is None
        assert result.feed_target_revision == source
        assert "Lossy UTF-8 display" in reviewer.reports[0].markdown
        assert "\ufffd" in reviewer.reports[0].markdown
        pending = await store.list_pending()
        assert len(pending) == 1
        assert pending[0].blocking_commit_id == source
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
        assert "current editable page" in pending.summary
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
