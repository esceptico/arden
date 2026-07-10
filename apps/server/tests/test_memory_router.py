"""Memory router (`/admin/memory`) — the contract the desktop memory UI calls,
served from the live flat RecordStore.

Hermetic: real tmp `memory.db` backs the records; FTS-only
(`search_index=None`), so search degrades to raw hybrid search — exercising the
no-LLM bridge paths end-to-end without a network. The KnowledgeRuntime dep is
overridden with a tiny holder exposing `_record_store`."""

from pathlib import Path
from types import SimpleNamespace

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ntrp.memory.records import RecordStore
from ntrp.server.app import app
from ntrp.server.deps import require_knowledge_runtime
from ntrp.server.routers.memory import router as memory_router


class _Knowledge:
    def __init__(self, records: RecordStore, artifacts_dir: Path):
        self._record_store = records
        self.config = SimpleNamespace(memory_artifacts_dir=artifacts_dir, memory_model=None)

    def _memory_llm(self):
        # Hermetic: no LLM → mechanical projection, same as the production helper
        # when memory_model is unset.
        return None, ""


@pytest_asyncio.fixture
async def client(tmp_path: Path):
    records = RecordStore(tmp_path / "memory.db", search_index=None)
    allergy = await records.add("Regina is allergic to penicillin", kind="fact")
    tea = await records.add("Regina prefers tea over coffee", kind="directive")
    fastapi = await records.add("ntrp uses FastAPI on the backend", kind="fact")
    await records.set_labels(allergy.id, ["health"], entity_labels=["Regina"])
    await records.set_labels(tea.id, [], entity_labels=["Regina"])
    await records.set_labels(fastapi.id, ["ntrp"])

    test_app = FastAPI()
    test_app.include_router(memory_router)
    test_app.dependency_overrides[require_knowledge_runtime] = lambda: _Knowledge(
        records, tmp_path / "artifacts"
    )
    with TestClient(test_app) as c:
        yield c, records
    await records.close()


def test_routes_registered():
    paths = TestClient(app).get("/openapi.json").json()["paths"]
    for p in (
        "/admin/memory/scopes",
        "/admin/memory/artifacts",
        "/admin/memory/artifacts/rebuild",
        "/admin/memory/artifacts/{path}",
        "/admin/memory/items",
        "/admin/memory/search",
    ):
        assert p in paths


def test_scopes_empty(client):
    c, *_ = client
    assert c.get("/admin/memory/scopes").json() == {"scopes": []}


def test_rebuild_artifacts_endpoint_is_noop(client):
    # File-canonical: /artifacts/rebuild no longer re-derives a projection (that
    # would clobber the canonical pages); it returns the current pages + a detail.
    c, *_ = client
    body = c.post("/admin/memory/artifacts/rebuild").json()
    assert "file-canonical" in body["detail"]
    assert isinstance(body["artifacts"], list)


def test_list_items_shape(client):
    c, *_ = client
    body = c.get("/admin/memory/items").json()
    assert body["limit"] == 100
    assert len(body["items"]) == 3
    item = body["items"][0]
    for key in (
        "id",
        "content",
        "kind",
        "canonical_subject",
        "labels",
        "scope",
        "provenance",
        "status",
        "valid_from",
        "invalid_at",
        "source_refs",
        "corroboration",
        "last_relevant_at",
        "feedback",
        "created_at",
        "updated_at",
    ):
        assert key in item
    assert item["canonical_subject"] == item["kind"]
    assert item["scope"] == {"kind": "global", "key": None}
    assert item["provenance"] in ("user_authored", "recorded", "inferred", "external")
    assert item["status"] == "active"
    # Labels are batch-hydrated onto every item.
    by_content = {i["content"]: i for i in body["items"]}
    assert by_content["Regina is allergic to penicillin"]["labels"] == ["Regina", "health"]
    assert by_content["ntrp uses FastAPI on the backend"]["labels"] == ["ntrp"]


def test_list_items_filters_by_kind(client):
    c, *_ = client

    body = c.get("/admin/memory/items", params={"kind": "directive"}).json()

    assert body["limit"] == 100
    assert len(body["items"]) == 1
    assert body["items"][0]["content"] == "Regina prefers tea over coffee"
    assert body["items"][0]["kind"] == "directive"
    assert body["items"][0]["canonical_subject"] == "directive"


def test_get_item_no_edges(client):
    c, _ = client
    rid = c.get("/admin/memory/items").json()["items"][0]["id"]
    body = c.get(f"/admin/memory/items/{rid}").json()
    assert body["item"]["id"] == rid
    assert body["parents"] == [] and body["children"] == []
    assert c.get("/admin/memory/items/missing").status_code == 404


def test_search_fts(client):
    c, *_ = client
    body = c.get("/admin/memory/search", params={"q": "Regina"}).json()
    assert body["mode"] == "fts"
    assert body["degraded"] is True  # search_index=None
    assert all("content" in i for i in body["items"])


def test_search_reports_hybrid_when_semantic_index_is_available(client):
    class _EmptyEmbedder:
        async def embed_one(self, _query: str):
            return [0.0]

    class _EmptyVectorStore:
        async def vector_search(self, *_args, **_kwargs):
            return []

    c, records = client
    records.attach_search_index(SimpleNamespace(embedder=_EmptyEmbedder(), store=_EmptyVectorStore()))

    body = c.get("/admin/memory/search", params={"q": "Regina"}).json()

    assert body["mode"] == "hybrid"
    assert body["degraded"] is False
    assert all("content" in i for i in body["items"])


def test_search_filters_by_kind(client):
    c, *_ = client

    body = c.get("/admin/memory/search", params={"q": "Regina", "kind": "directive"}).json()

    assert body["mode"] == "fts"
    assert len(body["items"]) == 1
    assert body["items"][0]["content"] == "Regina prefers tea over coffee"
    assert body["items"][0]["kind"] == "directive"


def test_graph_routes_absent_from_openapi():
    paths = TestClient(app).get("/openapi.json").json()["paths"]
    assert "/admin/memory/graph" not in paths
    assert "/admin/memory/items/{item_id}/graph" not in paths


def test_item_detail_returns_empty_edges(client):
    c, *_ = client
    item = c.get("/admin/memory/items").json()["items"][0]
    detail = c.get(f"/admin/memory/items/{item['id']}").json()
    assert detail["parents"] == []
    assert detail["children"] == []


def test_create_and_pin_record(client):
    c, records = client
    created = c.post("/admin/memory/record", json={"text": "pinned fact", "kind_tag": "source"})
    assert created.status_code == 200
    rid = created.json()["record"]["id"]
    assert c.post(f"/admin/memory/record/{rid}/pin", json={"pinned": True}).json() == {
        "ok": True,
        "pinned": True,
    }
    # pinned record reads back as user_authored / confirmed
    item = c.get(f"/admin/memory/items/{rid}").json()["item"]
    assert item["provenance"] == "user_authored" and item["feedback"] == "confirmed"
    changelog = c.get("/admin/memory/artifacts/changelog/index.md").json()["artifact"]["content"]
    assert rid not in changelog
    assert "scope=" not in changelog
    assert "added source memory" not in changelog
    assert "pinned memory record" not in changelog
    assert "events across" in changelog  # count-only rollup
    monthly = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (records._db_path.parent / "artifacts" / "changelog").glob("*/*.md")
    )
    assert "Remembered: pinned fact" in monthly  # create event carries the record text
    assert c.post("/admin/memory/record/missing/pin", json={"pinned": True}).status_code == 404
