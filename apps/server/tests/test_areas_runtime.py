from pathlib import Path
from types import SimpleNamespace

import pytest

from ntrp.areas.agent import AreaCustodianReport, render_work_context
from ntrp.areas.asks import AskStore
from ntrp.areas.work_models import AreaOutcome, AreaWorkItem, AreaWorkSnapshot
from ntrp.server.runtime.automation import AutomationRuntime


def report() -> AreaCustodianReport:
    return AreaCustodianReport.model_validate({
        "asks": [{
            "key": "choose-lab", "text": "Which lab should I use?", "kind": "question",
            "salience": 5, "why_now": "Booking is open", "what_next": "I will book it",
        }],
        "report": "Prepared the booking",
        "next_check_hours": 24,
        "next_check_reason": "waiting for lab choice",
        "made_progress": True,
        "work_remaining": True,
        "outcome_changes": [],
        "work_changes": [],
        "evidence": [],
    })


@pytest.mark.asyncio
async def test_work_report_commits_before_asks(tmp_path: Path) -> None:
    calls: list[str] = []

    class Work:
        async def apply_report(self, area_id, run_ref, structured):
            calls.append("work")
            raise RuntimeError("work commit failed")

    runtime = AutomationRuntime.__new__(AutomationRuntime)
    runtime.stores = SimpleNamespace(area_work=Work())
    runtime.area_asks = AskStore(tmp_path / "asks.json")

    with pytest.raises(RuntimeError, match="work commit failed"):
        await runtime._commit_area_report(
            "area_health", "topics/health.md", report().model_dump(), "run:r1"
        )

    assert calls == ["work"]
    assert runtime.area_asks.list("area_health") == []


def test_work_context_exposes_current_outcome_action_and_blocker() -> None:
    common = {
        "area_id": "area_health",
        "created_at": "2026-07-10T00:00:00+00:00",
        "updated_at": "2026-07-10T00:00:00+00:00",
        "completed_at": None,
    }
    snapshot = AreaWorkSnapshot(
        outcomes=[AreaOutcome(
            outcome_id="outcome:area_health:labs-normal", stable_key="labs-normal",
            title="Lab values normalized", success_criteria="All values in range",
            status="active", priority=5, source="user", **common,
        )],
        work_items=[
            AreaWorkItem(
                item_id="work:area_health:book-labs", stable_key="book-labs",
                outcome_id="outcome:area_health:labs-normal", kind="action",
                text="Book the follow-up panel", status="in_progress", owner="custodian",
                due_at=None, next_attempt_at=None, **common,
            ),
            AreaWorkItem(
                item_id="work:area_health:choose-lab", stable_key="choose-lab",
                outcome_id="outcome:area_health:labs-normal", kind="blocker",
                text="Need the user's preferred lab", status="active", owner="user",
                due_at=None, next_attempt_at=None, **common,
            ),
        ],
    )

    text = render_work_context(snapshot)

    assert "Lab values normalized" in text
    assert "Book the follow-up panel" in text
    assert "Need the user's preferred lab" in text
