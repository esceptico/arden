from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from arden.agent import Message, ProviderToolCall, ProviderToolPayloadError, Role
from arden.agent.llm.parsing import normalize_assistant_message
from arden.config import Config
from arden.llm.anthropic import AnthropicClient
from arden.llm.history import ModelHistory
from arden.llm.models import get_model
from arden.server.routers.settings import _validate_reasoning_patch


def test_reasoning_effort_patch_rejects_unsupported_model_value():
    fields = {"reasoning_effort": "max"}
    config = Config(memory=False, chat_model="gpt-5.2")

    with pytest.raises(HTTPException):
        _validate_reasoning_patch(fields, config)


def test_reasoning_effort_patch_stores_value_for_target_model():
    fields = {"reasoning_effort": "high"}
    config = Config(memory=False, chat_model="gpt-5.2")

    _validate_reasoning_patch(fields, config)

    assert "reasoning_effort" not in fields
    assert fields["model_reasoning_efforts"] == {"gpt-5.2": "high"}


def test_reasoning_effort_patch_can_target_non_chat_model():
    fields = {"reasoning_model": "claude-opus-4-7", "reasoning_effort": "max"}
    config = Config(memory=False, chat_model="gpt-5.2")

    _validate_reasoning_patch(fields, config)

    assert "reasoning_model" not in fields
    assert "reasoning_effort" not in fields
    assert fields["model_reasoning_efforts"] == {"claude-opus-4-7": "max"}


def test_reasoning_effort_patch_preserves_per_model_value_when_chat_model_changes():
    fields = {"chat_model": "qwen/qwen3.5-27b"}
    config = Config(memory=False, chat_model="gpt-5.2", model_reasoning_efforts={"gpt-5.2": "high"})

    _validate_reasoning_patch(fields, config)

    assert "reasoning_effort" not in fields
    assert "model_reasoning_efforts" not in fields


def test_opus_4_7_exposes_adaptive_efforts():
    model = get_model("claude-opus-4-7")

    assert model.max_context_tokens == 1_000_000
    assert model.reasoning_efforts == ("low", "medium", "high", "xhigh", "max")


def test_opus_4_7_reasoning_uses_adaptive_thinking_and_output_effort():
    client = AnthropicClient(api_key="test")

    request = client._build_request(
        model="claude-opus-4-7",
        messages=[{"role": "user", "content": "hi"}],
        system=None,
        tools=None,
        tool_choice=None,
        temperature=0,
        max_tokens=128_000,
        reasoning_effort="xhigh",
    )

    assert request["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert request["output_config"] == {"effort": "xhigh"}
    assert "temperature" not in request


def test_anthropic_request_uses_native_deferred_tool_search():
    client = AnthropicClient(api_key="test")

    _, request = client._prepare(
        messages=[{"role": "user", "content": "search slack"}],
        model="claude-opus-4-7",
        tools=[
            {"type": "function", "function": {"name": "echo", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "tool_search", "parameters": {"type": "object"}}},
        ],
        deferred_tools=[
            {
                "type": "function",
                "function": {
                    "name": "slack_search",
                    "description": "Search Slack",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                },
            }
        ],
        tool_choice="auto",
        temperature=None,
        max_tokens=4096,
        reasoning_effort=None,
        response_format=None,
    )

    assert {tool["name"] for tool in request["tools"]} == {"echo", "tool_search"}
    assert request["extra_headers"] == {"anthropic-beta": "advanced-tool-use-2025-11-20"}


def test_anthropic_classic_loader_does_not_enable_tool_reference_protocol():
    _, request = AnthropicClient(api_key="test")._prepare(
        messages=[{"role": "user", "content": "search slack"}],
        model="claude-haiku-4-5",
        tools=[{"type": "function", "function": {"name": "load_tools", "parameters": {"type": "object"}}}],
        deferred_tools=[
            {"type": "function", "function": {"name": "slack_search", "parameters": {"type": "object"}}}
        ],
        tool_choice="auto",
        temperature=None,
        max_tokens=4096,
        reasoning_effort=None,
        response_format=None,
    )

    assert {tool["name"] for tool in request["tools"]} == {"load_tools"}
    assert "extra_headers" not in request


def test_anthropic_request_replays_tool_references_and_exposes_loaded_schema():
    client = AnthropicClient(api_key="test")

    _, request = client._prepare(
        messages=[
            {"role": "user", "content": "search slack"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "search_1",
                        "type": "function",
                        "function": {"name": "tool_search", "arguments": '{"query":"select:slack_search"}'},
                    }
                ],
                "anthropic_content": [
                    {
                        "type": "tool_use",
                        "id": "search_1",
                        "name": "tool_search",
                        "input": {"query": "select:slack_search"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "search_1",
                "content": "Loaded 1 deferred tool",
                "data": {"tool_references": ["slack_search"]},
            },
        ],
        model="claude-opus-4-7",
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "tool_search",
                    "description": "Search Tools",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "slack_search",
                    "description": "Search Slack",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                },
            },
        ],
        deferred_tools=[{"type": "function", "function": {"name": "slack_search", "parameters": {"type": "object"}}}],
        tool_choice="auto",
        temperature=None,
        max_tokens=4096,
        reasoning_effort=None,
        response_format=None,
    )

    assert next(tool for tool in request["tools"] if tool.get("name") == "slack_search")["defer_loading"] is True
    assert request["messages"][2]["content"][0]["content"] == [
        {"type": "tool_reference", "tool_name": "slack_search"}
    ]


def test_anthropic_deferred_tools_do_not_get_cache_control_breakpoints():
    client = AnthropicClient(api_key="test")

    _, request = client._prepare(
        messages=[{"role": "user", "content": "search slack"}],
        model="claude-opus-4-7",
        tools=[
            {"type": "function", "function": {"name": "echo", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "tool_search", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "slack_search", "parameters": {"type": "object"}}},
        ],
        deferred_tools=[{"type": "function", "function": {"name": "slack_search", "parameters": {"type": "object"}}}],
        tool_choice="auto",
        temperature=None,
        max_tokens=4096,
        reasoning_effort=None,
        response_format=None,
    )

    loader = next(tool for tool in request["tools"] if tool.get("name") == "tool_search")
    deferred = next(tool for tool in request["tools"] if tool.get("name") == "slack_search")
    assert loader["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in deferred


def test_anthropic_preserves_tool_search_blocks_for_next_request():
    client = AnthropicClient(api_key="test")
    blocks = [
        SimpleNamespace(
            type="server_tool_use",
            model_dump=lambda exclude_none=True: {
                "type": "server_tool_use",
                "id": "srvtoolu_1",
                "name": "tool_search_tool_bm25",
                "input": {"query": "slack"},
            },
        ),
        SimpleNamespace(
            type="tool_search_tool_result",
            model_dump=lambda exclude_none=True: {
                "type": "tool_search_tool_result",
                "tool_use_id": "srvtoolu_1",
                "content": {
                    "type": "tool_search_tool_search_result",
                    "tool_references": [{"type": "tool_reference", "tool_name": "slack_search"}],
                },
            },
        ),
        SimpleNamespace(
            type="tool_use",
            id="toolu_1",
            name="slack_search",
            input={"query": "hello"},
            model_dump=lambda exclude_none=True: {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "slack_search",
                "input": {"query": "hello"},
            },
        ),
    ]

    _, tool_calls, _, anthropic_content = client._parse_content_blocks(blocks, None)
    provider_tool_calls = client._parse_provider_tool_calls(anthropic_content)
    assistant = normalize_assistant_message(
        Message(
            role=Role.ASSISTANT,
            content=None,
            tool_calls=tool_calls,
            reasoning_content=None,
            reasoning_encrypted_content=None,
            anthropic_content=anthropic_content,
            provider_tool_calls=provider_tool_calls,
            openai_response_items=None,
        )
    )

    assert assistant["anthropic_content"][1]["type"] == "tool_search_tool_result"
    assert assistant["provider_tool_calls"][0]["name"] == "tool_search"
    assert assistant["provider_tool_calls"][0]["arguments"] == {
        "query": "slack",
        "tools": ["slack_search"],
    }
    assert assistant["provider_tool_calls"][0]["loaded_tool_names"] == ["slack_search"]
    assert assistant["provider_tool_calls"][0]["result"] == "Matched tools: slack_search"
    assert client._convert_messages(ModelHistory.from_raw([assistant]))[0]["content"] == anthropic_content


def test_anthropic_rejects_orphan_tool_search_result():
    client = AnthropicClient(api_key="test")

    with pytest.raises(ProviderToolPayloadError, match="no matching server_tool_use"):
        client._parse_provider_tool_calls(
            [
                {
                    "type": "tool_search_tool_result",
                    "tool_use_id": "srvtoolu_1",
                    "content": {"tool_references": []},
                }
            ]
        )


def test_anthropic_tool_error_exposes_structured_recovery_to_model():
    client = AnthropicClient(api_key="test")

    messages = client._convert_messages(
        ModelHistory.from_raw(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "toolu_1", "function": {"name": "wiki_edit_page", "arguments": "{}"}}],
                },
                {
                    "role": "tool",
                    "tool_call_id": "toolu_1",
                    "content": "Read required.",
                    "outcome": {
                        "status": "failed",
                        "error": {
                            "code": "fresh_read_required",
                            "retryable": False,
                            "recovery_action": "Read the page again.",
                        },
                    },
                },
            ]
        )
    )

    content = messages[1]["content"][0]["content"]
    assert "<tool_outcome>" in content
    assert "fresh_read_required" in content


class _AsyncAnthropicStream:
    def __init__(self, events, final_message):
        self._events = events
        self._final_message = final_message

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for event in self._events:
            yield event

    async def get_final_message(self):
        return self._final_message


class _AnthropicMessages:
    def __init__(self, events, final_message):
        self._events = events
        self._final_message = final_message

    def stream(self, **kwargs):
        return _AsyncAnthropicStream(self._events, self._final_message)


class _AnthropicClient:
    def __init__(self, events, final_message):
        self.messages = _AnthropicMessages(events, final_message)


@pytest.mark.asyncio
async def test_anthropic_stream_emits_tool_search_before_loaded_tool_call():
    final_message = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="server_tool_use",
                model_dump=lambda exclude_none=True: {
                    "type": "server_tool_use",
                    "id": "srvtoolu_1",
                    "name": "tool_search_tool_bm25",
                    "input": {"query": "slack"},
                },
            ),
            SimpleNamespace(
                type="tool_search_tool_result",
                model_dump=lambda exclude_none=True: {
                    "type": "tool_search_tool_result",
                    "tool_use_id": "srvtoolu_1",
                    "content": {
                        "type": "tool_search_tool_search_result",
                        "tool_references": [{"type": "tool_reference", "tool_name": "slack_search"}],
                    },
                },
            ),
            SimpleNamespace(
                type="tool_use",
                id="toolu_1",
                name="slack_search",
                input={"query": "hello"},
                model_dump=lambda exclude_none=True: {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "slack_search",
                    "input": {"query": "hello"},
                },
            ),
        ],
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
        stop_reason="tool_use",
    )
    events = [
        SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block=SimpleNamespace(
                type="server_tool_use",
                model_dump=lambda exclude_none=True: {
                    "type": "server_tool_use",
                    "id": "srvtoolu_1",
                    "name": "tool_search_tool_bm25",
                    "input": {},
                },
            ),
        ),
        SimpleNamespace(
            type="content_block_start",
            index=1,
            content_block=SimpleNamespace(
                type="tool_search_tool_result",
                model_dump=lambda exclude_none=True: {
                    "type": "tool_search_tool_result",
                    "tool_use_id": "srvtoolu_1",
                    "content": {
                        "type": "tool_search_tool_search_result",
                        "tool_references": [{"type": "tool_reference", "tool_name": "slack_search"}],
                    },
                },
            ),
        ),
        SimpleNamespace(type="content_block_stop", index=1),
    ]
    client = AnthropicClient(api_key="test")
    client._client = _AnthropicClient(events, final_message)

    items = [
        item
        async for item in client._stream_completion(
            messages=[{"role": "user", "content": "search slack"}],
            model="claude-sonnet-4-6",
            tools=[],
            deferred_tools=[],
            tool_choice="auto",
        )
    ]

    assert isinstance(items[0], ProviderToolCall)
    assert items[0].id == "srvtoolu_1"
    assert items[0].done is False
    assert isinstance(items[1], ProviderToolCall)
    assert items[1].result == "Matched tools: slack_search"


def test_roles_sharing_a_model_no_longer_share_its_effort():
    """Each configured role keeps its own reasoning effort."""
    config = Config(
        memory=False,
        chat_model="gpt-5.2",
        model_roles={
            "research": {"model": "gpt-5.2", "reasoning_effort": "high"},
            "workflow": {"model": "gpt-5.2"},
            "memory": {"model": "gpt-5.2"},
            "auxiliary": {"model": "gpt-5.2", "reasoning_effort": "medium"},
        },
        model_reasoning_efforts={"gpt-5.2": "low"},
    )

    assert config.reasoning_effort_for_role("research", "gpt-5.2") == "high"
    # The other two keep following the model until they are given their own.
    assert config.reasoning_effort_for_role("workflow", "gpt-5.2") == "low"
    assert config.reasoning_effort_for_role("memory", "gpt-5.2") == "low"
    assert config.reasoning_effort_for_role("auxiliary", "gpt-5.2") == "medium"
    # And chat is untouched by any of it.
    assert config.reasoning_effort_for("gpt-5.2") == "low"


def test_role_effort_falls_back_when_unsupported_by_its_model():
    config = Config(
        memory=False,
        chat_model="gpt-5.2",
        model_roles={"research": {"model": "gpt-5.2", "reasoning_effort": "not-a-real-effort"}},
        model_reasoning_efforts={"gpt-5.2": "low"},
    )

    assert config.reasoning_effort_for_role("research", "gpt-5.2") == "low"


def test_role_patch_is_validated_against_its_own_role_model():
    config = Config(memory=False, chat_model="gpt-5.2", model_roles={"research": {"model": "gpt-5.2"}})

    fields = {"model_roles": {"research": {"reasoning_effort": "max"}}}
    with pytest.raises(HTTPException):
        _validate_reasoning_patch(fields, config)

    fields = {"model_roles": {"research": {"reasoning_effort": "high"}}}
    _validate_reasoning_patch(fields, config)
    # The patch merges onto stored setup: research keeps its model and the other
    # roles come through untouched rather than being blanked.
    assert fields["model_roles"]["research"] == {"model": "gpt-5.2", "reasoning_effort": "high"}
    assert fields["model_roles"]["workflow"]["reasoning_effort"] is None
    assert fields["model_roles"]["memory"]["model"] == "gpt-5.2"
    assert fields["model_roles"]["auxiliary"]["model"] == "gpt-5.2"


def test_unknown_role_in_a_patch_is_rejected():
    config = Config(memory=False, chat_model="gpt-5.2")
    with pytest.raises(HTTPException):
        _validate_reasoning_patch({"model_roles": {"nonsense": {"model": "gpt-5.2"}}}, config)


def test_roles_unset_still_fall_back_to_the_chat_model():
    config = Config(memory=False, chat_model="gpt-5.2")

    assert config.research_model == "gpt-5.2"
    assert config.workflow_model == "gpt-5.2"
    assert config.memory_model == "gpt-5.2"
    assert config.auxiliary_model == "gpt-5.2"


def test_auxiliary_model_starts_from_memory_but_remains_independent():
    inherited = Config(
        memory=False,
        chat_model="gpt-5.2",
        model_roles={"memory": {"model": "claude-sonnet-4-6"}},
    )
    explicit = Config(
        memory=False,
        chat_model="gpt-5.2",
        model_roles={
            "memory": {"model": "claude-sonnet-4-6"},
            "auxiliary": {"model": "gpt-5.2"},
        },
    )

    assert inherited.auxiliary_model == "claude-sonnet-4-6"
    assert explicit.auxiliary_model == "gpt-5.2"
