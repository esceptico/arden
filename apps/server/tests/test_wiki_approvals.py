import asyncio
import json
from pathlib import Path

import pytest

import arden.database as database
from arden.revisions import ChangeSet, Create, ManagedFileRepository, RevisionConflictError
from arden.wiki.approval_store import WikiRenameApprovalStatus, WikiRenameApprovalStore
from arden.wiki.approvals import (
    CorruptWikiRenameApprovalError,
    RenamePlanSerializationError,
    RenamePolicy,
    WikiRenameApprovalCoordinator,
    deserialize_rename_plan,
    rename_plan_fingerprint,
    serialize_rename_plan,
)
from arden.wiki.pages import create_page
from arden.wiki.service import WikiService


def _repo(tmp_path: Path) -> ManagedFileRepository:
    return ManagedFileRepository(tmp_path / "wiki")


def _seed(repo: ManagedFileRepository, *pages: tuple[str, str, object]) -> None:
    repo.commit(
        ChangeSet(
            operations=tuple(Create(page_id, path, page.to_bytes()) for page_id, path, page in pages),
            actor="test",
            origin="test",
            reason="seed",
            idempotency_key="seed-" + "-".join(page_id for page_id, _path, _page in pages),
        )
    )


async def _coordinator(tmp_path: Path) -> tuple[WikiService, WikiRenameApprovalCoordinator, object]:
    service = WikiService(_repo(tmp_path))
    conn = await database.connect(tmp_path / "approvals.db")
    store = WikiRenameApprovalStore(conn)
    await store.init_schema()
    return service, WikiRenameApprovalCoordinator(service, store), conn


def _request(service: WikiService, *, request_key: str = "rename:target") -> dict[str, str | None]:
    target = service.read_page("target")
    return {
        "request_key": request_key,
        "page_id": "target",
        "new_path": "new.md",
        "new_title": "New",
        "expected_version": target.resource.version_id,
        "base_head": service.repository.head,
    }


@pytest.mark.asyncio
async def test_plan_round_trip_is_exact_and_corruption_fails_closed(tmp_path: Path) -> None:
    service, _coordinator_instance, conn = await _coordinator(tmp_path)
    target = create_page(title="Old", page_id="target", body=b"Self [[Old]]")
    source = create_page(title="Source", page_id="source", body=b"[[Old#part|alias]]")
    _seed(service.repository, ("target", "old.md", target), ("source", "source.md", source))
    request = _request(service)
    plan = service.prepare_rename(
        "target",
        new_path=str(request["new_path"]),
        new_title=str(request["new_title"]),
        expected_version=str(request["expected_version"]),
        base_head=request["base_head"],
    )

    serialized = serialize_rename_plan(plan)
    assert deserialize_rename_plan(serialized) == plan
    assert len(rename_plan_fingerprint(serialized)) == 64

    corrupted = json.loads(serialized)
    corrupted["plan"]["moved_content"] = "!"
    with pytest.raises(RenamePlanSerializationError):
        deserialize_rename_plan(json.dumps(corrupted, sort_keys=True, separators=(",", ":")))
    await conn.close()


@pytest.mark.asyncio
async def test_ask_persists_reuses_duplicates_and_rejects(tmp_path: Path) -> None:
    service, coordinator, conn = await _coordinator(tmp_path)
    _seed(service.repository, ("target", "old.md", create_page(title="Old", page_id="target")))
    request = _request(service)
    first = await coordinator.request_rename(**request)
    duplicate = await coordinator.request_rename(**request)
    assert first.status == duplicate.status == "pending"
    assert first.approval is not None and duplicate.approval == first.approval
    approval_id = first.approval.approval_id
    await conn.close()

    reopened = await database.connect(tmp_path / "approvals.db")
    store = WikiRenameApprovalStore(reopened)
    await store.init_schema()
    restarted = WikiRenameApprovalCoordinator(service, store)
    assert [item.approval_id for item in await restarted.list_pending()] == [approval_id]
    rejected = await restarted.reject(approval_id, resolution="no thanks")
    assert rejected.status is WikiRenameApprovalStatus.REJECTED
    assert rejected.approval is not None and rejected.approval.commit_id is None
    await reopened.close()


@pytest.mark.asyncio
async def test_always_rewrites_and_never_keeps_inbound_links(tmp_path: Path) -> None:
    service, coordinator, conn = await _coordinator(tmp_path / "always")
    _seed(
        service.repository,
        ("target", "old.md", create_page(title="Old", page_id="target")),
        ("source", "source.md", create_page(title="Source", page_id="source", body=b"[[Old]]")),
    )
    committed = await coordinator.request_rename(**_request(service), policy=RenamePolicy.ALWAYS)
    assert committed.status == "committed"
    assert b"[[New]]" in service.repository.read("source")
    await conn.close()

    service, coordinator, conn = await _coordinator(tmp_path / "never")
    _seed(
        service.repository,
        ("target", "old.md", create_page(title="Old", page_id="target")),
        ("source", "source.md", create_page(title="Source", page_id="source", body=b"[[Old]]")),
    )
    committed = await coordinator.request_rename(**_request(service), policy=RenamePolicy.NEVER)
    assert committed.status == "committed"
    assert service.repository.read("source").endswith(b"[[Old]]")
    await conn.close()


@pytest.mark.asyncio
async def test_accept_commits_then_retries_same_approval(tmp_path: Path) -> None:
    service, coordinator, conn = await _coordinator(tmp_path)
    _seed(service.repository, ("target", "old.md", create_page(title="Old", page_id="target")))
    requested = await coordinator.request_rename(**_request(service))
    assert requested.approval is not None

    accepted = await coordinator.accept(requested.approval.approval_id)
    retried = await coordinator.accept(requested.approval.approval_id)
    assert accepted.status == retried.status == "accepted"
    assert accepted.commit_id == retried.commit_id == service.repository.head
    assert service.repository.get("target").path == "new.md"
    await conn.close()


@pytest.mark.asyncio
async def test_concurrent_accepts_have_one_applier(tmp_path: Path) -> None:
    service, coordinator, conn = await _coordinator(tmp_path)
    _seed(service.repository, ("target", "old.md", create_page(title="Old", page_id="target")))
    requested = await coordinator.request_rename(**_request(service))
    assert requested.approval is not None
    started = asyncio.Event()
    finish = asyncio.Event()
    calls = 0

    async def apply_once(plan):
        nonlocal calls
        calls += 1
        started.set()
        await finish.wait()
        return service.apply_rename(plan)

    first = asyncio.create_task(coordinator.accept(requested.approval.approval_id, apply_plan=apply_once))
    await started.wait()
    second = asyncio.create_task(coordinator.accept(requested.approval.approval_id, apply_plan=apply_once))
    finish.set()
    accepted, duplicate = await asyncio.gather(first, second)

    assert calls == 1
    assert accepted.status == duplicate.status == "accepted"
    await conn.close()


@pytest.mark.asyncio
async def test_second_runtime_does_not_steal_an_active_apply(tmp_path: Path) -> None:
    service, first, first_conn = await _coordinator(tmp_path)
    second_service = WikiService(_repo(tmp_path))
    second_conn = await database.connect(tmp_path / "approvals.db")
    second_store = WikiRenameApprovalStore(second_conn)
    await second_store.init_schema()
    second = WikiRenameApprovalCoordinator(second_service, second_store)
    _seed(service.repository, ("target", "old.md", create_page(title="Old", page_id="target")))
    requested = await first.request_rename(**_request(service))
    assert requested.approval is not None
    started = asyncio.Event()
    finish = asyncio.Event()
    calls = 0

    async def apply_once(plan):
        nonlocal calls
        calls += 1
        started.set()
        await finish.wait()
        return service.apply_rename(plan)

    active = asyncio.create_task(first.accept(requested.approval.approval_id, apply_plan=apply_once))
    await started.wait()
    duplicate = await second.accept(requested.approval.approval_id)
    assert duplicate.status == "applying"
    assert calls == 1
    finish.set()
    assert (await active).status == "accepted"
    await first_conn.close()
    await second_conn.close()


@pytest.mark.asyncio
async def test_accept_persistence_failure_keeps_a_committed_rename_applying_until_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, coordinator, conn = await _coordinator(tmp_path)
    _seed(service.repository, ("target", "old.md", create_page(title="Old", page_id="target")))
    requested = await coordinator.request_rename(**_request(service))
    assert requested.approval is not None
    persist = coordinator.store.accept
    failed = False

    async def fail_once(*args, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("approval database unavailable")
        return await persist(*args, **kwargs)

    monkeypatch.setattr(coordinator.store, "accept", fail_once)
    with pytest.raises(OSError, match="database unavailable"):
        await coordinator.accept(requested.approval.approval_id)

    applying = await coordinator.store.get(requested.approval.approval_id)
    assert applying is not None and applying.status is WikiRenameApprovalStatus.APPLYING
    assert (await coordinator.reject(requested.approval.approval_id)).status == "applying"
    accepted = await coordinator.accept(requested.approval.approval_id)
    assert accepted.status == "accepted"
    assert service.repository.get("target").path == "new.md"
    await conn.close()


@pytest.mark.asyncio
async def test_cancelled_accept_after_commit_stays_unrejectable_and_retries(tmp_path: Path) -> None:
    service, coordinator, conn = await _coordinator(tmp_path)
    _seed(service.repository, ("target", "old.md", create_page(title="Old", page_id="target")))
    requested = await coordinator.request_rename(**_request(service))
    assert requested.approval is not None

    async def commit_then_cancel(plan):
        service.apply_rename(plan)
        raise asyncio.CancelledError("response lost after commit")

    with pytest.raises(asyncio.CancelledError):
        await coordinator.accept(requested.approval.approval_id, apply_plan=commit_then_cancel)

    applying = await coordinator.store.get(requested.approval.approval_id)
    assert applying is not None and applying.status is WikiRenameApprovalStatus.APPLYING
    assert (await coordinator.reject(requested.approval.approval_id)).status == "applying"
    assert (await coordinator.accept(requested.approval.approval_id)).status == "accepted"
    await conn.close()


@pytest.mark.asyncio
async def test_error_after_commit_stays_unrejectable_and_retries(tmp_path: Path) -> None:
    service, coordinator, conn = await _coordinator(tmp_path)
    _seed(service.repository, ("target", "old.md", create_page(title="Old", page_id="target")))
    requested = await coordinator.request_rename(**_request(service))
    assert requested.approval is not None

    async def commit_then_fail(plan):
        service.apply_rename(plan)
        raise RuntimeError("response lost after commit")

    with pytest.raises(RuntimeError, match="response lost"):
        await coordinator.accept(requested.approval.approval_id, apply_plan=commit_then_fail)

    applying = await coordinator.store.get(requested.approval.approval_id)
    assert applying is not None and applying.status is WikiRenameApprovalStatus.APPLYING
    assert (await coordinator.reject(requested.approval.approval_id)).status == "applying"
    assert (await coordinator.accept(requested.approval.approval_id)).status == "accepted"
    await conn.close()


@pytest.mark.asyncio
async def test_preclaimed_applying_approval_retries_after_restart(tmp_path: Path) -> None:
    service, coordinator, conn = await _coordinator(tmp_path)
    _seed(service.repository, ("target", "old.md", create_page(title="Old", page_id="target")))
    requested = await coordinator.request_rename(**_request(service))
    assert requested.approval is not None
    assert (
        await coordinator.store.claim_accept(
            requested.approval.approval_id,
            owner=coordinator._claim_owner,
        )
        is True
    )
    applying = await coordinator.store.get(requested.approval.approval_id)
    assert applying is not None and applying.status is WikiRenameApprovalStatus.APPLYING
    assert [item.approval_id for item in await coordinator.list_pending()] == [requested.approval.approval_id]
    await conn.close()

    reopened = await database.connect(tmp_path / "approvals.db")
    store = WikiRenameApprovalStore(reopened)
    await store.init_schema()
    restarted = WikiRenameApprovalCoordinator(service, store)
    await store.conn.execute(
        "UPDATE wiki_rename_approvals SET claim_expires_at = ? WHERE approval_id = ?",
        ("2000-01-01T00:00:00+00:00", requested.approval.approval_id),
    )
    await store.conn.commit()
    accepted = await restarted.accept(requested.approval.approval_id)
    assert accepted.status == "accepted"
    assert service.repository.get("target").path == "new.md"
    await reopened.close()


@pytest.mark.asyncio
async def test_accept_reject_race_never_rejects_a_committed_rename(tmp_path: Path) -> None:
    service, coordinator, conn = await _coordinator(tmp_path)
    _seed(service.repository, ("target", "old.md", create_page(title="Old", page_id="target")))
    requested = await coordinator.request_rename(**_request(service))
    assert requested.approval is not None

    accepted, rejected = await asyncio.gather(
        coordinator.accept(requested.approval.approval_id),
        coordinator.reject(requested.approval.approval_id),
    )
    approval = await coordinator.store.get(requested.approval.approval_id)
    assert approval is not None
    assert not (
        approval.status is WikiRenameApprovalStatus.REJECTED and service.repository.get("target").path == "new.md"
    )
    if approval.status is WikiRenameApprovalStatus.ACCEPTED:
        assert accepted.status == "accepted"
        assert service.repository.get("target").path == "new.md"
    else:
        assert approval.status is WikiRenameApprovalStatus.REJECTED
        assert rejected.status is WikiRenameApprovalStatus.REJECTED
        assert service.repository.get("target").path == "old.md"
    await conn.close()


@pytest.mark.asyncio
async def test_head_conflict_supersedes_and_fresh_generation_rewrites_new_link(tmp_path: Path) -> None:
    service, coordinator, conn = await _coordinator(tmp_path)
    _seed(service.repository, ("target", "old.md", create_page(title="Old", page_id="target")))
    original = await coordinator.request_rename(**_request(service))
    assert original.approval is not None
    service.create_page(path="source.md", title="Source", page_id="source", body=b"[[Old]]")

    stale = await coordinator.accept(original.approval.approval_id)
    assert stale.status == "pending"
    assert stale.approval is not None
    assert stale.approval.approval_id != original.approval.approval_id
    old = await coordinator.store.get(original.approval.approval_id)
    assert old is not None and old.status is WikiRenameApprovalStatus.SUPERSEDED
    assert service.repository.get("target").path == "old.md"

    accepted = await coordinator.accept(stale.approval.approval_id)
    assert accepted.status == "accepted"
    assert b"[[New]]" in service.repository.read("source")
    await conn.close()


@pytest.mark.asyncio
async def test_unplannable_stale_rename_returns_to_pending_and_remains_rejectable(tmp_path: Path) -> None:
    service, coordinator, conn = await _coordinator(tmp_path)
    _seed(service.repository, ("target", "old.md", create_page(title="Old", page_id="target")))
    original = await coordinator.request_rename(**_request(service))
    assert original.approval is not None
    service.archive_page("target")

    stale = await coordinator.accept(original.approval.approval_id)
    assert stale.status == "pending"
    assert stale.approval is not None
    assert stale.approval.status is WikiRenameApprovalStatus.PENDING
    assert stale.approval.resolution is not None
    assert "could not be regenerated" in stale.approval.resolution
    assert await coordinator.list_pending() == [stale.approval]

    rejected = await coordinator.reject(original.approval.approval_id, resolution="leave archived")
    assert rejected.status == "rejected"
    assert rejected.approval is not None
    assert rejected.approval.status is WikiRenameApprovalStatus.REJECTED
    await conn.close()


@pytest.mark.asyncio
async def test_replan_race_returns_to_pending_for_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, coordinator, conn = await _coordinator(tmp_path)
    _seed(service.repository, ("target", "old.md", create_page(title="Old", page_id="target")))
    original = await coordinator.request_rename(**_request(service))
    assert original.approval is not None
    service.create_page(path="source.md", title="Source", page_id="source")

    def concurrent_change(*_args: object, **_kwargs: object) -> object:
        raise RevisionConflictError("head changed during replan")

    monkeypatch.setattr(service, "prepare_rename", concurrent_change)
    stale = await coordinator.accept(original.approval.approval_id)

    assert stale.status == "pending"
    assert stale.approval is not None
    assert stale.approval.status is WikiRenameApprovalStatus.PENDING
    assert stale.approval.resolution is not None
    assert stale.approval.resolution == "stale rename plan could not be regenerated; retry or reject"
    await conn.close()


@pytest.mark.asyncio
async def test_external_file_drift_does_not_create_endless_approval_generations(tmp_path: Path) -> None:
    service, coordinator, conn = await _coordinator(tmp_path)
    _seed(service.repository, ("target", "old.md", create_page(title="Old", page_id="target")))
    original = await coordinator.request_rename(**_request(service))
    assert original.approval is not None
    (service.repository.root / "old.md").write_bytes(b"changed outside revision history")

    conflicted = await coordinator.accept(original.approval.approval_id)

    assert conflicted.status == "pending"
    assert conflicted.approval is not None
    assert conflicted.approval.approval_id == original.approval.approval_id
    assert conflicted.approval.generation == 0
    assert conflicted.approval.resolution is not None
    assert "outside revision history" in conflicted.approval.resolution
    assert await coordinator.list_pending() == [conflicted.approval]
    await conn.close()


@pytest.mark.asyncio
async def test_failed_applier_releases_claim_for_retry_or_rejection(tmp_path: Path) -> None:
    service, coordinator, conn = await _coordinator(tmp_path)
    _seed(service.repository, ("target", "old.md", create_page(title="Old", page_id="target")))
    requested = await coordinator.request_rename(**_request(service))
    assert requested.approval is not None

    async def fail_for_area_name_collision(_plan: object) -> object:
        raise ValueError("An active Area with that name already exists")

    with pytest.raises(ValueError, match="active Area"):
        await coordinator.accept(requested.approval.approval_id, apply_plan=fail_for_area_name_collision)

    pending = await coordinator.store.get(requested.approval.approval_id)
    assert pending is not None
    assert pending.status is WikiRenameApprovalStatus.PENDING
    assert pending.resolution == "rename was not applied; fix the reported error, then retry or reject"
    rejected = await coordinator.reject(requested.approval.approval_id, resolution="keep the existing Area")
    assert rejected.status is WikiRenameApprovalStatus.REJECTED
    await conn.close()


@pytest.mark.asyncio
async def test_failed_applier_never_persists_backend_error_text(tmp_path: Path) -> None:
    service, coordinator, conn = await _coordinator(tmp_path)
    _seed(service.repository, ("target", "old.md", create_page(title="Old", page_id="target")))
    requested = await coordinator.request_rename(**_request(service))
    assert requested.approval is not None

    async def fail_with_secret(_plan: object) -> object:
        raise RuntimeError("postgres://agent:secret@db.internal/arden")

    with pytest.raises(RuntimeError, match="secret"):
        await coordinator.accept(requested.approval.approval_id, apply_plan=fail_with_secret)

    approval = await coordinator.store.get(requested.approval.approval_id)
    assert approval is not None
    assert approval.resolution == "rename was not applied; fix the reported error, then retry or reject"
    assert "secret" not in approval.resolution
    await conn.close()


@pytest.mark.asyncio
async def test_tampered_plan_or_metadata_is_never_applied(tmp_path: Path) -> None:
    service, coordinator, conn = await _coordinator(tmp_path)
    _seed(service.repository, ("target", "old.md", create_page(title="Old", page_id="target")))
    requested = await coordinator.request_rename(**_request(service))
    assert requested.approval is not None
    await coordinator.store.conn.execute(
        "UPDATE wiki_rename_approvals SET new_path = ? WHERE approval_id = ?",
        ("forged.md", requested.approval.approval_id),
    )
    await coordinator.store.conn.commit()
    with pytest.raises(CorruptWikiRenameApprovalError, match="metadata"):
        await coordinator.accept(requested.approval.approval_id)
    assert service.repository.get("target").path == "old.md"

    await coordinator.store.conn.execute(
        "UPDATE wiki_rename_approvals SET new_path = ?, plan_json = ? WHERE approval_id = ?",
        (requested.approval.new_path, "{}", requested.approval.approval_id),
    )
    await coordinator.store.conn.commit()
    with pytest.raises(CorruptWikiRenameApprovalError, match="corrupt"):
        await coordinator.accept(requested.approval.approval_id)
    await conn.close()
