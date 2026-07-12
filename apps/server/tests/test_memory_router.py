"""Memory router (`/admin/memory`) — the contract the desktop memory UI calls,
served from the live flat RecordStore.

Hermetic: real tmp `memory.db` backs the records; FTS-only
(`search_index=None`), so search degrades to raw hybrid search — exercising the
no-LLM bridge paths end-to-end without a network. The KnowledgeRuntime dep is
overridden with a tiny holder exposing `_record_store`."""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ntrp.memory.file_store import FilePageStore
from ntrp.memory.journal import JournalConflictError
from ntrp.memory.page_edit_service import PageEditService
from ntrp.memory.page_events import PageEditAnalysis, page_revision
from ntrp.memory.reconciler import RecordOperation
from ntrp.memory.records import RecordStore
from ntrp.server.app import app
from ntrp.server.deps import require_knowledge_runtime
from ntrp.server.routers.memory import router as memory_router
from ntrp.server.runtime.knowledge import KnowledgeRuntime


class _Knowledge:
    def __init__(self, records, artifacts_dir: Path, page_edits=None):
        self._record_store = records
        self._page_edit_service = page_edits
        self.config = SimpleNamespace(memory_artifacts_dir=artifacts_dir, memory_model=None)

    @property
    def page_edit_service(self):
        return self._page_edit_service

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


class _PageReconciler:
    def __init__(self, answer=(RecordOperation.noop(),)):
        self.answer = answer

    async def reconcile_page_edit(self, _analysis):
        return self.answer


@pytest_asyncio.fixture
async def page_edit_client(tmp_path: Path):
    vault = tmp_path / "artifacts"
    (vault / "topics").mkdir(parents=True)
    (vault / "raw" / "topics").mkdir(parents=True)
    page = vault / "topics" / "a.md"
    page.write_text("# A\n\nOriginal durable statement.\n", encoding="utf-8")
    (vault / "health.md").write_text("# Health\n", encoding="utf-8")
    (vault / "index.md").write_text("# Memory\n", encoding="utf-8")
    (vault / "raw" / "topics" / "a.md").write_text(
        "<!-- ntrp:records schema=2 page=topics/a.md -->\n",
        encoding="utf-8",
    )
    store = FilePageStore(vault)
    await store.open()
    reconciler = _PageReconciler()
    service = PageEditService(vault, store, reconciler=reconciler)
    knowledge = _Knowledge(store, vault, service)
    test_app = FastAPI()
    test_app.include_router(memory_router)
    test_app.dependency_overrides[require_knowledge_runtime] = lambda: knowledge
    with TestClient(test_app) as c:
        yield c, store, service, reconciler, page
    await store.close()


def test_routes_registered():
    paths = TestClient(app).get("/openapi.json").json()["paths"]
    for p in (
        "/admin/memory/scopes",
        "/admin/memory/artifacts",
        "/admin/memory/artifacts/rebuild",
        "/admin/memory/artifacts/{path}",
        "/admin/memory/page-edits/preview",
        "/admin/memory/page-edits/apply",
        "/admin/memory/page-edits/history",
        "/admin/memory/items",
        "/admin/memory/search",
    ):
        assert p in paths


def test_page_edit_preview_is_non_mutating(page_edit_client):
    c, _store, _service, _reconciler, page = page_edit_client
    base = page.read_bytes()
    candidate = base + b"\nCandidate statement.\n"

    response = c.post(
        "/admin/memory/page-edits/preview",
        json={
            "path": "topics/a.md",
            "base_revision": page_revision(base),
            "content": candidate.decode(),
        },
    )

    assert response.status_code == 200
    preview = response.json()["preview"]
    assert preview["result_revision"] == page_revision(candidate)
    assert page.read_bytes() == base
    artifact = c.get("/admin/memory/artifacts/topics/a.md").json()["artifact"]
    assert artifact["editable_content"] == base.decode()
    assert artifact["revision"] == page_revision(base)


def test_page_edit_apply_requires_ask_decisions(page_edit_client):
    c, _store, _service, reconciler, page = page_edit_client
    reconciler.answer = (RecordOperation.ask("Forget the prior memory?"),)
    base = page.read_bytes()
    preview = c.post(
        "/admin/memory/page-edits/preview",
        json={
            "path": "topics/a.md",
            "base_revision": page_revision(base),
            "content": "# A\n",
        },
    ).json()["preview"]

    response = c.put(
        "/admin/memory/page-edits/apply",
        json={"preview_id": preview["id"], "decisions": {}},
    )

    assert response.status_code == 422
    assert page.read_bytes() == base


def test_page_edit_apply_rejects_unknown_decision_ids(page_edit_client):
    c, _store, _service, _reconciler, page = page_edit_client
    base = page.read_bytes()
    preview = c.post(
        "/admin/memory/page-edits/preview",
        json={
            "path": "topics/a.md",
            "base_revision": page_revision(base),
            "content": (base + b"\nCandidate.\n").decode(),
        },
    ).json()["preview"]

    response = c.put(
        "/admin/memory/page-edits/apply",
        json={"preview_id": preview["id"], "decisions": {"unknown": "note_only"}},
    )

    assert response.status_code == 422
    assert page.read_bytes() == base


def test_stale_page_edit_apply_returns_complete_conflict_without_writes(page_edit_client):
    c, _store, service, _reconciler, page = page_edit_client
    base = page.read_bytes()
    candidate = base + b"\nCandidate statement.\n"
    preview = c.post(
        "/admin/memory/page-edits/preview",
        json={
            "path": "topics/a.md",
            "base_revision": page_revision(base),
            "content": candidate.decode(),
        },
    ).json()["preview"]
    current = base + b"\nConcurrent statement.\n"
    page.write_bytes(current)

    response = c.put(
        "/admin/memory/page-edits/apply",
        json={"preview_id": preview["id"], "decisions": {}},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "error": "page_revision_conflict",
        "current_content": current.decode(),
        "current_revision": page_revision(current),
        "base_revision": page_revision(base),
        "candidate_revision": page_revision(candidate),
    }
    assert page.read_bytes() == current
    assert service.history() == ()


def test_page_edit_journal_cas_returns_complete_conflict(page_edit_client, monkeypatch):
    c, store, service, _reconciler, page = page_edit_client
    base = page.read_bytes()
    candidate = base + b"\nCandidate statement.\n"
    preview = c.post(
        "/admin/memory/page-edits/preview",
        json={
            "path": "topics/a.md",
            "base_revision": page_revision(base),
            "content": candidate.decode(),
        },
    ).json()["preview"]

    def conflict(*_args, **_kwargs):
        raise JournalConflictError("concurrent canonical write")

    monkeypatch.setattr(store._journal, "commit", conflict)
    response = c.put(
        "/admin/memory/page-edits/apply",
        json={"preview_id": preview["id"], "decisions": {}},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "error": "page_revision_conflict",
        "current_content": base.decode(),
        "current_revision": page_revision(base),
        "base_revision": page_revision(base),
        "candidate_revision": page_revision(candidate),
    }
    assert service.history() == ()


def test_page_edit_apply_returns_event_and_revision(page_edit_client):
    c, _store, service, _reconciler, page = page_edit_client
    base = page.read_bytes()
    candidate = base + b"\nCandidate statement.\n"
    preview = c.post(
        "/admin/memory/page-edits/preview",
        json={
            "path": "topics/a.md",
            "base_revision": page_revision(base),
            "content": candidate.decode(),
        },
    ).json()["preview"]

    response = c.put(
        "/admin/memory/page-edits/apply",
        json={"preview_id": preview["id"], "decisions": {}},
    )

    assert response.status_code == 200
    assert response.json()["revision"] == page_revision(candidate)
    assert response.json()["event"]["id"] == preview["id"]
    assert service.history()[0].id == preview["id"]


def test_page_edit_pending_save_must_be_explicit(page_edit_client):
    c, _store, service, reconciler, page = page_edit_client
    reconciler.answer = None
    base = page.read_bytes()
    candidate = base + b"\nCandidate statement.\n"
    preview = c.post(
        "/admin/memory/page-edits/preview",
        json={
            "path": "topics/a.md",
            "base_revision": page_revision(base),
            "content": candidate.decode(),
        },
    ).json()["preview"]
    assert preview["analysis_pending"] is True

    unavailable = c.put(
        "/admin/memory/page-edits/apply",
        json={"preview_id": preview["id"], "decisions": {}},
    )
    assert unavailable.status_code == 503
    assert page.read_bytes() == base

    saved = c.put(
        "/admin/memory/page-edits/apply",
        json={"preview_id": preview["id"], "decisions": {}, "save_pending": True},
    )
    assert saved.status_code == 200
    assert saved.json()["event"]["reconciliation"] == "pending"
    assert service.history()[0].reconciliation == "pending"


def test_page_edit_history_is_newest_first_with_stable_pagination(page_edit_client):
    c, _store, service, _reconciler, page = page_edit_client
    times = (
        datetime(2026, 7, 12, 12, 3, tzinfo=UTC),
        datetime(2026, 7, 12, 12, 1, tzinfo=UTC),
        datetime(2026, 7, 12, 12, 2, tzinfo=UTC),
    )
    for suffix, now in zip(("One", "Two", "Three"), times, strict=True):
        service._now = lambda now=now: now
        base = page.read_bytes()
        candidate = base + f"\n{suffix}.\n".encode()
        preview = c.post(
            "/admin/memory/page-edits/preview",
            json={
                "path": "topics/a.md",
                "base_revision": page_revision(base),
                "content": candidate.decode(),
            },
        ).json()["preview"]
        assert c.put(
            "/admin/memory/page-edits/apply",
            json={"preview_id": preview["id"], "decisions": {}},
        ).status_code == 200

    first = c.get(
        "/admin/memory/page-edits/history",
        params={"path": "topics/a.md", "limit": 2},
    ).json()
    service._now = lambda: datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
    base = page.read_bytes()
    preview = c.post(
        "/admin/memory/page-edits/preview",
        json={
            "path": "topics/a.md",
            "base_revision": page_revision(base),
            "content": (base + b"\nFour.\n").decode(),
        },
    ).json()["preview"]
    assert c.put(
        "/admin/memory/page-edits/apply",
        json={"preview_id": preview["id"], "decisions": {}},
    ).status_code == 200
    second = c.get(
        "/admin/memory/page-edits/history",
        params={
            "path": "topics/a.md",
            "limit": 2,
            "before_sequence": first["next_before_sequence"],
        },
    ).json()

    assert first["total"] == 3
    assert second["total"] == 4
    assert [event["sequence"] for event in first["events"]] == [3, 2]
    assert [event["sequence"] for event in second["events"]] == [1]


def test_page_edit_maps_missing_and_machine_only_pages(page_edit_client):
    c, *_ = page_edit_client
    request = {"base_revision": "0" * 64, "content": "candidate"}

    missing = c.post(
        "/admin/memory/page-edits/preview",
        json={**request, "path": "topics/missing.md"},
    )
    machine = c.post(
        "/admin/memory/page-edits/preview",
        json={**request, "path": "raw/topics/a.md"},
    )
    health = c.post(
        "/admin/memory/page-edits/preview",
        json={**request, "path": "health.md"},
    )
    index = c.post(
        "/admin/memory/page-edits/preview",
        json={**request, "path": "index.md"},
    )

    assert missing.status_code == 404
    assert machine.status_code == 403
    assert health.status_code == 403
    assert index.status_code == 403

    missing_preview = c.put(
        "/admin/memory/page-edits/apply",
        json={"preview_id": "missing", "decisions": {}},
    )
    assert missing_preview.status_code == 404


@pytest.mark.asyncio
async def test_runtime_page_edit_adapter_uses_curator_typed_operations():
    analysis = PageEditAnalysis(
        path="topics/a.md",
        before=("Old.",),
        after=("New.",),
        changed_before=("Old.",),
        changed_after=("New.",),
        patch="patch",
    )
    expected = (RecordOperation.add("New."),)

    class _Curator:
        async def reconcile_page_edit(self, received):
            assert received == analysis
            return expected

    runtime = object.__new__(KnowledgeRuntime)
    runtime.memory_curator = _Curator()

    assert await runtime._reconcile_page_edit(analysis) == expected
    runtime.memory_curator = None
    assert await runtime._reconcile_page_edit(analysis) is None


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
