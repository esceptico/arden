from arden.agent.types.llm import Role
from arden.core.model_context_budget import (
    MODEL_TOOL_RESULT_KEEP_FULL_CHARS,
    MODEL_TOOL_RESULT_PREVIEW_CHARS,
    clamp_tool_results_for_model_context,
    compact_tool_result_text,
)


def _tool(call_id: str, content: str) -> dict:
    return {"role": Role.TOOL, "tool_call_id": call_id, "content": content}


def test_compact_tool_result_text_truncates_without_false_store_claim():
    out = compact_tool_result_text("x" * 5000, surface="history display", limit=2500)
    assert len(out) <= 2500
    assert "preview only" in out
    assert "tool result store" not in out  # the old footer claimed a store that didn't exist


def test_compact_tool_result_text_generic_makes_no_false_store_claim():
    out = compact_tool_result_text("x" * 5000, surface="history display", limit=2500)
    assert "tool result store" not in out


def test_clamp_keeps_recent_results_full_within_budget():
    big = "y" * (MODEL_TOOL_RESULT_KEEP_FULL_CHARS // 3)
    messages = [{"role": "system", "content": "s"}, _tool("a", big), _tool("b", big)]

    clamped = clamp_tool_results_for_model_context(messages)

    # total (2/3 of budget) is under budget → nothing clamped
    assert clamped is messages


def test_clamp_stubs_oldest_when_over_budget_keeps_recent_full():
    big = "y" * (MODEL_TOOL_RESULT_KEEP_FULL_CHARS // 2 + 1000)  # only one fits in budget
    messages = [{"role": "system", "content": "s"}, _tool("old", big), _tool("mid", big), _tool("new", big)]

    clamped = clamp_tool_results_for_model_context(messages)

    by_id = {m.get("tool_call_id"): m["content"] for m in clamped if m.get("role") == Role.TOOL}
    # most recent kept full
    assert by_id["new"] == big
    # older ones stubbed to a short informative line (no file persisted here, so no re-read ref)
    for cid in ("old", "mid"):
        assert by_id[cid] != big
        assert "cleared from context" in by_id[cid]
        assert "read_tool_result" not in by_id[cid]


def test_clamp_is_monotonic_old_stubs_are_frozen():
    big = "y" * (MODEL_TOOL_RESULT_KEEP_FULL_CHARS // 2 + 1000)
    base = [{"role": "system", "content": "s"}, _tool("a", big), _tool("b", big), _tool("c", big)]

    first = clamp_tool_results_for_model_context(base)
    first_by_id = {m.get("tool_call_id"): m["content"] for m in first if m.get("role") == Role.TOOL}

    grown = base + [_tool("d", big)]
    second = clamp_tool_results_for_model_context(grown)
    second_by_id = {m.get("tool_call_id"): m["content"] for m in second if m.get("role") == Role.TOOL}

    # results stubbed in the first pass are byte-identical in the second (frozen → cache-safe)
    for cid, content in first_by_id.items():
        if "cleared from context" in content:
            assert second_by_id[cid] == content


def test_clamp_small_stub_makes_no_reread_promise():
    big = "y" * (MODEL_TOOL_RESULT_KEEP_FULL_CHARS // 2 + 1000)
    tiny = "z" * 100  # below the blob/re-read threshold
    messages = [{"role": "system", "content": "s"}, _tool("tiny", tiny), _tool("b1", big), _tool("b2", big)]

    clamped = clamp_tool_results_for_model_context(messages)
    by_id = {m.get("tool_call_id"): m["content"] for m in clamped if m.get("role") == Role.TOOL}

    # tiny is pushed out (oldest) and stubbed, but its stub must NOT promise re-read (no blob for <2500)
    assert "cleared from context" in by_id["tiny"]
    assert "read_tool_result" not in by_id["tiny"]
    assert len(tiny) <= MODEL_TOOL_RESULT_PREVIEW_CHARS


def test_clamp_counts_and_removes_large_structured_tool_data():
    message = _tool("large-data", "summary")
    message["data"] = {"rows": [{"body": "x" * (MODEL_TOOL_RESULT_KEEP_FULL_CHARS + 1)}]}

    clamped = clamp_tool_results_for_model_context([message])

    assert "cleared from context" in clamped[0]["content"]
    assert "data" not in clamped[0]
