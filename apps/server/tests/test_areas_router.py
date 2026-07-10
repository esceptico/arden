"""`/areas` router — desktop's areas surface: overview + focus, detail
(universal rooms), ask resolution, autonomy updates, and capability attach.

Hermetic: an in-memory fake area store (areas are one concept —
the areas projection derives from it live), a tmp-path AskStore, and a
minimal FastAPI app mounting only the areas router with app.state wired
directly — the areas service is a plain constructor (no FastAPI Depends)."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ntrp.areas.asks import AskStore
from ntrp.areas.models import Ask, areas_from_records
from ntrp.areas.service import AreaService
from ntrp.areas.suggester import AreaSuggestionStore
from ntrp.memory.pages import parse_page
from ntrp.server.app import app
from ntrp.server.routers.areas import router as areas_router

PAGE = "---\ntitle: O-1A\nupdated: 2026-07-05\n---\n# O-1A\n\n## Open loops\n- Find counsel.\n"


class _FakeAreaStore:
    """The areas side of SessionService, in memory."""

    def __init__(self):
        self._rows: dict[str, dict] = {}
        self._n = 0

    def _seed(self, *, name, page_path=None, autonomy=None, **_kw) -> dict:
        self._n += 1
        row = {
            "area_id": f"p{self._n}",
            "name": name,
            "page_path": page_path,
            "autonomy": autonomy,
            "knowledge_scope": f"area:p{self._n}",
            "created_at": "2026-07-10T00:00:00+00:00",
            "updated_at": "2026-07-10T00:00:00+00:00",
        }
        self._rows[row["area_id"]] = row
        return dict(row)

    async def create_area(self, *, name, page_path=None, autonomy=None, **_kw):
        return self._seed(name=name, page_path=page_path, autonomy=autonomy)

    async def get_area(self, area_id):
        row = self._rows.get(area_id)
        return dict(row) if row else None

    async def update_area(self, area_id, **patch):
        row = self._rows.get(area_id)
        if not row:
            return None
        row.update(patch)
        return dict(row)

    async def list_areas(self):
        return [dict(r) for r in self._rows.values()]

    async def find_area_by_name(self, name):
        target = name.strip().casefold()
        for row in self._rows.values():
            if row["name"].strip().casefold() == target:
                return dict(row)
        return None

    async def archive_area(self, area_id):
        return self._rows.pop(area_id, None) is not None



@pytest.fixture
def client(tmp_path: Path):
    areas = _FakeAreaStore()
    o1a = areas._seed(name="O-1A", page_path="topics/o-1a.md", autonomy="observe")
    asks = AskStore(tmp_path / "state.json")
    svc = AreaService(
        areas=lambda: areas_from_records(list(areas._rows.values())),
        asks=asks,
        get_page=lambda path: parse_page(PAGE),
        pending_approvals=lambda: [
            {"run_id": "r1", "tool_call_id": "t1", "session_id": "s1", "tool_name": "bash", "preview": "gh pr create"}
        ],
        session_area=lambda sid: o1a["area_id"] if sid == "s1" else None,
        area_automations=lambda key: [],
        area_sessions=lambda key: [{"session_id": "s1", "name": "counsel"}],
        get_area=lambda pid: areas._rows.get(pid),
    )

    emitted: list[list[str]] = []

    async def _emit_areas_changed(keys: list[str]) -> None:
        emitted.append(keys)

    async def _hydrate_area_snapshot() -> None:
        pass  # no real session/automation stores in this router test

    test_app = FastAPI()
    test_app.include_router(areas_router)
    suggestions = AreaSuggestionStore(tmp_path / "suggestions.json")
    suggestions.replace_suggestions(
        [{"id": "sg1", "key": "health", "title": "Health", "page_path": "topics/health.md", "rationale": "r", "created_at": "2026-07-07"}]
    )
    test_app.state.area_service = svc
    test_app.state.runtime = SimpleNamespace(session_service=areas)
    test_app.state.emit_areas_changed = _emit_areas_changed
    test_app.state.hydrate_area_snapshot = _hydrate_area_snapshot
    test_app.state.area_suggestions = suggestions

    with TestClient(test_app) as c:
        yield c, svc, emitted, o1a["area_id"], areas


def test_routes_registered():
    paths = TestClient(app).get("/openapi.json").json()["paths"]
    for p in (
        "/areas",
        "/areas/overview",
        "/areas/{area_id}",
        "/areas/{area_id}/autonomy",
        "/areas/{area_id}/asks/{ask_id}/resolve",
    ):
        assert p in paths


def test_get_areas_returns_overview_with_focus(client):
    c, _, _, o1a, _ = client
    res = c.get("/areas/overview")
    assert res.status_code == 200
    body = res.json()
    assert "areas" in body and "focus" in body
    assert body["areas"][0]["key"] == o1a
    assert len(body["focus"]) == 1  # refresh_mechanical ran before overview


def test_get_area_detail_happy_path(client):
    c, _, _, o1a, _ = client
    res = c.get(f"/areas/{o1a}")
    assert res.status_code == 200
    body = res.json()
    assert body["key"] == o1a
    assert body["open_loops"] == ["Find counsel."]
    assert body["sessions"][0]["session_id"] == "s1"


def test_get_area_detail_unknown_key_404(client):
    c, _, _, o1a, _ = client
    res = c.get("/areas/nope")
    assert res.status_code == 404
    assert o1a in res.json()["detail"]


def test_plain_container_gets_a_bare_room(client):
    """Universal rooms: an area with no capabilities still opens — sessions
    and automations, no page sections."""
    c, _, _, _, areas = client
    plain = areas._seed(name="Design")
    res = c.get(f"/areas/{plain['area_id']}")
    assert res.status_code == 200
    body = res.json()
    assert body["title"] == "Design"
    assert body["autonomy"] is None
    assert body["page_path"] is None
    assert body["open_loops"] == []


def test_resolve_ask_and_unknown_area_404(client):
    c, _, emitted, o1a, _ = client
    c.get("/areas/overview")  # seed the mechanical ask
    res = c.post(f"/areas/{o1a}/asks/approval:r1:t1/resolve", json={"state": "dismissed"})
    assert res.status_code == 200
    assert res.json()["state"] == "dismissed"
    assert emitted == [[o1a]]

    res = c.get("/areas/nope")
    assert res.status_code == 404
    assert o1a in res.json()["detail"]


def test_resolve_unknown_ask_404(client):
    c, _, _, o1a, _ = client
    res = c.post(f"/areas/{o1a}/asks/missing/resolve", json={"state": "done"})
    assert res.status_code == 404


def test_resolve_ask_404s_when_ask_belongs_to_a_different_area(client):
    """An ask id that resolves fine but belongs to another area than the
    path segment must 404, not silently resolve + emit under the wrong
    area."""
    c, svc, emitted, o1a, _ = client
    res = c.post("/areas", json={"name": "Dex", "page_path": "topics/dex.md"})
    dex = res.json()["area_id"]
    emitted.clear()  # the attach above legitimately emitted; the resolve below must NOT
    svc._asks.upsert(Ask(
        id="agent:dex:1", area_key=dex, text="Dex thing", kind="review", source="agent",
        actions=[], state="active", created_at="2026-07-06T10:00:00",
    ))

    res = c.post(f"/areas/{o1a}/asks/agent:dex:1/resolve", json={"state": "dismissed"})
    assert res.status_code == 404
    assert dex in res.json()["detail"]
    assert emitted == []

    # sanity: resolving it under its real area still works and emits ask.area_key
    res = c.post(f"/areas/{dex}/asks/agent:dex:1/resolve", json={"state": "dismissed"})
    assert res.status_code == 200
    assert emitted == [[dex]]


def test_resolve_rejects_bad_state(client):
    c, _, emitted, o1a, _ = client
    res = c.post(f"/areas/{o1a}/asks/approval:r1:t1/resolve", json={"state": "yolo"})
    assert res.status_code == 422
    assert emitted == []


def test_resolve_snoozed_carries_snoozed_until(client):
    c, _, _, o1a, _ = client
    c.get("/areas/overview")  # seed the mechanical ask
    res = c.post(
        f"/areas/{o1a}/asks/approval:r1:t1/resolve",
        json={"state": "snoozed", "snoozed_until": "2099-01-01T00:00:00+00:00"},
    )
    assert res.status_code == 200
    assert res.json()["snoozed_until"] == "2099-01-01T00:00:00+00:00"


def test_put_area_updates_autonomy(client):
    c, svc, _, o1a, _ = client
    res = c.put(f"/areas/{o1a}/autonomy", json={"autonomy": "act"})
    assert res.status_code == 200
    assert res.json()["autonomy"] == "act"
    assert svc.detail(o1a)["autonomy"] == "act"  # projection reads the same rows


def test_put_unknown_area_404(client):
    c, *_ = client
    res = c.put("/areas/nope/autonomy", json={"autonomy": "act"})
    assert res.status_code == 404


def test_put_pageless_container_409(client):
    """Granting an agent requires a page — the agent's whole job is tending it."""
    c, _, _, _, areas = client
    plain = areas._seed(name="Design")
    res = c.put(f"/areas/{plain['area_id']}/autonomy", json={"autonomy": "act"})
    assert res.status_code == 409


def test_put_rejects_bad_autonomy(client):
    c, _, _, o1a, _ = client
    res = c.put(f"/areas/{o1a}/autonomy", json={"autonomy": "yolo"})
    assert res.status_code == 422


def test_post_areas_creates_container_with_capabilities(client):
    c, svc, _, o1a, _ = client
    res = c.post("/areas", json={"name": "O-1B", "page_path": "topics/o-1b.md"})
    assert res.status_code == 200
    body = res.json()
    assert body["autonomy"] == "observe"
    keys = {s["key"] for s in svc.overview()["areas"]}
    assert keys == {o1a, body["area_id"]}


def test_patch_area_attaches_page_to_existing_container(client):
    c, svc, _, _, areas = client
    plain = areas._seed(name="Design")
    res = c.patch(f"/areas/{plain['area_id']}", json={"page_path": "topics/design.md", "autonomy": "observe"})
    assert res.status_code == 200
    assert res.json()["page_path"] == "topics/design.md"
    assert plain["area_id"] in {s["key"] for s in svc.overview()["areas"]}


def test_post_areas_by_name_reuses_existing_area_case_insensitive(client):
    c, _, _, _, areas = client
    plain = areas._seed(name="mats")
    res = c.post("/areas", json={"name": "MATS", "page_path": "topics/mats.md"})
    assert res.status_code == 200
    body = res.json()
    assert body["area_id"] == plain["area_id"]
    assert body["page_path"] == "topics/mats.md"
    assert sum(1 for r in areas._rows.values() if r["name"].casefold() == "mats") == 1


def test_post_areas_requires_a_name(client):
    c, *_ = client
    assert c.post("/areas", json={"page_path": "topics/x.md"}).status_code == 422


def test_get_areas_flat_list(client):
    c, _, _, o1a, _ = client
    res = c.get("/areas")
    assert res.status_code == 200
    assert [a["area_id"] for a in res.json()["areas"]] == [o1a]


def test_overview_includes_suggestions_and_dismiss_persists(client):
    c, *_ = client
    body = c.get("/areas/overview").json()
    assert [s["key"] for s in body["suggested"]] == ["health"]
    c.post("/areas/suggestions/health/dismiss")
    assert c.get("/areas/overview").json()["suggested"] == []
