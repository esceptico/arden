"""`/slices` router — desktop's slices surface: overview + focus, detail
(universal rooms), ask resolution, autonomy updates, and capability attach.

Hermetic: an in-memory fake project store (slices/projects are one concept —
the slices projection derives from it live), a tmp-path AskStore, and a
minimal FastAPI app mounting only the slices router with app.state wired
directly — the slices service is a plain constructor (no FastAPI Depends)."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ntrp.memory.pages import parse_page
from ntrp.server.app import app
from ntrp.server.routers.slices import router as slices_router
from ntrp.slices.asks import AskStore
from ntrp.slices.models import Ask, slices_from_projects
from ntrp.slices.service import SliceService
from ntrp.slices.suggester import SliceSuggestionStore

PAGE = "---\ntitle: O-1A\nupdated: 2026-07-05\n---\n# O-1A\n\n## Open loops\n- Find counsel.\n"


class _FakeProjectStore:
    """The projects side of SessionService, in memory."""

    def __init__(self):
        self._rows: dict[str, dict] = {}
        self._n = 0

    def _seed(self, *, name, page_path=None, autonomy=None) -> dict:
        self._n += 1
        row = {"project_id": f"p{self._n}", "name": name, "page_path": page_path, "autonomy": autonomy}
        self._rows[row["project_id"]] = row
        return dict(row)

    async def create_project(self, *, name, page_path=None, autonomy=None, **_kw):
        return self._seed(name=name, page_path=page_path, autonomy=autonomy)

    async def get_project(self, project_id):
        row = self._rows.get(project_id)
        return dict(row) if row else None

    async def update_project(self, project_id, **patch):
        row = self._rows.get(project_id)
        if not row:
            return None
        row.update(patch)
        return dict(row)

    async def list_projects(self):
        return [dict(r) for r in self._rows.values()]


@pytest.fixture
def client(tmp_path: Path):
    projects = _FakeProjectStore()
    o1a = projects._seed(name="O-1A", page_path="topics/o-1a.md", autonomy="observe")
    asks = AskStore(tmp_path / "state.json")
    svc = SliceService(
        slices=lambda: slices_from_projects(list(projects._rows.values())),
        asks=asks,
        get_page=lambda path: parse_page(PAGE),
        pending_approvals=lambda: [
            {"run_id": "r1", "tool_call_id": "t1", "session_id": "s1", "tool_name": "bash", "preview": "gh pr create"}
        ],
        session_slice=lambda sid: o1a["project_id"] if sid == "s1" else None,
        slice_automations=lambda key: [],
        slice_sessions=lambda key: [{"session_id": "s1", "name": "counsel"}],
        get_project=lambda pid: projects._rows.get(pid),
    )

    emitted: list[list[str]] = []

    async def _emit_slices_changed(keys: list[str]) -> None:
        emitted.append(keys)

    async def _hydrate_slice_snapshot() -> None:
        pass  # no real session/automation stores in this router test

    test_app = FastAPI()
    test_app.include_router(slices_router)
    suggestions = SliceSuggestionStore(tmp_path / "suggestions.json")
    suggestions.replace_suggestions(
        [{"id": "sg1", "key": "health", "title": "Health", "page_path": "topics/health.md", "rationale": "r", "created_at": "2026-07-07"}]
    )
    test_app.state.slice_service = svc
    test_app.state.runtime = SimpleNamespace(session_service=projects)
    test_app.state.emit_slices_changed = _emit_slices_changed
    test_app.state.hydrate_slice_snapshot = _hydrate_slice_snapshot
    test_app.state.slice_suggestions = suggestions

    with TestClient(test_app) as c:
        yield c, svc, emitted, o1a["project_id"], projects


def test_routes_registered():
    paths = TestClient(app).get("/openapi.json").json()["paths"]
    for p in ("/slices", "/slices/{project_id}", "/slices/{project_id}/asks/{ask_id}/resolve"):
        assert p in paths


def test_get_slices_returns_overview_with_focus(client):
    c, _, _, o1a, _ = client
    res = c.get("/slices")
    assert res.status_code == 200
    body = res.json()
    assert "slices" in body and "focus" in body
    assert body["slices"][0]["key"] == o1a
    assert len(body["focus"]) == 1  # refresh_mechanical ran before overview


def test_get_slice_detail_happy_path(client):
    c, _, _, o1a, _ = client
    res = c.get(f"/slices/{o1a}")
    assert res.status_code == 200
    body = res.json()
    assert body["key"] == o1a
    assert body["open_loops"] == ["Find counsel."]
    assert body["sessions"][0]["session_id"] == "s1"


def test_get_slice_detail_unknown_key_404(client):
    c, _, _, o1a, _ = client
    res = c.get("/slices/nope")
    assert res.status_code == 404
    assert o1a in res.json()["detail"]


def test_plain_container_gets_a_bare_room(client):
    """Universal rooms: a project with no capabilities still opens — sessions
    and automations, no page sections."""
    c, _, _, _, projects = client
    plain = projects._seed(name="Design")
    res = c.get(f"/slices/{plain['project_id']}")
    assert res.status_code == 200
    body = res.json()
    assert body["title"] == "Design"
    assert body["autonomy"] is None
    assert body["page_path"] is None
    assert body["open_loops"] == []


def test_resolve_ask_and_unknown_slice_404(client):
    c, _, emitted, o1a, _ = client
    c.get("/slices")  # seed the mechanical ask
    res = c.post(f"/slices/{o1a}/asks/approval:r1:t1/resolve", json={"state": "dismissed"})
    assert res.status_code == 200
    assert res.json()["state"] == "dismissed"
    assert emitted == [[o1a]]

    res = c.get("/slices/nope")
    assert res.status_code == 404
    assert o1a in res.json()["detail"]


def test_resolve_unknown_ask_404(client):
    c, _, _, o1a, _ = client
    res = c.post(f"/slices/{o1a}/asks/missing/resolve", json={"state": "done"})
    assert res.status_code == 404


def test_resolve_ask_404s_when_ask_belongs_to_a_different_slice(client):
    """An ask id that resolves fine but belongs to another slice than the
    path segment must 404, not silently resolve + emit under the wrong
    slice."""
    c, svc, emitted, o1a, _ = client
    res = c.post("/slices", json={"name": "Dex", "page_path": "topics/dex.md"})
    dex = res.json()["project_id"]
    emitted.clear()  # the attach above legitimately emitted; the resolve below must NOT
    svc._asks.upsert(Ask(
        id="agent:dex:1", slice_key=dex, text="Dex thing", kind="review", source="agent",
        actions=[], state="active", created_at="2026-07-06T10:00:00",
    ))

    res = c.post(f"/slices/{o1a}/asks/agent:dex:1/resolve", json={"state": "dismissed"})
    assert res.status_code == 404
    assert dex in res.json()["detail"]
    assert emitted == []

    # sanity: resolving it under its real slice still works and emits ask.slice_key
    res = c.post(f"/slices/{dex}/asks/agent:dex:1/resolve", json={"state": "dismissed"})
    assert res.status_code == 200
    assert emitted == [[dex]]


def test_resolve_rejects_bad_state(client):
    c, _, emitted, o1a, _ = client
    res = c.post(f"/slices/{o1a}/asks/approval:r1:t1/resolve", json={"state": "yolo"})
    assert res.status_code == 422
    assert emitted == []


def test_resolve_snoozed_carries_snoozed_until(client):
    c, _, _, o1a, _ = client
    c.get("/slices")  # seed the mechanical ask
    res = c.post(
        f"/slices/{o1a}/asks/approval:r1:t1/resolve",
        json={"state": "snoozed", "snoozed_until": "2099-01-01T00:00:00+00:00"},
    )
    assert res.status_code == 200
    assert res.json()["snoozed_until"] == "2099-01-01T00:00:00+00:00"


def test_put_slice_updates_autonomy(client):
    c, svc, _, o1a, _ = client
    res = c.put(f"/slices/{o1a}", json={"autonomy": "act"})
    assert res.status_code == 200
    assert res.json()["autonomy"] == "act"
    assert svc.detail(o1a)["autonomy"] == "act"  # projection reads the same rows


def test_put_unknown_slice_404(client):
    c, *_ = client
    res = c.put("/slices/nope", json={"autonomy": "act"})
    assert res.status_code == 404


def test_put_pageless_container_409(client):
    """Granting an agent requires a page — the agent's whole job is tending it."""
    c, _, _, _, projects = client
    plain = projects._seed(name="Design")
    res = c.put(f"/slices/{plain['project_id']}", json={"autonomy": "act"})
    assert res.status_code == 409


def test_put_rejects_bad_autonomy(client):
    c, _, _, o1a, _ = client
    res = c.put(f"/slices/{o1a}", json={"autonomy": "yolo"})
    assert res.status_code == 422


def test_post_slices_creates_container_with_capabilities(client):
    c, svc, _, o1a, _ = client
    res = c.post("/slices", json={"name": "O-1B", "page_path": "topics/o-1b.md"})
    assert res.status_code == 200
    body = res.json()
    assert body["autonomy"] == "observe"
    keys = {s["key"] for s in svc.overview()["slices"]}
    assert keys == {o1a, body["project_id"]}


def test_post_slices_attaches_to_existing_container(client):
    c, svc, _, _, projects = client
    plain = projects._seed(name="Design")
    res = c.post("/slices", json={"project_id": plain["project_id"], "page_path": "topics/design.md"})
    assert res.status_code == 200
    assert res.json()["page_path"] == "topics/design.md"
    assert plain["project_id"] in {s["key"] for s in svc.overview()["slices"]}


def test_post_slices_requires_exactly_one_of_project_id_or_name(client):
    c, *_ = client
    assert c.post("/slices", json={"page_path": "topics/x.md"}).status_code == 422
    assert (
        c.post("/slices", json={"project_id": "p1", "name": "X", "page_path": "topics/x.md"}).status_code == 422
    )


def test_overview_includes_suggestions_and_dismiss_persists(client):
    c, *_ = client
    body = c.get("/slices").json()
    assert [s["key"] for s in body["suggested"]] == ["health"]
    c.post("/slices/suggestions/health/dismiss")
    assert c.get("/slices").json()["suggested"] == []
