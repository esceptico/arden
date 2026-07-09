"""`/slices` router — desktop's slices surface: overview + focus, detail,
ask resolution, autonomy updates, and topic-page promotion.

Hermetic: real tmp-path-backed SliceRegistry/AskStore, a minimal FastAPI app
mounting only the slices router with app.state wired directly — mirrors
test_memory_router's dependency-override idiom but the slices service is a
plain constructor (no FastAPI Depends), so it's set on app.state instead."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ntrp.memory.pages import parse_page
from ntrp.server.app import app
from ntrp.server.routers.slices import router as slices_router
from ntrp.slices.asks import AskStore
from ntrp.slices.models import Ask, Slice
from ntrp.slices.registry import SliceRegistry
from ntrp.slices.service import SliceService

PAGE = "---\ntitle: O-1A\nupdated: 2026-07-05\n---\n# O-1A\n\n## Open loops\n- Find counsel.\n"


@pytest.fixture
def client(tmp_path: Path):
    reg = SliceRegistry(tmp_path / "slices.json")
    reg.save([Slice(key="o-1a", title="O-1A", page_path="topics/o-1a.md", autonomy="observe")])
    asks = AskStore(tmp_path / "state.json")
    svc = SliceService(
        slices=reg.load,
        asks=asks,
        get_page=lambda path: parse_page(PAGE),
        pending_approvals=lambda: [
            {"run_id": "r1", "tool_call_id": "t1", "session_id": "s1", "tool_name": "bash", "preview": "gh pr create"}
        ],
        session_slice=lambda sid: "o-1a" if sid == "s1" else None,
        slice_automations=lambda key: [],
        slice_sessions=lambda key: [{"session_id": "s1", "name": "counsel"}],
        get_project=lambda pid: None,
    )

    emitted: list[list[str]] = []

    async def _emit_slices_changed(keys: list[str]) -> None:
        emitted.append(keys)

    async def _hydrate_slice_snapshot() -> None:
        pass  # no real session/automation stores in this router test

    test_app = FastAPI()
    test_app.include_router(slices_router)
    from ntrp.slices.suggester import SliceSuggestionStore

    suggestions = SliceSuggestionStore(tmp_path / "suggestions.json")
    suggestions.replace_suggestions(
        [{"id": "sg1", "key": "health", "title": "Health", "page_path": "topics/health.md", "rationale": "r", "created_at": "2026-07-07"}]
    )
    test_app.state.slice_service = svc
    test_app.state.slice_registry = reg
    test_app.state.emit_slices_changed = _emit_slices_changed
    test_app.state.hydrate_slice_snapshot = _hydrate_slice_snapshot
    test_app.state.slice_suggestions = suggestions

    with TestClient(test_app) as c:
        yield c, svc, emitted


def test_routes_registered():
    paths = TestClient(app).get("/openapi.json").json()["paths"]
    for p in ("/slices", "/slices/{key}", "/slices/{key}/asks/{ask_id}/resolve"):
        assert p in paths


def test_get_slices_returns_overview_with_focus(client):
    c, *_ = client
    res = c.get("/slices")
    assert res.status_code == 200
    body = res.json()
    assert "slices" in body and "focus" in body
    assert body["slices"][0]["key"] == "o-1a"
    assert len(body["focus"]) == 1  # refresh_mechanical ran before overview


def test_get_slice_detail_happy_path(client):
    c, *_ = client
    res = c.get("/slices/o-1a")
    assert res.status_code == 200
    body = res.json()
    assert body["key"] == "o-1a"
    assert body["open_loops"] == ["Find counsel."]
    assert body["sessions"][0]["session_id"] == "s1"


def test_get_slice_detail_unknown_key_404(client):
    c, *_ = client
    res = c.get("/slices/nope")
    assert res.status_code == 404
    assert "o-1a" in res.json()["detail"]


def test_resolve_ask_and_unknown_slice_404(client):
    c, _, emitted = client
    c.get("/slices")  # seed the mechanical ask
    res = c.post("/slices/o-1a/asks/approval:r1:t1/resolve", json={"state": "dismissed"})
    assert res.status_code == 200
    assert res.json()["state"] == "dismissed"
    assert emitted == [["o-1a"]]

    res = c.get("/slices/nope")
    assert res.status_code == 404
    assert "o-1a" in res.json()["detail"]


def test_resolve_unknown_ask_404(client):
    c, *_ = client
    res = c.post("/slices/o-1a/asks/missing/resolve", json={"state": "done"})
    assert res.status_code == 404


def test_resolve_ask_404s_when_ask_belongs_to_a_different_slice(client):
    """An ask id that resolves fine but belongs to another slice than the
    {key} path segment must 404, not silently resolve + emit under the
    wrong slice."""
    c, svc, emitted = client
    c.post("/slices", json={"key": "dex", "title": "Dex", "page_path": "topics/dex.md"})
    emitted.clear()  # the create above legitimately emitted; the resolve below must NOT
    svc._asks.upsert(Ask(
        id="agent:dex:1", slice_key="dex", text="Dex thing", kind="review", source="agent",
        actions=[], state="active", created_at="2026-07-06T10:00:00",
    ))

    res = c.post("/slices/o-1a/asks/agent:dex:1/resolve", json={"state": "dismissed"})
    assert res.status_code == 404
    assert "dex" in res.json()["detail"]
    assert emitted == []

    # sanity: resolving it under its real slice still works and emits ask.slice_key
    res = c.post("/slices/dex/asks/agent:dex:1/resolve", json={"state": "dismissed"})
    assert res.status_code == 200
    assert emitted == [["dex"]]


def test_resolve_rejects_bad_state(client):
    c, _, emitted = client
    res = c.post("/slices/o-1a/asks/approval:r1:t1/resolve", json={"state": "yolo"})
    assert res.status_code == 422
    assert emitted == []


def test_resolve_snoozed_carries_snoozed_until(client):
    c, *_ = client
    c.get("/slices")  # seed the mechanical ask
    res = c.post(
        "/slices/o-1a/asks/approval:r1:t1/resolve",
        json={"state": "snoozed", "snoozed_until": "2099-01-01T00:00:00+00:00"},
    )
    assert res.status_code == 200
    assert res.json()["snoozed_until"] == "2099-01-01T00:00:00+00:00"


def test_put_slice_updates_autonomy(client):
    c, svc, _ = client
    res = c.put("/slices/o-1a", json={"autonomy": "act"})
    assert res.status_code == 200
    body = res.json()
    assert body["autonomy"] == "act"
    assert svc.detail("o-1a")["autonomy"] == "act"


def test_put_unknown_slice_404(client):
    c, *_ = client
    res = c.put("/slices/nope", json={"autonomy": "act"})
    assert res.status_code == 404


def test_put_rejects_bad_autonomy(client):
    c, *_ = client
    res = c.put("/slices/o-1a", json={"autonomy": "yolo"})
    assert res.status_code == 422


def test_post_slices_creates_slice(client):
    c, svc, _ = client
    res = c.post("/slices", json={"key": "o-1b", "title": "O-1B", "page_path": "topics/o-1b.md"})
    assert res.status_code in (200, 201)
    body = res.json()
    assert body["key"] == "o-1b"
    assert body["autonomy"] == "observe"
    keys = {s["key"] for s in svc.overview()["slices"]}
    assert keys == {"o-1a", "o-1b"}


def test_post_duplicate_key_409(client):
    c, _, emitted = client
    res = c.post("/slices", json={"key": "o-1a", "title": "O-1A", "page_path": "topics/o-1a.md"})
    assert res.status_code == 409
    assert emitted == []


def test_overview_includes_suggestions_and_dismiss_persists(client):
    c, *_ = client
    body = c.get("/slices").json()
    assert [x["key"] for x in body["suggested"]] == ["health"]

    res = c.post("/slices/suggestions/health/dismiss")
    assert res.status_code == 200
    body = c.get("/slices").json()
    assert body["suggested"] == []  # dismissed persists, never re-suggested
