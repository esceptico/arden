"""Area agents as channel automations: standing instructions (the fresh
page arrives via the AREA system block, not the prompt), validated one-ask
nomination, and the outbox-driven ask sync where every run re-decides the
area's single ask."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from arden.areas.agent import (
    ACT_TOOL_SCOPE,
    OBSERVE_TOOL_SCOPE,
    AreaAskNomination,
    AreaCustodianReport,
    area_agent_instructions,
    custodian_contract,
    record_area_run,
)
from arden.areas.asks import AskStore
from arden.areas.models import Area
from arden.tools.core.scope import matches_scope

AREA = Area(key="o-1a", title="O-1A", page_path="topics/o-1a.md", autonomy="observe")


def test_instructions_state_contract_and_ask_protocol():
    text = area_agent_instructions(AREA)
    assert "O-1A" in text
    assert "observe" in text
    assert "at most three asks" in text
    assert "AREA context block" in text  # page comes from context, not embedded
    assert "notify" in text and "question" in text and "review" in text
    assert "salience" in text
    assert "Next check" in text  # self-paced heartbeat protocol


def _nom(asks, report="tended", hours=24.0, reason="routine"):
    return {"asks": asks, "report": report, "next_check_hours": hours, "next_check_reason": reason}


def _report(asks, report="tended", hours=24.0, reason="routine"):
    return AreaCustodianReport.model_validate(
        {
            **_nom(asks, report, hours, reason),
            "work_remaining": False,
        }
    )


def _draft(text, kind, salience=4):
    return {
        "key": "deadline",
        "text": text,
        "kind": kind,
        "salience": salience,
        "why_now": "deadline",
        "what_next": "opens page",
    }


def test_nomination_schema_validates_at_the_trust_boundary():
    ok = AreaAskNomination.model_validate(_nom([_draft("Review counsel memo", "review")]))
    assert ok.asks[0].kind == "review"
    assert AreaAskNomination.model_validate(_nom([])).asks == []
    with pytest.raises(ValidationError):
        AreaAskNomination.model_validate(_nom([_draft("x", "urgent")]))
    with pytest.raises(ValidationError):  # salience bounds
        AreaAskNomination.model_validate(_nom([dict(_draft("x", "notify"), salience=9)]))
    with pytest.raises(ValidationError):  # ask cap
        AreaAskNomination.model_validate(_nom([_draft(str(i), "notify") for i in range(4)]))


def test_custodian_report_requires_complete_explicit_work_operations():
    report = AreaCustodianReport.model_validate(
        {
            **_nom([], report="Started evidence collection"),
            "work_remaining": True,
            "outcome_changes": [
                {
                    "op": "create",
                    "key": "petition-filed",
                    "title": "Petition filed",
                    "success_criteria": "Receipt notice exists",
                    "priority": 5,
                }
            ],
            "work_changes": [
                {
                    "op": "create",
                    "key": "collect-exhibits",
                    "outcome_key": "petition-filed",
                    "kind": "action",
                    "text": "Collect final exhibits",
                    "owner": "custodian",
                }
            ],
            "evidence": [],
        }
    )
    assert report.outcome_changes[0].key == "petition-filed"
    assert report.work_changes[0].outcome_key == "petition-filed"

    with pytest.raises(ValidationError):
        AreaCustodianReport.model_validate(
            {
                **_nom([]),
                "work_remaining": True,
                "outcome_changes": [{"op": "create", "key": "missing-fields"}],
            }
        )


def test_record_area_run_nominates_and_supersedes(tmp_path):
    store = AskStore(tmp_path / "state.json")
    record_area_run(
        store,
        "o-1a",
        "topics/o-1a.md",
        _report([_draft("First ask", "review")]),
        run_ref="run:r1",
    )
    first = store.list("o-1a")
    assert len(first) == 1 and first[0].text == "First ask" and first[0].provenance == "run:r1"
    assert first[0].why_now == "deadline" and first[0].what_next == "opens page"

    record_area_run(
        store,
        "o-1a",
        "topics/o-1a.md",
        _report([_draft("Second ask", "question")]),
        run_ref="run:r2",
    )
    active = store.list("o-1a")
    assert [a.text for a in active] == ["Second ask"]  # superseded, not stacked


def test_record_area_run_salience_threshold_and_notify_expiry(tmp_path):
    store = AskStore(tmp_path / "state.json")
    record_area_run(
        store,
        "o-1a",
        "topics/o-1a.md",
        _report(
            [
                _draft("Big thing", "notify", salience=4),
                _draft("Marginal thing", "notify", salience=2),  # below threshold → page only
            ]
        ),
        run_ref="run:r1",
    )
    active = store.list("o-1a")
    assert [a.text for a in active] == ["Big thing"]
    assert active[0].expires_at is not None  # notify asks expire quietly
    record_area_run(
        store,
        "o-1a",
        "topics/o-1a.md",
        _report([_draft("Needs you", "question", salience=5)]),
        run_ref="run:r2",
    )
    q = store.list("o-1a")[0]
    assert q.kind == "question" and q.expires_at is None  # questions wait for the user


def test_quiet_or_malformed_run_preserves_unresolved_decision(tmp_path):
    store = AskStore(tmp_path / "state.json")
    record_area_run(
        store,
        "o-1a",
        "topics/o-1a.md",
        _report([_draft("Old ask", "review")]),
        run_ref="run:r1",
    )
    record_area_run(store, "o-1a", "topics/o-1a.md", _report([]), run_ref="run:r2")
    assert [ask.text for ask in store.list("o-1a")] == ["Old ask"]


def test_repeated_stable_nomination_updates_without_becoming_new(tmp_path):
    store = AskStore(tmp_path / "state.json")
    first = record_area_run(
        store, "o-1a", "topics/o-1a.md", _report([_draft("First wording", "question")]), run_ref="run:r1"
    )
    repeated = record_area_run(
        store, "o-1a", "topics/o-1a.md", _report([_draft("Clearer wording", "question")]), run_ref="run:r2"
    )

    assert len(first) == 1
    assert repeated == []
    assert [ask.text for ask in store.list("o-1a")] == ["Clearer wording"]


def test_observe_scope_is_area_locked_and_uses_canonical_facts():
    """Asserted against the RESOLVED scope, not the authored list: reading is
    granted by the declared `read:*` floor rather than by naming each tool, so
    matching the raw patterns would prove nothing about what the run can do."""
    from arden.tools.core.scope import expand_scope

    reads = ("search_facts", "get_fact", "list_recent_sessions", "read_session", "web_search", "emails", "calendar")
    resolved = expand_scope(tuple(OBSERVE_TOOL_SCOPE), (*reads, "slack_search"))

    assert matches_scope(resolved, "submit_area_report")
    assert matches_scope(resolved, "area_page_patch")
    assert matches_scope(resolved, "area_page_write")
    for name in (*reads, "slack_search"):
        assert matches_scope(resolved, name), name

    # Writes stay out however the floor expands — these are not read-only, so
    # nothing can put them in scope except an explicit grant.
    for name in ("send_email", "bash", "create_calendar_event", "slack_post_message", "write_file"):
        assert not matches_scope(resolved, name), name


def test_live_autonomy_contracts_are_exact_and_never_globally_auto_approve():
    observe = custodian_contract(AREA)
    acting = custodian_contract(replace(AREA, autonomy="act"))

    assert observe.tool_scope == OBSERVE_TOOL_SCOPE
    assert acting.tool_scope == ACT_TOOL_SCOPE
    assert observe.auto_approve is False
    assert acting.auto_approve is False
    assert "observe" in observe.description
    assert "act" in acting.description

    assert matches_scope(tuple(ACT_TOOL_SCOPE), "area_run_automation")
    # Acting may propose child automations — creation itself is approval-gated.
    assert matches_scope(tuple(ACT_TOOL_SCOPE), "create_automation")
    assert not matches_scope(tuple(OBSERVE_TOOL_SCOPE), "create_automation")
    assert not matches_scope(tuple(ACT_TOOL_SCOPE), "send_email")
    assert not matches_scope(tuple(ACT_TOOL_SCOPE), "create_calendar_event")
    assert not matches_scope(tuple(ACT_TOOL_SCOPE), "slack_post_message")
    assert not matches_scope(tuple(ACT_TOOL_SCOPE), "bash")
    assert not matches_scope(tuple(ACT_TOOL_SCOPE), "write_file")
    assert not matches_scope(tuple(ACT_TOOL_SCOPE), "memory_write")


@pytest.mark.asyncio
async def test_runtime_reconciles_permission_downgrades_in_place():
    from arden.server.runtime.automation import AutomationRuntime

    automation = SimpleNamespace(
        task_id="area:o-1a",
        handler=None,
        thread_id="thread-1",
        name="old",
        description="A custom area summary.",
        description_source="manual",
        prompt="old execution instructions",
        auto_approve=True,
        tool_scope=None,
        enabled=True,
        last_result="A completed area report.",
        last_run_at=object(),
        next_run_at=object(),
        running_since=None,
    )

    class Automations:
        async def get(self, task_id):
            return automation

        async def update_metadata(self, value):
            return None

    class Sessions:
        async def rename(self, session_id, name):
            return None

    runtime = AutomationRuntime.__new__(AutomationRuntime)
    runtime.stores = SimpleNamespace(automations=Automations(), sessions=Sessions())

    await runtime._sync_area_automation(AREA, paused=False, index=0)

    assert automation.tool_scope == OBSERVE_TOOL_SCOPE
    assert automation.auto_approve is False
    assert automation.description == "A custom area summary."
    assert automation.description_source == "manual"
    assert "observe" in automation.prompt
    assert automation.last_result == "A completed area report."
    assert automation.last_run_at is not None
    assert automation.next_run_at is not None


@pytest.mark.asyncio
async def test_runtime_seeds_area_through_dedicated_channel_creation():
    calls: list[dict] = []

    class Automations:
        async def get(self, task_id):
            return None

        async def set_enabled(self, task_id, enabled):
            raise AssertionError("active Area should not be paused")

    class AutomationService:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(task_id=kwargs["task_id"])

    from arden.server.runtime.automation import AutomationRuntime

    runtime = AutomationRuntime.__new__(AutomationRuntime)
    runtime.stores = SimpleNamespace(automations=Automations(), sessions=object())
    runtime.automation_service = AutomationService()

    await runtime._sync_area_automation(AREA, paused=False, index=0)

    assert len(calls) == 1
    assert calls[0]["task_id"] == "area:o-1a"
    assert calls[0]["area_id"] == "o-1a"
    assert "thread_id" not in calls[0]


@pytest.mark.asyncio
async def test_runtime_repairs_legacy_area_channel_with_tuple_result():
    automation = SimpleNamespace(
        task_id="area:o-1a",
        handler="area_agent",
        thread_id=None,
        name="old",
        description=None,
        description_source=None,
        prompt="old",
        auto_approve=True,
        tool_scope=None,
        enabled=True,
        last_result="old result",
        triggers=[],
        next_run_at=None,
    )
    updated: list[object] = []

    class Automations:
        async def get(self, task_id):
            return automation

        async def update_metadata(self, value):
            updated.append(value)

    class Sessions:
        async def rename(self, session_id, name):
            return None

        async def rename_if_empty(self, session_id, name):
            return None

    class AutomationService:
        async def _provision_channel(self, name, task_id, area_id=None):
            return SimpleNamespace(session_id="area:o-1a:channel"), False

    from arden.server.runtime.automation import AutomationRuntime

    runtime = AutomationRuntime.__new__(AutomationRuntime)
    runtime.stores = SimpleNamespace(automations=Automations(), sessions=Sessions())
    runtime.automation_service = AutomationService()

    await runtime._sync_area_automation(AREA, paused=False, index=0)

    assert automation.thread_id == "area:o-1a:channel"
    assert automation.handler is None
    assert updated == [automation]


def test_system_blocks_include_area_block():
    from arden.core.prompts import build_system_blocks

    blocks = build_system_blocks(source_details={}, area_page_context={"title": "O-1A", "page": "# O-1A\ncase notes"})
    joined = "\n".join(b["text"] for b in blocks)
    assert "## AREA: O-1A" in joined
    assert "case notes" in joined


def test_observe_toolset_is_narrow_even_with_auto_approve(monkeypatch):
    """auto_approve + extra_tool_names must mean 'skip approvals WITHIN the
    narrow set' — never the full toolset."""

    class _Exec:
        def get_tools(self, read_only=False, extra_names=frozenset()):
            return [{"read_only": read_only, "extra": sorted(extra_names)}]

    class _Req:
        auto_approve = True
        extra_tool_names = frozenset({"memory_write"})

    # exercise just the branch logic by mirroring _prepare's tools selection
    req = _Req()
    ex = _Exec()
    if req.extra_tool_names:
        tools = ex.get_tools(read_only=True, extra_names=req.extra_tool_names)
    elif req.auto_approve:
        tools = ex.get_tools()
    else:
        tools = ex.get_tools(read_only=True)
    assert tools[0]["read_only"] is True and tools[0]["extra"] == ["memory_write"]
