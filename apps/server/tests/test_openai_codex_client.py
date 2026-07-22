from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from arden.agent import FinishReason, Role
from arden.llm.openai_codex import OpenAICodexClient
from arden.llm.openai_codex_auth import OpenAICodexTokens


class _Structured(BaseModel):
    ok: bool


class _FakeItem:
    def __init__(self, data: dict):
        self._data = data

    def model_dump(self, exclude_none: bool = True) -> dict:
        return self._data


class _InputDetails:
    cached_tokens = 7


class _Usage:
    input_tokens = 20
    output_tokens = 5
    input_tokens_details = _InputDetails()


class _Response:
    status = "completed"
    usage = _Usage()

    output = [
        _FakeItem(
            {
                "type": "reasoning",
                "encrypted_content": "encrypted",
                "content": [{"type": "reasoning_text", "text": "thinking"}],
            }
        ),
        _FakeItem(
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "hello"}],
            }
        ),
        _FakeItem(
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "Read",
                "arguments": '{"path":"README.md"}',
            }
        ),
    ]


class _EmptyResponse:
    status = "completed"
    usage = _Usage()
    output = []


class _FakeResponses:
    def __init__(self):
        self.requests: list[dict] = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        if not kwargs.get("stream"):
            raise AssertionError("OpenAI Codex backend requires stream=True")
        return _Stream(
            [
                _Event({"type": "response.output_text.delta", "delta": "he"}),
                _Event({"type": "response.output_text.delta", "delta": "llo"}),
                _Event({"type": "response.output_item.done", "item": _Response.output[0].model_dump()}),
                _Event({"type": "response.output_item.done", "item": _Response.output[1].model_dump()}),
                _Event({"type": "response.output_item.done", "item": _Response.output[2].model_dump()}),
                _Event({"type": "response.completed"}, response=_EmptyResponse()),
            ]
        )


class _FakeOpenAI:
    def __init__(self):
        self.responses = _FakeResponses()
        self.closed = False

    async def close(self):
        self.closed = True


class _DoneOnlyResponses:
    def __init__(self):
        self.requests: list[dict] = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        return _Stream(
            [
                _Event({"type": "response.output_text.done", "text": "hello"}),
                _Event({"type": "response.completed"}, response=_EmptyResponse()),
            ]
        )


class _DoneOnlyOpenAI(_FakeOpenAI):
    def __init__(self):
        self.responses = _DoneOnlyResponses()
        self.closed = False


class _Event:
    def __init__(self, data: dict, response=None):
        self._data = data
        self.response = response

    def model_dump(self, exclude_none: bool = True) -> dict:
        return self._data


class _Stream:
    def __init__(self, events: list[_Event]):
        self._events = iter(events)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._events)
        except StopIteration:
            raise StopAsyncIteration


def test_prepare_responses_request_uses_codex_model_and_response_shapes():
    client = OpenAICodexClient()

    request = client._prepare(
        messages=[
            {"role": Role.SYSTEM, "content": "system"},
            {"role": Role.USER, "content": [{"type": "text", "text": "hi"}]},
            {
                "role": Role.ASSISTANT,
                "content": "",
                "reasoning_encrypted_content": "encrypted-prev",
                "tool_calls": [{"id": "call_1", "function": {"name": "Read", "arguments": "{}"}}],
            },
            {"role": Role.TOOL, "tool_call_id": "call_1", "content": "done"},
        ],
        model="openai-codex/gpt-5.4",
        tools=[{"type": "function", "function": {"name": "Read", "parameters": {"type": "object"}}}],
        tool_choice={"type": "function", "function": {"name": "Read"}},
        temperature=0.3,
        max_tokens=100,
        reasoning_effort="high",
        response_format=_Structured,
        prompt_cache_key="session-1",
    )

    assert request["model"] == "gpt-5.4"
    assert request["store"] is False
    assert "temperature" not in request
    assert "max_output_tokens" not in request
    assert request["instructions"] == "system"
    assert request["input"][0]["content"] == [{"type": "input_text", "text": "hi"}]
    assert request["input"][1] == {"type": "reasoning", "encrypted_content": "encrypted-prev", "summary": []}
    assert request["input"][2] == {
        "type": "function_call",
        "call_id": "call_1",
        "name": "Read",
        "arguments": "{}",
        "status": "completed",
    }
    assert request["input"][3] == {"type": "function_call_output", "call_id": "call_1", "output": "done"}
    assert request["tools"] == [
        {
            "type": "function",
            "name": "Read",
            "description": "",
            "parameters": {"type": "object"},
            "strict": False,
        }
    ]
    assert request["tool_choice"] == {"type": "function", "name": "Read"}
    assert request["reasoning"] == {"effort": "high", "summary": "auto"}
    assert request["text"]["format"]["name"] == "_Structured"
    assert request["prompt_cache_key"] == "session-1"


@pytest.mark.asyncio
async def test_client_advertises_compatible_codex_version(monkeypatch):
    monkeypatch.setattr(
        "arden.llm.openai_codex.get_valid_tokens",
        AsyncMock(return_value=OpenAICodexTokens("access", "refresh", 123, "account")),
    )

    client = await OpenAICodexClient()._client()

    assert client.default_headers["originator"] == "codex_cli_rs"
    assert client.default_headers["User-Agent"].startswith("codex_cli_rs/0.144.0 ")
    await client.close()


def test_prepare_responses_request_adds_required_default_instructions():
    request = OpenAICodexClient()._prepare(
        messages=[{"role": Role.USER, "content": "return json"}],
        model="openai-codex/gpt-5.5",
        tools=None,
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        reasoning_effort=None,
        response_format=_Structured,
    )

    assert request["instructions"]
    assert request["input"] == [{"role": "user", "content": "return json"}]


def test_parse_response_collects_text_reasoning_tools_and_usage():
    parsed = OpenAICodexClient()._parse_response(_Response(), "openai-codex/gpt-5.4")

    choice = parsed.choices[0]
    assert choice.finish_reason == FinishReason.TOOL_CALLS
    assert choice.message.content == "hello"
    assert choice.message.reasoning_content == "thinking"
    assert choice.message.reasoning_encrypted_content == "encrypted"
    assert choice.message.tool_calls is not None
    assert choice.message.tool_calls[0].id == "call_1"
    assert choice.message.tool_calls[0].function.name == "Read"
    assert parsed.usage.prompt_tokens == 13
    assert parsed.usage.cache_read_tokens == 7
    assert parsed.usage.completion_tokens == 5


def test_parse_response_accepts_stream_completed_items():
    parsed = OpenAICodexClient()._parse_response(
        _EmptyResponse(),
        "openai-codex/gpt-5.4-mini",
        [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "ok"}],
            },
            {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "used state"}],
                "encrypted_content": "encrypted-stream",
            },
            {
                "type": "function_call",
                "call_id": "call_2",
                "name": "Read",
                "arguments": '{"path":"README.md"}',
            },
        ],
    )

    choice = parsed.choices[0]
    assert choice.message.content == "ok"
    assert choice.message.reasoning_content == "used state"
    assert choice.message.reasoning_encrypted_content == "encrypted-stream"
    assert choice.message.tool_calls is not None
    assert choice.message.tool_calls[0].id == "call_2"
    assert choice.finish_reason == FinishReason.TOOL_CALLS


@pytest.mark.asyncio
async def test_completion_buffers_required_streaming_response(monkeypatch):
    fake = _FakeOpenAI()

    async def fake_client(self):
        return fake

    monkeypatch.setattr(OpenAICodexClient, "_client", fake_client)

    parsed = await OpenAICodexClient()._completion(
        messages=[{"role": Role.USER, "content": "hi"}],
        model="openai-codex/gpt-5.5",
    )

    request = fake.responses.requests[0]
    assert request["model"] == "gpt-5.5"
    assert request["stream"] is True
    assert parsed.model == "openai-codex/gpt-5.5"
    assert parsed.choices[0].message.content == "hello"
    assert parsed.choices[0].message.reasoning_content == "thinking"
    assert fake.closed is True


@pytest.mark.asyncio
async def test_codex_stream_yields_live_text_deltas_until_completion(monkeypatch):
    fake = _FakeOpenAI()

    async def fake_client(self):
        return fake

    monkeypatch.setattr(OpenAICodexClient, "_client", fake_client)

    events = [
        event
        async for event in OpenAICodexClient()._stream_completion(
            messages=[{"role": Role.USER, "content": "hi"}],
            model="openai-codex/gpt-5.5",
        )
    ]

    request = fake.responses.requests[0]
    assert request["model"] == "gpt-5.5"
    assert request["stream"] is True
    assert events[0] == "he"
    assert events[1] == "llo"
    assert events[-1].choices[0].message.content == "hello"
    assert events[-1].choices[0].message.reasoning_content == "thinking"
    assert fake.closed is True


@pytest.mark.asyncio
async def test_codex_stream_yields_done_text_when_delta_events_are_absent(monkeypatch):
    fake = _DoneOnlyOpenAI()

    async def fake_client(self):
        return fake

    monkeypatch.setattr(OpenAICodexClient, "_client", fake_client)

    events = [
        event
        async for event in OpenAICodexClient()._stream_completion(
            messages=[{"role": Role.USER, "content": "hi"}],
            model="openai-codex/gpt-5.5",
        )
    ]

    assert events[0] == "hello"
    assert events[-1].choices[0].message.content == "hello"
    assert fake.closed is True
