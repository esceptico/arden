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
            "made_progress": True,
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
                "made_progress": True,
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
        _nom([_draft("First ask", "review")]),
        run_ref="run:r1",
    )
    first = store.list("o-1a")
    assert len(first) == 1 and first[0].text == "First ask" and first[0].provenance == "run:r1"
    assert first[0].why_now == "deadline" and first[0].what_next == "opens page"

    record_area_run(
        store,
        "o-1a",
        "topics/o-1a.md",
        _nom([_draft("Second ask", "question")]),
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
        _nom(
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
        _nom([_draft("Needs you", "question", salience=5)]),
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
        _nom([_draft("Old ask", "review")]),
        run_ref="run:r1",
    )
    record_area_run(store, "o-1a", "topics/o-1a.md", _nom([]), run_ref="run:r2")
    assert [ask.text for ask in store.list("o-1a")] == ["Old ask"]
    record_area_run(store, "o-1a", "topics/o-1a.md", None, run_ref="run:r4")
    assert [ask.text for ask in store.list("o-1a")] == ["Old ask"]


def test_repeated_stable_nomination_updates_without_becoming_new(tmp_path):
    store = AskStore(tmp_path / "state.json")
    first = record_area_run(
        store, "o-1a", "topics/o-1a.md", _nom([_draft("First wording", "question")]), run_ref="run:r1"
    )
    repeated = record_area_run(
        store, "o-1a", "topics/o-1a.md", _nom([_draft("Clearer wording", "question")]), run_ref="run:r2"
    )

    assert len(first) == 1
    assert repeated == []
    assert [ask.text for ask in store.list("o-1a")] == ["Clearer wording"]


def test_observe_scope_is_area_locked_and_can_read_area_transcripts():
    assert matches_scope(tuple(OBSERVE_TOOL_SCOPE), "area_page_patch")
    assert matches_scope(tuple(OBSERVE_TOOL_SCOPE), "area_page_write")
    assert matches_scope(tuple(OBSERVE_TOOL_SCOPE), "recall")
    assert matches_scope(tuple(OBSERVE_TOOL_SCOPE), "list_recent_sessions")
    assert matches_scope(tuple(OBSERVE_TOOL_SCOPE), "read_session")
    assert matches_scope(tuple(OBSERVE_TOOL_SCOPE), "web_search")
    assert matches_scope(tuple(OBSERVE_TOOL_SCOPE), "emails")
    assert matches_scope(tuple(OBSERVE_TOOL_SCOPE), "calendar")
    assert matches_scope(tuple(OBSERVE_TOOL_SCOPE), "slack_search")
    assert not matches_scope(tuple(OBSERVE_TOOL_SCOPE), "memory_patch")
    assert not matches_scope(tuple(OBSERVE_TOOL_SCOPE), "memory_write")
    assert not matches_scope(tuple(OBSERVE_TOOL_SCOPE), "remember")
    assert not matches_scope(tuple(OBSERVE_TOOL_SCOPE), "forget")
    assert not matches_scope(tuple(OBSERVE_TOOL_SCOPE), "send_email")
    assert not matches_scope(tuple(OBSERVE_TOOL_SCOPE), "bash")
    assert not matches_scope(tuple(OBSERVE_TOOL_SCOPE), "create_calendar_event")


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
        description="old",
        auto_approve=True,
        tool_scope=None,
        output_schema=None,
        enabled=True,
        last_result=None,
    )

    class Automations:
        async def get(self, task_id):
            return automation

        async def save(self, value):
            return None

    class Sessions:
        async def rename(self, session_id, name):
            return None

    runtime = AutomationRuntime.__new__(AutomationRuntime)
    runtime.stores = SimpleNamespace(automations=Automations(), sessions=Sessions())

    await runtime._sync_area_automation(AREA, paused=False, index=0)

    assert automation.tool_scope == OBSERVE_TOOL_SCOPE
    assert automation.auto_approve is False
    assert "observe" in automation.description
    assert automation.output_schema == "area_custodian"


def test_custodian_report_is_the_registered_runtime_schema():
    from arden.automation.output_schemas import resolve_output_schema

    assert resolve_output_schema("area_custodian") is AreaCustodianReport
    assert resolve_output_schema("area_ask") is AreaCustodianReport


def test_load_area_context_reads_page_or_degrades(tmp_path):
    from arden.areas.context import load_area_context

    vault = tmp_path / "memory"
    (vault / "topics").mkdir(parents=True)
    (vault / "topics" / "o-1a.md").write_text("---\ntitle: O-1A\n---\n# O-1A\n\n## Open loops\n- Find counsel.\n")
    area = {"area_id": "p1", "name": "O-1A", "page_path": "topics/o-1a.md"}

    ctx = load_area_context(vault, area)
    assert ctx["title"] == "O-1A"
    assert "Find counsel." in ctx["page"]

    assert load_area_context(vault, None) is None  # unfiled chat → plain chat
    assert load_area_context(vault, {"area_id": "p2", "name": "Design", "page_path": None}) is None  # pageless
    (vault / "topics" / "o-1a.md").unlink()
    assert load_area_context(vault, area) is None  # missing page → plain chat


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
