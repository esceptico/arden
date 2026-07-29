"""Canonical managed-wiki and fact HTTP contracts."""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from arden.database import connect
from arden.memory.facts.ledger import FactLedger
from arden.memory.facts.plan_store import FactPlanStore
from arden.memory.facts.service import FactService
from arden.revisions import ManagedFileRepository
from arden.server.app import app as server_app
from arden.server.routers.canonical_memory import facts_router, wiki_router
from arden.wiki.models import WikiMaintenancePageUpdate
from arden.wiki.service import WikiService


def _fact_create(fact_id: str, text: str) -> dict[str, object]:
    return {
        "op": "create",
        "fact_id": fact_id,
        "text": text,
        "kind": "fact",
        "labels": ["memory"],
        "subjects": ["alpha"],
        "scope": {"kind": "area", "key": "project"},
        "sources": [
            {
                "kind": "chat_message",
                "ref": f"session:{fact_id}",
                "extra": {"nested": {"value": fact_id}, "items": [{"value": text}]},
            }
        ],
    }


def _client(tmp_path: Path) -> TestClient:
    wiki = WikiService(
        ManagedFileRepository(
            tmp_path / "wiki" / "pages",
            history_root=tmp_path / "wiki" / ".wiki-history",
        )
    )
    ledger = FactLedger(tmp_path / "facts", clock=lambda: datetime(2026, 7, 28, 12, tzinfo=UTC))
    plan = ledger.plan(
        [_fact_create("a", "Coffee is good"), _fact_create("b", "Tea is good")],
        actor="test",
        origin="test",
        reason="seed facts",
    )
    ledger.commit(plan)
    queued: list[str] = []
    projected: list[str | None] = []

    async def enqueue_wiki_user_edit(commit_id: str) -> None:
        queued.append(commit_id)

    def require_wiki_user_edit_queue() -> None:
        return None

    async def project_wiki_state() -> None:
        projected.append(wiki.repository.head)

    app = FastAPI()
    runtime = SimpleNamespace(
        connected=True,
        wiki_service=wiki,
        fact_service=None,
        require_wiki_user_edit_queue=require_wiki_user_edit_queue,
        enqueue_wiki_user_edit=enqueue_wiki_user_edit,
        queued_wiki_edits=queued,
        project_wiki_state=project_wiki_state,
        projected_wiki_heads=projected,
        facts_connection=None,
    )
    app.state.runtime = runtime

    async def open_facts() -> None:
        runtime.facts_connection = await connect(tmp_path / "facts.db")
        plans = FactPlanStore(runtime.facts_connection)
        await plans.init_schema()
        runtime.fact_service = FactService(ledger, plans)

    async def close_facts() -> None:
        await runtime.facts_connection.close()

    app.add_event_handler("startup", open_facts)
    app.add_event_handler("shutdown", close_facts)
    app.include_router(wiki_router)
    app.include_router(facts_router)
    return TestClient(app)


def test_canonical_routes_are_registered_on_server_app() -> None:
    paths = {route.path for route in server_app.routes}
    assert {
        "/admin/wiki/pages",
        "/admin/wiki/pages/{page_id}",
        "/admin/wiki/pages/{page_id}/archive",
        "/admin/wiki/pages/{page_id}/restore",
        "/admin/wiki/pages/{page_id}/history",
        "/admin/wiki/pages/{page_id}/history/{commit_id}/restore",
        "/admin/wiki/pages/{page_id}/diff",
        "/admin/wiki/pages/{page_id}/links",
        "/admin/facts",
        "/admin/facts/search",
        "/admin/facts/{fact_id}",
    } <= paths


def test_wiki_crud_history_diff_links_and_recursive_metadata(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = client.post(
            "/admin/wiki/pages",
            json={
                "path": "one.md",
                "title": "One",
                "page_id": "one",
                "metadata": {"nested": {"values": ["one", "two"]}},
                "expected_head": None,
            },
        )
        assert created.status_code == 201
        one = created.json()
        assert one["content"].startswith("---\npage_id: one\n")
        assert one["metadata"] == {"nested": {"values": ["one", "two"]}}
        assert one["created_at"] is not None
        assert one["updated_at"] == one["created_at"]

        linked = client.post(
            "/admin/wiki/pages",
            json={
                "path": "linked.md",
                "title": "Linked",
                "page_id": "linked",
                "body": "See [[One]].\n",
                "expected_head": one["repository_head"],
            },
        )
        assert linked.status_code == 201
        before_update = linked.json()["repository_head"]

        listing = client.get("/admin/wiki/pages").json()
        assert listing["repository_head"] == before_update
        assert [page["page_id"] for page in listing["pages"]] == ["linked", "one"]
        assert "content" not in listing["pages"][0]
        assert all(page["created_at"] is not None and page["updated_at"] is not None for page in listing["pages"])

        candidate = one["content"].replace("title: One", "title: Updated") + "Exact body bytes.\n"
        updated = client.put(
            "/admin/wiki/pages/one",
            json={
                "content": candidate,
                "expected_version": one["version"],
                "expected_head": before_update,
            },
        )
        assert updated.status_code == 200
        current = updated.json()
        assert current["content"] == candidate
        assert current["title"] == "Updated"
        assert current["created_at"] == one["created_at"]
        assert current["updated_at"] >= one["updated_at"]
        assert client.app.state.runtime.queued_wiki_edits == [current["repository_head"]]
        no_op = client.put(
            "/admin/wiki/pages/one",
            json={
                "content": candidate,
                "expected_version": current["version"],
                "expected_head": current["repository_head"],
            },
        )
        assert no_op.status_code == 200
        assert client.app.state.runtime.queued_wiki_edits == [current["repository_head"]]

        stale = client.put(
            "/admin/wiki/pages/one",
            json={
                "content": candidate + "stale",
                "expected_version": one["version"],
                "expected_head": before_update,
            },
        )
        assert stale.status_code == 409
        assert stale.json()["detail"] == {
            **stale.json()["detail"],
            "error": "page_revision_conflict",
            "current_content": candidate,
            "current_revision": current["version"],
            "current_head": current["repository_head"],
        }

        history = client.get("/admin/wiki/pages/one/history").json()
        assert history["commits"][0]["actor"] == "user:desktop"
        assert history["commits"][0]["origin"] == "desktop"
        diff = client.get(
            "/admin/wiki/pages/one/diff",
            params={"base": before_update, "target": current["repository_head"]},
        ).json()
        assert "-title: One" in diff["diff"]["unified_diff"]
        assert "+title: Updated" in diff["diff"]["unified_diff"]

        links = client.get("/admin/wiki/pages/linked/links").json()
        assert links["outgoing"][0]["target_page_id"] == "one"
        assert links["outgoing"][0]["status"] == "resolved"

        archived = client.post(
            "/admin/wiki/pages/one/archive",
            json={"expected_version": current["version"], "expected_head": current["repository_head"]},
        )
        assert archived.status_code == 200
        archived_page = archived.json()
        assert archived_page["resource_state"] == "archived"
        assert client.get("/admin/wiki/pages/one").status_code == 404

        restored = client.post(
            "/admin/wiki/pages/one/restore",
            json={
                "expected_version": archived_page["version"],
                "expected_head": archived_page["repository_head"],
            },
        )
        assert restored.status_code == 200
        assert restored.json()["content"] == candidate
        assert client.app.state.runtime.projected_wiki_heads == [
            one["repository_head"],
            linked.json()["repository_head"],
            current["repository_head"],
            archived_page["repository_head"],
            restored.json()["repository_head"],
        ]


def test_fact_list_search_detail_and_seek_pagination(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        first = client.get("/admin/facts", params={"limit": 1})
        assert first.status_code == 200
        page = first.json()
        assert [fact["fact_id"] for fact in page["facts"]] == ["a"]
        assert page["total"] == 2
        assert page["has_more"] is True

        second = client.get(
            "/admin/facts",
            params={
                "limit": 1,
                "after_created_at": page["next_after"]["created_at"],
                "after_fact_id": page["next_after"]["fact_id"],
            },
        )
        assert [fact["fact_id"] for fact in second.json()["facts"]] == ["b"]
        assert second.json()["has_more"] is False

        searched = client.get("/admin/facts/search", params={"query": "coffee"})
        assert [fact["fact_id"] for fact in searched.json()["facts"]] == ["a"]

        detail = client.get("/admin/facts/a")
        assert detail.status_code == 200
        fact = detail.json()["fact"]
        assert len(fact["version"]) == 64
        assert fact["scope"] == {"kind": "area", "key": "project"}
        assert fact["sources"][0]["extra"] == {
            "nested": {"value": "a"},
            "items": [{"value": "Coffee is good"}],
        }
        assert client.get("/admin/facts/missing").status_code == 404


def test_pin_fact_is_canonical_idempotent_and_strict(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        first = client.post(
            "/admin/facts",
            json={"request_id": "agent-result:1", "text": "  Completed agent result  "},
        )
        assert first.status_code == 201
        assert first.json()["written"] is True

        duplicate = client.post(
            "/admin/facts",
            json={"request_id": "agent-result:2", "text": "completed   AGENT result"},
        )
        assert duplicate.status_code == 201
        assert duplicate.json() == {
            "written": False,
            "fact_id": first.json()["fact_id"],
        }

        detail = client.get(f"/admin/facts/{first.json()['fact_id']}")
        assert detail.status_code == 200
        fact = detail.json()["fact"]
        assert fact["text"] == "Completed agent result"
        assert fact["kind"] == "source"
        assert fact["labels"] == ["pinned"]
        assert fact["scope"] == {"kind": "user", "key": None}
        assert fact["sources"][0]["ref"] == "agent-result:1"

        conflict = client.post(
            "/admin/facts",
            json={"request_id": "agent-result:1", "text": "A different result"},
        )
        assert conflict.status_code == 409
        assert (
            client.post(
                "/admin/facts",
                json={"request_id": "agent-result:blank", "text": "   "},
            ).status_code
            == 422
        )


def test_canonical_routes_report_unavailable_services() -> None:
    app = FastAPI()
    app.include_router(wiki_router)
    app.include_router(facts_router)
    with TestClient(app) as client:
        assert client.get("/admin/wiki/pages").status_code == 503
        assert client.get("/admin/facts").status_code == 503


def test_wiki_update_rejects_before_commit_when_curator_queue_is_unavailable(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = client.post(
            "/admin/wiki/pages",
            json={"path": "one.md", "title": "One", "page_id": "one", "expected_head": None},
        ).json()

        def unavailable() -> None:
            raise RuntimeError("wiki edit curator queue is unavailable")

        client.app.state.runtime.require_wiki_user_edit_queue = unavailable
        response = client.put(
            "/admin/wiki/pages/one",
            json={
                "content": created["content"] + "Not committed.\n",
                "expected_version": created["version"],
                "expected_head": created["repository_head"],
            },
        )

        assert response.status_code == 503
        current = client.get("/admin/wiki/pages/one").json()
        assert current["content"] == created["content"]
        assert current["version"] == created["version"]


def test_wiki_update_reports_committed_edit_when_curator_enqueue_fails(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = client.post(
            "/admin/wiki/pages",
            json={"path": "one.md", "title": "One", "page_id": "one", "expected_head": None},
        ).json()

        async def fail_enqueue(_commit_id: str) -> None:
            raise RuntimeError("curator queue write failed")

        client.app.state.runtime.enqueue_wiki_user_edit = fail_enqueue
        replacement = created["content"] + "Committed before queue failure.\n"
        response = client.put(
            "/admin/wiki/pages/one",
            json={
                "content": replacement,
                "expected_version": created["version"],
                "expected_head": created["repository_head"],
            },
        )

        assert response.status_code == 200
        assert response.json()["content"] == replacement
        assert response.json()["curation_pending"] is True
        assert client.get("/admin/wiki/pages/one").json()["content"] == replacement
        assert client.app.state.runtime.projected_wiki_heads[-1] == response.json()["repository_head"]


def test_wiki_create_reports_committed_page_when_projection_fails(tmp_path: Path) -> None:
    with _client(tmp_path) as client:

        async def fail_projection() -> None:
            raise RuntimeError("wiki projection failed")

        client.app.state.runtime.project_wiki_state = fail_projection
        response = client.post(
            "/admin/wiki/pages",
            json={"path": "one.md", "title": "One", "page_id": "one", "expected_head": None},
        )

        assert response.status_code == 201
        assert response.json()["projection_pending"] is True
        assert client.get("/admin/wiki/pages/one").status_code == 200


def test_wiki_maintenance_history_restore_commits_and_reports_projection_pending(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = client.post(
            "/admin/wiki/pages",
            json={"path": "one.md", "title": "Original", "page_id": "one", "expected_head": None},
        ).json()
        service = client.app.state.runtime.wiki_service
        record = service.read_page("one")
        maintenance_base = service.repository.head
        assert maintenance_base is not None
        maintenance_commit_id = service.apply_maintenance_updates(
            (
                WikiMaintenancePageUpdate(
                    "one",
                    record.resource.version_id,
                    "Maintained",
                    (),
                    b"Maintained body.\n",
                ),
            ),
            base_head=maintenance_base,
            reason="rewrite page",
        )
        maintained = service.read_page("one")

        async def fail_projection() -> None:
            raise RuntimeError("wiki projection failed")

        client.app.state.runtime.project_wiki_state = fail_projection
        response = client.post(
            f"/admin/wiki/pages/one/history/{maintenance_commit_id}/restore",
            json={
                "expected_version": maintained.resource.version_id,
                "expected_head": maintenance_commit_id,
            },
        )

        assert response.status_code == 200
        restored = response.json()
        assert restored["title"] == "Original"
        assert restored["content"] == created["content"]
        assert restored["projection_pending"] is True
        restore_commit = service.repository.history(start=restored["repository_head"], limit=1)[0]
        assert (restore_commit.actor, restore_commit.origin) == ("user:desktop", "desktop.restore")
        assert restore_commit.reason == f"restore Maintenance commit {maintenance_commit_id}"

        stale = client.post(
            f"/admin/wiki/pages/one/history/{maintenance_commit_id}/restore",
            json={
                "expected_version": maintained.resource.version_id,
                "expected_head": restored["repository_head"],
            },
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["error"] == "page_revision_conflict"
        assert stale.json()["detail"]["current_revision"] == restored["version"]
