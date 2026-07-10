from pathlib import Path

from ntrp.areas.asks import AskStore
from ntrp.areas.models import Area
from ntrp.areas.service import AreaService
from ntrp.memory.pages import parse_page

PAGE = "---\ntitle: O-1A\nupdated: 2026-07-05\n---\n# O-1A\n\n## Open loops\n- Find counsel.\n"

AREAS = [Area(key="o-1a", title="O-1A", page_path="topics/o-1a.md", autonomy="observe")]


def make_service(tmp_path: Path) -> AreaService:
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
    )


def test_mechanical_approval_becomes_decide_ask(tmp_path: Path):
    svc = make_service(tmp_path)
    svc.refresh_mechanical()
    svc.refresh_mechanical()  # idempotent — no duplicates
    overview = svc.overview()
    assert len(overview["focus"]) == 1
    ask = overview["focus"][0]
    assert ask["kind"] == "decide" and ask["area_key"] == "o-1a"
    assert {"verb": "open_session", "ref": "s1"} in ask["actions"]


def test_detail_includes_open_loops_and_sessions(tmp_path: Path):
    svc = make_service(tmp_path)
    d = svc.detail("o-1a")
    assert d["open_loops"] == ["Find counsel."]
    assert d["sessions"][0]["session_id"] == "s1"
