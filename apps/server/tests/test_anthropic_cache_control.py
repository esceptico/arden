"""Anthropic cache anchors are per-request: replayed history must never carry
stale cache_control (the API allows 4 total; accumulation 400s the request),
and injection must not mutate the stored session blocks."""

from arden.llm.anthropic import AnthropicClient
from arden.llm.history import ModelHistory


def _assistant(text: str) -> dict:
    return {
        "role": "assistant",
        "content": text,
        "anthropic_content": [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}],
    }


def test_replayed_history_strips_stale_cache_anchors():
    client = AnthropicClient(api_key="test")
    messages = [_assistant("one"), {"role": "user", "content": "hi"}, _assistant("two")]

    converted = client._convert_messages(ModelHistory.from_raw(messages))

    anchors = [
        block
        for msg in converted
        if isinstance(msg["content"], list)
        for block in msg["content"]
        if isinstance(block, dict) and "cache_control" in block
    ]
    assert anchors == []


def test_injection_never_mutates_stored_history():
    client = AnthropicClient(api_key="test")
    stored = _assistant("final")
    del stored["anthropic_content"][0]["cache_control"]  # clean stored block

    converted = client._convert_messages(ModelHistory.from_raw([stored]))
    client._inject_cache_control_last_message(converted)

    assert "cache_control" in converted[-1]["content"][-1]
    assert "cache_control" not in stored["anthropic_content"][0]  # session history untouched


def test_system_prompt_cache_breakpoints_survive_typed_projection():
    client = AnthropicClient(api_key="test")
    system_blocks = [
        {"type": "text", "text": "stable", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "dynamic"},
    ]

    _model, request = client._prepare(
        messages=[{"role": "system", "content": system_blocks}, {"role": "user", "content": "hello"}],
        model="claude-sonnet-4-6",
        tools=None,
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        reasoning_effort=None,
        response_format=None,
    )

    assert request["system"] == system_blocks
