import json
from dataclasses import replace

from ntrp.agent.model_request import ModelRequest, ModelRequestNext
from ntrp.agent.types.llm import Role
from ntrp.constants import OFFLOAD_PREVIEW_CHARS
from ntrp.core.tool_result_files import find_result_file

HISTORY_TOOL_RESULT_PREVIEW_CHARS = OFFLOAD_PREVIEW_CHARS
# Default preview length for the history-display compaction helper.
MODEL_TOOL_RESULT_PREVIEW_CHARS = 2_500
# Recency budget: keep the most-recent tool results in FULL up to this many chars; older results
# collapse to a short stub (offloaded ones point at their durable file via read_file). ~80K chars
# (≈20K tokens) keeps the active working set intact instead of forcing re-reads, while bounding
# per-request tool content.
MODEL_TOOL_RESULT_KEEP_FULL_CHARS = 80_000


class ToolResultContextBudgetMiddleware:
    async def __call__(self, request: ModelRequest, next_request: ModelRequestNext) -> ModelRequest:
        prepared = await next_request(request)
        messages = clamp_tool_results_for_model_context(prepared.messages)
        if messages is prepared.messages:
            return prepared
        return replace(prepared, messages=messages)


def _is_tool_message(message: dict) -> bool:
    return message.get("role") in {Role.TOOL, "tool"} and isinstance(message.get("content"), str)


def _tool_message_size(message: dict) -> int:
    payload = {"content": message.get("content", ""), "data": message.get("data")}
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str))


def clamp_tool_results_for_model_context(messages: list[dict]) -> list[dict]:
    """Keep the most-recent tool results in full up to MODEL_TOOL_RESULT_KEEP_FULL_CHARS;
    collapse older ones to an informative stub.

    Recency-first (the field's consensus: keep the active tail, evict the stale) and monotonic —
    a result only ever transitions full→stub as newer results arrive, never back — so each stub is
    byte-stable across turns and the prompt prefix stays cache-friendly. Results above the blob
    threshold remain exactly re-readable via read_tool_result.
    """
    tool_indices = [i for i, m in enumerate(messages) if _is_tool_message(m)]
    keep_full: set[int] = set()
    remaining = MODEL_TOOL_RESULT_KEEP_FULL_CHARS
    for i in reversed(tool_indices):  # newest first
        length = _tool_message_size(messages[i])
        if length <= remaining:
            keep_full.add(i)
            remaining -= length
        else:
            break  # once a result overflows the budget, it and everything older are stubbed
    if len(keep_full) == len(tool_indices):
        return messages
    clamped = list(messages)
    for i in tool_indices:
        if i not in keep_full:
            clamped[i] = {
                key: value
                for key, value in messages[i].items()
                if key != "data"
            } | {"content": _tool_result_stub(messages[i])}
    return clamped


def _tool_result_stub(message: dict) -> str:
    content = message["content"]
    name = message.get("name") or message.get("tool_name") or "tool"
    line_count = content.count("\n") + 1
    retrieval = ""
    tool_call_id = message.get("tool_call_id")
    if tool_call_id:
        path = find_result_file(tool_call_id)
        if path is not None:
            retrieval = f" Full output: read_file(path={str(path)!r}, offset=N)."
    return f"[Older {name} result cleared from context — {len(content)} chars, {line_count} lines.{retrieval}]"


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
