from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

import arden.database as database
from arden.config import Config
from arden.revisions import ChangeSet, CorruptRepositoryError, Create, ManagedFileRepository, Update
from arden.server.app import app as server_app
from arden.server.routers.wiki import router as wiki_router
from arden.server.runtime import Runtime
from arden.wiki.approval_store import WikiRenameApprovalStore
from arden.wiki.approvals import WikiRenameApprovalCoordinator
from arden.wiki.constants import WIKI_MAINTENANCE_FACT_DUPLICATE_EVIDENCE_PREFIX
from arden.wiki.exceptions import WikiRenameApplyAmbiguousError
from arden.wiki.maintenance.store import WikiMaintenanceReviewInput, WikiMaintenanceStore
from arden.wiki.pages import create_page
from arden.wiki.service import WikiService


def _seed(service: WikiService, page_id: str, path: str, title: str) -> None:
    page = create_page(page_id=page_id, title=title)
    service.repository.commit(
        ChangeSet(
            operations=(Create(page_id, path, page.to_bytes()),),
            actor="test",
            origin="test",
            reason="seed",
            idempotency_key=f"seed-{page_id}",
        )
    )


def test_wiki_routes_are_registered_on_the_server_app():
    paths = set(server_app.openapi()["paths"])
    assert "/admin/wiki/rename-approvals" in paths
    assert "/admin/wiki/rename-approvals/{approval_id}/accept" in paths
    assert "/admin/wiki/rename-approvals/{approval_id}/reject" in paths
    assert "/admin/wiki/maintenance-reviews" in paths
    assert "/admin/wiki/maintenance-reviews/{review_id}/evidence" in paths
    assert "/admin/wiki/maintenance-reviews/{review_id}/accept" in paths
    assert "/admin/wiki/maintenance-reviews/{review_id}/reject" in paths
    assert "/admin/wiki/maintenance-reviews/{review_id}/resolve-manually" in paths


@pytest_asyncio.fixture
async def wiki_client(tmp_path: Path):
    repository = ManagedFileRepository(tmp_path / "wiki" / "pages", history_root=tmp_path / "wiki" / ".wiki-history")
    service = WikiService(repository)
    _seed(service, "target", "old.md", "Old")
    _seed(service, "other", "other.md", "Other")
    conn = await database.connect(tmp_path / "sessions.db")
    store = WikiRenameApprovalStore(conn)
    await store.init_schema()
    coordinator = WikiRenameApprovalCoordinator(service, store)
    maintenance_store = await WikiMaintenanceStore.open(tmp_path / "maintenance.sqlite")

    app = FastAPI()
    runtime = SimpleNamespace(
        connected=True,
        wiki_rename_coordinator=coordinator,
        wiki_maintenance_store=maintenance_store,
        wiki_service=service,
        health_calls=0,
        maintenance_resume_requests=0,
        fact_maintenance_resume_requests=0,
        maintenance_review_notifications=[],
    )

    async def project_wiki_health() -> None:
        runtime.health_calls += 1

    async def project_wiki_state() -> None:
        await runtime.project_wiki_health()

    async def notify_wiki_maintenance_reviews_changed(revision: str) -> None:
        runtime.maintenance_review_notifications.append(revision)

    async def request_wiki_maintenance() -> None:
        runtime.maintenance_resume_requests += 1

    async def request_fact_maintenance() -> None:
        runtime.fact_maintenance_resume_requests += 1

    runtime.project_wiki_health = project_wiki_health
    runtime.project_wiki_state = project_wiki_state
    runtime.request_wiki_maintenance = request_wiki_maintenance
    runtime.request_fact_maintenance = request_fact_maintenance
    runtime.notify_wiki_maintenance_reviews_changed = notify_wiki_maintenance_reviews_changed
    app.state.runtime = runtime

    async def apply_rename_plan(plan):
        return service.apply_rename(plan)

    async def validate_rename_request(_page_id: str, _new_path: str, _new_title: str) -> None:
        return None

    app.state.area_pages = SimpleNamespace(
        apply_rename_plan=apply_rename_plan,
        validate_rename_request=validate_rename_request,
    )
    app.include_router(wiki_router)
    with TestClient(app) as client:
        yield client, service, store, maintenance_store
    await maintenance_store.close()
    await conn.close()


@pytest.mark.asyncio
async def test_rename_request_is_safe_idempotent_and_default_ask(wiki_client):
    client, service, store, _maintenance_store = wiki_client
    client.app.state.area_pages = SimpleNamespace(
        validate_rename_request=client.app.state.area_pages.validate_rename_request,
    )
    response = client.post(
        "/admin/wiki/rename-approvals",
        json={"page_id": "target", "new_path": "new.md", "new_title": "New"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["commit_id"] is None
    approval = body["approval"]
    assert approval == {
        **approval,
        "old_path": "old.md",
        "new_path": "new.md",
        "old_title": "Old",
        "new_title": "New",
        "link_count": 0,
        "page_count": 0,
        "generation": 0,
        "status": "pending",
        "resolution": None,
        "replacement_approval_id": None,
        "commit_id": None,
    }
    assert "plan_json" not in approval
    assert "request_key" not in approval
    assert service.repository.get("target").path == "old.md"

    duplicate = client.post(
        "/admin/wiki/rename-approvals",
        json={"page_id": "target", "new_path": "new.md", "new_title": "New"},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["approval"]["approval_id"] == approval["approval_id"]

    persisted = await store.get(approval["approval_id"])
    assert persisted is not None
    assert persisted.request_key == "wiki-rename:target"

    bypass = client.post(
        "/admin/wiki/rename-approvals",
        json={
            "page_id": "other",
            "new_path": "bypass.md",
            "new_title": "Bypass",
            "policy": "always",
        },
    )
    assert bypass.status_code == 422
    assert service.repository.get("other").path == "other.md"


def test_rename_request_rejects_an_invalid_bound_area_path_before_approval(wiki_client):
    client, service, _store, _maintenance_store = wiki_client

    async def reject_bound_path(_page_id: str, _new_path: str, _new_title: str) -> None:
        raise ValueError("An attached Area name determines its page path")

    client.app.state.area_pages.validate_rename_request = reject_bound_path
    response = client.post(
        "/admin/wiki/rename-approvals",
        json={"page_id": "target", "new_path": "projects/new.md", "new_title": "New"},
    )

    assert response.status_code == 422
    assert service.repository.get("target").path == "old.md"
    assert not client.get("/admin/wiki/rename-approvals").json()["approvals"]


def test_rename_request_rejects_a_nested_directory_readme_before_approval(wiki_client):
    client, service, _store, _maintenance_store = wiki_client
    page = create_page(page_id="notes-readme", title="Notes guide")
    service.repository.commit(
        ChangeSet(
            operations=(Create("notes-readme", "notes/README.md", page.to_bytes()),),
            actor="test",
            origin="test",
            reason="seed directory contract",
            idempotency_key="seed-notes-readme",
        )
    )

    response = client.post(
        "/admin/wiki/rename-approvals",
        json={"page_id": "notes-readme", "new_path": "notes/guide.md", "new_title": "Notes guide"},
    )

    assert response.status_code == 422
    assert "README paths are fixed" in response.json()["detail"]
    assert not client.get("/admin/wiki/rename-approvals").json()["approvals"]


@pytest.mark.asyncio
async def test_rename_accept_releases_a_failed_area_applier_for_rejection(wiki_client):
    client, service, store, _maintenance_store = wiki_client
    approval = client.post(
        "/admin/wiki/rename-approvals",
        json={"page_id": "target", "new_path": "new.md", "new_title": "New"},
    ).json()["approval"]

    async def fail_for_area_name_collision(_plan):
        raise ValueError("An active Area with that name already exists")

    client.app.state.area_pages.apply_rename_plan = fail_for_area_name_collision
    failed = client.post(f"/admin/wiki/rename-approvals/{approval['approval_id']}/accept")

    assert failed.status_code == 422
    persisted = await store.get(approval["approval_id"])
    assert persisted is not None
    assert persisted.status.value == "pending"
    assert persisted.resolution == "rename was not applied; fix the reported error, then retry or reject"
    rejected = client.post(
        f"/admin/wiki/rename-approvals/{approval['approval_id']}/reject",
        json={"resolution": "keep the existing Area"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert service.repository.get("target").path == "old.md"


@pytest.mark.asyncio
async def test_rename_accept_returns_retryable_error_for_an_ambiguous_applier(wiki_client):
    client, service, store, _maintenance_store = wiki_client
    approval = client.post(
        "/admin/wiki/rename-approvals",
        json={"page_id": "target", "new_path": "new.md", "new_title": "New"},
    ).json()["approval"]

    async def commit_then_lose_response(plan):
        service.apply_rename(plan)
        raise WikiRenameApplyAmbiguousError("response lost")

    client.app.state.area_pages.apply_rename_plan = commit_then_lose_response
    ambiguous = client.post(f"/admin/wiki/rename-approvals/{approval['approval_id']}/accept")
    assert ambiguous.status_code == 503
    assert "retry accept" in ambiguous.json()["detail"]
    applying = await store.get(approval["approval_id"])
    assert applying is not None
    assert applying.status.value == "applying"
    assert applying.resolution == "rename outcome is uncertain; retry accept to finish"
    assert "response lost" not in applying.resolution

    async def apply(plan):
        return service.apply_rename(plan)

    client.app.state.area_pages.apply_rename_plan = apply
    accepted = client.post(f"/admin/wiki/rename-approvals/{approval['approval_id']}/accept")
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"


def test_rename_approval_list_accept_and_reject(wiki_client):
    client, service, _store, _maintenance_store = wiki_client
    target = client.post(
        "/admin/wiki/rename-approvals",
        json={"page_id": "target", "new_path": "new.md", "new_title": "New"},
    ).json()["approval"]
    other = client.post(
        "/admin/wiki/rename-approvals",
        json={"page_id": "other", "new_path": "renamed-other.md", "new_title": "Renamed Other"},
    ).json()["approval"]

    pending = client.get("/admin/wiki/rename-approvals")
    assert pending.status_code == 200
    assert [item["approval_id"] for item in pending.json()["approvals"]] == [
        target["approval_id"],
        other["approval_id"],
    ]

    accepted = client.post(f"/admin/wiki/rename-approvals/{target['approval_id']}/accept")
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"
    assert accepted.json()["approval"]["status"] == "accepted"
    assert service.repository.get("target").path == "new.md"
    assert client.app.state.runtime.health_calls == 1

    rejected = client.post(
        f"/admin/wiki/rename-approvals/{other['approval_id']}/reject",
        json={"resolution": "keep this name"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["approval"]["resolution"] == "keep this name"
    assert service.repository.get("other").path == "other.md"
    assert client.app.state.runtime.health_calls == 1


def test_rename_accept_keeps_committed_result_when_projection_fails(wiki_client):
    client, service, _store, _maintenance_store = wiki_client
    approval = client.post(
        "/admin/wiki/rename-approvals",
        json={"page_id": "target", "new_path": "new.md", "new_title": "New"},
    ).json()["approval"]

    async def fail_health_projection() -> None:
        raise RuntimeError("health projector unavailable")

    client.app.state.runtime.project_wiki_health = fail_health_projection
    response = client.post(f"/admin/wiki/rename-approvals/{approval['approval_id']}/accept")

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert response.json()["projection_pending"] is True
    assert service.repository.get("target").path == "new.md"


def test_router_maps_title_only_not_found_and_unavailable_consistently(wiki_client):
    client, _service, _store, _maintenance_store = wiki_client
    title_only = client.post(
        "/admin/wiki/rename-approvals",
        json={"page_id": "target", "new_path": "old.md", "new_title": "New"},
    )
    assert title_only.status_code == 422

    missing = client.post(
        "/admin/wiki/rename-approvals/missing/accept",
    )
    assert missing.status_code == 404

    unavailable_app = FastAPI()
    unavailable_app.include_router(wiki_router)
    unavailable = TestClient(unavailable_app).get("/admin/wiki/rename-approvals")
    assert unavailable.status_code == 503

    unavailable_maintenance_app = FastAPI()
    unavailable_maintenance_app.state.runtime = SimpleNamespace(connected=True, wiki_maintenance_store=None)
    unavailable_maintenance_app.include_router(wiki_router)
    unavailable_maintenance = TestClient(unavailable_maintenance_app).get("/admin/wiki/maintenance-reviews")
    assert unavailable_maintenance.status_code == 503


def test_router_returns_conflict_when_the_server_snapshot_races(wiki_client, monkeypatch: pytest.MonkeyPatch):
    client, service, _store, _maintenance_store = wiki_client
    original_read_page = service.read_page

    def stale_read(page_id: str):
        record = original_read_page(page_id)
        changed = record.page.with_title("Changed")
        service.repository.commit(
            ChangeSet(
                operations=(Update(page_id, record.resource.version_id, changed.to_bytes()),),
                actor="test",
                origin="test",
                reason="race",
                idempotency_key="race-target",
                expected_head=service.repository.head,
            )
        )
        return record

    monkeypatch.setattr(service, "read_page", stale_read)
    response = client.post(
        "/admin/wiki/rename-approvals",
        json={"page_id": "target", "new_path": "new.md", "new_title": "New"},
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_router_fails_closed_for_a_corrupt_approval(wiki_client):
    client, _service, store, _maintenance_store = wiki_client
    requested = client.post(
        "/admin/wiki/rename-approvals",
        json={"page_id": "target", "new_path": "new.md", "new_title": "New"},
    ).json()["approval"]
    await store.conn.execute(
        "UPDATE wiki_rename_approvals SET plan_json = ? WHERE approval_id = ?",
        ("{}", requested["approval_id"]),
    )
    await store.conn.commit()

    response = client.post(f"/admin/wiki/rename-approvals/{requested['approval_id']}/accept")

    assert response.status_code == 503


async def _pending_maintenance_review(
    store: WikiMaintenanceStore,
    *,
    commit: str,
    key: str,
    proposal: dict | None,
):
    review = WikiMaintenanceReviewInput(
        blocking_commit_id=commit,
        evidence_key=key,
        evidence_fingerprint="f" * 64,
        summary="Needs a human decision.",
        proposal_json=proposal,
    )
    if key.startswith(WIKI_MAINTENANCE_FACT_DUPLICATE_EVIDENCE_PREFIX):
        return await store.record_fact_duplicate_review(review)
    result = await store.apply_run(
        expected_revision=None,
        ordered_commit_ids=(commit,),
        reviewed_through=commit,
        reviews=(review,),
    )
    return result.reviews[0]


@pytest.mark.asyncio
async def test_maintenance_review_routes_sanitize_proposals_and_resolve_with_generation_cas(wiki_client):
    client, service, _rename_store, store = wiki_client
    head_before_decisions = service.repository.head

    async def reject_full_health_audit() -> None:
        raise AssertionError("a decision response must not synchronously run full wiki health")

    client.app.state.runtime.project_wiki_health = reject_full_health_audit
    accepted = await _pending_maintenance_review(
        store,
        commit="a" * 64,
        key="proposal",
        proposal={
            "kind": "maintenance_updates",
            "reason": f"wiki maintenance {'a' * 64} {'1' * 64}",
            "replay_fingerprint": "1" * 64,
            "summary": "Apply this edit.",
            "updates": [
                {
                    "page_id": "target",
                    "expected_version": "2" * 64,
                    "title": "New title",
                    "aliases": ["Alt"],
                    "body": "New body",
                }
            ],
        },
    )
    rejected = await _pending_maintenance_review(
        store,
        commit="b" * 64,
        key="reject",
        proposal={
            "kind": "manual_evidence_review",
            "section": "change 1 diff",
            "actual_bytes": 101,
            "limit_bytes": 100,
            "evidence_fingerprint": "hidden",
        },
    )
    manual = await _pending_maintenance_review(
        store,
        commit="c" * 64,
        key="manual",
        proposal=None,
    )
    empty = await _pending_maintenance_review(
        store,
        commit="d" * 64,
        key="empty",
        proposal={
            "kind": "maintenance_updates",
            "reason": "internal reason",
            "summary": "Nothing to apply.",
            "updates": [],
        },
    )
    malformed = await _pending_maintenance_review(
        store,
        commit="e" * 64,
        key="malformed",
        proposal={
            "kind": "maintenance_updates",
            "summary": "Looks public but cannot execute.",
            "updates": [
                {
                    "page_id": "target",
                    "title": "New title",
                    "aliases": [],
                    "body": "New body",
                }
            ],
        },
    )
    accepted_evidence = await _pending_maintenance_review(
        store,
        commit="f" * 64,
        key="accepted-evidence",
        proposal={
            "kind": "manual_evidence_review",
            "section": "change 1 diff",
            "actual_bytes": 101,
            "limit_bytes": 100,
        },
    )
    pending = client.get("/admin/wiki/maintenance-reviews")
    assert pending.status_code == 200
    reviews = {review["review_id"]: review for review in pending.json()["reviews"]}
    proposal = reviews[accepted.review_id]["proposal"]
    assert proposal == {
        "kind": "maintenance_updates",
        "summary": "Apply this edit.",
        "updates": [{"pageId": "target", "title": "New title", "aliases": ["Alt"], "body": "New body"}],
    }
    assert "expected_version" not in str(proposal)
    assert "reason" not in str(proposal)
    assert "replay_fingerprint" not in str(proposal)
    assert reviews[rejected.review_id]["proposal"] == {
        "kind": "manual_evidence_review",
        "section": "change 1 diff",
        "actualBytes": 101,
        "actualBytesAtLeast": False,
        "limitBytes": 100,
    }
    for non_executable in (manual, empty):
        response = client.post(
            f"/admin/wiki/maintenance-reviews/{non_executable.review_id}/accept",
            json={"generation": non_executable.generation},
        )
        assert response.status_code == 422
    corrupt_accept = client.post(
        f"/admin/wiki/maintenance-reviews/{malformed.review_id}/accept",
        json={"generation": malformed.generation},
    )
    assert corrupt_accept.status_code == 503

    accepted_response = client.post(
        f"/admin/wiki/maintenance-reviews/{accepted.review_id}/accept",
        json={"generation": accepted.generation},
    )
    assert accepted_response.status_code == 200
    assert accepted_response.json()["status"] == "accepted"
    # Decisions only make the scheduled pass eligible to apply the proposal.
    assert service.repository.head == head_before_decisions

    accepted_evidence_response = client.post(
        f"/admin/wiki/maintenance-reviews/{accepted_evidence.review_id}/accept",
        json={"generation": accepted_evidence.generation},
    )
    assert accepted_evidence_response.status_code == 200
    assert accepted_evidence_response.json()["status"] == "accepted"

    rejected_response = client.post(
        f"/admin/wiki/maintenance-reviews/{rejected.review_id}/reject",
        json={"generation": rejected.generation},
    )
    assert rejected_response.status_code == 200
    assert rejected_response.json()["status"] == "rejected"

    missing_note = client.post(
        f"/admin/wiki/maintenance-reviews/{manual.review_id}/resolve-manually",
        json={"generation": manual.generation, "note": ""},
    )
    assert missing_note.status_code == 422
    invalid_generation = client.post(
        f"/admin/wiki/maintenance-reviews/{manual.review_id}/resolve-manually",
        json={"generation": True, "note": "checked externally"},
    )
    assert invalid_generation.status_code == 422
    manual_response = client.post(
        f"/admin/wiki/maintenance-reviews/{manual.review_id}/resolve-manually",
        json={"generation": manual.generation, "note": "checked externally"},
    )
    assert manual_response.status_code == 200
    assert manual_response.json()["decision_note"] == "checked externally"
    assert client.app.state.runtime.health_calls == 0
    assert client.app.state.runtime.maintenance_resume_requests == 4
    assert client.app.state.runtime.fact_maintenance_resume_requests == 0
    assert client.app.state.runtime.maintenance_review_notifications == [
        "a" * 64,
        "f" * 64,
        "b" * 64,
        "c" * 64,
    ]

    conflict = client.post(
        f"/admin/wiki/maintenance-reviews/{accepted.review_id}/accept",
        json={"generation": accepted.generation},
    )
    assert conflict.status_code == 409
    assert client.post("/admin/wiki/maintenance-reviews/missing/accept", json={"generation": 0}).status_code == 404


@pytest.mark.asyncio
async def test_maintenance_decision_surfaces_resume_request_failures(wiki_client):
    client, _service, _rename_store, store = wiki_client
    review = await _pending_maintenance_review(
        store,
        commit="a" * 64,
        key="resume-failure",
        proposal=None,
    )

    async def fail_request() -> None:
        raise RuntimeError("scheduler unavailable")

    client.app.state.runtime.request_wiki_maintenance = fail_request
    with pytest.raises(RuntimeError, match="scheduler unavailable"):
        client.post(
            f"/admin/wiki/maintenance-reviews/{review.review_id}/resolve-manually",
            json={"generation": review.generation, "note": "checked externally"},
        )

    persisted = await store.get_review(review.review_id)
    assert persisted is not None
    assert persisted.status.value == "resolved_manual"


@pytest.mark.asyncio
async def test_duplicate_page_decision_resumes_fact_maintenance(wiki_client):
    client, _service, _rename_store, store = wiki_client
    review = await _pending_maintenance_review(
        store,
        commit="d" * 64,
        key=f"{WIKI_MAINTENANCE_FACT_DUPLICATE_EVIDENCE_PREFIX}first:second",
        proposal=None,
    )

    response = client.post(
        f"/admin/wiki/maintenance-reviews/{review.review_id}/reject",
        json={"generation": review.generation},
    )

    assert response.status_code == 200
    assert client.app.state.runtime.maintenance_resume_requests == 1
    assert client.app.state.runtime.fact_maintenance_resume_requests == 1


@pytest.mark.asyncio
async def test_maintenance_decision_surfaces_review_notification_failures(wiki_client):
    client, _service, _rename_store, store = wiki_client
    review = await _pending_maintenance_review(
        store,
        commit="b" * 64,
        key="notification-failure",
        proposal=None,
    )

    async def fail_notification(_revision: str) -> None:
        raise RuntimeError("notification unavailable")

    client.app.state.runtime.notify_wiki_maintenance_reviews_changed = fail_notification
    with pytest.raises(RuntimeError, match="notification unavailable"):
        client.post(
            f"/admin/wiki/maintenance-reviews/{review.review_id}/resolve-manually",
            json={"generation": review.generation, "note": "checked externally"},
        )

    assert client.app.state.runtime.maintenance_resume_requests == 1
    persisted = await store.get_review(review.review_id)
    assert persisted is not None
    assert persisted.status.value == "resolved_manual"


@pytest.mark.asyncio
async def test_maintenance_review_evidence_returns_only_the_blocking_revision(wiki_client):
    client, service, _rename_store, store = wiki_client
    record = service.read_page("target")
    commit = service.repository.commit(
        ChangeSet(
            operations=(
                Update(
                    "target",
                    record.resource.version_id,
                    record.page.with_body(b"reviewed evidence\n" * 2_000).to_bytes(),
                ),
                Create("private", "private.bin", b"not wiki maintenance evidence"),
            ),
            actor="wiki-maintenance",
            origin="wiki.maintenance",
            reason="repair outgoing links",
            idempotency_key="maintenance-evidence",
            expected_head=service.repository.head,
        )
    )
    review = await _pending_maintenance_review(store, commit=commit.commit_id, key="evidence", proposal=None)

    response = client.get(
        f"/admin/wiki/maintenance-reviews/{review.review_id}/evidence",
        params={"generation": review.generation},
    )

    assert response.status_code == 200
    expected_diff = next(
        diff.unified_diff
        for diff in service.repository.diff(commit.parent_id, commit.commit_id)
        if diff.resource_id == "target"
    )
    body = response.json()
    assert body == {
        "review_id": review.review_id,
        "generation": review.generation,
        "actor": "wiki-maintenance",
        "origin": "wiki.maintenance",
        "reason": "repair outgoing links",
        "occurred_at": commit.timestamp.isoformat().replace("+00:00", "Z"),
        "changeIndex": 0,
        "changeCount": 1,
        "diffOffset": 0,
        "diffEndOffset": 16_384,
        "moreInChange": True,
        "previousCursor": None,
        "nextCursor": {"changeIndex": 0, "diffOffset": 16_384},
        "change": {
            "resourceId": "target",
            "path": "old.md",
            "action": "update",
            "unifiedDiff": expected_diff[:16_384],
            "displayLossy": False,
        },
    }
    assert "fingerprint" not in response.text
    assert "version" not in response.text
    assert "private" not in response.text

    next_response = client.get(
        f"/admin/wiki/maintenance-reviews/{review.review_id}/evidence",
        params={
            "generation": review.generation,
            "change_index": 0,
            "diff_offset": body["nextCursor"]["diffOffset"],
        },
    )
    assert next_response.status_code == 200
    next_body = next_response.json()
    assert next_body["previousCursor"] == {"changeIndex": 0, "diffOffset": 0}
    assert next_body["diffEndOffset"] == 32_768
    assert next_body["change"]["unifiedDiff"] == expected_diff[16_384:32_768]
    assert len(next_body["change"]["unifiedDiff"]) <= 16_384


@pytest.mark.asyncio
async def test_maintenance_review_evidence_cursors_span_changes_and_report_unicode_offsets(wiki_client):
    client, service, _rename_store, store = wiki_client
    other = service.read_page("other")
    target = service.read_page("target")
    commit = service.repository.commit(
        ChangeSet(
            operations=(
                Update(
                    "other", other.resource.version_id, other.page.with_body(b"older evidence\n" * 2_000).to_bytes()
                ),
                Update(
                    "target", target.resource.version_id, target.page.with_body("😀 evidence\n".encode()).to_bytes()
                ),
            ),
            actor="wiki-maintenance",
            origin="wiki.maintenance",
            reason="review two changed pages",
            idempotency_key="maintenance-cursors",
            expected_head=service.repository.head,
        )
    )
    review = await _pending_maintenance_review(store, commit=commit.commit_id, key="cursors", proposal=None)
    diffs = {
        diff.resource_id: diff.unified_diff for diff in service.repository.diff(commit.parent_id, commit.commit_id)
    }

    target_response = client.get(
        f"/admin/wiki/maintenance-reviews/{review.review_id}/evidence",
        params={"generation": review.generation, "change_index": 1},
    )
    assert target_response.status_code == 200
    target_body = target_response.json()
    assert target_body["change"]["resourceId"] == "target"
    assert target_body["diffEndOffset"] == len(diffs["target"])
    other_tail_offset = ((len(diffs["other"]) - 1) // 16_384) * 16_384
    assert target_body["previousCursor"] == {
        "changeIndex": 0,
        "diffOffset": other_tail_offset,
    }

    previous_response = client.get(
        f"/admin/wiki/maintenance-reviews/{review.review_id}/evidence",
        params={
            "generation": review.generation,
            "change_index": target_body["previousCursor"]["changeIndex"],
            "diff_offset": target_body["previousCursor"]["diffOffset"],
        },
    )
    assert previous_response.status_code == 200
    previous_body = previous_response.json()
    assert previous_body["change"]["unifiedDiff"] == diffs["other"][other_tail_offset:]
    assert previous_body["nextCursor"] == {"changeIndex": 1, "diffOffset": 0}


@pytest.mark.asyncio
async def test_maintenance_review_evidence_marks_invalid_utf8_as_lossy_display(wiki_client):
    client, service, _rename_store, store = wiki_client
    target = service.read_page("target")
    commit = service.repository.commit(
        ChangeSet(
            operations=(Update("target", target.resource.version_id, b"\xff"),),
            actor="external",
            origin="filesystem",
            reason="historical invalid UTF-8",
            idempotency_key="invalid-utf8-evidence",
            expected_head=service.repository.head,
        )
    )
    review = await _pending_maintenance_review(store, commit=commit.commit_id, key="invalid-utf8", proposal=None)

    response = client.get(
        f"/admin/wiki/maintenance-reviews/{review.review_id}/evidence",
        params={"generation": review.generation},
    )

    assert response.status_code == 200
    assert response.json()["change"]["displayLossy"] is True
    assert "\ufffd" in response.json()["change"]["unifiedDiff"]


@pytest.mark.asyncio
async def test_maintenance_review_evidence_rejects_large_sources_before_reads_or_history_walks(
    wiki_client,
    monkeypatch: pytest.MonkeyPatch,
):
    client, service, _rename_store, store = wiki_client
    target = service.read_page("target")
    commit = service.repository.commit(
        ChangeSet(
            operations=(
                Update(
                    "target",
                    target.resource.version_id,
                    target.page.with_body(b"x" * (2 * 1024 * 1024)).to_bytes(),
                ),
            ),
            actor="external",
            origin="filesystem",
            reason="large reviewed source",
            idempotency_key="large-evidence-source",
            expected_head=service.repository.head,
        )
    )
    review = await _pending_maintenance_review(store, commit=commit.commit_id, key="large-source", proposal=None)

    def reject_walk(*_args, **_kwargs):
        raise AssertionError("pending evidence must not walk current history or revision trees")

    def reject_read(*_args, **_kwargs):
        raise AssertionError("oversized evidence must be rejected before reading blobs")

    monkeypatch.setattr(service.repository, "history", reject_walk)
    monkeypatch.setattr(service.repository, "_tree_at", reject_walk)
    monkeypatch.setattr(service.repository._storage, "read_blob", reject_read)

    response = client.get(
        f"/admin/wiki/maintenance-reviews/{review.review_id}/evidence",
        params={"generation": review.generation},
    )

    assert response.status_code == 413
    assert "in-app source limit" in response.json()["detail"]


@pytest.mark.asyncio
async def test_maintenance_review_evidence_validates_review_generation_and_commit(
    wiki_client,
    monkeypatch: pytest.MonkeyPatch,
):
    client, service, _rename_store, store = wiki_client
    commit = service.repository.head
    assert commit is not None
    review = await _pending_maintenance_review(store, commit=commit, key="evidence", proposal=None)
    path = f"/admin/wiki/maintenance-reviews/{review.review_id}/evidence"

    assert client.get(path).status_code == 422
    assert client.get(path, params={"generation": -1}).status_code == 422
    assert client.get(path, params={"generation": review.generation + 1}).status_code == 409
    assert client.get(path, params={"generation": review.generation, "change_index": 99}).status_code == 422
    assert client.get(path, params={"generation": review.generation, "diff_offset": 99_999}).status_code == 422
    assert client.get("/admin/wiki/maintenance-reviews/missing/evidence", params={"generation": 0}).status_code == 404

    client.app.state.runtime.wiki_service = None
    assert client.get(path, params={"generation": review.generation}).status_code == 503
    client.app.state.runtime.wiki_service = service

    monkeypatch.setattr(
        service.repository,
        "inspect_commit",
        lambda *_args: (_ for _ in ()).throw(CorruptRepositoryError("bad commit")),
    )
    assert client.get(path, params={"generation": review.generation}).status_code == 503
    monkeypatch.undo()

    unreachable = await _pending_maintenance_review(store, commit="f" * 64, key="unreachable", proposal=None)
    response = client.get(
        f"/admin/wiki/maintenance-reviews/{unreachable.review_id}/evidence",
        params={"generation": unreachable.generation},
    )
    assert response.status_code == 404

    target = service.read_page("target")

    def stop_after_objects(point: str) -> None:
        if point == "after_objects":
            raise RuntimeError(point)

    monkeypatch.setattr(service.repository, "_checkpoint", stop_after_objects)
    with pytest.raises(RuntimeError, match="after_objects"):
        service.repository.commit(
            ChangeSet(
                operations=(Update("target", target.resource.version_id, target.page.with_body(b"orphan").to_bytes()),),
                actor="external",
                origin="filesystem",
                reason="unpublished evidence",
                idempotency_key="orphan-evidence",
                expected_head=service.repository.head,
            )
        )
    published = {path.name for path in service.repository.history_root.joinpath("published").iterdir()}
    orphan = next(
        path.stem
        for path in service.repository.history_root.joinpath("commits").iterdir()
        if path.stem not in published
    )
    orphan_review = await _pending_maintenance_review(store, commit=orphan, key="orphan", proposal=None)
    orphan_response = client.get(
        f"/admin/wiki/maintenance-reviews/{orphan_review.review_id}/evidence",
        params={"generation": orphan_review.generation},
    )
    assert orphan_response.status_code == 404


@pytest.mark.asyncio
async def test_runtime_uses_wiki_paths_and_a_separate_sessions_db_connection(tmp_path: Path):
    runtime = Runtime(Config(arden_dir=tmp_path))
    await runtime._init_wiki()
    try:
        assert runtime.wiki_repository is not None
        assert runtime.wiki_repository.root == tmp_path / "memory" / "wiki" / "pages"
        assert runtime.wiki_repository.history_root == tmp_path / "memory" / "wiki" / ".wiki-history"
        assert runtime.wiki_rename_coordinator is not None
        assert runtime._wiki_approval_conn is runtime.wiki_rename_coordinator.store.conn

        check = await database.connect(tmp_path / "sessions.db", readonly=True)
        try:
            rows = await check.execute_fetchall(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'wiki_rename_approvals'"
            )
            assert rows
        finally:
            await check.close()
    finally:
        await runtime._wiki_approval_conn.close()
