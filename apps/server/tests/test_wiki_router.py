from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

import arden.database as database
from arden.config import Config
from arden.revisions import ChangeSet, Create, ManagedFileRepository, Update
from arden.server.app import app as server_app
from arden.server.routers.wiki import router as wiki_router
from arden.server.runtime import Runtime
from arden.wiki import WikiRenameApprovalCoordinator, WikiRenameApprovalStore, WikiService, create_page


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
    paths = {route.path for route in server_app.routes}
    assert "/admin/wiki/rename-approvals" in paths
    assert "/admin/wiki/rename-approvals/{approval_id}/accept" in paths
    assert "/admin/wiki/rename-approvals/{approval_id}/reject" in paths


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

    app = FastAPI()
    runtime = SimpleNamespace(wiki_rename_coordinator=coordinator, health_calls=0)

    async def project_wiki_health() -> None:
        runtime.health_calls += 1

    runtime.project_wiki_health = project_wiki_health
    app.state.runtime = runtime
    app.include_router(wiki_router)
    with TestClient(app) as client:
        yield client, service, store
    await conn.close()


@pytest.mark.asyncio
async def test_rename_request_is_safe_idempotent_and_default_ask(wiki_client):
    client, service, store = wiki_client
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


def test_rename_approval_list_accept_and_reject(wiki_client):
    client, service, _store = wiki_client
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


def test_router_maps_title_only_not_found_and_unavailable_consistently(wiki_client):
    client, _service, _store = wiki_client
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


def test_router_returns_conflict_when_the_server_snapshot_races(wiki_client, monkeypatch: pytest.MonkeyPatch):
    client, service, _store = wiki_client
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
    client, _service, store = wiki_client
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
