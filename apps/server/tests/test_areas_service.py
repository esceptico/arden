from pathlib import Path

from ntrp.areas.asks import AskStore
from ntrp.areas.models import Area, Ask
from ntrp.areas.service import AreaService
from ntrp.areas.work_models import AreaWorkSnapshot
from ntrp.memory.pages import parse_page

PAGE = "---\ntitle: O-1A\nupdated: 2026-07-05\n---\n# O-1A\n\n## Open loops\n- Find counsel.\n"

AREAS = [Area(key="o-1a", title="O-1A", page_path="topics/o-1a.md", autonomy="observe")]


def make_service(
    tmp_path: Path,
    *,
    work: AreaWorkSnapshot | None = None,
    brief: dict | None = None,
) -> AreaService:
    return AreaService(
        areas=lambda: AREAS,
        asks=AskStore(tmp_path / "state.json"),
        get_page=lambda path: parse_page(PAGE),
        pending_approvals=lambda: [
            {"run_id": "r1", "tool_call_id": "t1", "session_id": "s1", "tool_name": "bash", "preview": "gh pr create"}
        ],
        session_area=lambda sid: "o-1a" if sid == "s1" else None,
        area_automations=lambda key: [],
        area_sessions=lambda key: [{"session_id": "s1", "name": "counsel"}],
        get_area=lambda pid: None,
        area_work=lambda key: work or AreaWorkSnapshot(),
        work_brief=lambda area_ids: brief or {"done": [], "in_progress": []},
    )


def test_mechanical_approval_becomes_review_ask(tmp_path: Path):
    svc = make_service(tmp_path)
    svc.refresh_mechanical()
    svc.refresh_mechanical()  # idempotent — no duplicates
    overview = svc.overview()
    assert len(overview["focus"]) == 1
    ask = overview["focus"][0]
    assert ask["kind"] == "review" and ask["area_key"] == "o-1a"
    assert {"verb": "open_session", "ref": "s1"} in ask["actions"]


def test_detail_includes_open_loops_and_sessions(tmp_path: Path):
    svc = make_service(tmp_path)
    d = svc.detail("o-1a")
    assert d["open_loops"] == ["Find counsel."]
    assert d["sessions"][0]["session_id"] == "s1"
    assert d["agent"]["availability"] == "unavailable"


def test_detail_includes_canonical_work_snapshot(tmp_path: Path):
    work = AreaWorkSnapshot.model_validate({
        "outcomes": [{
            "outcome_id": "outcome:o-1a:file", "area_id": "o-1a", "stable_key": "file",
            "title": "Petition filed", "success_criteria": "Receipt exists", "status": "active",
            "priority": 5, "source": "user", "created_at": "2026-07-10T00:00:00Z",
            "updated_at": "2026-07-10T00:00:00Z",
        }],
        "work_items": [], "events": [],
    })

    detail = make_service(tmp_path, work=work).detail("o-1a")

    assert detail["work"]["outcomes"][0]["stable_key"] == "file"


def test_overview_returns_finite_enriched_work_brief(tmp_path: Path):
    store = AskStore(tmp_path / "state.json")
    store.upsert(Ask(
        id="question", area_key="o-1a", text="Which counsel?", kind="question",
        source="agent", actions=[], state="active", created_at="2026-07-10T00:00:00Z",
    ))
    brief = {
        "done": [{"area_id": "o-1a", "type": "work", "stable_key": "draft", "text": "Drafted petition"}],
        "in_progress": [{"area_id": "o-1a", "stable_key": "file", "text": "File petition"}],
    }
    svc = AreaService(
        areas=lambda: AREAS, asks=store, get_page=lambda path: parse_page(PAGE),
        pending_approvals=lambda: [], session_area=lambda sid: None,
        area_automations=lambda key: [], area_sessions=lambda key: [], get_area=lambda pid: None,
        area_work=lambda key: AreaWorkSnapshot(), work_brief=lambda area_ids: brief,
    )

    result = svc.overview()["brief"]

    assert result["done"][0]["area_title"] == "O-1A"
    assert result["in_progress"][0]["area_title"] == "O-1A"
    assert [ask["id"] for ask in result["needs_you"]] == ["question"]


def test_detail_surfaces_latest_custodian_failure(tmp_path: Path):
    svc = AreaService(
        areas=lambda: AREAS,
        asks=AskStore(tmp_path / "state.json"),
        get_page=lambda path: parse_page(PAGE),
        pending_approvals=lambda: [],
        session_area=lambda sid: None,
        area_automations=lambda key: [{
            "task_id": "area:o-1a", "enabled": True,
            "latest_run": {"status": "failed", "error": "provider unavailable"},
        }],
        area_sessions=lambda key: [],
        get_area=lambda pid: None,
    )

    agent = svc.detail("o-1a")["agent"]

    assert agent["availability"] == "error"
    assert agent["last_error"] == "provider unavailable"


def test_mechanical_approval_retires_when_no_longer_pending(tmp_path: Path):
    pending = [
        {"run_id": "r1", "tool_call_id": "t1", "session_id": "s1", "tool_name": "bash", "preview": "gh pr create"}
    ]
    svc = AreaService(
        areas=lambda: AREAS,
        asks=AskStore(tmp_path / "state.json"),
        get_page=lambda path: parse_page(PAGE),
        pending_approvals=lambda: pending,
        session_area=lambda sid: "o-1a",
        area_automations=lambda key: [],
        area_sessions=lambda key: [],
        get_area=lambda pid: None,
    )
    svc.refresh_mechanical()
    assert len(svc.overview()["focus"]) == 1

    pending.clear()
    svc.refresh_mechanical()

    assert svc.overview()["focus"] == []


def test_latest_failed_run_reconciles_and_success_retires_failure_ask(tmp_path: Path):
    automations = [
        {
            "task_id": "area:o-1a:daily",
            "name": "Daily check",
            "latest_run": {"id": 7, "status": "failed", "error": "provider unavailable", "started_at": "2026-07-10"},
        }
    ]
    svc = AreaService(
        areas=lambda: AREAS,
        asks=AskStore(tmp_path / "state.json"),
        get_page=lambda path: parse_page(PAGE),
        pending_approvals=lambda: [],
        session_area=lambda sid: None,
        area_automations=lambda key: automations,
        area_sessions=lambda key: [],
        get_area=lambda pid: None,
    )
    svc.refresh_mechanical()
    ask = svc.overview()["focus"][0]
    assert ask["source"] == "run_failed"
    assert "provider unavailable" in ask["text"]

    automations[0]["latest_run"] = {"id": 8, "status": "completed", "error": None, "started_at": "2026-07-11"}
    svc.refresh_mechanical()

    assert svc.overview()["focus"] == []


def test_overview_excludes_asks_for_non_active_areas(tmp_path: Path):
    store = AskStore(tmp_path / "state.json")
    store.upsert(
        Ask(
            id="archived", area_key="archived-area", text="stale", kind="review",
            source="agent", actions=[], state="active", created_at="2026-07-10",
        )
    )
    svc = AreaService(
        areas=lambda: AREAS,
        asks=store,
        get_page=lambda path: parse_page(PAGE),
        pending_approvals=lambda: [],
        session_area=lambda sid: None,
        area_automations=lambda key: [],
        area_sessions=lambda key: [],
        get_area=lambda pid: None,
    )

    assert svc.overview()["focus"] == []
