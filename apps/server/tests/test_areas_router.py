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

from arden.areas.asks import AskStore
from arden.areas.lifecycle import AreaLifecycleService, AreaPageService
from arden.areas.models import Ask, areas_from_records
from arden.areas.service import AreaService
from arden.areas.work_models import AreaWorkSnapshot
from arden.areas.work_store import AreaWorkConflict
from arden.revisions import ManagedFileRepository
from arden.server.app import app
from arden.server.routers.areas import asks_router
from arden.server.routers.areas import router as areas_router
from arden.tools.core.scope import ToolFacts
from arden.tools.core.types import ToolAction
from arden.tools.scopes import resolve
from arden.wiki.pages import create_page
from arden.wiki.service import WikiService

PAGE = create_page(
    page_id="o-1a-page",
    title="O-1A",
    body=b"# O-1A\n\n## Open loops\n- Find counsel.\n",
    metadata={"updated": "2026-07-05"},
)


class _FakeAreaStore:
    """The areas side of SessionService, in memory."""

    def __init__(self):
        self._rows: dict[str, dict] = {}
        self._archived: dict[str, dict] = {}
        self._n = 0

    def _seed(self, *, name, page_path=None, autonomy=None, **_kw) -> dict:
        self._n += 1
        row = {
            "area_id": f"p{self._n}",
            "name": name,
            "page_path": page_path,
            "page_id": None,
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
        row = self._rows.pop(area_id, None)
        if row is None:
            return False
        self._archived[area_id] = row
        return True

    async def restore_area(self, area_id):
        row = self._archived.pop(area_id, None)
        if row is None:
            return None
        self._rows[area_id] = row
        return dict(row)


class _FakeWorkStore:
    def __init__(self):
        self.outcomes: dict[tuple[str, str], dict] = {}
        self.items: dict[tuple[str, str], dict] = {}

    async def create_outcome(self, area_id: str, **values):
        row = {
            "outcome_id": f"outcome:{area_id}:{values['key']}",
            "area_id": area_id,
            "stable_key": values.pop("key"),
            "status": "active",
            "created_at": "2026-07-10T00:00:00Z",
            "updated_at": "v1",
            "completed_at": None,
            **values,
        }
        self.outcomes[(area_id, row["stable_key"])] = row
        return SimpleNamespace(model_dump=lambda mode=None: row, **row)

    async def update_outcome(self, area_id: str, key: str, *, expected_updated_at=None, **patch):
        row = self.outcomes.get((area_id, key))
        if row is None:
            return None
        if expected_updated_at is not None and expected_updated_at != row["updated_at"]:
            raise AreaWorkConflict("stale")
        row.update(patch)
        row["updated_at"] = "v2"
        return SimpleNamespace(model_dump=lambda mode=None: row, **row)

    async def update_work_item(self, area_id: str, key: str, *, expected_updated_at=None, **patch):
        row = self.items.get((area_id, key))
        if row is None:
            return None
        if expected_updated_at is not None and expected_updated_at != row["updated_at"]:
            raise AreaWorkConflict("stale")
        row.update(patch)
        row["updated_at"] = "v2"
        return SimpleNamespace(model_dump=lambda mode=None: row, **row)


@pytest.fixture
def client(tmp_path: Path):
    areas = _FakeAreaStore()
    o1a = areas._seed(name="O-1A", page_path="topics/o-1a.md", autonomy="observe")
    asks = AskStore(tmp_path / "state.json")
    svc = AreaService(
        areas=lambda: areas_from_records(list(areas._rows.values())),
        asks=asks,
        get_page=lambda path: PAGE,
        pending_approvals=lambda: [
            {"run_id": "r1", "tool_call_id": "t1", "session_id": "s1", "tool_name": "bash", "preview": "gh pr create"}
        ],
        session_area=lambda sid: o1a["area_id"] if sid == "s1" else None,
        area_automations=lambda key: [],
        area_sessions=lambda key: [{"session_id": "s1", "name": "counsel"}],
        get_area=lambda pid: areas._rows.get(pid),
        area_work=lambda key: AreaWorkSnapshot(),
        work_brief=lambda area_ids: {"done": [], "in_progress": []},
    )

    emitted: list[list[str]] = []

    async def _emit_areas_changed(keys: list[str]) -> None:
        emitted.append(keys)

    async def _hydrate_area_snapshot() -> None:
        pass  # no real session/automation stores in this router test

    test_app = FastAPI()
    test_app.include_router(areas_router)
    test_app.include_router(asks_router)
    test_app.state.area_service = svc

    class _Automations:
        async def get(self, task_id: str):
            if task_id != f"area:{o1a['area_id']}":
                return None
            return SimpleNamespace(
                thread_id="custodian-thread",
                tool_scope="area_observe",
            )

    dispatched: list[tuple] = []

    async def _dispatch_session_message(session_id, message, **kwargs):
        dispatched.append((session_id, message, kwargs.get("tool_scope"), kwargs.get("skip_approvals")))

    work = _FakeWorkStore()
    test_app.state.runtime = SimpleNamespace(
        session_service=areas,
        stores=SimpleNamespace(automations=_Automations(), area_work=work),
        dispatch_session_message=_dispatch_session_message,
    )
    test_app.state.dispatched = dispatched

    async def _sync_custodian(area: dict) -> None:
        return None

    async def _disable_custodian(area_id: str) -> None:
        return None

    test_app.state.area_lifecycle = AreaLifecycleService(
        sessions=areas,
        sync_custodian=_sync_custodian,
        disable_custodian=_disable_custodian,
    )
    test_app.state.area_pages = AreaPageService(
        wiki=WikiService(ManagedFileRepository(tmp_path / "wiki")),
        sessions=areas,
        lifecycle=test_app.state.area_lifecycle,
        project_wiki_state=_hydrate_area_snapshot,
    )
    test_app.state.emit_areas_changed = _emit_areas_changed

    wakes: list[tuple[str, str, bool]] = []

    async def _request_area_wake(area_id: str, description: str, *, engaged: bool = False) -> None:
        wakes.append((area_id, description, engaged))

    test_app.state.request_area_wake = _request_area_wake
    test_app.state.wakes = wakes
    test_app.state.hydrate_area_snapshot = _hydrate_area_snapshot
    with TestClient(test_app) as c:
        yield c, svc, emitted, o1a["area_id"], areas


def test_routes_registered():
    paths = TestClient(app).get("/openapi.json").json()["paths"]
    for p in (
        "/areas",
        "/areas/overview",
        "/areas/{area_id}",
        "/areas/{area_id}/page",
        "/areas/{area_id}/restore",
        "/areas/{area_id}/autonomy",
        "/asks/{ask_id}/resolve",
        "/asks/{ask_id}/reply",
        "/areas/{area_id}/outcomes",
        "/areas/{area_id}/outcomes/{key}",
        "/areas/{area_id}/work/{key}",
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
    assert body["work"] == {"outcomes": [], "work_items": [], "events": []}


def test_create_and_update_outcome_emit_and_wake(client):
    c, _, emitted, o1a, _ = client

    created = c.post(
        f"/areas/{o1a}/outcomes",
        json={
            "key": "petition-filed",
            "title": "Petition filed",
            "success_criteria": "Receipt exists",
            "priority": 5,
        },
    )
    assert created.status_code == 200
    assert created.json()["source"] == "user"

    updated = c.patch(
        f"/areas/{o1a}/outcomes/petition-filed",
        json={
            "expected_updated_at": "v1",
            "status": "completed",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "completed"
    assert emitted == [[o1a], [o1a]]
    assert len(c.app.state.wakes) == 2


def test_work_mutations_validate_area_key_and_optimistic_version(client):
    c, _, emitted, o1a, _ = client
    work = c.app.state.runtime.stores.area_work
    work.items[(o1a, "collect-evidence")] = {
        "item_id": f"work:{o1a}:collect-evidence",
        "area_id": o1a,
        "stable_key": "collect-evidence",
        "outcome_id": None,
        "kind": "action",
        "text": "Collect evidence",
        "status": "active",
        "owner": "custodian",
        "due_at": None,
        "next_attempt_at": None,
        "created_at": "t0",
        "updated_at": "v1",
        "completed_at": None,
    }

    stale = c.patch(
        f"/areas/{o1a}/work/collect-evidence",
        json={
            "expected_updated_at": "old",
            "status": "in_progress",
        },
    )
    missing_area = c.patch(
        "/areas/nope/work/collect-evidence",
        json={
            "expected_updated_at": "v1",
            "status": "completed",
        },
    )

    assert stale.status_code == 409
    assert missing_area.status_code == 404
    assert emitted == []


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
    res = c.post("/asks/approval:r1:t1/resolve", json={"state": "dismissed"})
    assert res.status_code == 200
    assert res.json()["state"] == "dismissed"
    assert emitted == [[o1a]]

    res = c.get("/areas/nope")
    assert res.status_code == 404
    assert o1a in res.json()["detail"]


def test_resolve_ask_can_reopen_a_depth_one_undo(client):
    c, _, emitted, o1a, _ = client
    c.get("/areas/overview")  # seed the mechanical ask
    dismissed = c.post("/asks/approval:r1:t1/resolve", json={"state": "dismissed"})
    assert dismissed.status_code == 200
    emitted.clear()

    reopened = c.post("/asks/approval:r1:t1/resolve", json={"state": "active"})

    assert reopened.status_code == 200
    assert reopened.json()["state"] == "active"
    assert reopened.json()["resolution"] is None
    assert reopened.json()["resolved_at"] is None
    assert emitted == [[o1a]]
    overview = c.get("/areas/overview").json()
    assert [ask["id"] for ask in overview["brief"]["needs_you"]] == ["approval:r1:t1"]


def test_reply_posts_linked_message_to_custodian_channel(client):
    c, svc, emitted, o1a, _ = client
    svc._asks.upsert(
        Ask(
            id=f"agent:{o1a}:dose",
            area_key=o1a,
            text="Which dose?",
            kind="question",
            source="agent",
            actions=[],
            state="active",
            created_at="2026-07-06T10:00:00",
            stable_key="dose",
        )
    )

    res = c.post(f"/asks/agent:{o1a}:dose/reply", json={"message": "Use 5 mg."})

    assert res.status_code == 200
    assert res.json()["resolution"] == "replied"
    # Reply turns keep read/edit permissions but cannot submit a custodian report.
    session_id, message, tool_scope, skip_approvals = c.app.state.dispatched[0]
    assert (session_id, message) == ("custodian-thread", f"REPLY TO ASK [agent:{o1a}:dose]\nUse 5 mg.")
    assert tool_scope == "area_reply"
    # A reply is a conversation, not an approved action — it mints nothing and
    # waives nothing.
    assert skip_approvals is False
    scope = resolve(tool_scope)
    assert not scope.matches(ToolFacts("submit_area_report", ToolAction.EXECUTE, "_area", False))
    assert scope.matches(ToolFacts("area_page_read", ToolAction.READ, "_area", False))
    assert emitted == [[o1a]]


def test_approving_a_review_runs_its_action_with_minted_internal_reach(client):
    """The custodian is read-only and a child it spawned would inherit that
    allowlist, so approval is what mints the capability — `area_action`,
    granted per run and only for what the user just saw. It carries the
    approval too: re-asking inside an approved action is the same question."""
    c, svc, _, o1a, _ = client
    svc._asks.upsert(
        Ask(
            id=f"agent:{o1a}:assay",
            area_key=o1a,
            text="Implement the model-native assay v0",
            kind="review",
            source="agent",
            actions=[],
            state="active",
            created_at="2026-07-06T10:00:00",
            stable_key="assay",
            action="Scaffold src/aside_oracle/jspace/ and make its replication-gate tests pass.",
        )
    )

    res = c.post(f"/asks/agent:{o1a}:assay/resolve", json={"state": "done", "resolution": "approved"})

    assert res.status_code == 200
    session_id, message, tool_scope, skip_approvals = c.app.state.dispatched[0]
    assert session_id == "custodian-thread"
    assert "Scaffold src/aside_oracle/jspace/" in message
    # Approval mints reach the custodian never had — but only inside Arden.
    # Touching an outside service is a separate consent from "yes, do this".
    assert tool_scope == "area_action"
    scope = resolve(tool_scope)
    assert scope.matches(ToolFacts("write_file", ToolAction.WRITE, "_system", False))
    assert not scope.matches(ToolFacts("send_email", ToolAction.WRITE, "gmail", True))
    # The user read the action and approved it. Re-asking per tool call inside
    # it poses the same question again, which stalled approved edits behind a
    # second prompt; bash and app control still block, they refuse a bypass.
    assert skip_approvals is True


def test_rejecting_a_review_runs_nothing(client):
    c, svc, _, o1a, _ = client
    svc._asks.upsert(
        Ask(
            id=f"agent:{o1a}:assay",
            area_key=o1a,
            text="Implement the model-native assay v0",
            kind="review",
            source="agent",
            actions=[],
            state="active",
            created_at="2026-07-06T10:00:00",
            stable_key="assay",
            action="Scaffold src/aside_oracle/jspace/.",
        )
    )

    c.post(f"/asks/agent:{o1a}:assay/resolve", json={"state": "dismissed", "resolution": "rejected"})

    assert c.app.state.dispatched == []


def _unfiled_ask(ask_id: str = "tool:chat-1:overdue", kind: str = "question") -> Ask:
    return Ask(
        id=ask_id,
        area_key=None,
        text="Invoice #4021 is 12 days overdue",
        kind=kind,
        source="agent_tool",
        actions=[{"verb": "open_session", "ref": "chat-1"}],
        state="active",
        created_at="2026-07-06T10:00:00",
        stable_key="overdue",
        reply_session_id="chat-1",
    )


def test_resolve_unfiled_ask_does_not_wake_a_custodian(client):
    c, svc, emitted, _o1a, _ = client
    svc._asks.upsert(_unfiled_ask())
    emitted.clear()
    c.app.state.wakes.clear()

    res = c.post("/asks/tool:chat-1:overdue/resolve", json={"state": "done"})

    assert res.status_code == 200
    assert res.json()["state"] == "done"
    assert emitted == [[]]
    assert c.app.state.wakes == []


def test_reply_to_unfiled_ask_dispatches_into_the_origin_session(client):
    c, svc, emitted, _o1a, _ = client
    svc._asks.upsert(_unfiled_ask())
    emitted.clear()

    res = c.post("/asks/tool:chat-1:overdue/reply", json={"message": "Chase it."})

    assert res.status_code == 200
    assert res.json()["resolution"] == "replied"
    # A chat's own session keeps its own scope and approval policy: the reply
    # goes back where the ask came from, it does not mint anything.
    assert c.app.state.dispatched == [("chat-1", "REPLY TO ASK [tool:chat-1:overdue]\nChase it.", None, False)]
    assert emitted == [[]]


def test_resolve_unknown_ask_404(client):
    c, _, _, _o1a, _ = client
    res = c.post("/asks/missing/resolve", json={"state": "done"})
    assert res.status_code == 404


def test_resolve_ask_emits_the_asks_own_area_key(client):
    """Ask ids are globally unique, so the route carries no area segment —
    the emitted key comes off the ask itself."""
    c, svc, emitted, _o1a, _ = client
    res = c.post("/areas", json={"name": "Dex", "page_path": "topics/dex.md"})
    dex = res.json()["area_id"]
    emitted.clear()  # the attach above legitimately emitted
    svc._asks.upsert(
        Ask(
            id="agent:dex:1",
            area_key=dex,
            text="Dex thing",
            kind="review",
            source="agent",
            actions=[],
            state="active",
            created_at="2026-07-06T10:00:00",
        )
    )

    res = c.post("/asks/agent:dex:1/resolve", json={"state": "dismissed"})
    assert res.status_code == 200
    assert emitted == [[dex]]


def test_resolve_rejects_bad_state(client):
    c, _, emitted, _o1a, _ = client
    res = c.post("/asks/approval:r1:t1/resolve", json={"state": "yolo"})
    assert res.status_code == 422
    assert emitted == []


def test_resolve_snoozed_carries_snoozed_until(client):
    c, _, _, _o1a, _ = client
    c.get("/areas/overview")  # seed the mechanical ask
    res = c.post(
        "/asks/approval:r1:t1/resolve",
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


def test_put_area_can_disable_delegation(client):
    c, svc, _, o1a, _ = client

    res = c.put(f"/areas/{o1a}/autonomy", json={"autonomy": None})

    assert res.status_code == 200
    assert res.json()["autonomy"] is None
    assert svc.detail(o1a)["autonomy"] is None


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
    assert body["autonomy"] is None
    keys = {s["key"] for s in svc.overview()["areas"]}
    assert keys == {o1a, body["area_id"]}


def test_archive_and_restore_area(client):
    c, _, _, o1a, areas = client

    assert c.delete(f"/areas/{o1a}").status_code == 200
    assert o1a not in areas._rows

    restored = c.post(f"/areas/{o1a}/restore")
    assert restored.status_code == 200
    assert restored.json()["area_id"] == o1a


def test_create_and_detach_area_page(client):
    c, _, _, _, areas = client
    plain = areas._seed(name="Design")

    created = c.post(f"/areas/{plain['area_id']}/page")
    assert created.status_code == 200
    assert created.json()["page_path"] == "topics/design.md"

    detached = c.delete(f"/areas/{plain['area_id']}/page")
    assert detached.status_code == 200
    assert detached.json()["page_path"] is None


def test_create_area_page_returns_503_when_wiki_is_disabled(client):
    c, _, _, _, areas = client
    plain = areas._seed(name="Design")
    c.app.state.area_pages = AreaPageService(
        wiki=None,
        sessions=areas,
        lifecycle=c.app.state.area_lifecycle,
        project_wiki_state=c.app.state.hydrate_area_snapshot,
    )

    response = c.post(f"/areas/{plain['area_id']}/page")

    assert response.status_code == 503
    assert response.json()["detail"] == "Managed wiki is unavailable"


def test_detach_area_page_rejects_delegated_area(client):
    c, _, _, o1a, _ = client

    detached = c.delete(f"/areas/{o1a}/page")

    assert detached.status_code == 409
    assert "Disable the Custodian" in detached.json()["detail"]


def test_patch_area_attaches_page_to_existing_container(client):
    c, svc, _, _, areas = client
    plain = areas._seed(name="Design")
    res = c.patch(f"/areas/{plain['area_id']}", json={"page_path": "topics/design.md", "autonomy": "observe"})
    assert res.status_code == 200
    assert res.json()["page_path"] == "topics/design.md"
    assert plain["area_id"] in {s["key"] for s in svc.overview()["areas"]}


def test_patch_area_runtime_failure_is_not_reported_as_success(client):
    c, _, emitted, o1a, areas = client

    async def fail_sync(area: dict) -> None:
        if area["name"] == "Broken":
            raise RuntimeError("runtime unavailable")

    async def disable(_area_id: str) -> None:
        return None

    async def project_wiki_state() -> bool:
        return False

    c.app.state.area_lifecycle = AreaLifecycleService(
        sessions=areas,
        sync_custodian=fail_sync,
        disable_custodian=disable,
    )
    c.app.state.area_pages = AreaPageService(
        wiki=None,
        sessions=areas,
        lifecycle=c.app.state.area_lifecycle,
        project_wiki_state=project_wiki_state,
    )
    failed_client = TestClient(c.app, raise_server_exceptions=False)
    try:
        response = failed_client.patch(f"/areas/{o1a}", json={"name": "Broken"})
    finally:
        failed_client.close()

    assert response.status_code == 500
    assert areas._rows[o1a]["name"] == "O-1A"
    assert emitted == []


def test_patch_bound_area_reports_unavailable_wiki_as_503(client):
    c, _, emitted, o1a, areas = client
    areas._rows[o1a]["page_id"] = "page-o1a"

    async def sync(_area: dict) -> None:
        return None

    async def disable(_area_id: str) -> None:
        return None

    async def project_wiki_state() -> bool:
        return False

    c.app.state.area_lifecycle = AreaLifecycleService(
        sessions=areas,
        sync_custodian=sync,
        disable_custodian=disable,
    )
    c.app.state.area_pages = AreaPageService(
        wiki=None,
        sessions=areas,
        lifecycle=c.app.state.area_lifecycle,
        project_wiki_state=project_wiki_state,
    )

    response = c.patch(f"/areas/{o1a}", json={"name": "O-1B"})

    assert response.status_code == 503
    assert areas._rows[o1a]["name"] == "O-1A"
    assert emitted == []


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
