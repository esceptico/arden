from arden.constants import OFFLOAD_PREVIEW_CHARS

HISTORY_TOOL_RESULT_PREVIEW_CHARS = OFFLOAD_PREVIEW_CHARS
# Default preview length for the history-display compaction helper.
MODEL_TOOL_RESULT_PREVIEW_CHARS = 2_500


def compact_tool_result_text(
    content: str,
    *,
    surface: str,
    limit: int = MODEL_TOOL_RESULT_PREVIEW_CHARS,
) -> str:
    footer = f"\n... [{surface} preview truncated]"
    header = f"[Tool result compacted for {surface}: {len(content)} chars total, showing preview only.]\n"
    if limit <= len(header) + len(footer):
        brief = f"[Tool result compacted for {surface}: {len(content)} chars total. Preview omitted.]"
        if len(brief) <= limit:
            return brief
        return f"{brief[: max(0, limit - 3)]}..."

    preview_budget = limit - len(header) - len(footer)
    return f"{header}{content[:preview_budget]}{footer}"
