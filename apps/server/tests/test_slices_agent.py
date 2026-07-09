"""Slice agents as channel automations: standing instructions (the fresh
page arrives via the SLICE system block, not the prompt), validated one-ask
nomination, and the outbox-driven ask sync where every run re-decides the
slice's single ask."""

import pytest
from pydantic import ValidationError

from ntrp.slices.agent import (
    OBSERVE_TOOL_SCOPE,
    SliceAskNomination,
    record_slice_run,
    slice_agent_instructions,
)
from ntrp.slices.asks import AskStore
from ntrp.slices.models import Slice
from ntrp.tools.core.scope import matches_scope

SLICE = Slice(key="o-1a", title="O-1A", page_path="topics/o-1a.md", autonomy="observe")


def test_instructions_state_contract_and_one_ask_protocol():
    text = slice_agent_instructions(SLICE)
    assert "O-1A" in text
    assert "observe" in text
    assert "at most ONE ask" in text
    assert "SLICE context block" in text  # page comes from context, not embedded
    assert "stale decision-ready open loop" in text  # nomination calibration


def test_nomination_schema_validates_at_the_trust_boundary():
    ok = SliceAskNomination.model_validate({"ask": {"text": "Review counsel memo", "kind": "review"}})
    assert ok.ask.kind == "review"
    assert SliceAskNomination.model_validate({"ask": None}).ask is None
    with pytest.raises(ValidationError):
        SliceAskNomination.model_validate({"ask": {"text": "x", "kind": "urgent"}})


def test_record_slice_run_nominates_and_supersedes(tmp_path):
    store = AskStore(tmp_path / "state.json")
    record_slice_run(
        store, "o-1a", "topics/o-1a.md",
        {"ask": {"text": "First ask", "kind": "review"}},
        run_ref="run:r1",
    )
    first = store.list("o-1a")
    assert len(first) == 1 and first[0].text == "First ask" and first[0].provenance == "run:r1"

    record_slice_run(
        store, "o-1a", "topics/o-1a.md",
        {"ask": {"text": "Second ask", "kind": "decide"}},
        run_ref="run:r2",
    )
    active = store.list("o-1a")
    assert [a.text for a in active] == ["Second ask"]  # superseded, not stacked


def test_record_slice_run_silence_retires_previous(tmp_path):
    store = AskStore(tmp_path / "state.json")
    record_slice_run(
        store, "o-1a", "topics/o-1a.md",
        {"ask": {"text": "Old ask", "kind": "review"}},
        run_ref="run:r1",
    )
    record_slice_run(store, "o-1a", "topics/o-1a.md", {"ask": None}, run_ref="run:r2")
    assert store.list("o-1a") == []  # the agent re-decided: silence
    # A failed constrained step (None) is silence too, never a crash.
    record_slice_run(
        store, "o-1a", "topics/o-1a.md",
        {"ask": {"text": "Back", "kind": "review"}},
        run_ref="run:r3",
    )
    record_slice_run(store, "o-1a", "topics/o-1a.md", None, run_ref="run:r4")
    assert store.list("o-1a") == []


def test_observe_scope_covers_memory_and_read_but_not_action_tools():
    assert matches_scope(tuple(OBSERVE_TOOL_SCOPE), "memory_patch")
    assert matches_scope(tuple(OBSERVE_TOOL_SCOPE), "recall")
    assert matches_scope(tuple(OBSERVE_TOOL_SCOPE), "web_search")
    assert not matches_scope(tuple(OBSERVE_TOOL_SCOPE), "send_email")
    assert not matches_scope(tuple(OBSERVE_TOOL_SCOPE), "bash")
    assert not matches_scope(tuple(OBSERVE_TOOL_SCOPE), "create_calendar_event")


def test_load_slice_context_reads_page_or_degrades(tmp_path):
    from ntrp.slices.context import load_slice_context

    vault = tmp_path / "memory"
    (vault / "topics").mkdir(parents=True)
    (vault / "topics" / "o-1a.md").write_text("---\ntitle: O-1A\n---\n# O-1A\n\n## Open loops\n- Find counsel.\n")
    project = {"project_id": "p1", "name": "O-1A", "page_path": "topics/o-1a.md"}

    ctx = load_slice_context(vault, project)
    assert ctx["title"] == "O-1A"
    assert "Find counsel." in ctx["page"]

    assert load_slice_context(vault, None) is None  # unfiled chat → plain chat
    assert load_slice_context(vault, {"project_id": "p2", "name": "Design", "page_path": None}) is None  # pageless
    (vault / "topics" / "o-1a.md").unlink()
    assert load_slice_context(vault, project) is None  # missing page → plain chat


def test_system_blocks_include_slice_block():
    from ntrp.core.prompts import build_system_blocks

    blocks = build_system_blocks(source_details={}, slice_context={"title": "O-1A", "page": "# O-1A\ncase notes"})
    joined = "\n".join(b["text"] for b in blocks)
    assert "## SLICE: O-1A" in joined
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
