from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

import arden.database as database
from arden.wiki.approval_store import (
    PendingWikiRenameApprovalConflictError,
    WikiRenameApprovalStatus,
    WikiRenameApprovalStore,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest_asyncio.fixture
async def store(tmp_path: Path):
    conn = await database.connect(tmp_path / "wiki-approvals.db")
    approval_store = WikiRenameApprovalStore(conn)
    await approval_store.init_schema()
    yield approval_store
    await conn.close()


async def _create(store: WikiRenameApprovalStore, approval_id: str = "approval-1", **changes: object):
    fields: dict[str, object] = {
        "approval_id": approval_id,
        "request_key": "rename:page-1",
        "request_fingerprint": "fingerprint-1",
        "generation": 0,
        "base_head": "head-1",
        "plan_json": '{"rewrite":true,"pages":["page-1"]}',
        "old_path": "Old.md",
        "new_path": "New.md",
        "link_count": 2,
        "page_count": 3,
    }
    fields.update(changes)
    return await store.create_pending(**fields)  # type: ignore[arg-type]


async def _replace(
    store: WikiRenameApprovalStore,
    old_approval_id: str = "approval-1",
    approval_id: str = "approval-2",
    **changes: object,
):
    fields: dict[str, object] = {
        "approval_id": approval_id,
        "request_key": "rename:page-1",
        "request_fingerprint": "fingerprint-2",
        "generation": 1,
        "base_head": "head-2",
        "plan_json": {"pages": ["page-1", "page-2"], "rewrite": True},
        "old_path": "Old.md",
        "new_path": "Newer.md",
        "link_count": 3,
        "page_count": 4,
    }
    fields.update(changes)
    return await store.replace_pending(old_approval_id, **fields)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_persists_canonical_opaque_plan_across_reconnect(tmp_path: Path) -> None:
    path = tmp_path / "wiki-approvals.db"
    conn = await database.connect(path)
    store = WikiRenameApprovalStore(conn)
    await store.init_schema()
    created = await _create(store, plan_json=' { "z": 1, "a": {"x": true} } ')
    await conn.close()

    reopened = await database.connect(path)
    restored = WikiRenameApprovalStore(reopened)
    await restored.init_schema()
    approval = await restored.get(created.approval_id)
    await reopened.close()

    assert approval is not None
    assert approval.plan_json == '{"a":{"x":true},"z":1}'
    assert approval.status is WikiRenameApprovalStatus.PENDING


@pytest.mark.asyncio
async def test_same_pending_request_and_fingerprint_is_reused(store: WikiRenameApprovalStore) -> None:
    original = await _create(store)
    repeated = await _create(store, "different-id", generation=9, plan_json={"different": "payload"})

    assert repeated == original
    assert await store.list_pending() == [original]


@pytest.mark.asyncio
async def test_different_pending_fingerprint_is_not_silently_replaced(store: WikiRenameApprovalStore) -> None:
    await _create(store)

    with pytest.raises(PendingWikiRenameApprovalConflictError, match="different open"):
        await _create(store, "approval-2", request_fingerprint="fingerprint-2")


@pytest.mark.asyncio
async def test_applying_request_remains_unique_and_reusable(store: WikiRenameApprovalStore) -> None:
    original = await _create(store)
    assert await store.claim_accept(original.approval_id) is True

    reused = await _create(store, "approval-2")
    assert reused.status is WikiRenameApprovalStatus.APPLYING
    with pytest.raises(PendingWikiRenameApprovalConflictError, match="different open"):
        await _create(store, "approval-3", request_fingerprint="fingerprint-3")


@pytest.mark.asyncio
async def test_accept_is_atomic_with_commit_and_same_commit_retries(store: WikiRenameApprovalStore) -> None:
    await _create(store)

    assert await store.claim_accept("approval-1") is True
    assert await store.accept("approval-1", commit_id="commit-1", resolution="applied") is True
    assert await store.record_commit("approval-1", commit_id="commit-1") is True
    assert await store.accept("approval-1", commit_id="commit-2") is False

    approval = await store.get("approval-1")
    assert approval is not None
    assert approval.status is WikiRenameApprovalStatus.ACCEPTED
    assert approval.commit_id == "commit-1"
    assert approval.resolved_at is not None
    assert approval.resolution == "applied"


@pytest.mark.asyncio
async def test_accept_requires_applying_claim(store: WikiRenameApprovalStore) -> None:
    pending = await _create(store)

    assert await store.accept("approval-1", commit_id="commit-1") is False
    assert await store.get("approval-1") == pending


@pytest.mark.asyncio
async def test_failed_apply_claim_returns_to_pending_and_can_be_rejected(store: WikiRenameApprovalStore) -> None:
    await _create(store)
    assert await store.claim_accept("approval-1") is True

    assert await store.release_accept("approval-1", resolution="replan unavailable") is True
    pending = await store.get("approval-1")
    assert pending is not None
    assert pending.status is WikiRenameApprovalStatus.PENDING
    assert pending.resolution == "replan unavailable"

    assert await store.claim_accept("approval-1") is True
    applying = await store.get("approval-1")
    assert applying is not None and applying.resolution is None
    assert await store.release_accept("approval-1", resolution="still unavailable") is True
    assert await store.reject("approval-1", resolution="user rejected") is True


@pytest.mark.asyncio
async def test_reject_uses_pending_compare_and_swap(store: WikiRenameApprovalStore) -> None:
    await _create(store)

    assert await store.reject("approval-1", resolution="stale preview") is True
    assert await store.reject("approval-1", resolution="again") is False
    assert await store.accept("approval-1", commit_id="commit-1") is False

    approval = await store.get("approval-1")
    assert approval is not None
    assert approval.status is WikiRenameApprovalStatus.REJECTED
    assert approval.commit_id is None
    assert approval.resolution == "stale preview"


@pytest.mark.asyncio
async def test_supersede_records_replacement_and_allows_next_generation(store: WikiRenameApprovalStore) -> None:
    await _create(store)
    replacement = await _create(
        store,
        "approval-2",
        request_key="rename:page-2",
        generation=1,
        request_fingerprint="fingerprint-2",
    )

    assert await store.supersede("approval-1", replacement_approval_id=replacement.approval_id) is True
    successor = await _create(
        store,
        "approval-3",
        generation=2,
        request_fingerprint="fingerprint-3",
    )

    prior = await store.get("approval-1")
    assert prior is not None
    assert prior.status is WikiRenameApprovalStatus.SUPERSEDED
    assert prior.replacement_approval_id == "approval-2"
    assert successor.generation == 2


@pytest.mark.asyncio
async def test_replace_pending_atomically_creates_same_request_successor(store: WikiRenameApprovalStore) -> None:
    await _create(store)

    replacement = await _replace(store, resolution="base head changed")

    assert replacement is not None
    assert replacement.request_key == "rename:page-1"
    assert replacement.status is WikiRenameApprovalStatus.PENDING
    prior = await store.get("approval-1")
    assert prior is not None
    assert prior.status is WikiRenameApprovalStatus.SUPERSEDED
    assert prior.replacement_approval_id == replacement.approval_id
    assert prior.resolution == "base head changed"
    assert await store.list_pending() == [replacement]


@pytest.mark.asyncio
async def test_replace_pending_can_supersede_applying_plan(store: WikiRenameApprovalStore) -> None:
    await _create(store)
    assert await store.claim_accept("approval-1") is True

    replacement = await _replace(store)

    assert replacement is not None
    prior = await store.get("approval-1")
    assert prior is not None
    assert prior.status is WikiRenameApprovalStatus.SUPERSEDED
    assert prior.replacement_approval_id == replacement.approval_id
    assert await store.list_pending() == [replacement]


@pytest.mark.asyncio
async def test_replace_pending_rolls_back_supersede_when_insert_conflicts(store: WikiRenameApprovalStore) -> None:
    original = await _create(store)
    await _create(
        store,
        "approval-2",
        request_key="rename:other",
        request_fingerprint="fingerprint-other",
    )
    assert await store.reject("approval-2") is True

    with pytest.raises(PendingWikiRenameApprovalConflictError, match="conflicts"):
        await _replace(store, approval_id="approval-2")

    assert await store.get("approval-1") == original
    assert await store.list_pending() == [original]


@pytest.mark.asyncio
async def test_list_pending_excludes_resolved_approvals(store: WikiRenameApprovalStore) -> None:
    await _create(store, "approval-1")
    await _create(
        store,
        "approval-2",
        request_key="rename:page-2",
        request_fingerprint="fingerprint-2",
    )
    assert await store.reject("approval-1") is True

    assert [approval.approval_id for approval in await store.list_pending()] == ["approval-2"]


@pytest.mark.asyncio
async def test_concurrent_claim_and_reject_have_one_compare_and_swap_winner(store: WikiRenameApprovalStore) -> None:
    await _create(store)

    claimed, rejected = await asyncio.gather(
        store.claim_accept("approval-1"),
        store.reject("approval-1"),
    )

    assert (claimed, rejected) in {(True, False), (False, True)}
    approval = await store.get("approval-1")
    assert approval is not None
    if claimed:
        assert approval.status is WikiRenameApprovalStatus.APPLYING
        assert await store.reject("approval-1") is False
        assert await store.accept("approval-1", commit_id="commit-1") is True
    else:
        assert approval.status is WikiRenameApprovalStatus.REJECTED
        assert await store.claim_accept("approval-1") is False


@pytest.mark.asyncio
async def test_applying_claim_survives_reopen_and_is_retryable(tmp_path: Path) -> None:
    path = tmp_path / "wiki-approvals.db"
    conn = await database.connect(path)
    store = WikiRenameApprovalStore(conn)
    await store.init_schema()
    applying = await _create(store)
    assert await store.claim_accept(applying.approval_id) is True
    await conn.close()

    reopened = await database.connect(path)
    restored = WikiRenameApprovalStore(reopened)
    await restored.init_schema()
    open_approvals = await restored.list_pending()

    assert len(open_approvals) == 1
    assert open_approvals[0].status is WikiRenameApprovalStatus.APPLYING
    assert await restored.claim_accept(applying.approval_id) is True
    assert await restored.record_commit(applying.approval_id, commit_id="commit-after-restart") is True
    await reopened.close()


@pytest.mark.asyncio
async def test_schema_reinitialization_and_input_validation(store: WikiRenameApprovalStore) -> None:
    await store.init_schema()
    await store.init_schema()

    with pytest.raises(ValueError, match="JSON object"):
        await _create(store, plan_json="[]")
    with pytest.raises(ValueError, match="nonnegative"):
        await _create(store, generation=-1)
    with pytest.raises(ValueError, match="nonempty"):
        await _create(store, old_path=" ")
