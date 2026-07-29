import asyncio
import sqlite3

import pytest

from arden.database import connect
from arden.wiki.constants import TOPIC_PAGE_COLLISION_EVIDENCE_PREFIX
from arden.wiki.maintenance.store import (
    CONSUMER_ID,
    WikiMaintenanceReview,
    WikiMaintenanceReviewAction,
    WikiMaintenanceReviewConflictError,
    WikiMaintenanceReviewInput,
    WikiMaintenanceReviewStatus,
    WikiMaintenanceStore,
    WikiMaintenanceWatermarkConflictError,
    WikiProjectionWatermarkConflictError,
)

pytestmark = pytest.mark.asyncio


def _revision(character: str) -> str:
    return character * 64


def _review(
    *,
    commit: str = _revision("a"),
    key: str = "unresolved_link:source:missing",
    fingerprint: str = _revision("1"),
) -> WikiMaintenanceReviewInput:
    return WikiMaintenanceReviewInput(
        blocking_commit_id=commit,
        evidence_key=key,
        evidence_fingerprint=fingerprint,
        summary="Source page links to a missing target.",
        proposal_json={"action": "create_or_retarget"},
    )


async def _persist_review(
    store: WikiMaintenanceStore,
    review: WikiMaintenanceReviewInput,
) -> WikiMaintenanceReview:
    result = await store.apply_run(
        expected_revision=None,
        ordered_commit_ids=(review.blocking_commit_id,),
        reviewed_through=review.blocking_commit_id,
        reviews=(review,),
    )
    return result.reviews[0]


async def test_review_and_watermark_survive_restart(tmp_path) -> None:
    path = tmp_path / "maintenance.sqlite"
    first = await WikiMaintenanceStore.open(path)
    try:
        created = await _persist_review(first, _review())
        resolved = await first.resolve(
            created.review_id,
            expected_generation=created.generation,
            action=WikiMaintenanceReviewAction.ACCEPT,
        )
        applied = await first.apply_run(
            expected_revision=None,
            ordered_commit_ids=(),
            reviewed_through=_revision("a"),
        )
        assert applied.watermark is not None and applied.watermark.consumer_id == CONSUMER_ID
        assert resolved.status is WikiMaintenanceReviewStatus.ACCEPTED
    finally:
        await first.close()

    second = await WikiMaintenanceStore.open(path)
    try:
        restored = await second.get_review(created.review_id)
        assert restored is not None
        assert restored.status is WikiMaintenanceReviewStatus.ACCEPTED
        assert restored.proposal_json == '{"action":"create_or_retarget"}'
        assert (await second.get_watermark()).revision == _revision("a")
    finally:
        await second.close()


async def test_non_executable_review_cannot_be_accepted(tmp_path) -> None:
    store = await WikiMaintenanceStore.open(tmp_path / "maintenance.sqlite")
    review = WikiMaintenanceReviewInput(
        blocking_commit_id=_revision("a"),
        evidence_key="manual-choice",
        evidence_fingerprint=_revision("1"),
        summary="This needs a manual decision.",
    )
    try:
        created = await _persist_review(store, review)
        with pytest.raises(ValueError, match="non-executable"):
            await store.resolve(
                created.review_id,
                expected_generation=created.generation,
                action=WikiMaintenanceReviewAction.ACCEPT,
            )
        unchanged = await store.get_review(created.review_id)
        assert unchanged is not None
        assert unchanged.status is WikiMaintenanceReviewStatus.NEEDS_REVIEW
    finally:
        await store.close()


async def test_page_collision_refresh_cannot_overwrite_user_decision(tmp_path) -> None:
    store = await WikiMaintenanceStore.open(tmp_path / "maintenance.sqlite")
    review = _review(key=f"{TOPIC_PAGE_COLLISION_EVIDENCE_PREFIX}pair")
    try:
        created = await store.record_page_collision_review(review)
        rejected = await store.resolve(
            created.review_id,
            expected_generation=created.generation,
            action=WikiMaintenanceReviewAction.REJECT,
        )
        with pytest.raises(WikiMaintenanceReviewConflictError, match="before evidence refresh"):
            await store.refresh_page_collision_review(
                created.review_id,
                expected_generation=created.generation,
                expected_status=WikiMaintenanceReviewStatus.NEEDS_REVIEW,
                review=_review(
                    key=review.evidence_key,
                    fingerprint=_revision("2"),
                ),
            )
        unchanged = await store.get_review(created.review_id)
        assert unchanged == rejected
    finally:
        await store.close()


async def test_normal_run_cannot_persist_reserved_collision_evidence(tmp_path) -> None:
    store = await WikiMaintenanceStore.open(tmp_path / "maintenance.sqlite")
    try:
        with pytest.raises(ValueError, match="trusted collision API"):
            await _persist_review(
                store,
                _review(key=f"{TOPIC_PAGE_COLLISION_EVIDENCE_PREFIX}forged"),
            )
        assert await store.list_pending() == []
    finally:
        await store.close()


async def test_projection_watermark_advances_with_compare_and_swap(tmp_path) -> None:
    store = await WikiMaintenanceStore.open(tmp_path / "maintenance.sqlite")
    first = _revision("a")
    second = _revision("b")
    try:
        assert await store.get_projection_revision() is None
        await store.record_projection_revision(expected_revision=None, revision=first)
        assert await store.get_projection_revision() == first

        with pytest.raises(WikiProjectionWatermarkConflictError, match="before advance"):
            await store.record_projection_revision(
                expected_revision=_revision("c"),
                revision=second,
            )

        assert await store.get_projection_revision() == first
        await store.record_projection_revision(expected_revision=first, revision=second)
        assert await store.get_projection_revision() == second
    finally:
        await store.close()


async def test_system_clear_is_generation_guarded_and_survives_restart(tmp_path) -> None:
    path = tmp_path / "maintenance.sqlite"
    store = await WikiMaintenanceStore.open(path)
    try:
        created = await _persist_review(store, _review())
        with pytest.raises(WikiMaintenanceReviewConflictError, match="before clearing"):
            await store.clear(created.review_id, expected_generation=created.generation + 1)
        cleared = await store.clear(created.review_id, expected_generation=created.generation)
        assert cleared.status is WikiMaintenanceReviewStatus.CLEARED
        assert cleared.resolved_at is not None
        assert await store.list_pending() == []
    finally:
        await store.close()

    restored = await WikiMaintenanceStore.open(path)
    try:
        review = (await restored.list_history())[0]
        assert review.status is WikiMaintenanceReviewStatus.CLEARED
    finally:
        await restored.close()


async def test_same_evidence_is_idempotent_and_does_not_reopen_resolution(tmp_path) -> None:
    store = await WikiMaintenanceStore.open(tmp_path / "maintenance.sqlite")
    try:
        created = await _persist_review(store, _review())
        await store.resolve(
            created.review_id,
            expected_generation=0,
            action=WikiMaintenanceReviewAction.REJECT,
            decision_note="This page is intentionally external.",
        )
        repeated = await _persist_review(store, _review())
        assert repeated.review_id == created.review_id
        assert repeated.generation == 0
        assert repeated.status is WikiMaintenanceReviewStatus.REJECTED
        assert await store.list_pending() == []
    finally:
        await store.close()


async def test_changed_evidence_refreshes_one_review_generation(tmp_path) -> None:
    store = await WikiMaintenanceStore.open(tmp_path / "maintenance.sqlite")
    try:
        created = await _persist_review(store, _review())
        await store.resolve(
            created.review_id,
            expected_generation=0,
            action=WikiMaintenanceReviewAction.ACCEPT,
        )
        refreshed = await _persist_review(store, _review(fingerprint=_revision("2")))
        assert refreshed.review_id == created.review_id
        assert refreshed.generation == 1
        assert refreshed.status is WikiMaintenanceReviewStatus.NEEDS_REVIEW
        assert refreshed.decision_note is None
        assert [review.review_id for review in await store.list_pending()] == [created.review_id]
    finally:
        await store.close()


async def test_resolution_is_generation_cas_and_manual_requires_note(tmp_path) -> None:
    store = await WikiMaintenanceStore.open(tmp_path / "maintenance.sqlite")
    try:
        created = await _persist_review(store, _review())
        with pytest.raises(ValueError, match="decision_note"):
            await store.resolve(
                created.review_id,
                expected_generation=0,
                action=WikiMaintenanceReviewAction.RESOLVE_MANUAL,
                decision_note=" ",
            )
        resolved = await store.resolve(
            created.review_id,
            expected_generation=0,
            action=WikiMaintenanceReviewAction.RESOLVE_MANUAL,
            decision_note="Fixed directly in the source document.",
        )
        assert resolved.status is WikiMaintenanceReviewStatus.RESOLVED_MANUAL
        with pytest.raises(WikiMaintenanceReviewConflictError, match="changed"):
            await store.resolve(
                created.review_id,
                expected_generation=0,
                action=WikiMaintenanceReviewAction.ACCEPT,
            )
    finally:
        await store.close()


async def test_apply_run_persists_later_open_review_and_advances_only_safe_prefix(tmp_path) -> None:
    store = await WikiMaintenanceStore.open(tmp_path / "maintenance.sqlite")
    first, second, third = _revision("a"), _revision("b"), _revision("c")
    try:
        applied = await store.apply_run(
            expected_revision=None,
            ordered_commit_ids=(first, second, third),
            reviewed_through=first,
            reviews=(_review(commit=second),),
        )
        assert applied.watermark is not None and applied.watermark.revision == first
        assert len(applied.reviews) == 1
        review = await store.get_by_evidence(second, "unresolved_link:source:missing")
        assert review is not None and review.status is WikiMaintenanceReviewStatus.NEEDS_REVIEW

        blocked = await store.apply_run(
            expected_revision=first,
            ordered_commit_ids=(second, third),
            reviewed_through=third,
        )
        assert blocked.watermark is not None and blocked.watermark.revision == first
        assert (await store.get_watermark()).revision == first

        await store.resolve(
            review.review_id,
            expected_generation=review.generation,
            action=WikiMaintenanceReviewAction.ACCEPT,
        )
        advanced = await store.apply_run(
            expected_revision=first,
            ordered_commit_ids=(second, third),
            reviewed_through=third,
        )
        assert advanced.watermark is not None and advanced.watermark.revision == third
    finally:
        await store.close()


async def test_apply_run_persists_a_first_commit_block_without_advancing(tmp_path) -> None:
    store = await WikiMaintenanceStore.open(tmp_path / "maintenance.sqlite")
    first, second = _revision("a"), _revision("b")
    try:
        applied = await store.apply_run(
            expected_revision=None,
            ordered_commit_ids=(first, second),
            reviewed_through=first,
            reviews=(_review(commit=first),),
        )
        assert applied.watermark is None
        assert await store.get_watermark() is None
        assert await store.get_by_evidence(first, "unresolved_link:source:missing") is not None
    finally:
        await store.close()


async def test_apply_run_watermark_cas_has_one_concurrent_winner(tmp_path) -> None:
    path = tmp_path / "maintenance.sqlite"
    first = await WikiMaintenanceStore.open(path)
    second = await WikiMaintenanceStore.open(path)
    try:
        initial = _revision("a")
        await first.apply_run(expected_revision=None, ordered_commit_ids=(), reviewed_through=initial)
        results = await asyncio.gather(
            first.apply_run(
                expected_revision=initial,
                ordered_commit_ids=(_revision("b"),),
                reviewed_through=_revision("b"),
            ),
            second.apply_run(
                expected_revision=initial,
                ordered_commit_ids=(_revision("c"),),
                reviewed_through=_revision("c"),
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(result, WikiMaintenanceWatermarkConflictError) for result in results) == 1
        assert (await first.get_watermark()).revision in {_revision("b"), _revision("c")}
    finally:
        await first.close()
        await second.close()


async def test_two_connections_allow_only_one_open_concern_per_commit(tmp_path) -> None:
    path = tmp_path / "maintenance.sqlite"
    first = await WikiMaintenanceStore.open(path)
    second = await WikiMaintenanceStore.open(path)
    commit = _revision("a")
    try:
        results = await asyncio.gather(
            first.apply_run(
                expected_revision=None,
                ordered_commit_ids=(commit,),
                reviewed_through=commit,
                reviews=(_review(commit=commit, key="first"),),
            ),
            second.apply_run(
                expected_revision=None,
                ordered_commit_ids=(commit,),
                reviewed_through=commit,
                reviews=(_review(commit=commit, key="second"),),
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(result, WikiMaintenanceReviewConflictError) for result in results) == 1
        pending = await first.list_pending()
        assert len(pending) == 1
        assert pending[0].evidence_key in {"first", "second"}
        assert await first.get_watermark() is None
    finally:
        await first.close()
        await second.close()


async def test_apply_run_rejects_checkpoint_commit_in_change_chain(tmp_path) -> None:
    store = await WikiMaintenanceStore.open(tmp_path / "maintenance.sqlite")
    checkpoint = _revision("a")
    try:
        await store.apply_run(expected_revision=None, ordered_commit_ids=(), reviewed_through=checkpoint)
        with pytest.raises(ValueError, match="only commits after"):
            await store.apply_run(
                expected_revision=checkpoint,
                ordered_commit_ids=(checkpoint,),
                reviewed_through=checkpoint,
                reviews=(_review(commit=checkpoint, fingerprint=_revision("2")),),
            )
        assert (await store.get_watermark()).revision == checkpoint
        assert await store.list_pending() == []
    finally:
        await store.close()


async def test_stale_blocked_run_rolls_back_its_review(tmp_path) -> None:
    path = tmp_path / "maintenance.sqlite"
    first = await WikiMaintenanceStore.open(path)
    second = await WikiMaintenanceStore.open(path)
    initial, current, blocked = _revision("a"), _revision("b"), _revision("c")
    try:
        await first.apply_run(expected_revision=None, ordered_commit_ids=(), reviewed_through=initial)
        await second.apply_run(
            expected_revision=initial,
            ordered_commit_ids=(current,),
            reviewed_through=current,
        )

        with pytest.raises(WikiMaintenanceWatermarkConflictError, match="before the run"):
            await first.apply_run(
                expected_revision=initial,
                ordered_commit_ids=(current, blocked),
                reviewed_through=current,
                reviews=(_review(commit=current),),
            )
        assert await second.get_by_evidence(current, "unresolved_link:source:missing") is None
        assert (await second.get_watermark()).revision == current
    finally:
        await first.close()
        await second.close()


async def test_cancelled_begin_cannot_poison_store_connection(tmp_path, monkeypatch) -> None:
    path = tmp_path / "maintenance.sqlite"
    store = await WikiMaintenanceStore.open(path)
    blocker = await connect(path, autocommit=True)
    entered = asyncio.Event()
    original_execute = store._conn.execute

    async def observed_execute(sql, parameters=None):
        if sql == "BEGIN IMMEDIATE":
            entered.set()
        if parameters is None:
            return await original_execute(sql)
        return await original_execute(sql, parameters)

    monkeypatch.setattr(store._conn, "execute", observed_execute)
    await blocker.execute("BEGIN IMMEDIATE")
    task = asyncio.create_task(_persist_review(store, _review()))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        await blocker.rollback()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert not store._conn.in_transaction
        created = await _persist_review(store, _review())
        assert created.status is WikiMaintenanceReviewStatus.NEEDS_REVIEW
    finally:
        if blocker.in_transaction:
            await blocker.rollback()
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        await blocker.close()
        await store.close()


async def test_apply_run_allows_no_markdown_through_target(tmp_path) -> None:
    store = await WikiMaintenanceStore.open(tmp_path / "maintenance.sqlite")
    try:
        applied = await store.apply_run(
            expected_revision=None,
            ordered_commit_ids=(),
            reviewed_through=_revision("a"),
        )
        assert applied.watermark is not None and applied.watermark.revision == _revision("a")
    finally:
        await store.close()


async def test_store_validates_sha256_fingerprints_and_persisted_review_states(tmp_path) -> None:
    store = await WikiMaintenanceStore.open(tmp_path / "maintenance.sqlite")
    try:
        with pytest.raises(ValueError, match="64-character"):
            await _persist_review(store, _review(fingerprint="not-a-sha256"))
        with pytest.raises(sqlite3.IntegrityError):
            await store._conn.execute(
                """
                INSERT INTO wiki_maintenance_reviews (
                    review_id, blocking_commit_id, evidence_key, evidence_fingerprint,
                    generation, status, summary, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, 'accepted', ?, ?, ?)
                """,
                (
                    "bad-review",
                    _revision("a"),
                    "bad-state",
                    _revision("1"),
                    "Missing a resolved timestamp.",
                    "2026-07-28T00:00:00+00:00",
                    "2026-07-28T00:00:00+00:00",
                ),
            )
        with pytest.raises(sqlite3.IntegrityError):
            await store._conn.execute(
                """
                INSERT INTO wiki_maintenance_reviews (
                    review_id, blocking_commit_id, evidence_key, evidence_fingerprint,
                    generation, status, summary, created_at, updated_at, resolved_at
                ) VALUES (?, ?, ?, ?, 0, 'resolved_manual', ?, ?, ?, ?)
                """,
                (
                    "manual-without-note",
                    _revision("c"),
                    "manual-without-note",
                    _revision("3"),
                    "A manual resolution requires a decision note.",
                    "2026-07-28T00:00:00+00:00",
                    "2026-07-28T00:00:00+00:00",
                    "2026-07-28T00:00:00+00:00",
                ),
            )
        with pytest.raises(sqlite3.IntegrityError):
            await store._conn.execute(
                """
                INSERT INTO wiki_maintenance_reviews (
                    review_id, blocking_commit_id, evidence_key, evidence_fingerprint,
                    generation, status, summary, created_at, updated_at, resolved_at, decision_note
                ) VALUES (?, ?, ?, ?, 0, 'accepted', ?, ?, ?, ?, ' ')
                """,
                (
                    "blank-note",
                    _revision("b"),
                    "blank-note",
                    _revision("2"),
                    "An accepted review cannot carry a blank note.",
                    "2026-07-28T00:00:00+00:00",
                    "2026-07-28T00:00:00+00:00",
                    "2026-07-28T00:00:00+00:00",
                ),
            )
    finally:
        await store.close()


async def test_requires_autocommit_and_close_releases_connection(tmp_path) -> None:
    path = tmp_path / "maintenance.sqlite"
    transactional = await connect(path)
    try:
        with pytest.raises(ValueError, match="autocommit"):
            WikiMaintenanceStore(transactional)
    finally:
        await transactional.close()

    store = await WikiMaintenanceStore.open(path)
    await store.close()
    with pytest.raises(ValueError, match="Connection closed"):
        await store.get_watermark()
