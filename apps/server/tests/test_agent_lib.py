"""Standalone tests for the arden.agent library. No arden imports beyond arden.agent.*"""

import asyncio
import json
from collections.abc import AsyncGenerator
from dataclasses import replace
from typing import Any

import pytest

from arden.agent import (
    Agent,
    AgentHooks,
    Choice,
    CompletionResponse,
    FinishReason,
    FunctionCall,
    Message,
    ProviderResponseError,
    ProviderToolCall,
    Result,
    Role,
    RunBudget,
    SharedLedger,
    StopReason,
    TextBlock,
    TextDelta,
    TextEnded,
    TextStarted,
    ToolCall,
    ToolCallStreamDelta,
    ToolChoiceMode,
    ToolCompleted,
    ToolInputDelta,
    ToolInputEnded,
    ToolInputStarted,
    ToolMeta,
    ToolOutcomeStatus,
    ToolResult,
    Usage,
)

# ============================================================
# Test doubles — pure, zero arden dependencies
# ============================================================


def _response(
    text: str | None = None,
    tool_calls: list[ToolCall] | None = None,
    usage: Usage | None = None,
    provider_tool_calls: list[ProviderToolCall] | None = None,
) -> CompletionResponse:
    return CompletionResponse(
        choices=[
            Choice(
                message=Message(
                    role=Role.ASSISTANT,
                    content=text,
                    tool_calls=tool_calls,
                    reasoning_content=None,
                    provider_tool_calls=provider_tool_calls,
                ),
                finish_reason=FinishReason.STOP,
            )
        ],
        usage=usage or Usage(prompt_tokens=10, completion_tokens=5),
        model="test",
    )


def _tc(tool_id: str, name: str, args: dict) -> ToolCall:
    return ToolCall(id=tool_id, type="function", function=FunctionCall(name=name, arguments=json.dumps(args)))


class FakeLLM:
    """Queue-based LLM: yields responses in order, raises if exhausted."""

    def __init__(self, responses: list[CompletionResponse | Exception]):
        self._queue = list(responses)
        self.call_count = 0
        self.last_messages: list[dict] | None = None
        self.last_tool_choice = None

    async def stream(
        self,
        messages,
        model,
        tools,
        tool_choice=None,
        reasoning_effort=None,
    ) -> AsyncGenerator:
        self.call_count += 1
        self.last_messages = list(messages)
        self.last_tool_choice = tool_choice
        if not self._queue:
            raise RuntimeError("FakeLLM exhausted")
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if item.choices[0].message.content:
            yield item.choices[0].message.content
        yield item

    async def complete(self, model, messages, **kwargs):
        self.call_count += 1
        self.last_messages = list(messages)
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeExecutor:
    """In-memory tool executor with configurable results and metadata."""

    def __init__(self, handlers: dict[str, Any], meta: dict[str, ToolMeta] | None = None):
        self._handlers = handlers
        self._meta = meta or {}
        self.call_log: list[tuple[str, dict]] = []

    async def execute(self, name: str, args: dict, tool_call_id: str) -> ToolResult:
        self.call_log.append((name, args))
        handler = self._handlers.get(name)
        if handler is None:
            return ToolResult(content=f"unknown: {name}", preview="unknown", is_error=True)
        if isinstance(handler, Exception):
            raise handler
        if callable(handler):
            return handler(args)
        return handler

    def get_meta(self, name: str) -> ToolMeta | None:
        return self._meta.get(
            name,
            ToolMeta(name=name, display_name=name),
        )

    def mark_provider_loaded_tools(self, names: set[str]) -> None:
        del names


def _msgs(user: str = "hi") -> list[dict]:
    return [{"role": "system", "content": "sys"}, {"role": "user", "content": user}]


def _make_agent(llm: FakeLLM, executor: FakeExecutor, **kwargs) -> Agent:
    return Agent(
        tools=[{"type": "function", "function": {"name": "test"}}],
        client=llm,
        executor=executor,
        model="test",
        **kwargs,
    )


# ============================================================
# Basic loop
# ============================================================


@pytest.mark.asyncio
async def test_streamed_tool_input_surfaces_before_tool_execution():
    class StreamingToolLLM:
        def __init__(self):
            self.call_count = 0

        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None) -> AsyncGenerator:
            self.call_count += 1
            if self.call_count == 1:
                yield ToolCallStreamDelta(index=0, tool_id="c1", name="test")
                yield ToolCallStreamDelta(index=0, arguments_delta='{"x"')
                yield ToolCallStreamDelta(index=0, arguments_delta=":1}")
                yield ToolCallStreamDelta(index=0, tool_id="c1", name="test", done=True)
                yield _response(tool_calls=[_tc("c1", "test", {"x": 1})])
                return
            yield "done"
            yield _response(text="done")

        async def complete(self, model, messages, **kwargs):
            raise AssertionError("complete should not be called")

    executor = FakeExecutor(
        {"test": ToolResult(content="ok", preview="ok")},
        meta={"test": ToolMeta(name="test", display_name="Research", kind="agent")},
    )
    agent = _make_agent(StreamingToolLLM(), executor)

    stream = agent.stream(_msgs())

    event = await anext(stream)
    assert isinstance(event, ToolInputStarted)
    assert event.tool_id == "c1"
    assert event.name == "test"
    assert event.display_name == "Research"
    assert event.kind == "agent"
    assert executor.call_log == []

    event = await anext(stream)
    assert isinstance(event, ToolInputDelta)
    assert event.delta == '{"x"'
    assert executor.call_log == []

    event = await anext(stream)
    assert isinstance(event, ToolInputDelta)
    assert event.delta == ":1}"
    assert executor.call_log == []

    event = await anext(stream)
    assert isinstance(event, ToolInputEnded)
    assert executor.call_log == []

    rest = [item async for item in stream]
    assert executor.call_log == [("test", {"x": 1})]
    assert any(isinstance(item, ToolCompleted) for item in rest)
    assert not any(item.__class__.__name__ == "ToolStarted" and item.tool_id == "c1" for item in rest)


@pytest.mark.asyncio
async def test_simple_text_response_returns_result():
    llm = FakeLLM([_response(text="hello")])
    agent = _make_agent(llm, FakeExecutor({}))
    result = await agent.run(_msgs())
    assert result.text == "hello"
    assert result.stop_reason == StopReason.END_TURN
    assert result.steps == 0


@pytest.mark.asyncio
async def test_resume_incomplete_tool_step_reuses_completed_call_and_executes_created_call():
    calls = [_tc("c1", "test", {"x": 1}), _tc("c2", "test", {"x": 2})]
    messages = [
        *_msgs(),
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.function.name, "arguments": call.function.arguments},
                }
                for call in calls
            ],
        },
    ]

    async def recover(pending_calls):
        assert [call.tool_call.id for call in pending_calls] == ["c1", "c2"]
        return {"c1": ToolResult(content="durable one", preview="one")}

    llm = FakeLLM([_response(text="done")])
    executor = FakeExecutor({"test": ToolResult(content="executed two", preview="two")})
    agent = _make_agent(llm, executor, hooks=AgentHooks(recover_tool_calls=recover))

    result = await agent.run(messages)

    assert result.text == "done"
    assert executor.call_log == [("test", {"x": 2})]
    assert [(message["tool_call_id"], message["content"]) for message in messages if message["role"] == "tool"] == [
        ("c1", "durable one"),
        ("c2", "executed two"),
    ]


@pytest.mark.asyncio
async def test_resume_recovered_terminal_tool_ends_without_another_model_turn():
    call = _tc("c1", "submit", {})
    messages = [
        *_msgs(),
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.function.name, "arguments": call.function.arguments},
                }
            ],
        },
    ]

    async def recover(_pending_calls):
        return {"c1": ToolResult(content="Recovered report.", preview="Report recorded")}

    llm = FakeLLM([])
    executor = FakeExecutor(
        {},
        meta={"submit": ToolMeta(name="submit", display_name="Submit", ends_turn=True)},
    )
    agent = _make_agent(llm, executor, hooks=AgentHooks(recover_tool_calls=recover))

    result = await agent.run(messages)

    assert result.text == "Recovered report."
    assert result.steps == 1
    assert llm.call_count == 0
    assert executor.call_log == []
    assert [(message["tool_call_id"], message["content"]) for message in messages if message["role"] == "tool"] == [
        ("c1", "Recovered report."),
    ]


@pytest.mark.asyncio
async def test_resume_rejected_terminal_batch_does_not_consume_retry_budget():
    calls = [_tc("c1", "write", {}), _tc("c2", "submit", {})]
    messages = [
        *_msgs(),
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.function.name, "arguments": call.function.arguments},
                }
                for call in calls
            ],
        },
    ]
    llm = FakeLLM([_response(tool_calls=[_tc("c3", "submit", {})])])
    executor = FakeExecutor(
        {
            "write": ToolResult(content="changed", preview="Changed"),
            "submit": ToolResult(content="Report recorded.", preview="Report recorded"),
        },
        meta={
            "write": ToolMeta(name="write", display_name="Write", changes_state=True),
            "submit": ToolMeta(name="submit", display_name="Submit", ends_turn=True),
        },
    )
    agent = _make_agent(llm, executor, max_tool_calls=1)

    result = await agent.run(messages)

    assert result.text == "Report recorded."
    assert executor.call_log == [("submit", {})]


@pytest.mark.asyncio
async def test_resume_incomplete_tool_step_keeps_existing_partial_result_without_duplication():
    calls = [_tc("c1", "test", {"x": 1}), _tc("c2", "test", {"x": 2})]
    messages = [
        *_msgs(),
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.function.name, "arguments": call.function.arguments},
                }
                for call in calls
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "already saved"},
    ]

    async def recover(pending_calls):
        assert [call.tool_call.id for call in pending_calls] == ["c2"]
        return {}

    llm = FakeLLM([_response(text="done")])
    executor = FakeExecutor({"test": ToolResult(content="executed two", preview="two")})
    agent = _make_agent(llm, executor, hooks=AgentHooks(recover_tool_calls=recover))

    await agent.run(messages)

    assert executor.call_log == [("test", {"x": 2})]
    assert [message["tool_call_id"] for message in messages if message["role"] == "tool"] == ["c1", "c2"]


@pytest.mark.asyncio
async def test_provider_tool_call_renders_without_executor_dispatch():
    llm = FakeLLM(
        [
            _response(
                provider_tool_calls=[
                    ProviderToolCall(
                        id="tsc_1",
                        name="tool_search",
                        arguments={"query": "slack"},
                        result='[{"name":"slack_search"}]',
                    )
                ],
                text="done",
            )
        ]
    )
    executor = FakeExecutor({})
    agent = _make_agent(llm, executor)

    events = [e async for e in agent.stream(_msgs())]
    started = next(e for e in events if e.__class__.__name__ == "ToolStarted")
    completed = next(e for e in events if isinstance(e, ToolCompleted))

    assert started.tool_id == "tsc_1"
    assert started.name == "tool_search"
    assert started.display_name == "Search Tools"
    assert completed.result == '[{"name":"slack_search"}]'
    assert completed.outcome is not None
    assert completed.outcome.status == ToolOutcomeStatus.SUCCEEDED
    assert executor.call_log == []


def test_provider_tool_call_freezes_nested_arguments():
    source = {"query": {"paths": ["slack"]}}
    call = ProviderToolCall(id="tsc_1", name="tool_search", arguments=source)

    source["query"]["paths"].append("email")

    assert call.arguments_dict() == {"query": {"paths": ["slack"]}}
    with pytest.raises(TypeError):
        call.arguments["query"] = "changed"


@pytest.mark.asyncio
async def test_streamed_provider_tool_call_keeps_trace_order_before_loaded_tool():
    class StreamingProviderToolLLM:
        def __init__(self):
            self.call_count = 0

        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None) -> AsyncGenerator:
            self.call_count += 1
            if self.call_count > 1:
                yield _response(text="done")
                return
            yield ProviderToolCall(id="tsc_1", name="tool_search", arguments={}, done=False)
            yield ProviderToolCall(id="tsc_1", name="tool_search", arguments={"paths": ["test"]}, done=True)
            yield ToolCallStreamDelta(index=0, tool_id="call_1", name="test")
            yield ToolCallStreamDelta(index=0, arguments_delta='{"x":1}')
            yield ToolCallStreamDelta(index=0, tool_id="call_1", name="test", done=True)
            yield _response(
                provider_tool_calls=[
                    ProviderToolCall(
                        id="tsc_1",
                        name="tool_search",
                        arguments={"tools": ["test"]},
                        result="Matched tools: test",
                        loaded_tool_names=("test",),
                    )
                ],
                tool_calls=[_tc("call_1", "test", {"x": 1})],
            )

        async def complete(self, model, messages, **kwargs):
            raise AssertionError("complete should not be called")

    executor = FakeExecutor({"test": ToolResult(content="ok", preview="ok")})
    agent = _make_agent(StreamingProviderToolLLM(), executor)

    events = [e async for e in agent.stream(_msgs())]
    started_names = [e.name for e in events if e.__class__.__name__ in {"ToolStarted", "ToolInputStarted"}]
    completed = [e for e in events if isinstance(e, ToolCompleted)]
    provider_completed = [event for event in completed if event.tool_id == "tsc_1"]

    assert started_names[:2] == ["tool_search", "test"]
    assert len(provider_completed) == 1
    assert provider_completed[0].result == "Matched tools: test"


@pytest.mark.asyncio
async def test_stream_yields_text_deltas_then_result():
    llm = FakeLLM([_response(text="answer")])
    agent = _make_agent(llm, FakeExecutor({}))
    events = [e async for e in agent.stream(_msgs())]
    assert any(isinstance(e, TextDelta) and e.content == "answer" for e in events)
    assert isinstance(events[-1], Result)
    assert events[-1].text == "answer"


@pytest.mark.asyncio
async def test_call_llm_closes_started_text_when_stream_raises():
    class FailingAfterTextLLM:
        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None):
            yield "partial"
            raise RuntimeError("boom")

        async def complete(self, *args, **kwargs):
            raise NotImplementedError

    errors = []

    async def on_error(exc):
        errors.append(exc)

    agent = _make_agent(FailingAfterTextLLM(), FakeExecutor({}), hooks=AgentHooks(on_error=on_error))
    events = []

    with pytest.raises(RuntimeError, match="boom"):
        async for event in agent._call_llm(_msgs(), "test", [], ToolChoiceMode.AUTO, None):
            events.append(event)

    assert [type(event) for event in events] == [TextStarted, TextDelta, TextEnded]
    assert events[1].content == "partial"
    assert events[2].content == "partial"
    assert len(errors) == 1
    assert str(errors[0]) == "boom"


@pytest.mark.asyncio
async def test_stream_closes_started_text_when_completion_response_is_missing():
    class MissingResponseAfterTextLLM:
        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None):
            yield "partial"

        async def complete(self, *args, **kwargs):
            raise NotImplementedError

    agent = _make_agent(MissingResponseAfterTextLLM(), FakeExecutor({}))
    events = []

    with pytest.raises(RuntimeError, match="LLM stream ended without a CompletionResponse"):
        async for event in agent.stream(_msgs()):
            events.append(event)

    assert [type(event) for event in events] == [TextStarted, TextDelta, TextEnded]
    assert events[1].content == "partial"
    assert events[2].content == "partial"


@pytest.mark.asyncio
async def test_call_llm_closes_started_text_when_cancelled():
    blocked = asyncio.Event()

    class BlockingAfterTextLLM:
        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None):
            yield "partial"
            blocked.set()
            await asyncio.Event().wait()

        async def complete(self, *args, **kwargs):
            raise NotImplementedError

    agent = _make_agent(BlockingAfterTextLLM(), FakeExecutor({}))
    events = []

    async def consume():
        async for event in agent._call_llm(_msgs(), "test", [], ToolChoiceMode.AUTO, None):
            events.append(event)

    task = asyncio.create_task(consume())
    await asyncio.wait_for(blocked.wait(), timeout=1)
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert [type(event) for event in events] == [TextStarted, TextDelta, TextEnded]
    assert events[1].content == "partial"
    assert events[2].content == "partial"


@pytest.mark.asyncio
async def test_single_tool_call_then_response():
    llm = FakeLLM(
        [
            _response(tool_calls=[_tc("c1", "echo", {"x": 1})]),
            _response(text="done"),
        ]
    )
    executor = FakeExecutor({"echo": ToolResult(content="echoed", preview="e")})
    agent = _make_agent(llm, executor)

    messages = _msgs()
    result = await agent.run(messages)

    assert result.text == "done"
    assert result.steps == 1
    assert executor.call_log == [("echo", {"x": 1})]
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["content"] == "echoed"


# ============================================================
# Edge cases — malformed LLM output
# ============================================================


@pytest.mark.asyncio
async def test_empty_text_response():
    llm = FakeLLM([_response(text="")])
    agent = _make_agent(llm, FakeExecutor({}))
    result = await agent.run(_msgs())
    assert result.text == ""
    assert result.stop_reason == StopReason.END_TURN


@pytest.mark.asyncio
async def test_none_text_response():
    llm = FakeLLM([_response(text=None)])
    agent = _make_agent(llm, FakeExecutor({}))
    result = await agent.run(_msgs())
    assert result.text == ""


@pytest.mark.asyncio
async def test_text_and_tool_calls_yields_text_block():
    llm = FakeLLM(
        [
            _response(text="thinking...", tool_calls=[_tc("c1", "t", {})]),
            _response(text="done"),
        ]
    )
    agent = _make_agent(llm, FakeExecutor({"t": ToolResult(content="ok", preview="ok")}))
    events = [e async for e in agent.stream(_msgs())]
    assert any(isinstance(e, TextBlock) and e.content == "thinking..." for e in events)


@pytest.mark.asyncio
async def test_malformed_tool_arguments_returns_error_without_execution():
    bad_tc = ToolCall(id="c1", type="function", function=FunctionCall(name="t", arguments="{not json}"))
    llm = FakeLLM([_response(tool_calls=[bad_tc]), _response(text="done")])
    executor = FakeExecutor({"t": ToolResult(content="ok", preview="ok")})
    agent = _make_agent(llm, executor)
    messages = _msgs()

    await agent.run(messages)

    assert executor.call_log == []
    tool_message = next(message for message in messages if message["role"] == "tool")
    assert "invalid_tool_arguments" in tool_message["content"]
    assert "valid JSON object" in tool_message["content"]


@pytest.mark.parametrize("raw_arguments", ["[]", "null", '"text"', "1"])
@pytest.mark.asyncio
async def test_non_object_tool_arguments_return_error_without_execution(raw_arguments: str):
    bad_tc = ToolCall(id="c1", type="function", function=FunctionCall(name="t", arguments=raw_arguments))
    llm = FakeLLM([_response(tool_calls=[bad_tc]), _response(text="done")])
    executor = FakeExecutor({"t": ToolResult(content="ok", preview="ok")})
    agent = _make_agent(llm, executor)
    messages = _msgs()

    await agent.run(messages)

    assert executor.call_log == []
    tool_message = next(message for message in messages if message["role"] == "tool")
    assert "invalid_tool_arguments" in tool_message["content"]
    assert "JSON object" in tool_message["content"]


# ============================================================
# Multiple tool calls — concurrent dispatch
# ============================================================


@pytest.mark.asyncio
async def test_multiple_non_mutating_tools_run_concurrently():
    events_order: list[str] = []

    def slow_a(args):
        events_order.append("a_start")
        return ToolResult(content="a", preview="a")

    def slow_b(args):
        events_order.append("b_start")
        return ToolResult(content="b", preview="b")

    llm = FakeLLM(
        [
            _response(tool_calls=[_tc("1", "a", {}), _tc("2", "b", {})]),
            _response(text="done"),
        ]
    )
    executor = FakeExecutor(
        {"a": slow_a, "b": slow_b},
        meta={
            "a": ToolMeta(name="a", display_name="A"),
            "b": ToolMeta(name="b", display_name="B"),
        },
    )
    agent = _make_agent(llm, executor)
    result = await agent.run(_msgs())
    assert result.text == "done"
    assert set(events_order) == {"a_start", "b_start"}


@pytest.mark.asyncio
async def test_successful_terminal_tool_ends_without_another_model_turn():
    llm = FakeLLM(
        [
            _response(tool_calls=[_tc("1", "submit", {})]),
            _response(text="unreached"),
        ]
    )
    executor = FakeExecutor(
        {"submit": ToolResult(content="Filed the final exhibits.", preview="Report accepted")},
        meta={"submit": ToolMeta(name="submit", display_name="Submit", ends_turn=True)},
    )
    checkpoints: list[int] = []

    async def checkpoint(step, _response, _messages):
        checkpoints.append(step)

    agent = _make_agent(llm, executor, hooks=AgentHooks(on_step_finish=checkpoint))
    result = await agent.run(_msgs())

    assert result.text == "Filed the final exhibits."
    assert result.stop_reason == StopReason.END_TURN
    assert result.steps == 1
    assert llm.call_count == 1
    assert checkpoints == [1]


@pytest.mark.asyncio
async def test_failed_terminal_tool_stays_in_the_recovery_loop():
    llm = FakeLLM(
        [
            _response(tool_calls=[_tc("1", "submit", {})]),
            _response(text="Report could not be recorded."),
        ]
    )
    executor = FakeExecutor(
        {
            "submit": ToolResult.failure(
                code="conflict",
                message="Refresh and retry.",
                preview="Report rejected",
            )
        },
        meta={"submit": ToolMeta(name="submit", display_name="Submit", ends_turn=True)},
    )

    result = await _make_agent(llm, executor).run(_msgs())

    assert result.text == "Report could not be recorded."
    assert llm.call_count == 2


@pytest.mark.asyncio
async def test_terminal_tool_uses_its_preview_when_content_is_empty():
    llm = FakeLLM([_response(tool_calls=[_tc("1", "submit", {})])])
    executor = FakeExecutor(
        {"submit": ToolResult(content="", preview="Report recorded")},
        meta={"submit": ToolMeta(name="submit", display_name="Submit", ends_turn=True)},
    )

    result = await _make_agent(llm, executor).run(_msgs())

    assert result.text == "Report recorded"
    assert llm.call_count == 1


@pytest.mark.asyncio
async def test_terminal_tool_is_rejected_when_mixed_with_another_call():
    llm = FakeLLM(
        [
            _response(tool_calls=[_tc("1", "write", {}), _tc("2", "submit", {})]),
            _response(tool_calls=[_tc("3", "submit", {})]),
        ]
    )
    executor = FakeExecutor(
        {
            "write": ToolResult(content="changed", preview="Changed"),
            "submit": ToolResult(content="Report recorded.", preview="Report recorded"),
        },
        meta={
            "write": ToolMeta(name="write", display_name="Write", changes_state=True),
            "submit": ToolMeta(name="submit", display_name="Submit", ends_turn=True),
        },
    )
    messages = _msgs()

    result = await _make_agent(llm, executor).run(messages)

    assert result.text == "Report recorded."
    assert executor.call_log == [("submit", {})]
    rejected = [message for message in messages if message["role"] == "tool"][:2]
    assert all("turn-ending tool must be the only tool call" in message["content"] for message in rejected)


@pytest.mark.asyncio
async def test_mutating_tools_run_sequentially_after_non_mutating():
    llm = FakeLLM(
        [
            _response(tool_calls=[_tc("1", "mut", {}), _tc("2", "read", {})]),
            _response(text="done"),
        ]
    )
    executor = FakeExecutor(
        {
            "mut": ToolResult(content="mutated", preview="m"),
            "read": ToolResult(content="read", preview="r"),
        },
        meta={
            "mut": ToolMeta(name="mut", display_name="Mut"),
            "read": ToolMeta(name="read", display_name="Read"),
        },
    )
    agent = _make_agent(llm, executor)
    messages = _msgs()
    await agent.run(messages)
    # Both tools executed
    names = [c[0] for c in executor.call_log]
    assert set(names) == {"mut", "read"}


@pytest.mark.asyncio
async def test_tool_exception_wrapped_as_error_result():
    llm = FakeLLM(
        [
            _response(tool_calls=[_tc("1", "boom", {})]),
            _response(text="recovered"),
        ]
    )
    executor = FakeExecutor({"boom": RuntimeError("kaboom")})
    agent = _make_agent(llm, executor)
    messages = _msgs()
    result = await agent.run(messages)
    assert result.text == "recovered"
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert "kaboom" not in tool_msgs[0]["content"]
    assert "Diagnostic reference:" in tool_msgs[0]["content"]


# ============================================================
# Stop conditions
# ============================================================


@pytest.mark.asyncio
async def test_max_iterations_stops_with_reason():
    llm = FakeLLM(
        [
            _response(tool_calls=[_tc("1", "t", {})]),
            _response(tool_calls=[_tc("2", "t", {})]),
            _response(tool_calls=[_tc("3", "t", {})]),
            _response(text="unreached"),
        ]
    )
    executor = FakeExecutor({"t": ToolResult(content="r", preview="r")})
    agent = _make_agent(llm, executor, max_iterations=2)
    result = await agent.run(_msgs())
    assert result.stop_reason == StopReason.MAX_ITERATIONS
    assert result.steps == 2


@pytest.mark.asyncio
async def test_zero_text_non_end_turn_stop_after_tool_work_returns_fallback():
    llm = FakeLLM(
        [
            _response(tool_calls=[_tc("1", "t", {})]),
            _response(text="unreached"),
        ]
    )
    executor = FakeExecutor({"t": ToolResult(content="useful result", preview="useful")})
    agent = _make_agent(llm, executor, max_iterations=1)

    result = await agent.run(_msgs())

    assert result.stop_reason == StopReason.MAX_ITERATIONS
    assert result.text == (
        "Stopped because max_iterations was reached after tool work. Recent tool results:\n- useful result"
    )


@pytest.mark.asyncio
async def test_truncated_response_stops_instead_of_looping_on_tool_calls():
    # A model that hits its per-response max_tokens (finish_reason=length) WITH
    # pending tool calls must STOP, not execute the truncated calls and loop. Only
    # one response is queued, so a loop would exhaust FakeLLM and fail the test.
    truncated = CompletionResponse(
        choices=[
            Choice(
                message=Message(
                    role=Role.ASSISTANT, content="partial", tool_calls=[_tc("c1", "t", {})], reasoning_content=None
                ),
                finish_reason="length",
            )
        ],
        usage=Usage(prompt_tokens=10, completion_tokens=5),
        model="test",
    )
    llm = FakeLLM([truncated])
    executor = FakeExecutor({"t": ToolResult(content="r", preview="r")})
    agent = _make_agent(llm, executor)
    result = await agent.run(_msgs())
    assert result.stop_reason == StopReason.MAX_OUTPUT_LENGTH
    assert llm.call_count == 1  # did not loop
    assert executor.call_log == []  # did not execute the truncated tool call


@pytest.mark.asyncio
async def test_content_filtered_response_fails_instead_of_ending_turn():
    blocked = _response()
    blocked = replace(blocked, choices=[replace(blocked.choices[0], finish_reason=FinishReason.CONTENT_FILTER)])
    responses = []
    errors = []

    async def on_response(response):
        responses.append(response)

    async def on_error(error):
        errors.append(error)

    messages = _msgs()
    original = list(messages)
    agent = _make_agent(
        FakeLLM([blocked]),
        FakeExecutor({}),
        hooks=AgentHooks(on_response=on_response, on_error=on_error),
    )

    with pytest.raises(ProviderResponseError, match="blocked"):
        await agent.run(messages)

    assert messages == original
    assert responses == []
    assert len(errors) == 1


@pytest.mark.asyncio
async def test_max_depth_stops_before_calling_llm():
    llm = FakeLLM([_response(text="unreached")])
    agent = _make_agent(llm, FakeExecutor({}), max_depth=2, current_depth=2)
    result = await agent.run(_msgs())
    assert result.stop_reason == StopReason.MAX_DEPTH
    assert llm.call_count == 0


@pytest.mark.asyncio
async def test_max_tool_calls_denies_batch_that_would_exceed_budget():
    llm = FakeLLM([_response(tool_calls=[_tc("c1", "t", {}), _tc("c2", "t", {})])])
    executor = FakeExecutor({"t": ToolResult(content="r", preview="r")})
    agent = _make_agent(llm, executor, max_tool_calls=1)
    messages = _msgs()

    result = await agent.run(messages)

    assert result.stop_reason == StopReason.MAX_TOOL_CALLS
    assert executor.call_log == []
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    assert [(m["tool_call_id"], m["content"]) for m in tool_msgs] == [
        ("c1", "Tool call denied: max tool-call budget of 1 would be exceeded."),
        ("c2", "Tool call denied: max tool-call budget of 1 would be exceeded."),
    ]


@pytest.mark.asyncio
async def test_per_step_tool_cap_rejects_batch_atomically_and_allows_regrouping():
    oversized = [_tc(f"c{i}", "t", {}) for i in range(11)]
    llm = FakeLLM([_response(tool_calls=oversized), _response(text="regrouped")])
    executor = FakeExecutor({"t": ToolResult(content="r", preview="r")})
    messages = _msgs()

    result = await _make_agent(llm, executor).run(messages)

    assert result.stop_reason == StopReason.END_TURN
    assert result.text == "regrouped"
    assert executor.call_log == []
    tool_messages = [message for message in messages if message["role"] == "tool"]
    assert len(tool_messages) == 11
    assert {message["tool_call_id"] for message in tool_messages} == {f"c{i}" for i in range(11)}
    assert all("per-step limit of 10" in message["content"] for message in tool_messages)


@pytest.mark.asyncio
async def test_three_non_retryable_failure_only_steps_stop_as_no_progress():
    llm = FakeLLM(
        [
            _response(tool_calls=[_tc("c1", "t", {})]),
            _response(tool_calls=[_tc("c2", "t", {})]),
            _response(tool_calls=[_tc("c3", "t", {})]),
            _response(text="unreached"),
        ]
    )
    executor = FakeExecutor(
        {
            "t": ToolResult.failure(
                code="terminal_failure",
                message="cannot proceed",
                preview="failed",
            )
        }
    )

    result = await _make_agent(llm, executor).run(_msgs())

    assert result.stop_reason == StopReason.NO_PROGRESS
    assert result.steps == 3
    assert llm.call_count == 3
    assert len(executor.call_log) == 3


@pytest.mark.asyncio
async def test_failed_tool_message_preserves_structured_recovery_outcome():
    llm = FakeLLM([_response(tool_calls=[_tc("c1", "t", {})]), _response(text="done")])
    executor = FakeExecutor(
        {
            "t": ToolResult.failure(
                code="fresh_read_required",
                message="Read required.",
                preview="Read required",
                recovery_action="Read the page again.",
            )
        }
    )
    messages = _msgs()

    await _make_agent(llm, executor).run(messages)

    tool_message = next(message for message in messages if message["role"] == "tool")
    assert tool_message["content"] == "Read required."
    assert tool_message["outcome"]["error"]["code"] == "fresh_read_required"
    assert tool_message["outcome"]["error"]["recovery_action"] == "Read the page again."


@pytest.mark.asyncio
async def test_success_resets_no_progress_counter():
    outcomes = iter(
        [
            ToolResult.failure(code="terminal_failure", message="no", preview="no"),
            ToolResult.failure(code="terminal_failure", message="no", preview="no"),
            ToolResult(content="yes", preview="yes"),
            ToolResult.failure(code="terminal_failure", message="no", preview="no"),
            ToolResult.failure(code="terminal_failure", message="no", preview="no"),
        ]
    )
    llm = FakeLLM([_response(tool_calls=[_tc(f"c{i}", "t", {})]) for i in range(5)] + [_response(text="done")])
    executor = FakeExecutor({"t": lambda _args: next(outcomes)})

    result = await _make_agent(llm, executor).run(_msgs())

    assert result.stop_reason == StopReason.END_TURN
    assert result.text == "done"


@pytest.mark.asyncio
async def test_max_wall_time_seconds_stops_after_tool_dispatch():
    clock_values = iter([0.0, 0.0, 0.0, 0.0, 2.0])
    llm = FakeLLM(
        [
            _response(tool_calls=[_tc("c1", "t", {})]),
            _response(text="unreached"),
        ]
    )
    executor = FakeExecutor({"t": ToolResult(content="r", preview="r")})
    agent = _make_agent(llm, executor, max_wall_time_seconds=1.0, clock=lambda: next(clock_values))

    result = await agent.run(_msgs())

    assert result.stop_reason == StopReason.MAX_WALL_TIME
    assert llm.call_count == 1
    assert executor.call_log == [("t", {})]


@pytest.mark.asyncio
async def test_max_wall_time_seconds_rechecks_after_prepare_before_model_call():
    clock_values = iter([0.0, 0.0, 2.0])
    llm = FakeLLM([_response(text="unreached")])

    async def noop_middleware(request, next_request):
        return await next_request(request)

    agent = _make_agent(
        llm,
        FakeExecutor({}),
        max_wall_time_seconds=1.0,
        clock=lambda: next(clock_values),
        model_request_middlewares=(noop_middleware,),
    )

    result = await agent.run(_msgs())

    assert result.stop_reason == StopReason.MAX_WALL_TIME
    assert llm.call_count == 0


@pytest.mark.asyncio
async def test_max_cost_stops_after_model_response_before_tool_dispatch():
    shared_cost = {"value": 0.0}

    async def track_cost(response):
        shared_cost["value"] = 1.0

    llm = FakeLLM([_response(tool_calls=[_tc("c1", "t", {})], usage=Usage(prompt_tokens=10))])
    executor = FakeExecutor({"t": ToolResult(content="r", preview="r")})
    agent = _make_agent(
        llm,
        executor,
        max_cost=0.5,
        hooks=AgentHooks(on_response=track_cost),
        cost_getter=lambda: shared_cost["value"],
    )
    messages = _msgs()

    result = await agent.run(messages)

    assert result.stop_reason == StopReason.MAX_COST
    assert llm.call_count == 1
    assert executor.call_log == []
    assert [m for m in messages if m["role"] == "tool"] == [
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": "Tool call denied: max cost budget exceeded.",
        }
    ]


def test_max_cost_requires_cost_source():
    llm = FakeLLM([_response(text="ok")])

    with pytest.raises(ValueError, match="max_cost requires"):
        _make_agent(llm, FakeExecutor({}), max_cost=1.0)


@pytest.mark.asyncio
async def test_max_cost_stops_after_tool_dispatch_when_shared_cost_rolls_up():
    shared_cost = {"value": 0.0}

    def tool_spends(args):
        shared_cost["value"] = 1.0
        return ToolResult(content="r", preview="r")

    llm = FakeLLM(
        [
            _response(tool_calls=[_tc("c1", "t", {})], usage=Usage(prompt_tokens=10)),
            _response(text="unreached"),
        ]
    )
    executor = FakeExecutor({"t": tool_spends})
    agent = _make_agent(llm, executor, max_cost=0.5, cost_getter=lambda: shared_cost["value"])

    result = await agent.run(_msgs())

    assert result.stop_reason == StopReason.MAX_COST
    assert llm.call_count == 1
    assert executor.call_log == [("t", {})]
    assert "Stopped because max_cost" in result.text
    assert "- r" in result.text


@pytest.mark.asyncio
async def test_max_token_budget_stops_after_model_response_before_tool_dispatch():
    llm = FakeLLM([_response(tool_calls=[_tc("c1", "t", {})], usage=Usage(completion_tokens=100))])
    executor = FakeExecutor({"t": ToolResult(content="r", preview="r")})
    agent = _make_agent(llm, executor, budget=RunBudget(total=50))

    messages = _msgs()
    result = await agent.run(messages)

    assert result.stop_reason == StopReason.MAX_TOKEN_BUDGET
    assert executor.call_log == []  # stopped before dispatch
    assert [m for m in messages if m["role"] == "tool"] == [
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": "Tool call denied: max output-token budget of 50 exceeded.",
        }
    ]


@pytest.mark.asyncio
async def test_max_token_budget_after_tool_work_returns_recent_result():
    budget = RunBudget(total=50)

    def child_agent_result(args):
        budget.output_tokens = 75
        return ToolResult(content="child agent completed the requested analysis", preview="child done")

    llm = FakeLLM(
        [
            _response(tool_calls=[_tc("c1", "t", {})], usage=Usage(completion_tokens=10)),
            _response(text=""),
        ]
    )
    executor = FakeExecutor({"t": child_agent_result})
    agent = _make_agent(llm, executor, budget=budget)

    result = await agent.run(_msgs())

    assert result.stop_reason == StopReason.MAX_TOKEN_BUDGET
    assert llm.call_count == 2
    assert executor.call_log == [("t", {})]
    assert result.text == (
        "Stopped because max_token_budget was reached after tool work. Recent tool results:\n"
        "- child agent completed the requested analysis"
    )


@pytest.mark.asyncio
async def test_max_token_budget_final_answer_failure_propagates():
    budget = RunBudget(total=50)

    def child_agent_result(args):
        budget.output_tokens = 75
        return ToolResult(content="child evidence", preview="child done")

    llm = FakeLLM(
        [
            _response(tool_calls=[_tc("c1", "t", {})], usage=Usage(completion_tokens=10)),
            RuntimeError("provider failed"),
        ]
    )
    agent = _make_agent(llm, FakeExecutor({"t": child_agent_result}), budget=budget)

    with pytest.raises(RuntimeError, match="provider failed"):
        await agent.run(_msgs())


@pytest.mark.asyncio
async def test_max_token_budget_after_tool_work_tries_final_answer_once():
    budget = RunBudget(total=50)

    def child_agent_result(args):
        budget.output_tokens = 75
        return ToolResult(content="child evidence", preview="child done")

    llm = FakeLLM(
        [
            _response(tool_calls=[_tc("c1", "t", {})], usage=Usage(completion_tokens=10)),
            _response(text="final answer from evidence", usage=Usage(completion_tokens=5)),
        ]
    )
    executor = FakeExecutor({"t": child_agent_result})
    agent = _make_agent(llm, executor, budget=budget)

    result = await agent.run(_msgs())

    assert result.stop_reason == StopReason.MAX_TOKEN_BUDGET
    assert llm.call_count == 2
    assert result.text == "final answer from evidence"
    assert any(
        "Return the final answer now from the tool results" in str(m.get("content")) for m in llm.last_messages or []
    )


@pytest.mark.asyncio
async def test_token_budget_reminder_is_not_sent_above_pressure_threshold():
    budget = RunBudget(total=1000, output_tokens=700)
    llm = FakeLLM([_response(text="done")])
    agent = _make_agent(llm, FakeExecutor({}), budget=budget)

    await agent.run(_msgs())

    assert all("output-token budget" not in str(m.get("content")) for m in llm.last_messages or [])


@pytest.mark.asyncio
async def test_token_budget_reminder_is_sent_when_budget_is_low():
    budget = RunBudget(total=1000, output_tokens=900)
    llm = FakeLLM([_response(text="done")])
    agent = _make_agent(llm, FakeExecutor({}), budget=budget)
    messages = _msgs()
    messages[0]["content"] = [{"type": "text", "text": "sys"}]

    await agent.run(messages)

    assert llm.last_messages is not None
    assert [message["role"] for message in llm.last_messages].count(Role.SYSTEM) == 1
    assert llm.last_messages[0] == {
        "role": Role.SYSTEM,
        "content": [
            {"type": "text", "text": "sys"},
            {
                "type": "text",
                "text": (
                    "Output-token budget pressure: 100 of 1000 tokens remain. "
                    "Wrap up now; do not start broad new work. Preserve a useful final answer."
                ),
            },
        ],
    }


@pytest.mark.asyncio
async def test_output_tokens_accumulate_into_shared_budget_across_agents():
    # The same RunBudget instance is shared by the top agent and every spawned
    # child (core/spawner.py), so accumulating at the single chokepoint makes it
    # the cumulative subtree spend.
    budget = RunBudget()
    for _ in range(2):
        llm = FakeLLM([_response(text="done", usage=Usage(completion_tokens=40))])
        agent = _make_agent(llm, FakeExecutor({}), budget=budget)
        await agent.run(_msgs())
    assert budget.output_tokens == 80


# ============================================================
# Model request middleware
# ============================================================


@pytest.mark.asyncio
async def test_model_request_middleware_can_override_model_and_tool_choice():
    llm = FakeLLM([_response(text="ok")])
    captured: dict = {}

    async def override_request(request, next_request):
        return await next_request(replace(request, model="override-model", tool_choice=ToolChoiceMode.REQUIRED))

    class Capture(FakeLLM):
        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None):
            captured["model"] = model
            captured["tool_choice"] = tool_choice
            async for item in super().stream(messages, model, tools, tool_choice, reasoning_effort):
                yield item

    llm = Capture([_response(text="ok")])
    agent = _make_agent(llm, FakeExecutor({}), model_request_middlewares=(override_request,))
    await agent.run(_msgs())
    assert captured["model"] == "override-model"
    assert captured["tool_choice"] == ToolChoiceMode.REQUIRED


@pytest.mark.asyncio
async def test_model_request_middleware_can_replace_request_messages_without_mutating_history():
    async def replace_messages(request, next_request):
        return await next_request(replace(request, messages=[{"role": "system", "content": "replaced"}]))

    llm = FakeLLM([_response(text="ok")])
    agent = _make_agent(llm, FakeExecutor({}), model_request_middlewares=(replace_messages,))
    messages = _msgs()
    await agent.run(messages)
    assert llm.last_messages == [{"role": "system", "content": "replaced"}]
    assert messages[0]["content"] == "sys"
    assert messages[-1]["content"] == "ok"


@pytest.mark.asyncio
async def test_model_request_middleware_explicit_compaction_replacement_starts_new_epoch():
    replacement = [{"role": "system", "content": "compacted"}]

    async def replace_history(request, next_request):
        return await next_request(replace(request, messages=replacement, compaction_replacement=replacement))

    llm = FakeLLM([_response(text="ok")])
    agent = _make_agent(llm, FakeExecutor({}), model_request_middlewares=(replace_history,))
    messages = _msgs()
    await agent.run(messages)

    assert messages[0]["content"] == "compacted"
    assert messages[-1]["content"] == "ok"


@pytest.mark.asyncio
async def test_model_request_middlewares_run_in_order():
    calls: list[str] = []

    async def outer(request, next_request):
        calls.append("outer:before")
        prepared = await next_request(request)
        calls.append("outer:after")
        return prepared

    async def inner(request, next_request):
        calls.append("inner:before")
        prepared = await next_request(request)
        calls.append("inner:after")
        return prepared

    llm = FakeLLM([_response(text="ok")])
    agent = _make_agent(llm, FakeExecutor({}), model_request_middlewares=(outer, inner))
    await agent.run(_msgs())
    assert calls == ["outer:before", "inner:before", "inner:after", "outer:after"]


# ============================================================
# Hooks
# ============================================================


@pytest.mark.asyncio
async def test_on_response_fires_with_accumulated_usage():
    seen: list[CompletionResponse] = []

    async def on_response(resp):
        seen.append(resp)

    llm = FakeLLM(
        [
            _response(text="", tool_calls=[_tc("1", "t", {})], usage=Usage(prompt_tokens=5, completion_tokens=3)),
            _response(text="done", usage=Usage(prompt_tokens=10, completion_tokens=7)),
        ]
    )
    executor = FakeExecutor({"t": ToolResult(content="r", preview="r")})
    agent = _make_agent(llm, executor, hooks=AgentHooks(on_response=on_response))
    result = await agent.run(_msgs())
    assert len(seen) == 2
    # Agent's accumulated usage = sum of both responses
    assert result.usage.prompt_tokens == 15
    assert result.usage.completion_tokens == 10


@pytest.mark.asyncio
async def test_on_error_fires_before_reraise():
    seen: list[Exception] = []

    async def on_error(exc):
        seen.append(exc)

    llm = FakeLLM([RuntimeError("llm failed")])
    agent = _make_agent(llm, FakeExecutor({}), hooks=AgentHooks(on_error=on_error))
    with pytest.raises(RuntimeError, match="llm failed"):
        await agent.run(_msgs())
    assert len(seen) == 1
    assert str(seen[0]) == "llm failed"


@pytest.mark.asyncio
async def test_on_finish_fires_on_normal_completion():
    seen: list[tuple] = []

    async def on_finish(text, steps, messages):
        seen.append((text, steps, len(messages)))

    llm = FakeLLM([_response(text="done")])
    agent = _make_agent(llm, FakeExecutor({}), hooks=AgentHooks(on_finish=on_finish))
    await agent.run(_msgs())
    assert len(seen) == 1
    assert seen[0][0] == "done"


@pytest.mark.asyncio
async def test_on_finish_fires_even_on_exception():
    seen: list[tuple] = []

    async def on_finish(text, steps, messages):
        seen.append((text, steps))

    llm = FakeLLM([RuntimeError("boom")])
    agent = _make_agent(llm, FakeExecutor({}), hooks=AgentHooks(on_finish=on_finish))
    with pytest.raises(RuntimeError):
        await agent.run(_msgs())
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_get_pending_messages_injects_between_steps():
    injected = [{"role": "user", "content": "injected"}]
    calls = 0

    async def get_pending():
        nonlocal calls
        calls += 1
        if calls == 1:
            return []
        if calls == 2:
            batch = list(injected)
            injected.clear()
            return batch
        return []

    llm = FakeLLM(
        [
            _response(tool_calls=[_tc("1", "t", {})]),
            _response(text="done"),
        ]
    )
    executor = FakeExecutor({"t": ToolResult(content="r", preview="r")})
    agent = _make_agent(llm, executor, hooks=AgentHooks(get_pending_messages=get_pending))
    messages = _msgs()
    await agent.run(messages)
    assert any(m.get("content") == "injected" for m in messages)


@pytest.mark.asyncio
async def test_pending_arrived_during_final_turn_continues_loop():
    """User queues a message while the LLM is producing its end-turn response.

    The agent must drain pending before exiting and respond to it instead
    of stranding it as an orphaned queue entry.
    """
    injected = [{"role": "user", "content": "follow-up"}]
    calls = 0

    async def get_pending():
        nonlocal calls
        calls += 1
        # First drain (top of iter 1) — empty
        # Second drain (after end-turn check on iter 1) — return follow-up
        # Third drain (top of iter 2) — empty
        # Fourth drain (after end-turn check on iter 2) — empty, stop
        if calls == 2:
            batch = list(injected)
            injected.clear()
            return batch
        return []

    llm = FakeLLM(
        [
            _response(text="first answer"),  # iter 1: would end, but pending arrives
            _response(text="second answer"),  # iter 2: ends cleanly
        ]
    )
    agent = _make_agent(llm, FakeExecutor({}), hooks=AgentHooks(get_pending_messages=get_pending))
    messages = _msgs()
    result = await agent.run(messages)

    assert any(m.get("content") == "follow-up" for m in messages), "queued message must land in conversation context"
    assert result.text == "second answer", "agent must produce a fresh response after consuming the queued message"


# ============================================================
# Cancellation
# ============================================================


@pytest.mark.asyncio
async def test_cancellation_yields_cancelled_result_and_reraises():
    slow_event = asyncio.Event()

    class BlockingLLM:
        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None):
            await slow_event.wait()
            yield _response(text="unreached")

        async def complete(self, *args, **kwargs):
            raise NotImplementedError

    async def on_finish(text, steps, messages):
        assert text == "Cancelled."

    agent = Agent(
        tools=[],
        client=BlockingLLM(),
        executor=FakeExecutor({}),
        model="test",
        hooks=AgentHooks(on_finish=on_finish),
    )

    gen = agent.stream(_msgs())
    task = asyncio.create_task(_consume(gen))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_cancellation_appends_completed_and_cancelled_tool_results():
    completed_seen = asyncio.Event()
    slow_started = asyncio.Event()

    class OneFastOneBlockedExecutor:
        async def execute(self, name: str, args: dict, tool_call_id: str) -> ToolResult:
            if name == "fast":
                return ToolResult(content="fast-result", preview="fast")
            slow_started.set()
            await asyncio.Event().wait()
            return ToolResult(content="unreached", preview="slow")

        def get_meta(self, name: str) -> ToolMeta:
            return ToolMeta(name=name, display_name=name)

    llm = FakeLLM([_response(tool_calls=[_tc("c1", "fast", {}), _tc("c2", "slow", {})])])
    agent = _make_agent(llm, OneFastOneBlockedExecutor())
    messages = _msgs()
    events = []

    async def consume():
        async for event in agent.stream(messages):
            events.append(event)
            if isinstance(event, ToolCompleted) and event.tool_id == "c1":
                completed_seen.set()

    task = asyncio.create_task(consume())
    await asyncio.wait_for(slow_started.wait(), timeout=1)
    await asyncio.wait_for(completed_seen.wait(), timeout=1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    tool_msgs = [m for m in messages if m["role"] == "tool"]
    assert [(m["tool_call_id"], m["content"]) for m in tool_msgs] == [
        ("c1", "fast-result"),
        ("c2", "Tool call cancelled."),
    ]


async def _consume(gen):
    events = []
    async for e in gen:
        events.append(e)
    return events


# ============================================================
# SharedLedger
# ============================================================


@pytest.mark.asyncio
async def test_ledger_register_and_complete():
    ledger = SharedLedger()
    await ledger.register("id1", "research topic X", depth="deep")
    items = ledger.get_items()
    assert len(items) == 1
    assert items[0].label == "research topic X"
    assert items[0].done is False
    assert items[0].metadata["depth"] == "deep"

    await ledger.complete("id1")
    items = ledger.get_items()
    assert items[0].done is True


@pytest.mark.asyncio
async def test_ledger_mark_accessed_returns_true_on_duplicate():
    ledger = SharedLedger()
    assert await ledger.mark_accessed("file.md") is False
    assert await ledger.mark_accessed("file.md") is True
    assert await ledger.mark_accessed("other.md") is False
    assert ledger.accessed_count == 2


@pytest.mark.asyncio
async def test_ledger_exclude_id_filters_items():
    ledger = SharedLedger()
    await ledger.register("a", "task a")
    await ledger.register("b", "task b")
    items = ledger.get_items(exclude_id="a")
    assert len(items) == 1
    assert items[0].id == "b"


@pytest.mark.asyncio
async def test_ledger_concurrent_register_is_safe():
    ledger = SharedLedger()

    async def add(i):
        await ledger.register(f"id{i}", f"task {i}")

    await asyncio.gather(*[add(i) for i in range(100)])
    assert len(ledger.get_items()) == 100


# ============================================================
# Usage accumulation
# ============================================================


@pytest.mark.asyncio
async def test_usage_accumulates_across_steps():
    llm = FakeLLM(
        [
            _response(text="", tool_calls=[_tc("1", "t", {})], usage=Usage(prompt_tokens=100, completion_tokens=50)),
            _response(text="done", usage=Usage(prompt_tokens=200, completion_tokens=75)),
        ]
    )
    executor = FakeExecutor({"t": ToolResult(content="r", preview="r")})
    agent = _make_agent(llm, executor)
    result = await agent.run(_msgs())
    assert result.usage.prompt_tokens == 300
    assert result.usage.completion_tokens == 125
    assert result.usage.total_tokens == 425


@pytest.mark.asyncio
async def test_usage_input_tokens_property_includes_cache():
    u = Usage(prompt_tokens=100, completion_tokens=50, cache_read_tokens=10, cache_write_tokens=5)
    assert u.input_tokens == 115
    assert u.total_tokens == 165


# ============================================================
# Messages list ownership — caller-owned message list changes during a run
# ============================================================


@pytest.mark.asyncio
async def test_agent_mutates_caller_messages_list_in_place():
    llm = FakeLLM(
        [
            _response(tool_calls=[_tc("1", "t", {})]),
            _response(text="done"),
        ]
    )
    executor = FakeExecutor({"t": ToolResult(content="r", preview="r")})
    agent = _make_agent(llm, executor)
    messages = _msgs()
    original_len = len(messages)
    await agent.run(messages)
    assert len(messages) > original_len
    assert any(m["role"] == "assistant" for m in messages)
    assert any(m["role"] == "tool" for m in messages)


def test_agent_exposes_its_executor():
    # The respawn host reads agent.executor.ctx to reach the wired ToolContext;
    # a private-only executor was the E2E-caught respawn crash.
    executor = FakeExecutor({})
    agent = Agent(client=FakeLLM([]), executor=executor, tools=[], model="test")
    assert agent.executor is executor
