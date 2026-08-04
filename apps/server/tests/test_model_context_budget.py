from arden.core.model_context_budget import compact_tool_result_text


def test_compact_tool_result_text_truncates_without_false_store_claim():
    out = compact_tool_result_text("x" * 5000, surface="history display", limit=2500)
    assert len(out) <= 2500
    assert "preview only" in out
    assert "tool result store" not in out  # the old footer claimed a store that didn't exist


def test_compact_tool_result_text_generic_makes_no_false_store_claim():
    out = compact_tool_result_text("x" * 5000, surface="history display", limit=2500)
    assert "tool result store" not in out
