from types import SimpleNamespace

import httpx
import pytest

from arden.agent.llm.parsing import normalize_assistant_message
from arden.agent.types.llm import ProviderToolCall, ToolCallStreamDelta
from arden.llm.openai import OpenAIClient
from arden.llm.openai_responses import (
    buffered_stream_responses_completion,
    complete_responses_completion,
    parse_responses_response,
    stream_responses_completion,
)


def test_native_openai_request_includes_prompt_cache_key():
    client = OpenAIClient(api_key="test")

    request = client._prepare(
        messages=[{"role": "user", "content": "hi"}],
        model="gpt-5.2",
        tools=None,
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        reasoning_effort=None,
        response_format=None,
        prompt_cache_key="session-1",
    )

    assert request["prompt_cache_key"] == "session-1"


def test_openai_compatible_request_omits_prompt_cache_key():
    client = OpenAIClient(api_key="test", base_url="https://example.test", native_openai=False)

    request = client._prepare(
        messages=[{"role": "user", "content": "hi"}],
        model="custom-model",
        tools=None,
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        reasoning_effort=None,
        response_format=None,
        prompt_cache_key="session-1",
    )

    assert "prompt_cache_key" not in request


def test_openai_tool_error_exposes_structured_recovery_to_model():
    client = OpenAIClient(api_key="test", base_url="https://example.test", native_openai=False)
    request = client._prepare(
        messages=[
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "Read this wiki page before changing it.",
                "outcome": {
                    "status": "failed",
                    "error": {
                        "code": "fresh_read_required",
                        "retryable": False,
                        "recovery_action": "Read the page again, then retry the change.",
                    },
                },
            }
        ],
        model="custom-model",
        tools=None,
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        reasoning_effort=None,
        response_format=None,
    )

    content = request["messages"][0]["content"]
    assert "<tool_outcome>" in content
    assert "fresh_read_required" in content
    assert "recovery_action" in content
    assert "outcome" not in request["messages"][0]


def test_chat_completions_request_formats_image_blocks_as_data_urls():
    client = OpenAIClient(api_key="test")

    request = client._prepare(
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "inspect"},
                    {"type": "image", "media_type": "image/png", "data": "iVBORw0KGgo="},
                ],
            }
        ],
        model="gpt-5.2",
        tools=None,
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        reasoning_effort=None,
        response_format=None,
    )

    assert request["messages"][0]["content"] == [
        {"type": "text", "text": "inspect"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}},
    ]


def test_chat_completions_request_strips_internal_tool_result_data():
    client = OpenAIClient(api_key="test")

    request = client._prepare(
        messages=[
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "Started background agent.",
                "data": {"child_agent": {"child_run_id": "child-run-1"}},
            }
        ],
        model="gpt-5.2",
        tools=None,
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        reasoning_effort=None,
        response_format=None,
    )

    assert request["messages"] == [{"role": "tool", "tool_call_id": "call_1", "content": "Started background agent."}]


def test_chat_completions_request_strips_openai_response_items():
    client = OpenAIClient(api_key="test")

    request = client._prepare(
        messages=[
            {
                "role": "assistant",
                "content": "done",
                "openai_response_items": [{"type": "message", "content": []}],
                "background_result_ref": "bg-1",
            }
        ],
        model="gpt-5.2",
        tools=None,
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        reasoning_effort=None,
        response_format=None,
    )

    assert request["messages"] == [{"role": "assistant", "content": "done"}]


def test_responses_request_formats_image_blocks_as_input_images():
    client = OpenAIClient(api_key="test")

    request = client._prepare_responses(
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "inspect"},
                    {"type": "image", "media_type": "image/jpeg", "data": "/9j/4A=="},
                ],
            }
        ],
        model="gpt-5.5",
        tools=[{"type": "function", "function": {"name": "Search", "parameters": {"type": "object"}}}],
        tool_choice="auto",
        temperature=None,
        max_tokens=None,
        reasoning_effort="high",
        response_format=None,
    )

    assert request["input"][0]["content"] == [
        {"type": "input_text", "text": "inspect"},
        {"type": "input_image", "detail": "auto", "image_url": "data:image/jpeg;base64,/9j/4A=="},
    ]


def test_responses_request_uses_native_deferred_tool_search():
    client = OpenAIClient(api_key="test")

    request = client._prepare_responses(
        messages=[{"role": "user", "content": "search slack"}],
        model="gpt-5.5",
        tools=[
            {"type": "function", "function": {"name": "load_tools", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "echo", "parameters": {"type": "object"}}},
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
        max_tokens=None,
        reasoning_effort="high",
        response_format=None,
    )

    assert {"type": "tool_search"} in request["tools"]
    deferred = next(tool for tool in request["tools"] if tool.get("name") == "slack_search")
    assert deferred["defer_loading"] is True


def test_responses_request_omits_function_loader_with_native_deferred_tools():
    client = OpenAIClient(api_key="test")

    request = client._prepare_responses(
        messages=[{"role": "user", "content": "search slack"}],
        model="gpt-5.5",
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "tool_search",
                    "description": "Search Tools",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                },
            }
        ],
        deferred_tools=[{"type": "function", "function": {"name": "slack_search", "parameters": {"type": "object"}}}],
        tool_choice="auto",
        temperature=None,
        max_tokens=None,
        reasoning_effort="high",
        response_format=None,
    )

    assert not any(tool.get("name") == "tool_search" for tool in request["tools"])
    assert {"type": "tool_search"} in request["tools"]
    assert next(tool for tool in request["tools"] if tool.get("name") == "slack_search")["defer_loading"] is True


def test_responses_deferred_tool_search_preserves_prompt_cache_key():
    client = OpenAIClient(api_key="test")

    request = client._prepare_responses(
        messages=[{"role": "user", "content": "search slack"}],
        model="gpt-5.5",
        tools=[{"type": "function", "function": {"name": "echo", "parameters": {"type": "object"}}}],
        deferred_tools=[{"type": "function", "function": {"name": "slack_search", "parameters": {"type": "object"}}}],
        tool_choice="auto",
        temperature=None,
        max_tokens=None,
        reasoning_effort="high",
        response_format=None,
        prompt_cache_key="session-1",
    )

    assert request["prompt_cache_key"] == "session-1"
    assert {"type": "tool_search"} in request["tools"]


def test_responses_legacy_tool_search_receipt_does_not_emit_orphan_call():
    client = OpenAIClient(api_key="test")

    request = client._prepare_responses(
        messages=[
            {"role": "user", "content": "read email"},
            {
                "role": "assistant",
                "content": "",
                "provider_tool_calls": [
                    {
                        "id": "tsc_1",
                        "name": "tool_search",
                        "arguments": '{"tools":["emails"]}',
                        "result": "Matched tools: emails",
                    }
                ],
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "emails", "arguments": '{"days":1}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "20 emails"},
        ],
        model="gpt-5.5",
        tools=[{"type": "function", "function": {"name": "current_time", "parameters": {"type": "object"}}}],
        deferred_tools=[{"type": "function", "function": {"name": "emails", "parameters": {"type": "object"}}}],
        tool_choice="auto",
        temperature=None,
        max_tokens=None,
        reasoning_effort="high",
        response_format=None,
    )

    replay = request["input"][1:]
    assert [item["type"] for item in replay] == ["function_call", "function_call_output"]
    assert replay[0]["name"] == "emails"


def test_responses_legacy_stored_tool_search_item_does_not_emit_orphan_call():
    client = OpenAIClient(api_key="test")

    request = client._prepare_responses(
        messages=[
            {"role": "user", "content": "read email"},
            {
                "role": "assistant",
                "content": "",
                "provider_tool_calls": [
                    {
                        "id": "tsc_1",
                        "name": "tool_search",
                        "arguments": '{"tools":["emails"]}',
                        "result": "Matched tools: emails",
                        "provider_item": {"type": "tool_search_call", "id": "tsc_1", "status": "completed"},
                    }
                ],
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "emails", "arguments": '{"days":1}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "20 emails"},
        ],
        model="gpt-5.5",
        tools=[{"type": "function", "function": {"name": "current_time", "parameters": {"type": "object"}}}],
        deferred_tools=[{"type": "function", "function": {"name": "emails", "parameters": {"type": "object"}}}],
        tool_choice="auto",
        temperature=None,
        max_tokens=None,
        reasoning_effort="high",
        response_format=None,
    )

    assert [item["type"] for item in request["input"][1:]] == ["function_call", "function_call_output"]
    assert request["input"][1]["name"] == "emails"


def test_responses_request_skips_native_deferred_tool_search_for_unsupported_model():
    client = OpenAIClient(api_key="test")

    request = client._prepare_responses(
        messages=[{"role": "user", "content": "search slack"}],
        model="gpt-5.2",
        tools=[{"type": "function", "function": {"name": "load_tools", "parameters": {"type": "object"}}}],
        deferred_tools=[{"type": "function", "function": {"name": "slack_search", "parameters": {"type": "object"}}}],
        tool_choice="auto",
        temperature=None,
        max_tokens=None,
        reasoning_effort="high",
        response_format=None,
    )

    assert {"type": "tool_search"} not in request["tools"]
    assert all(tool.get("name") != "slack_search" for tool in request["tools"])


class _FakeItem:
    def __init__(self, data: dict):
        self._data = data

    def model_dump(self, exclude_none: bool = True) -> dict:
        return self._data


class _InputDetails:
    cached_tokens = 0


class _Usage:
    input_tokens = 12
    output_tokens = 3
    input_tokens_details = _InputDetails()


class _Response:
    status = "completed"
    usage = _Usage()

    output = [
        _FakeItem(
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "ok"}],
            }
        )
    ]


class _Event:
    def __init__(self, data: dict, response=None):
        self._data = data
        self.response = response

    def model_dump(self, exclude_none: bool = True) -> dict:
        return self._data


class _Stream:
    def __init__(self, events: list[_Event | BaseException]):
        self._events = iter(events)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            event = next(self._events)
        except StopIteration:
            raise StopAsyncIteration
        if isinstance(event, BaseException):
            raise event
        return event


class _FakeResponses:
    def __init__(self):
        self.requests: list[dict] = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        if kwargs.get("stream"):
            return _Stream(
                [
                    _Event({"type": "response.output_text.delta", "delta": "ok"}),
                    _Event({"type": "response.completed"}, response=_Response()),
                ]
            )
        return _Response()


class _FlakyAfterDeltaResponses:
    def __init__(self):
        self.calls = 0
        self.requests: list[dict] = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        self.calls += 1
        if self.calls == 1:
            return _Stream(
                [
                    _Event({"type": "response.output_text.delta", "delta": "partial"}),
                    httpx.RemoteProtocolError("peer closed connection without sending complete message body"),
                ]
            )
        return _Stream(
            [
                _Event({"type": "response.output_text.delta", "delta": "ok"}),
                _Event({"type": "response.completed"}, response=_Response()),
            ]
        )


class _FlakyAfterDeltaOpenAI:
    def __init__(self):
        self.responses = _FlakyAfterDeltaResponses()


class _FakeChatCompletions:
    async def create(self, **kwargs):
        raise AssertionError("chat completions should not be used")


class _FakeOpenAI:
    def __init__(self):
        self.responses = _FakeResponses()
        self.chat = type("Chat", (), {"completions": _FakeChatCompletions()})()


class _DisconnectingResponses:
    async def create(self, **kwargs):
        return _Stream(
            [
                _Event({"type": "response.output_text.delta", "delta": "partial"}),
                httpx.RemoteProtocolError("peer closed connection without sending complete message body"),
            ]
        )


class _DisconnectingResponsesOpenAI:
    def __init__(self):
        self.responses = _DisconnectingResponses()


class _FailingResponses:
    async def create(self, **kwargs):
        return _Stream(
            [
                _Event(
                    {
                        "type": "response.failed",
                        "response": {
                            "error": {
                                "type": "invalid_request_error",
                                "code": "context_length_exceeded",
                                "message": "Your input exceeds the context window of this model.",
                            }
                        },
                    }
                ),
            ]
        )


class _FailingResponsesOpenAI:
    def __init__(self):
        self.responses = _FailingResponses()


class _ChatDelta:
    content = "partial"
    tool_calls = None


class _ChatChoice:
    finish_reason = None
    delta = _ChatDelta()


class _ChatChunk:
    usage = None
    choices = [_ChatChoice()]


class _DisconnectingChatCompletions:
    async def create(self, **kwargs):
        return _Stream(
            [
                _ChatChunk(),
                httpx.RemoteProtocolError("peer closed connection without sending complete message body"),
            ]
        )


class _DisconnectingChatOpenAI:
    def __init__(self):
        self.chat = type("Chat", (), {"completions": _DisconnectingChatCompletions()})()


@pytest.mark.asyncio
async def test_native_openai_tools_with_reasoning_use_responses_for_completion():
    client = OpenAIClient(api_key="test")
    fake = _FakeOpenAI()
    client._client = fake

    response = await client._completion(
        messages=[{"role": "user", "content": "search"}],
        model="gpt-5.5",
        tools=[{"type": "function", "function": {"name": "Search", "parameters": {"type": "object"}}}],
        tool_choice="auto",
        reasoning_effort="high",
    )

    request = fake.responses.requests[0]
    assert request["model"] == "gpt-5.5"
    assert request["store"] is False
    assert request["reasoning"] == {"effort": "high", "summary": "auto"}
    assert request["tools"][0]["name"] == "Search"
    assert response.choices[0].message.content == "ok"


@pytest.mark.asyncio
async def test_native_openai_tools_with_reasoning_use_responses_for_streaming():
    client = OpenAIClient(api_key="test")
    fake = _FakeOpenAI()
    client._client = fake

    events = [
        item
        async for item in client._stream_completion(
            messages=[{"role": "user", "content": "search"}],
            model="gpt-5.5",
            tools=[{"type": "function", "function": {"name": "Search", "parameters": {"type": "object"}}}],
            tool_choice="auto",
            reasoning_effort="high",
        )
    ]

    request = fake.responses.requests[0]
    assert "stream" not in request
    assert request["reasoning"] == {"effort": "high", "summary": "auto"}
    assert events[0] == "ok"
    assert events[-1].choices[0].message.content == "ok"


@pytest.mark.asyncio
async def test_completed_responses_call_emits_text_before_final_response():
    fake = _FakeOpenAI()

    events = [
        item
        async for item in complete_responses_completion(
            fake,
            {"model": "gpt-5.5", "input": "hi"},
            model="gpt-5.5",
        )
    ]

    assert fake.responses.requests[0] == {"model": "gpt-5.5", "input": "hi"}
    assert events[0] == "ok"
    assert events[-1].choices[0].message.content == "ok"


@pytest.mark.asyncio
async def test_buffered_responses_stream_retries_disconnect_after_upstream_delta():
    fake = _FlakyAfterDeltaOpenAI()

    events = [
        item
        async for item in buffered_stream_responses_completion(
            fake,
            {"model": "gpt-5.5", "input": "hi"},
            model="gpt-5.5",
        )
    ]

    assert fake.responses.calls == 2
    assert fake.responses.requests[0]["stream"] is True
    assert events[0] == "ok"
    assert events[-1].choices[0].message.content == "ok"


@pytest.mark.asyncio
async def test_live_responses_stream_reports_remote_protocol_disconnect():
    fake = _DisconnectingResponsesOpenAI()
    stream = stream_responses_completion(
        fake,
        {"model": "gpt-5.5", "input": "hi"},
        model="gpt-5.5",
    )

    assert await anext(stream) == "partial"
    with pytest.raises(RuntimeError, match="OpenAI response stream disconnected before completion"):
        await anext(stream)


@pytest.mark.asyncio
async def test_live_responses_stream_preserves_provider_error_code():
    from arden.services.chat import _safe_error

    stream = stream_responses_completion(
        _FailingResponsesOpenAI(),
        {"model": "gpt-5.5", "input": "hi"},
        model="gpt-5.5",
    )

    with pytest.raises(RuntimeError) as exc_info:
        await anext(stream)

    code, message, _debug_id = _safe_error(exc_info.value)
    assert code == "context_length_exceeded"
    assert "context window" in message


@pytest.mark.asyncio
async def test_chat_completion_stream_reports_remote_protocol_disconnect():
    client = OpenAIClient(api_key="test")
    client._client = _DisconnectingChatOpenAI()
    stream = client._stream_completion(
        messages=[{"role": "user", "content": "hi"}],
        model="gpt-5.2",
    )

    assert await anext(stream) == "partial"
    with pytest.raises(RuntimeError, match="OpenAI chat completion stream disconnected before completion"):
        await anext(stream)


class _ToolStreamChatCompletions:
    async def create(self, **kwargs):
        def chunk(*, finish_reason=None, tool_calls=None):
            return SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        finish_reason=finish_reason,
                        delta=SimpleNamespace(content=None, reasoning_content=None, tool_calls=tool_calls),
                    )
                ],
            )

        def tool_call(*, index=0, call_id=None, name=None, arguments=None):
            return SimpleNamespace(
                index=index,
                id=call_id,
                function=SimpleNamespace(name=name, arguments=arguments),
            )

        return _Stream(
            [
                chunk(tool_calls=[tool_call(call_id="call_1", name="Search")]),
                chunk(tool_calls=[tool_call(arguments='{"q"')]),
                chunk(finish_reason="tool_calls", tool_calls=[tool_call(arguments=':"hi"}')]),
            ]
        )


class _ToolStreamChatOpenAI:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_ToolStreamChatCompletions())


@pytest.mark.asyncio
async def test_chat_completion_stream_emits_tool_call_deltas_before_final_response():
    client = OpenAIClient(api_key="test")
    client._client = _ToolStreamChatOpenAI()

    events = [
        item
        async for item in client._stream_completion(
            messages=[{"role": "user", "content": "search"}],
            model="gpt-5.2",
            tools=[{"type": "function", "function": {"name": "Search", "parameters": {"type": "object"}}}],
            tool_choice="auto",
        )
    ]

    deltas = [item for item in events if isinstance(item, ToolCallStreamDelta)]
    assert deltas == [
        ToolCallStreamDelta(index=0, tool_id="call_1", name="Search"),
        ToolCallStreamDelta(index=0, arguments_delta='{"q"'),
        ToolCallStreamDelta(index=0, arguments_delta=':"hi"}'),
        ToolCallStreamDelta(index=0, tool_id="call_1", name="Search", done=True),
    ]
    assert events[-1].choices[0].message.tool_calls[0].function.arguments == '{"q":"hi"}'


class _ToolResponse:
    status = "completed"
    usage = _Usage()
    output = [
        _FakeItem(
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "Search",
                "arguments": '{"q":"hi"}',
            }
        )
    ]


def test_responses_parser_preserves_provider_tool_search_call():
    response = SimpleNamespace(
        status="completed",
        usage=_Usage(),
        output=[
            _FakeItem(
                {
                    "type": "tool_search_call",
                    "id": "tsc_1",
                    "query": "slack",
                    "results": [{"name": "slack_search"}],
                }
            )
        ],
    )

    parsed = parse_responses_response(response, "gpt-5.5")
    provider_calls = parsed.choices[0].message.provider_tool_calls

    assert provider_calls is not None
    assert provider_calls[0].id == "tsc_1"
    assert provider_calls[0].name == "tool_search"
    assert provider_calls[0].arguments == '{"tools": ["slack_search"]}'
    assert provider_calls[0].result == "Matched tools: slack_search"
    assert provider_calls[0].provider_item == {
        "type": "tool_search_call",
        "id": "tsc_1",
        "query": "slack",
        "status": "completed",
    }
    assert parsed.choices[0].message.openai_response_items == [
        {
            "type": "tool_search_call",
            "id": "tsc_1",
            "query": "slack",
            "results": [{"name": "slack_search"}],
        }
    ]


def test_responses_replays_exact_ordered_output_without_duplicates():
    output = [
        {
            "type": "tool_search_call",
            "id": "tsc_1",
            "execution": "server",
            "status": "completed",
            "arguments": {"paths": ["slack"]},
        },
        {
            "type": "tool_search_output",
            "execution": "server",
            "status": "completed",
            "tools": [{"type": "function", "name": "slack_search", "description": "Search Slack"}],
        },
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "slack_search",
            "arguments": '{"query":"release"}',
            "status": "completed",
        },
    ]
    response = SimpleNamespace(
        status="completed",
        usage=_Usage(),
        output=[_FakeItem(item) for item in output],
    )

    parsed = parse_responses_response(response, "gpt-5.5")
    message = parsed.choices[0].message
    normalized = normalize_assistant_message(message)
    client = OpenAIClient(api_key="test")
    request = client._prepare_responses(
        messages=[
            {"role": "user", "content": "search slack"},
            normalized,
            {"role": "tool", "tool_call_id": "call_1", "content": "found it"},
        ],
        model="gpt-5.5",
        tools=[],
        deferred_tools=[{"type": "function", "function": {"name": "slack_search", "parameters": {"type": "object"}}}],
        tool_choice="auto",
        temperature=None,
        max_tokens=None,
        reasoning_effort="high",
        response_format=None,
    )

    assert normalized["openai_response_items"] == output
    assert request["input"][1:4] == output
    assert request["input"][4]["type"] == "function_call_output"
    assert [item.get("type") for item in request["input"]].count("tool_search_call") == 1
    assert [item.get("type") for item in request["input"]].count("tool_search_output") == 1
    assert normalized["provider_tool_calls"][0]["arguments"] == '{"tools": ["slack_search"]}'
    assert normalized["provider_tool_calls"][0]["result"] == "Matched tools: slack_search"


def test_responses_normalizes_provider_tool_search_replay_item():
    message = SimpleNamespace(
        content=None,
        tool_calls=None,
        reasoning_content=None,
        reasoning_encrypted_content=None,
        anthropic_content=None,
        provider_tool_calls=[
            ProviderToolCall(
                id="tsc_1",
                name="tool_search",
                arguments='{"query":"slack"}',
                result="Matched tools: slack_search",
                provider_item={"type": "tool_search_call", "id": "tsc_1", "status": "completed"},
            )
        ],
    )

    normalized = normalize_assistant_message(message)

    assert normalized["provider_tool_calls"][0]["provider_item"] == {
        "type": "tool_search_call",
        "id": "tsc_1",
        "status": "completed",
    }


def test_responses_parser_infers_tool_search_matches_from_function_calls():
    response = SimpleNamespace(
        status="completed",
        usage=_Usage(),
        output=[
            _FakeItem({"type": "tool_search_call", "id": "tsc_1", "status": "completed"}),
            _FakeItem(
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "slack_search",
                    "arguments": '{"query":"after:2026-06-28"}',
                }
            ),
        ],
    )

    parsed = parse_responses_response(response, "gpt-5.5")
    provider_call = parsed.choices[0].message.provider_tool_calls[0]

    assert provider_call.arguments == '{"tools": ["slack_search"]}'
    assert provider_call.result == "Matched tools: slack_search"


class _ToolResponses:
    async def create(self, **kwargs):
        return _Stream(
            [
                _Event(
                    {
                        "type": "response.output_item.added",
                        "output_index": 0,
                        "item": {"type": "function_call", "call_id": "call_1", "name": "Search", "arguments": ""},
                    }
                ),
                _Event({"type": "response.function_call_arguments.delta", "output_index": 0, "delta": '{"q"'}),
                _Event({"type": "response.function_call_arguments.delta", "output_index": 0, "delta": ':"hi"}'}),
                _Event(
                    {
                        "type": "response.output_item.done",
                        "output_index": 0,
                        "item": {
                            "type": "function_call",
                            "call_id": "call_1",
                            "name": "Search",
                            "arguments": '{"q":"hi"}',
                        },
                    }
                ),
                _Event({"type": "response.completed"}, response=_ToolResponse()),
            ]
        )


class _ToolResponsesOpenAI:
    def __init__(self):
        self.responses = _ToolResponses()


@pytest.mark.asyncio
async def test_responses_stream_emits_tool_call_deltas_before_final_response():
    events = [
        item
        async for item in stream_responses_completion(
            _ToolResponsesOpenAI(),
            {"model": "gpt-5.5", "input": "search"},
            model="gpt-5.5",
        )
    ]

    deltas = [item for item in events if isinstance(item, ToolCallStreamDelta)]
    assert deltas == [
        ToolCallStreamDelta(index=0, tool_id="call_1", name="Search"),
        ToolCallStreamDelta(index=0, arguments_delta='{"q"'),
        ToolCallStreamDelta(index=0, arguments_delta=':"hi"}'),
        ToolCallStreamDelta(index=0, tool_id="call_1", name="Search", done=True),
    ]
    assert events[-1].choices[0].message.tool_calls[0].function.arguments == '{"q":"hi"}'


class _ToolSearchThenFunctionResponses:
    async def create(self, **kwargs):
        response = SimpleNamespace(
            status="completed",
            usage=_Usage(),
            output=[
                _FakeItem({"type": "tool_search_call", "id": "tsc_1", "status": "completed"}),
                _FakeItem(
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "Search",
                        "arguments": '{"q":"hi"}',
                    }
                ),
            ],
        )
        return _Stream(
            [
                _Event(
                    {
                        "type": "response.output_item.added",
                        "output_index": 0,
                        "item": {"type": "tool_search_call", "id": "tsc_1", "status": "in_progress"},
                    }
                ),
                _Event(
                    {
                        "type": "response.output_item.added",
                        "output_index": 1,
                        "item": {"type": "function_call", "call_id": "call_1", "name": "Search", "arguments": ""},
                    }
                ),
                _Event({"type": "response.completed"}, response=response),
            ]
        )


class _ToolSearchThenFunctionOpenAI:
    def __init__(self):
        self.responses = _ToolSearchThenFunctionResponses()


@pytest.mark.asyncio
async def test_responses_stream_emits_tool_search_before_loaded_tool_call():
    events = [
        item
        async for item in stream_responses_completion(
            _ToolSearchThenFunctionOpenAI(),
            {"model": "gpt-5.5", "input": "search"},
            model="gpt-5.5",
        )
    ]

    assert isinstance(events[0], ProviderToolCall)
    assert events[0].id == "tsc_1"
    assert isinstance(events[1], ToolCallStreamDelta)
    assert events[1].tool_id == "call_1"
