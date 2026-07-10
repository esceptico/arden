from pathlib import Path
from types import SimpleNamespace

import pytest

from ntrp.areas.agent import AreaCustodianReport
from ntrp.areas.asks import AskStore
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
