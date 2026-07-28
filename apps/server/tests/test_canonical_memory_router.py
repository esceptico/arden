"""Canonical managed-wiki and fact HTTP contracts."""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from arden.memory.facts import FactLedger, FactService
from arden.revisions import ManagedFileRepository
from arden.server.app import app as server_app
from arden.server.routers.canonical_memory import facts_router, wiki_router
from arden.wiki import WikiService


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
    facts = FactService(ledger, plans=None)  # type: ignore[arg-type]

    queued: list[str] = []

    async def enqueue_wiki_user_edit(commit_id: str) -> None:
        queued.append(commit_id)

    app = FastAPI()
    app.state.runtime = SimpleNamespace(
        wiki_service=wiki,
        fact_service=facts,
        enqueue_wiki_user_edit=enqueue_wiki_user_edit,
        queued_wiki_edits=queued,
    )
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


def test_canonical_routes_report_unavailable_services() -> None:
    app = FastAPI()
    app.include_router(wiki_router)
    app.include_router(facts_router)
    with TestClient(app) as client:
        assert client.get("/admin/wiki/pages").status_code == 503
        assert client.get("/admin/facts").status_code == 503
