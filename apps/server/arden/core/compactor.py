import asyncio
import threading
from typing import Protocol

from arden.agent import CompletionResponse, Role
from arden.constants import (
    COMPACTION_TIMEOUT,
    COMPRESSION_KEEP_RATIO,
    COMPRESSION_THRESHOLD,
    COMPRESSION_TOKEN_HEADROOM,
    MAX_MESSAGES,
    SESSION_HANDOFF_MARKER,
    SUMMARY_MAX_TOKENS,
)
from arden.context.prompts import MERGE_SUMMARY_PROMPT_TEMPLATE, SUMMARIZE_PROMPT_TEMPLATE
from arden.llm.models import get_model
from arden.llm.router import create_completion_client
from arden.llm.utils import blocks_to_text
from arden.observability import observed_trace

_COMPACTION_MAX_THREADS = 2
_COMPACTION_SLOTS = threading.BoundedSemaphore(_COMPACTION_MAX_THREADS)


class Compactor(Protocol):
    def should_compact(
        self,
        messages: list[dict],
        model: str,
        last_input_tokens: int | None,
    ) -> bool:
        """True iff a maybe_compact() call would produce a real compaction."""
        ...

    async def maybe_compact(
        self,
        messages: list[dict],
        model: str,
        last_input_tokens: int | None,
        *,
        rehydration_state: dict | None = None,
    ) -> list[dict] | None:
        """Return compacted messages, or None if no compaction needed."""
        ...


def compact_needed(
    messages: list[dict],
    model: str,
    actual_input_tokens: int | None = None,
    *,
    threshold: float = COMPRESSION_THRESHOLD,
    token_headroom: float = COMPRESSION_TOKEN_HEADROOM,
    max_messages: int = MAX_MESSAGES,
) -> bool:
    if len(messages) >= max_messages:
        return True
    if actual_input_tokens is None:
        return False
    limit = get_model(model).max_context_tokens
    return actual_input_tokens >= int(limit * threshold * token_headroom)


def compactable_range(
    messages: list[dict],
    keep_ratio: float = COMPRESSION_KEEP_RATIO,
) -> tuple[int, int] | None:
    """Find (start, end) range of messages to summarize, or None if nothing to compact.

    Keeps the most recent `keep_ratio` fraction of messages (excluding system),
    snapping the boundary forward past tool messages to avoid splitting a turn.
    """
    n = len(messages)
    if n <= 4:
        return None

    compressible = n - 1
    keep_count = max(4, int(compressible * keep_ratio))
    tail_start = n - keep_count

    while tail_start < n and messages[tail_start]["role"] == Role.TOOL:
        tail_start += 1

    if tail_start <= 1 or tail_start >= n:
        return None

    return (1, tail_start)


def is_handoff_message(msg: dict) -> bool:
    return blocks_to_text(msg.get("content", "")).startswith(SESSION_HANDOFF_MARKER)


def _extract_prior_summary(messages: list, start: int, end: int) -> str | None:
    """If the compactable range starts with a prior handoff, extract and return it."""
    if start >= end:
        return None
    content = blocks_to_text(messages[start].get("content", ""))
    if is_handoff_message(messages[start]):
        return content.removeprefix(SESSION_HANDOFF_MARKER).strip()
    return None


def _build_conversation_text(
    messages: list,
    start: int,
    end: int,
    *,
    skip_handoff: bool = False,
    include_tool_messages: bool = False,
) -> str:
    text_parts = []
    for msg in messages[start:end]:
        if (role := msg["role"]) == Role.TOOL:
            if include_tool_messages:
                tool_name = msg.get("name") or msg.get("tool_name") or "tool"
                content = blocks_to_text(msg.get("content", ""))
                if content:
                    text_parts.append(f"tool {tool_name}: {content}")
            continue
        content = blocks_to_text(msg["content"])
        if not content:
            continue
        if content.startswith(SESSION_HANDOFF_MARKER):
            if skip_handoff:
                continue
            text_parts.append(f"[PRIOR SUMMARY — preserve key points]\n{content}")
        else:
            text_parts.append(f"{role}: {content}")
    return "\n\n".join(text_parts)


# Reserved for the summarize/merge system prompt when bounding the
# summarizer's own input; ~3 chars/token keeps the estimate conservative.
_SUMMARY_PROMPT_HEADROOM_TOKENS = 2000
_CHARS_PER_TOKEN = 3
_DROPPED_HISTORY_MARKER = "[oldest history omitted — it did not fit the summarizer's context window]"


def _fit_for_summary(text: str, model: str, summary_max_tokens: int) -> str:
    """Cap the summarizer's own input to the model's window.

    The compactor exists to rescue oversized histories, so its summary
    request must never itself exceed the window — an unbounded channel
    transcript once 400'd the summarize call and bricked every fire of
    the automation it was meant to save. The oldest text is dropped
    first: the newest is what the next iterations need verbatim, and the
    summary absorbs the loss."""
    budget_tokens = get_model(model).max_context_tokens - summary_max_tokens - _SUMMARY_PROMPT_HEADROOM_TOKENS
    budget_chars = max(4000, budget_tokens * _CHARS_PER_TOKEN)
    if len(text) <= budget_chars:
        return text
    return f"{_DROPPED_HISTORY_MARKER}\n\n{text[-budget_chars:]}"


def _build_summarize_request(
    conversation_text: str,
    model: str,
    summary_max_tokens: int = SUMMARY_MAX_TOKENS,
    *,
    prompt_context: str | None = None,
) -> dict:
    word_budget = int(summary_max_tokens * 0.75)
    prompt = SUMMARIZE_PROMPT_TEMPLATE.render(budget=word_budget)
    if prompt_context:
        prompt = f"{prompt}\n\n{prompt_context}"
    return {
        "model": model,
        "messages": [
            {"role": Role.SYSTEM, "content": prompt},
            {"role": Role.USER, "content": conversation_text},
        ],
        "temperature": 0.3,
        "max_tokens": summary_max_tokens,
    }


def _build_merge_request(
    existing_summary: str,
    new_conversation: str,
    model: str,
    summary_max_tokens: int = SUMMARY_MAX_TOKENS,
    *,
    prompt_context: str | None = None,
) -> dict:
    word_budget = int(summary_max_tokens * 0.75)
    prompt = MERGE_SUMMARY_PROMPT_TEMPLATE.render(budget=word_budget)
    if prompt_context:
        prompt = f"{prompt}\n\n{prompt_context}"
    user_content = f"## Existing Summary:\n{existing_summary}\n\n## New Conversation:\n{new_conversation}"
    return {
        "model": model,
        "messages": [
            {"role": Role.SYSTEM, "content": prompt},
            {"role": Role.USER, "content": user_content},
        ],
        "temperature": 0.3,
        "max_tokens": summary_max_tokens,
    }


async def compact_summarize(
    messages: list,
    start: int,
    end: int,
    model: str,
    summary_max_tokens: int = SUMMARY_MAX_TOKENS,
    *,
    prompt_context: str | None = None,
    include_tool_messages: bool = False,
) -> str:
    prior_summary = _extract_prior_summary(messages, start, end)

    if prior_summary:
        new_start = start + 1
        new_conversation = _fit_for_summary(
            _build_conversation_text(
                messages,
                new_start,
                end,
                skip_handoff=True,
                include_tool_messages=include_tool_messages,
            ),
            model,
            summary_max_tokens,
        )
        request = _build_merge_request(
            prior_summary,
            new_conversation,
            model,
            summary_max_tokens,
            prompt_context=prompt_context,
        )
    else:
        conversation_text = _fit_for_summary(
            _build_conversation_text(
                messages,
                start,
                end,
                include_tool_messages=include_tool_messages,
            ),
            model,
            summary_max_tokens,
        )
        request = _build_summarize_request(
            conversation_text,
            model,
            summary_max_tokens,
            prompt_context=prompt_context,
        )

    response = await _complete_compaction_request(model, request)
    content = response.choices[0].message.content
    if not content:
        return "Unable to summarize."
    return content.strip()


@observed_trace("compaction", tags="compaction")
async def _complete_compaction_request(model: str, request: dict) -> CompletionResponse:
    loop = asyncio.get_running_loop()
    if not _COMPACTION_SLOTS.acquire(blocking=False):
        raise TimeoutError("Compaction model worker pool is saturated")
    future: asyncio.Future[CompletionResponse] = loop.create_future()

    def finish(response: CompletionResponse | None = None, error: BaseException | None = None) -> None:
        if future.done():
            return
        if error is not None:
            future.set_exception(error)
            return
        if response is None:
            future.set_exception(RuntimeError("Compaction model returned no response"))
            return
        future.set_result(response)

    async def complete() -> CompletionResponse:
        client = create_completion_client(model)
        try:
            return await client.completion(**request)
        finally:
            await client.close()

    def run() -> None:
        try:
            response = asyncio.run(complete())
        except BaseException as exc:
            try:
                loop.call_soon_threadsafe(finish, None, exc)
            except RuntimeError:
                pass
        else:
            try:
                loop.call_soon_threadsafe(finish, response, None)
            except RuntimeError:
                pass
        finally:
            _COMPACTION_SLOTS.release()

    thread = threading.Thread(target=run, name="arden-compaction-llm", daemon=True)
    try:
        thread.start()
    except BaseException:
        _COMPACTION_SLOTS.release()
        raise
    return await asyncio.wait_for(future, timeout=COMPACTION_TIMEOUT)


def _message_ref_id(msg: dict) -> str | None:
    value = msg.get("message_id") or msg.get("client_id")
    return value if isinstance(value, str) and value else None


def _build_compacted_messages(
    messages: list[dict],
    start: int,
    end: int,
    summary: str,
    *,
    rehydration_state: dict | None = None,
) -> list[dict]:
    compaction: dict = {
        "kind": "session_handoff",
        "message_start": start,
        "message_end": end,
    }
    if start_id := _message_ref_id(messages[start]):
        compaction["message_start_id"] = start_id
    if end > start and (end_id := _message_ref_id(messages[end - 1])):
        compaction["message_end_id"] = end_id
    tool_messages = [msg for msg in messages[start:end] if msg.get("role") == Role.TOOL and msg.get("tool_call_id")]
    if tool_messages:
        compaction["tool_result_refs"] = [msg["tool_call_id"] for msg in tool_messages]
    if rehydration_state:
        compaction["rehydration"] = rehydration_state

    return [
        messages[0],
        {
            "role": Role.ASSISTANT,
            "content": f"{SESSION_HANDOFF_MARKER}\n{summary}",
            "compaction": compaction,
        },
        *messages[end:],
    ]


async def compact_messages(
    messages: list[dict],
    model: str,
    *,
    keep_ratio: float = COMPRESSION_KEEP_RATIO,
    summary_max_tokens: int = SUMMARY_MAX_TOKENS,
    rehydration_state: dict | None = None,
    prompt_context: str | None = None,
    include_tool_messages: bool = False,
) -> list[dict] | None:
    """Compact messages by summarizing old ones. Returns new messages or None if nothing to compact."""
    r = compactable_range(messages, keep_ratio=keep_ratio)
    if r is None:
        return None
    start, end = r
    summary = await compact_summarize(
        messages,
        start,
        end,
        model,
        summary_max_tokens,
        prompt_context=prompt_context,
        include_tool_messages=include_tool_messages,
    )
    return _build_compacted_messages(messages, start, end, summary, rehydration_state=rehydration_state)


class SummaryCompactor:
    def __init__(
        self,
        threshold: float = COMPRESSION_THRESHOLD,
        max_messages: int = MAX_MESSAGES,
        keep_ratio: float = COMPRESSION_KEEP_RATIO,
        summary_max_tokens: int = SUMMARY_MAX_TOKENS,
        prompt_context: str | None = None,
        include_tool_messages: bool = False,
    ):
        self.threshold = threshold
        self.max_messages = max_messages
        self.keep_ratio = keep_ratio
        self.summary_max_tokens = summary_max_tokens
        self.prompt_context = prompt_context
        self.include_tool_messages = include_tool_messages

    def with_prompt_context(
        self,
        prompt_context: str,
        *,
        include_tool_messages: bool = False,
    ) -> "SummaryCompactor":
        return SummaryCompactor(
            threshold=self.threshold,
            max_messages=self.max_messages,
            keep_ratio=self.keep_ratio,
            summary_max_tokens=self.summary_max_tokens,
            prompt_context=prompt_context,
            include_tool_messages=include_tool_messages,
        )

    def should_compact(
        self,
        messages: list[dict],
        model: str,
        last_input_tokens: int | None,
    ) -> bool:
        if not compact_needed(
            messages,
            model,
            last_input_tokens,
            threshold=self.threshold,
            max_messages=self.max_messages,
        ):
            return False
        return compactable_range(messages, keep_ratio=self.keep_ratio) is not None

    async def maybe_compact(
        self,
        messages: list[dict],
        model: str,
        last_input_tokens: int | None,
        *,
        rehydration_state: dict | None = None,
    ) -> list[dict] | None:
        if not self.should_compact(messages, model, last_input_tokens):
            return None

        return await compact_messages(
            messages,
            model,
            keep_ratio=self.keep_ratio,
            summary_max_tokens=self.summary_max_tokens,
            rehydration_state=rehydration_state,
            prompt_context=self.prompt_context,
            include_tool_messages=self.include_tool_messages,
        )
