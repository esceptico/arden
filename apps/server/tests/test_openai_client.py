import base64
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest

from arden.agent.llm.parsing import normalize_assistant_message
from arden.agent.types.llm import Message, ProviderToolCall, ProviderToolPayloadError, Role, ToolCallStreamDelta
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
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_1", "function": {"name": "wiki_read_page", "arguments": "{}"}}],
            },
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
            },
        ],
        model="custom-model",
        tools=None,
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        reasoning_effort=None,
        response_format=None,
    )

    content = request["messages"][1]["content"]
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
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_1", "function": {"name": "research", "arguments": "{}"}}],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "Started background agent.",
                "data": {"child_agent": {"child_run_id": "child-run-1"}},
            },
        ],
        model="gpt-5.2",
        tools=None,
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        reasoning_effort=None,
        response_format=None,
    )

    assert request["messages"][1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "Started background agent.",
    }


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


def test_chat_completions_projects_foreign_provider_history_without_mutation():
    client = OpenAIClient(api_key="test")
    messages = [
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "provider reasoning",
            "reasoning_encrypted_content": "responses-only-state",
            "anthropic_content": [{"type": "thinking", "signature": "anthropic-state"}],
            "provider_tool_calls": [
                {
                    "id": "tsc_1",
                    "name": "tool_search",
                    "arguments": {},
                    "result": "",
                    "done": True,
                    "loaded_tool_names": [],
                }
            ],
            "openai_response_items": [{"type": "reasoning", "encrypted_content": "opaque"}],
            "metadata": {"provider": "foreign"},
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "thought_signature": base64.b64encode(b"gemini-state").decode(),
                    "function": {
                        "name": "Search",
                        "arguments": '{"q":"arden"}',
                        "provider_state": "opaque",
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "found it",
            "data": {"raw": "internal"},
            "background_result_ref": "bg-1",
        },
    ]
    original = deepcopy(messages)

    request = client._prepare(
        messages=messages,
        model="gpt-5.2",
        tools=None,
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        reasoning_effort=None,
        response_format=None,
    )

    assert request["messages"] == [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "Search", "arguments": '{"q":"arden"}'},
                }
            ],
        },
        {"role": "tool", "content": "found it", "tool_call_id": "call_1"},
    ]
    assert messages == original


def test_responses_request_retains_encrypted_reasoning_replay():
    client = OpenAIClient(api_key="test")
    messages = [
        {
            "role": "assistant",
            "content": "",
            "reasoning_encrypted_content": "responses-only-state",
        }
    ]
    original = deepcopy(messages)

    request = client._prepare_responses(
        messages=messages,
        model="gpt-5.5",
        tools=None,
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        reasoning_effort="high",
        response_format=None,
    )

    assert request["input"] == [{"type": "reasoning", "encrypted_content": "responses-only-state", "summary": []}]
    assert messages == original


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

    search = next(tool for tool in request["tools"] if tool.get("type") == "tool_search")
    assert search["execution"] == "client"
    assert search["parameters"]["required"] == ["query"]
    assert not any(tool.get("name") == "slack_search" for tool in request["tools"])


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
    assert next(tool for tool in request["tools"] if tool.get("type") == "tool_search")["execution"] == "client"
    assert not any(tool.get("name") == "slack_search" for tool in request["tools"])


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
    assert next(tool for tool in request["tools"] if tool.get("type") == "tool_search")["execution"] == "client"


def test_responses_client_tool_search_returns_schema_and_replays_it():
    catalog = [
        {
            "type": "function",
            "function": {
                "name": "wiki_create_page",
                "description": "Create a managed wiki page",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        }
    ]
    response = SimpleNamespace(
        status="completed",
        usage=_Usage(),
        output=[
            _FakeItem(
                {
                    "type": "tool_search_call",
                    "id": "tsc_1",
                    "call_id": "search_1",
                    "execution": "client",
                    "status": "completed",
                    "arguments": {"query": "select:wiki_create_page"},
                }
            )
        ],
    )

    parsed = parse_responses_response(response, "gpt-5.5", client_tool_catalog=catalog)
    message = parsed.choices[0].message
    assert message.provider_tool_calls[0].loaded_tool_names == ("wiki_create_page",)
    assert message.openai_response_items[-1] == {
        "type": "tool_search_output",
        "call_id": "search_1",
        "status": "completed",
        "execution": "client",
        "tools": [
            {
                "type": "function",
                "name": "wiki_create_page",
                "description": "Create a managed wiki page",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                "strict": False,
                "defer_loading": True,
            }
        ],
    }

    normalized = normalize_assistant_message(message)
    request = OpenAIClient(api_key="test")._prepare_responses(
        messages=[{"role": "user", "content": "create it"}, normalized],
        model="gpt-5.5",
        tools=[catalog[0]],
        deferred_tools=catalog,
        tool_choice="auto",
        temperature=None,
        max_tokens=None,
        reasoning_effort="high",
        response_format=None,
    )
    assert request["input"][-1]["type"] == "tool_search_output"
    assert request["input"][-1]["tools"][0]["name"] == "wiki_create_page"
    assert not any(tool.get("name") == "wiki_create_page" for tool in request["tools"])


def test_responses_keeps_run_loaded_tool_callable_after_compaction():
    schema = {
        "type": "function",
        "function": {
            "name": "wiki_create_page",
            "description": "Create a managed wiki page",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    }

    request = OpenAIClient(api_key="test")._prepare_responses(
        messages=[{"role": "system", "content": "compacted"}, {"role": "user", "content": "continue"}],
        model="gpt-5.5",
        tools=[schema],
        deferred_tools=[schema],
        tool_choice="auto",
        temperature=None,
        max_tokens=None,
        reasoning_effort="high",
        response_format=None,
    )

    assert next(tool for tool in request["tools"] if tool.get("name") == "wiki_create_page")["type"] == "function"


def test_responses_stored_tool_search_envelope_does_not_emit_orphan_call():
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
                        "arguments": {"tools": ["emails"]},
                        "result": "Matched tools: emails",
                        "done": True,
                        "loaded_tool_names": ["emails"],
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
    assert request["reasoning"] == {"effort": "high", "summary": "detailed"}
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
    assert request["reasoning"] == {"effort": "high", "summary": "detailed"}
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
                    "status": "completed",
                    "arguments": {"paths": ["slack"]},
                }
            ),
            _FakeItem(
                {
                    "type": "tool_search_output",
                    "status": "completed",
                    "tools": [{"type": "function", "name": "slack_search"}],
                }
            ),
        ],
    )

    parsed = parse_responses_response(response, "gpt-5.5")
    provider_calls = parsed.choices[0].message.provider_tool_calls

    assert provider_calls is not None
    assert provider_calls[0].id == "tsc_1"
    assert provider_calls[0].name == "tool_search"
    assert provider_calls[0].arguments_dict() == {"paths": ["slack"], "tools": ["slack_search"]}
    assert provider_calls[0].result == "Matched tools: slack_search"
    assert provider_calls[0].loaded_tool_names == ("slack_search",)
    assert parsed.choices[0].message.openai_response_items == [
        {
            "type": "tool_search_call",
            "id": "tsc_1",
            "status": "completed",
            "arguments": {"paths": ["slack"]},
        },
        {
            "type": "tool_search_output",
            "status": "completed",
            "tools": [{"type": "function", "name": "slack_search"}],
        },
    ]


def test_responses_parser_pairs_each_tool_search_output_with_its_call():
    response = SimpleNamespace(
        status="completed",
        usage=_Usage(),
        output=[
            _FakeItem(
                {
                    "type": "tool_search_call",
                    "id": "tsc_1",
                    "call_id": "search_1",
                    "status": "completed",
                    "arguments": {"paths": ["slack"]},
                }
            ),
            _FakeItem(
                {
                    "type": "tool_search_call",
                    "id": "tsc_2",
                    "call_id": "search_2",
                    "status": "completed",
                    "arguments": {"paths": ["email"]},
                }
            ),
            _FakeItem(
                {
                    "type": "tool_search_output",
                    "call_id": "search_2",
                    "status": "completed",
                    "tools": [{"type": "function", "name": "email_search"}],
                }
            ),
            _FakeItem(
                {
                    "type": "tool_search_output",
                    "call_id": "search_1",
                    "status": "completed",
                    "tools": [{"type": "function", "name": "slack_search"}],
                }
            ),
        ],
    )

    parsed = parse_responses_response(response, "gpt-5.5")
    provider_calls = parsed.choices[0].message.provider_tool_calls

    assert provider_calls is not None
    assert [(call.id, call.loaded_tool_names) for call in provider_calls] == [
        ("tsc_1", ("slack_search",)),
        ("tsc_2", ("email_search",)),
    ]


def test_responses_parser_pairs_ordered_tool_searches_without_call_ids():
    response = SimpleNamespace(
        status="completed",
        usage=_Usage(),
        output=[
            _FakeItem(
                {
                    "type": "tool_search_call",
                    "id": f"tsc_{index}",
                    "status": "completed",
                    "arguments": {"paths": [path]},
                }
            )
            for index, path in ((1, "wiki_list_pages"), (2, "wiki_create_page"))
        ]
        + [
            _FakeItem(
                {
                    "type": "tool_search_output",
                    "status": "completed",
                    "tools": [{"type": "function", "name": name}],
                }
            )
            for name in ("wiki_list_pages", "wiki_create_page")
        ],
    )

    parsed = parse_responses_response(response, "gpt-5.5")

    calls = parsed.choices[0].message.provider_tool_calls
    assert calls is not None
    assert [(call.id, call.loaded_tool_names) for call in calls] == [
        ("tsc_1", ("wiki_list_pages",)),
        ("tsc_2", ("wiki_create_page",)),
    ]


def test_responses_parser_rejects_partial_tool_search_call_ids():
    response = SimpleNamespace(
        status="completed",
        usage=_Usage(),
        output=[
            _FakeItem(
                {
                    "type": "tool_search_call",
                    "id": "tsc_1",
                    "call_id": "search_1",
                    "status": "completed",
                    "arguments": {},
                }
            ),
            _FakeItem(
                {
                    "type": "tool_search_call",
                    "id": "tsc_2",
                    "status": "completed",
                    "arguments": {},
                }
            ),
            _FakeItem(
                {
                    "type": "tool_search_output",
                    "call_id": "search_1",
                    "status": "completed",
                    "tools": [],
                }
            ),
            _FakeItem({"type": "tool_search_output", "status": "completed", "tools": []}),
        ],
    )

    with pytest.raises(ProviderToolPayloadError, match="either omit call_id from every"):
        parse_responses_response(response, "gpt-5.5")


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
    assert normalized["provider_tool_calls"][0]["arguments"] == {
        "paths": ["slack"],
        "tools": ["slack_search"],
    }
    assert normalized["provider_tool_calls"][0]["result"] == "Matched tools: slack_search"


def test_responses_normalizes_provider_tool_search_envelope():
    message = Message(
        role=Role.ASSISTANT,
        content=None,
        tool_calls=None,
        reasoning_content=None,
        reasoning_encrypted_content=None,
        anthropic_content=None,
        provider_tool_calls=[
            ProviderToolCall(
                id="tsc_1",
                name="tool_search",
                arguments={"query": "slack"},
                result="Matched tools: slack_search",
                loaded_tool_names=("slack_search",),
            )
        ],
        openai_response_items=None,
    )

    normalized = normalize_assistant_message(message)

    assert normalized["provider_tool_calls"][0] == {
        "id": "tsc_1",
        "name": "tool_search",
        "arguments": {"query": "slack"},
        "result": "Matched tools: slack_search",
        "done": True,
        "loaded_tool_names": ["slack_search"],
    }


def test_responses_parser_requires_tool_search_output():
    response = SimpleNamespace(
        status="completed",
        usage=_Usage(),
        output=[
            _FakeItem(
                {
                    "type": "tool_search_call",
                    "id": "tsc_1",
                    "status": "completed",
                    "arguments": {"paths": ["slack_search"]},
                }
            ),
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

    with pytest.raises(ProviderToolPayloadError, match="without tool_search_output"):
        parse_responses_response(response, "gpt-5.5")


@pytest.mark.parametrize(
    "item",
    [
        {"type": "tool_search_call", "status": "completed"},
        {"type": "tool_search_call", "id": "tsc_1", "status": "completed", "arguments": "{bad"},
        {"type": "tool_search_call", "id": "tsc_1", "status": "unknown", "arguments": {}},
    ],
)
def test_responses_rejects_invalid_successful_tool_search_payload(item):
    response = SimpleNamespace(status="completed", usage=_Usage(), output=[_FakeItem(item)])

    with pytest.raises(ProviderToolPayloadError):
        parse_responses_response(response, "gpt-5.5")


@pytest.mark.parametrize("status", [None, "in_progress", "completed"])
def test_responses_uses_completed_response_boundary_for_tool_search(status):
    call = {
        "type": "tool_search_call",
        "id": "tsc_1",
        "arguments": {"paths": ["wiki_create_page"]},
    }
    if status is not None:
        call["status"] = status
    response = SimpleNamespace(
        status="completed",
        usage=_Usage(),
        output=[
            _FakeItem(call),
            _FakeItem(
                {
                    "type": "tool_search_output",
                    "status": "completed",
                    "tools": [{"type": "function", "name": "wiki_create_page"}],
                }
            ),
        ],
    )

    parsed = parse_responses_response(response, "gpt-5.5")

    provider_calls = parsed.choices[0].message.provider_tool_calls
    assert provider_calls is not None
    assert provider_calls[0].loaded_tool_names == ("wiki_create_page",)


def test_responses_rejects_invalid_tool_search_output():
    response = SimpleNamespace(
        status="completed",
        usage=_Usage(),
        output=[
            _FakeItem(
                {
                    "type": "tool_search_call",
                    "id": "tsc_1",
                    "status": "completed",
                    "arguments": {},
                }
            ),
            _FakeItem({"type": "tool_search_output", "status": "completed", "tools": [{"name": ""}]}),
        ],
    )

    with pytest.raises(ProviderToolPayloadError, match="name must be a non-empty string"):
        parse_responses_response(response, "gpt-5.5")


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
                _FakeItem(
                    {
                        "type": "tool_search_call",
                        "id": "tsc_1",
                        "status": "completed",
                        "arguments": {"paths": ["Search"]},
                    }
                ),
                _FakeItem(
                    {
                        "type": "tool_search_output",
                        "status": "completed",
                        "tools": [{"type": "function", "name": "Search"}],
                    }
                ),
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
                        "item": {"type": "tool_search_call", "id": "tsc_1", "status": "completed"},
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


class _ClientToolSearchResponses:
    async def create(self, **kwargs):
        call = {
            "type": "tool_search_call",
            "id": "tsc_client",
            "call_id": "search_client",
            "execution": "client",
            "status": "completed",
            "arguments": {"query": "select:wiki_create_page"},
        }
        response = SimpleNamespace(status="completed", usage=_Usage(), output=[_FakeItem(call)])
        return _Stream(
            [
                _Event(
                    {
                        "type": "response.output_item.added",
                        "output_index": 0,
                        "item": {**call, "status": "in_progress"},
                    }
                ),
                _Event({"type": "response.completed"}, response=response),
            ]
        )


class _ClientToolSearchOpenAI:
    def __init__(self):
        self.responses = _ClientToolSearchResponses()


@pytest.mark.asyncio
async def test_responses_stream_executes_client_tool_search_locally():
    catalog = [
        {
            "type": "function",
            "function": {"name": "wiki_create_page", "parameters": {"type": "object"}},
        }
    ]
    events = [
        item
        async for item in stream_responses_completion(
            _ClientToolSearchOpenAI(),
            {"model": "gpt-5.5", "input": "search"},
            model="gpt-5.5",
            deferred_tools=catalog,
        )
    ]

    assert isinstance(events[0], ProviderToolCall)
    final = events[-1].choices[0].message
    assert final.provider_tool_calls[0].loaded_tool_names == ("wiki_create_page",)
    assert final.openai_response_items[-1]["type"] == "tool_search_output"
