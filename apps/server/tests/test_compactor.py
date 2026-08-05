import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

import arden.constants as constants
from arden.agent import Role
from arden.constants import SESSION_HANDOFF_MARKER, SUMMARY_MAX_TOKENS
from arden.context.prompts import (
    MERGE_SUMMARY_PROMPT_TEMPLATE,
    RESEARCH_AGENT_COMPACTION_CONTEXT,
    SUMMARIZE_PROMPT_TEMPLATE,
)
from arden.core.compactor import (
    _CHARS_PER_TOKEN,
    _DROPPED_HISTORY_MARKER,
    _SUMMARY_PROMPT_HEADROOM_TOKENS,
    SummaryCompactor,
    _build_compacted_messages,
    compact_needed,
    compact_summarize,
    compactable_range,
    is_handoff_message,
    token_compaction_budget,
)
from arden.llm.models import get_model


def test_handoff_summary_tracks_raw_message_range():
    messages = [
        {"role": Role.SYSTEM, "content": "system", "message_id": "sys"},
        {"role": Role.USER, "content": "first", "message_id": "m-1"},
        {"role": Role.ASSISTANT, "content": "reply", "message_id": "m-2"},
        {"role": Role.USER, "content": "second", "message_id": "m-3"},
        {"role": Role.ASSISTANT, "content": "tail", "message_id": "m-4"},
    ]

    compacted = _build_compacted_messages(messages, 1, 4, "Useful summary")

    summary = compacted[1]
    assert summary["content"] == f"{SESSION_HANDOFF_MARKER}\nUseful summary"
    compaction_id = summary["message_id"]
    assert compaction_id.startswith("compact-")
    assert summary["compaction"] == {
        "compaction_id": compaction_id,
        "kind": "session_handoff",
        "message_start": 1,
        "message_end": 4,
        "messages_before": 5,
        "messages_after": 3,
        "preserved_tail_ids": ["m-4"],
        "message_start_id": "m-1",
        "message_end_id": "m-3",
    }
    assert is_handoff_message(summary)


def test_short_history_can_compact_opaque_provider_state():
    messages = [
        {"role": Role.SYSTEM, "content": "system"},
        {"role": Role.USER, "content": "find the tool"},
        {
            "role": Role.ASSISTANT,
            "content": "",
            "openai_response_items": [{"type": "tool_search_output", "tools": [{"description": "x" * 200_000}]}],
        },
        {"role": Role.USER, "content": "use it now"},
    ]

    assert compactable_range(messages) == (1, 3)


def test_short_history_compacts_provider_state_when_it_is_latest():
    messages = [
        {"role": Role.SYSTEM, "content": "system"},
        {"role": Role.USER, "content": "find the tool"},
        {"role": Role.ASSISTANT, "content": "", "anthropic_content": [{"type": "text", "text": "x" * 200_000}]},
    ]

    assert compactable_range(messages) == (1, 3)


def test_build_compacted_messages_embeds_rehydration_state():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "old", "message_id": "m1"},
        {"role": "assistant", "content": "new", "message_id": "m2"},
    ]
    state = {"active_plan_ref": "plan:abc", "pending_approval_ids": ["call-1"]}

    compacted = _build_compacted_messages(messages, 1, 2, "summary", rehydration_state=state)

    assert compacted[1]["compaction"]["rehydration"] == state


def test_compacted_messages_record_tool_result_refs():
    messages = [
        {"role": Role.SYSTEM, "content": "system", "message_id": "sys"},
        {"role": Role.ASSISTANT, "content": "calling", "message_id": "m-1"},
        {"role": Role.TOOL, "content": "big output", "tool_call_id": "call-A", "name": "file_read"},
        {"role": Role.TOOL, "content": "more output", "tool_call_id": "call-B", "name": "bash"},
        {"role": Role.USER, "content": "tail", "message_id": "m-4"},
    ]

    compacted = _build_compacted_messages(messages, 1, 4, "summary")

    assert compacted[1]["compaction"]["tool_result_refs"] == ["call-A", "call-B"]


def test_recompaction_preserves_nested_tool_result_refs():
    messages = [
        {"role": "system", "content": "system", "message_id": "sys"},
        {
            "role": "assistant",
            "content": "prior handoff",
            "message_id": "compact-old",
            "compaction": {
                "kind": "session_handoff",
                "compaction_id": "compact-old",
                "tool_result_refs": ["call-old", "call-shared"],
            },
        },
        {"role": "tool", "content": "new", "tool_call_id": "call-shared", "message_id": "tool-new"},
        {"role": "assistant", "content": "tail", "message_id": "tail"},
    ]

    compacted = _build_compacted_messages(messages, 1, 3, "new summary")

    assert compacted[1]["compaction"]["tool_result_refs"] == ["call-old", "call-shared"]


def test_compacted_messages_record_and_retain_background_result_refs():
    messages = [
        {"role": "system", "content": "system", "message_id": "sys"},
        {
            "role": "assistant",
            "content": "prior handoff",
            "message_id": "compact-old",
            "compaction": {
                "kind": "session_handoff",
                "compaction_id": "compact-old",
                "background_result_refs": ["agent-old", "agent-shared"],
            },
        },
        {
            "role": "user",
            "content": (
                '<background_agent_result task_id="wrong-xml-ref" result_ref="background://wrong-xml-ref" '
                'status="completed">\n<result>done</result>\n</background_agent_result>'
            ),
            "client_id": "bg:wrong-client-ref:completed",
            "background_result_ref": "agent-shared",
            "message_id": "bg-shared",
        },
        {
            "role": "user",
            "content": '<background_agent_result status="failed">legacy</background_agent_result>',
            "client_id": "bg:agent-legacy:failed",
            "message_id": "bg-legacy",
        },
        {"role": "assistant", "content": "tail", "message_id": "tail"},
    ]

    compacted = _build_compacted_messages(messages, 1, 4, "new summary")

    assert compacted[1]["compaction"]["background_result_refs"] == [
        "agent-old",
        "agent-shared",
        "agent-legacy",
    ]


def test_compaction_prompts_preserve_exact_background_result_locators():
    prompts = (
        SUMMARIZE_PROMPT_TEMPLATE.render(budget=100),
        MERGE_SUMMARY_PROMPT_TEMPLATE.render(budget=100),
        RESEARCH_AGENT_COMPACTION_CONTEXT,
    )

    assert all("agent_result_read" in prompt and "task_id" in prompt for prompt in prompts)


def test_compacted_messages_omit_tool_result_refs_when_none():
    messages = [
        {"role": Role.SYSTEM, "content": "system"},
        {"role": Role.USER, "content": "old", "message_id": "m1"},
        {"role": Role.ASSISTANT, "content": "new", "message_id": "m2"},
    ]

    compacted = _build_compacted_messages(messages, 1, 2, "summary")

    assert "tool_result_refs" not in compacted[1]["compaction"]
    assert "background_result_refs" not in compacted[1]["compaction"]


def test_handoff_content_has_no_reread_ref_list():
    # The ref-list is an invitation to re-read (all 4 leader systems omit it); only the summary remains.
    messages = [
        {"role": Role.SYSTEM, "content": "system"},
        {"role": Role.ASSISTANT, "content": "calling", "message_id": "m-1"},
        {"role": Role.TOOL, "content": "the full pricing data here", "tool_call_id": "call-A", "name": "web_fetch"},
        {"role": Role.USER, "content": "tail", "message_id": "m-4"},
    ]

    compacted = _build_compacted_messages(messages, 1, 3, "summary text")
    content = compacted[1]["content"]

    assert content == f"{SESSION_HANDOFF_MARKER}\nsummary text"
    assert "read_tool_result" not in content
    assert "tool_result:" not in content
    # the ids are still in metadata for the UI, just not surfaced to the model
    assert compacted[1]["compaction"]["tool_result_refs"] == ["call-A"]


def _large_history(message_count: int = 10, chars_per_message: int = 45_000) -> list[dict]:
    return [
        {"role": "system", "content": "system"},
        *[
            {
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"msg-{i} " + ("x" * chars_per_message),
            }
            for i in range(message_count)
        ],
    ]


def test_compact_needed_requires_usage_or_message_ceiling():
    messages = _large_history()

    assert not compact_needed(messages, "gpt-5.2", actual_input_tokens=None, threshold=0.01, max_messages=999)


def test_compact_needed_trusts_saved_usage_when_present():
    messages = _large_history()

    assert not compact_needed(messages, "gpt-5.2", actual_input_tokens=1_000, threshold=0.01)


def test_compact_needed_uses_headroom_before_configured_threshold(monkeypatch):
    monkeypatch.setattr(
        "arden.core.compactor.get_model",
        lambda _model: type("Model", (), {"max_context_tokens": 1000})(),
    )
    messages = [{"role": "system", "content": "system"}]

    assert compact_needed(messages, "test-model", actual_input_tokens=760, threshold=0.8)
    assert not compact_needed(messages, "test-model", actual_input_tokens=759, threshold=0.8)


def test_token_budget_reports_the_same_trigger_used_by_compaction(monkeypatch):
    monkeypatch.setattr(
        "arden.core.compactor.get_model",
        lambda _model: type("Model", (), {"max_context_tokens": 1000})(),
    )

    budget = token_compaction_budget("test-model", threshold=0.8)

    assert budget.hard_limit == 1000
    assert budget.compaction_trigger == 760


def test_compact_needed_triggers_at_message_ceiling():
    messages = [{"role": "user", "content": str(i)} for i in range(5)]

    assert compact_needed(messages, "gpt-5.2", actual_input_tokens=0, max_messages=5)


def test_summary_compactor_waits_for_usage_or_message_ceiling():
    messages = _large_history()
    compactor = SummaryCompactor(threshold=0.01, max_messages=999)

    assert not compactor.should_compact(messages, "gpt-5.2", last_input_tokens=None)


def test_compaction_timeout_is_finite():
    assert constants.COMPACTION_TIMEOUT is not None
    assert constants.COMPACTION_TIMEOUT == 600


@pytest.mark.asyncio
async def test_compact_summarize_keeps_server_event_loop_responsive(monkeypatch):
    class BlockingClient:
        async def completion(self, **_kwargs):
            time.sleep(0.1)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="summary"))],
            )

        async def close(self):
            return None

    monkeypatch.setattr("arden.core.compactor.create_completion_client", lambda _model: BlockingClient())

    messages = [
        {"role": Role.SYSTEM, "content": "system"},
        {"role": Role.USER, "content": "old"},
        {"role": Role.ASSISTANT, "content": "reply"},
        {"role": Role.USER, "content": "tail"},
        {"role": Role.ASSISTANT, "content": "tail reply"},
    ]

    started = time.perf_counter()
    task = asyncio.create_task(compact_summarize(messages, 1, 3, "gpt-5.2"))
    await asyncio.wait_for(asyncio.sleep(0.01), timeout=0.05)

    assert time.perf_counter() - started < 0.05
    assert await task == "summary"


@pytest.mark.asyncio
async def test_research_compaction_prompt_includes_research_context_and_tool_messages(monkeypatch):
    captured = {}

    class CapturingClient:
        async def completion(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="summary"))],
            )

        async def close(self):
            return None

    monkeypatch.setattr("arden.core.compactor.create_completion_client", lambda _model: CapturingClient())

    messages = [
        {"role": Role.SYSTEM, "content": "research system"},
        {"role": Role.USER, "content": "research task"},
        {"role": Role.ASSISTANT, "content": "I will search", "tool_calls": []},
        {"role": Role.TOOL, "content": "source result with key evidence", "tool_call_id": "call-1", "name": "search"},
        {"role": Role.ASSISTANT, "content": "tail"},
    ]

    await compact_summarize(
        messages,
        1,
        4,
        "gpt-5.2",
        prompt_context="## Research Agent Handoff\n- Preserve research_outline state.",
        include_tool_messages=True,
    )

    system_prompt = captured["messages"][0]["content"]
    user_prompt = captured["messages"][1]["content"]
    assert "Research Agent Handoff" in system_prompt
    assert "research_outline" in system_prompt
    assert "tool search:" in user_prompt
    assert "source result with key evidence" in user_prompt


@pytest.mark.asyncio
async def test_compaction_timeouts_do_not_spawn_unbounded_threads(monkeypatch):
    class BlockingClient:
        async def completion(self, **_kwargs):
            time.sleep(0.2)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="summary"))],
            )

        async def close(self):
            return None

    monkeypatch.setattr("arden.core.compactor.COMPACTION_TIMEOUT", 0.01)
    monkeypatch.setattr("arden.core.compactor.create_completion_client", lambda _model: BlockingClient())

    messages = [
        {"role": Role.SYSTEM, "content": "system"},
        {"role": Role.USER, "content": "old"},
        {"role": Role.ASSISTANT, "content": "reply"},
        {"role": Role.USER, "content": "tail"},
        {"role": Role.ASSISTANT, "content": "tail reply"},
    ]

    for _ in range(3):
        with pytest.raises(asyncio.TimeoutError):
            await compact_summarize(messages, 1, 3, "gpt-5.2")

    live = [thread for thread in threading.enumerate() if thread.name.startswith("arden-compaction-llm")]
    assert len(live) <= 2
    deadline = time.perf_counter() + 1
    while time.perf_counter() < deadline:
        if not [thread for thread in threading.enumerate() if thread.name.startswith("arden-compaction-llm")]:
            break
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_compact_summarize_caps_its_own_request_to_the_model_window(monkeypatch):
    # The compactor rescues oversized histories — its summary request must
    # never itself exceed the window. A 30 MB channel transcript once 400'd
    # the summarize call and bricked every fire of its automation.
    captured = {}

    class CapturingClient:
        async def completion(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="summary"))],
            )

        async def close(self):
            return None

    monkeypatch.setattr("arden.core.compactor.create_completion_client", lambda _model: CapturingClient())

    messages = [
        {"role": Role.SYSTEM, "content": "system"},
        *(
            {"role": Role.USER if i % 2 == 0 else Role.ASSISTANT, "content": "x" * 400_000 + f" tail-{i}"}
            for i in range(4)
        ),
        {"role": Role.ASSISTANT, "content": "kept tail"},
    ]

    await compact_summarize(messages, 1, 5, "gpt-5.2")

    budget_chars = (
        get_model("gpt-5.2").max_context_tokens - SUMMARY_MAX_TOKENS - _SUMMARY_PROMPT_HEADROOM_TOKENS
    ) * _CHARS_PER_TOKEN
    user_prompt = captured["messages"][1]["content"]
    assert user_prompt.startswith(_DROPPED_HISTORY_MARKER)
    assert len(user_prompt) <= budget_chars + len(_DROPPED_HISTORY_MARKER) + 2
    # The newest end of the range survives verbatim; the oldest is what drops.
    assert user_prompt.endswith("tail-3")
    assert "tail-0" not in user_prompt


@pytest.mark.asyncio
async def test_compact_summarize_leaves_small_conversations_uncapped(monkeypatch):
    captured = {}

    class CapturingClient:
        async def completion(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="summary"))],
            )

        async def close(self):
            return None

    monkeypatch.setattr("arden.core.compactor.create_completion_client", lambda _model: CapturingClient())

    messages = [
        {"role": Role.SYSTEM, "content": "system"},
        {"role": Role.USER, "content": "short question"},
        {"role": Role.ASSISTANT, "content": "short answer"},
        {"role": Role.ASSISTANT, "content": "kept"},
    ]

    await compact_summarize(messages, 1, 3, "gpt-5.2")

    user_prompt = captured["messages"][1]["content"]
    assert _DROPPED_HISTORY_MARKER not in user_prompt
    assert "short question" in user_prompt
