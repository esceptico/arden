import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from arden.agent import Usage
from arden.areas.agent import AreaCustodianReport, render_work_context
from arden.areas.asks import AskStore
from arden.areas.custodian import CustodianStore
from arden.areas.models import Area
from arden.areas.work_models import AreaOutcome, AreaWorkEvent, AreaWorkItem, AreaWorkSnapshot
from arden.events.internal import RunCompleted, RunCompletionRejected, RunFailed
from arden.server.runtime.automation import AutomationRuntime


def report() -> AreaCustodianReport:
    return AreaCustodianReport.model_validate(
        {
            "asks": [
                {
                    "key": "choose-lab",
                    "text": "Which lab should I use?",
                    "kind": "question",
                    "salience": 5,
                    "why_now": "Booking is open",
                    "what_next": "I will book it",
                }
            ],
            "report": "Prepared the booking",
            "next_check_hours": 24,
            "next_check_reason": "waiting for lab choice",
            "work_remaining": True,
            "outcome_changes": [],
            "work_changes": [],
            "evidence": [],
        }
    )


@pytest.mark.asyncio
async def test_runtime_wires_auxiliary_model_into_automation_descriptions(tmp_path: Path) -> None:
    calls: list[dict] = []

    class AuxiliaryClient:
        async def completion(self, **kwargs):
            calls.append(kwargs)
            content = {"description": "Summarizes the configured work."}
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(content)))])

    async def project_wiki_state() -> None:
        return None

    runtime = AutomationRuntime(
        stores=SimpleNamespace(automations=object(), sessions=object(), outbox=object()),
        config=SimpleNamespace(arden_dir=tmp_path),
        build_operator_deps=lambda: None,
        get_calendar_source=lambda: None,
        get_slack_client=lambda: None,
        resolve_auxiliary_completion=lambda: (AuxiliaryClient(), "auxiliary-model", "low"),
        project_wiki_state=project_wiki_state,
    )

    description, source = await runtime.automation_service._resolve_description(
        name="Configured work",
        prompt="Summarize it.",
        description=None,
    )

    assert (description, source) == ("Summarizes the configured work.", "generated")
    assert calls[0]["model"] == "auxiliary-model"
    assert calls[0]["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_manual_area_chat_does_not_settle_custodian_state(tmp_path: Path) -> None:
    class Automations:
        async def list_session_bound_by_session(self, session_id: str):
            return [SimpleNamespace(task_id="area:health")]

    runtime = AutomationRuntime.__new__(AutomationRuntime)
    runtime.stores = SimpleNamespace(automations=Automations())
    runtime.area_asks = AskStore(tmp_path / "asks.json")

    await runtime._on_area_run_completed(RunCompleted("chat-run", "area-channel", (), Usage(), "User chat completed"))


@pytest.mark.asyncio
async def test_custodian_completion_requires_an_accepted_report(tmp_path: Path) -> None:
    class Automations:
        async def list_session_bound_by_session(self, session_id: str):
            return [SimpleNamespace(task_id="area:health")]

    class Work:
        async def report(self, run_ref: str):
            return None

    runtime = AutomationRuntime.__new__(AutomationRuntime)
    runtime.stores = SimpleNamespace(automations=Automations(), area_work=Work())
    runtime.area_asks = AskStore(tmp_path / "asks.json")
    runtime.custodians = CustodianStore(tmp_path / "custodians.json")

    async def load_areas():
        return [Area(key="health", title="Health", page_path="topics/health.md", autonomy="observe")]

    runtime.load_areas = load_areas
    allowed, delivery = runtime.custodians.begin_or_resume_delivery(
        "health",
        iteration=1,
        client_id="area-health-attempt",
        attention="ambient",
        manual=False,
        skip_approvals=True,
        build_message=lambda _: "run",
    )
    assert allowed and delivery is not None
    runtime.custodians.bind_delivery_run(
        "health",
        client_id=delivery.client_id,
        run_id="custodian-run",
    )

    with pytest.raises(RunCompletionRejected, match="without submitting a report"):
        await runtime._on_area_run_completed(
            RunCompleted(
                "custodian-run",
                "area-channel",
                (),
                Usage(),
                "Done",
                automation_task_id="area:health",
            )
        )
    assert runtime.custodians.runs_today("health") == 0


@pytest.mark.asyncio
async def test_failed_custodian_chat_releases_its_run_budget(tmp_path: Path) -> None:
    class Automations:
        async def list_session_bound_by_session(self, session_id: str):
            return [SimpleNamespace(task_id="area:health")]

    class Work:
        async def report(self, run_ref: str):
            return None

    runtime = AutomationRuntime.__new__(AutomationRuntime)
    runtime.stores = SimpleNamespace(automations=Automations(), area_work=Work())
    runtime.area_asks = AskStore(tmp_path / "asks.json")
    runtime.custodians = CustodianStore(tmp_path / "custodians.json")

    async def load_areas():
        return [Area(key="health", title="Health", page_path="topics/health.md", autonomy="observe")]

    runtime.load_areas = load_areas
    runtime.custodians.note_event("health", "health page changed", attention="ambient", paused=False)
    allowed, delivery = runtime.custodians.begin_or_resume_delivery(
        "health",
        iteration=1,
        client_id="area-health-attempt",
        attention="ambient",
        manual=False,
        skip_approvals=True,
        build_message=lambda events: ", ".join(events),
    )
    assert allowed and delivery is not None
    runtime.custodians.bind_delivery_run(
        "health",
        client_id=delivery.client_id,
        run_id="custodian-run",
    )

    accepted = await runtime._on_area_run_failed(
        RunFailed(
            "custodian-run",
            "area-channel",
            "ChatIdempotencyConflict: idempotency_conflict",
            automation_task_id="area:health",
        )
    )

    assert not accepted
    assert runtime.custodians.runs_today("health") == 0
    assert runtime.custodians.state("health")["pending_events"] == ["health page changed"]


@pytest.mark.asyncio
async def test_wiki_change_does_not_wake_its_originating_custodian(tmp_path: Path) -> None:
    wakes: list[tuple[str, str]] = []
    emitted: list[object] = []

    class Sessions:
        async def list_areas(self):
            return [
                {"area_id": "dex", "page_path": "topics/dex.md"},
                {"area_id": "visa", "page_path": "topics/visa.md"},
            ]

    class Scheduler:
        async def emit_automation_event(self, event) -> None:
            emitted.append(event)

    runtime = AutomationRuntime.__new__(AutomationRuntime)
    runtime.stores = SimpleNamespace(sessions=Sessions())
    runtime.scheduler = Scheduler()

    async def request_area_wake(area_id: str, reason: str) -> None:
        wakes.append((area_id, reason))

    runtime.request_area_wake = request_area_wake

    await runtime.notify_wiki_changed(
        ["topics/dex.md", "topics/visa.md"],
        "revision",
        source_areas_by_path={"topics/dex.md": {"dex"}},
    )

    assert len(emitted) == 1
    assert wakes == [("visa", "topic page edited (topics/visa.md)")]


@pytest.mark.asyncio
async def test_area_event_reschedules_and_wakes_the_scheduler(tmp_path: Path) -> None:
    calls: list[tuple[str, object, bool]] = []

    class Sessions:
        async def get_area(self, area_id: str):
            return {
                "area_id": area_id,
                "autonomy": "observe",
                "attention": "ambient",
                "paused_at": None,
            }

    class Scheduler:
        async def reschedule_run(self, task_id, deadline, *, only_if_earlier=False):
            calls.append((task_id, deadline, only_if_earlier))
            return True

    runtime = AutomationRuntime.__new__(AutomationRuntime)
    runtime.stores = SimpleNamespace(sessions=Sessions())
    runtime.scheduler = Scheduler()
    runtime.custodians = CustodianStore(tmp_path / "custodians.json")

    await runtime.request_area_wake("dex", "new evidence")

    assert len(calls) == 1
    assert calls[0][0] == "area:dex"
    assert calls[0][2] is True


@pytest.mark.asyncio
async def test_user_engagement_lifts_a_dormant_area_back_to_ambient(tmp_path: Path) -> None:
    """Attention decay is one-way — note_ignored_asks only ever lowers it and
    nothing in the runtime raises it. An area the user got busy and stopped
    answering would otherwise stay dormant (72h-14d) permanently."""
    updates: list[tuple[str, str]] = []

    class Sessions:
        async def get_area(self, area_id: str):
            return {"area_id": area_id, "autonomy": "observe", "attention": "dormant", "paused_at": None}

        async def update_area(self, area_id: str, **fields):
            updates.append((area_id, fields["attention"]))

    class Scheduler:
        async def reschedule_run(self, task_id, deadline, *, only_if_earlier=False):
            return True

    def _runtime() -> AutomationRuntime:
        runtime = AutomationRuntime.__new__(AutomationRuntime)
        runtime.stores = SimpleNamespace(sessions=Sessions())
        runtime.scheduler = Scheduler()
        runtime.custodians = CustodianStore(tmp_path / "custodians.json")
        return runtime

    # Observing the area is not engagement — a custodian editing its own page
    # must not resurrect the cadence the user's silence earned.
    await _runtime().request_area_wake("dex", "topic page edited (topics/dex.md)")
    assert updates == []

    await _runtime().request_area_wake("dex", "user resolved ask 'ship it' as done", engaged=True)
    assert updates == [("dex", "ambient")]


def test_work_context_uses_report_keys_instead_of_internal_ids() -> None:
    common = {
        "area_id": "area_health",
        "created_at": "2026-07-10T00:00:00+00:00",
        "updated_at": "2026-07-10T00:00:00+00:00",
        "completed_at": None,
    }
    snapshot = AreaWorkSnapshot(
        outcomes=[
            AreaOutcome(
                outcome_id="outcome:area_health:labs-normal",
                stable_key="labs-normal",
                title="Lab values normalized",
                success_criteria="All values in range",
                status="active",
                priority=5,
                source="user",
                **common,
            )
        ],
        work_items=[
            AreaWorkItem(
                item_id="work:area_health:book-labs",
                stable_key="book-labs",
                outcome_id="outcome:area_health:labs-normal",
                kind="action",
                text="Book the follow-up panel",
                status="in_progress",
                owner="custodian",
                due_at=None,
                next_attempt_at=None,
                **common,
            ),
            AreaWorkItem(
                item_id="work:area_health:choose-lab",
                stable_key="choose-lab",
                outcome_id="outcome:area_health:labs-normal",
                kind="blocker",
                text="Need the user's preferred lab",
                status="active",
                owner="user",
                due_at=None,
                next_attempt_at=None,
                **common,
            ),
        ],
        events=[
            AreaWorkEvent(
                event_id=7,
                area_id="area_health",
                outcome_id=None,
                item_id="work:area_health:book-labs",
                run_ref="run:r1",
                event_type="progress",
                summary="Found an available appointment",
                source_refs=[
                    {
                        "provider": "arden",
                        "kind": "session",
                        "ref": "health-chat~abc123",
                        "title": "Health chat",
                    }
                ],
                created_at="2026-07-10T01:00:00+00:00",
            )
        ],
    )

    text = render_work_context(snapshot)
    payload = json.loads(text.split("\n", 1)[1])

    assert payload["outcomes"][0]["key"] == "labs-normal"
    assert payload["work_items"][0]["key"] == "book-labs"
    assert payload["work_items"][0]["outcome_key"] == "labs-normal"
    assert payload["recent_evidence"][0]["target_type"] == "work"
    assert payload["recent_evidence"][0]["target_key"] == "book-labs"
    assert payload["recent_evidence"][0]["source_refs"] == [
        {
            "provider": "arden",
            "kind": "session",
            "ref": "health-chat~abc123",
            "title": "Health chat",
        }
    ]
    assert "area_id" not in text
    assert "item_id" not in text
    assert "outcome_id" not in text
    assert "stable_key" not in text
    assert "work:area_health" not in text
    assert "outcome:area_health" not in text
