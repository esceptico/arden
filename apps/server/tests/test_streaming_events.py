import asyncio
import json
from contextlib import suppress
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from ntrp.agent import (
    ReasoningContentDelta,
    ReasoningDelta,
    Result,
    StopReason,
    TextDelta,
    TextEnded,
    TextStarted,
    Usage,
)
from ntrp.agent.types.events import ToolCompleted
from ntrp.agent.types.tools import ToolEffect, ToolOutcome, ToolOutcomeStatus, ToolSourceRef
from ntrp.context.models import SessionData, SessionState
from ntrp.core import spawner as spawner_module
from ntrp.core.spawner import create_spawn_fn
from ntrp.core.usage_tracker import UsageTracker
from ntrp.events.sse import (
    ApprovalNeededEvent,
    AutomationProgressEvent,
    CompactionFinishedEvent,
    CompactionStartedEvent,
    KeepaliveEvent,
    ReasoningMessageContentEvent,
    TaskFinishedEvent,
    TaskStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ThinkingEvent,
    TodoUpdatedEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
    agent_events_to_sse,
)
from ntrp.server.bus import RECENT_BUFFER_MAX, BusRegistry, SessionBus
from ntrp.server.routers.automation import _automation_event_stream
from ntrp.server.routers.chat import _event_stream, _keepalive
from ntrp.server.state import RunRegistry, RunState, RunStatus
from ntrp.server.stream import run_agent_loop
from ntrp.services.chat import (
    ChatContext,
    _checkpoint_tool_step,
    _drain_backgrounded,
    _recover_durable_tool_calls,
    run_chat,
)
from ntrp.tools.core.context import BackgroundTaskRegistry, IOBridge, RunContext, ToolContext
from tests.helpers import make_executor, make_text_response


def test_text_boundary_events_convert_to_sse():
    (start,) = agent_events_to_sse(TextStarted(message_id="text-1"))
    (content,) = agent_events_to_sse(TextDelta(message_id="text-1", content="hello"))
    (end,) = agent_events_to_sse(TextEnded(message_id="text-1", content="hello"))

    assert isinstance(start, TextMessageStartEvent)
    assert start.message_id == "text-1"
    assert content.message_id == "text-1"
    assert isinstance(end, TextMessageEndEvent)
    assert end.message_id == "text-1"
    assert end.content == "hello"


@pytest.mark.asyncio
async def test_recovery_reuses_terminal_calls_blocks_ambiguous_calls_and_leaves_safe_calls():
    class Store:
        async def list_tool_calls(self, *, run_id):
            assert run_id == "run-1"
            return [
                {"tool_call_id": "c1", "status": "success", "result_preview": "done", "outcome": {"status": "succeeded"}},
                {"tool_call_id": "c2", "status": "running", "result_preview": None, "outcome": None},
                {"tool_call_id": "c3", "status": "created", "result_preview": None, "outcome": None},
                {"tool_call_id": "c4", "status": "awaiting", "result_preview": None, "outcome": None},
            ]

        async def get_tool_result_for_call(self, *, run_id, tool_call_id):
            assert (run_id, tool_call_id) == ("run-1", "c1")
            return {"content": "durable result"}

    calls = [SimpleNamespace(tool_call=SimpleNamespace(id=f"c{i}")) for i in range(1, 5)]

    recovered = await _recover_durable_tool_calls(Store(), "run-1", calls)

    assert set(recovered) == {"c1", "c2"}
    assert recovered["c1"].content == "durable result"
    assert recovered["c1"].outcome.status == ToolOutcomeStatus.SUCCEEDED
    assert recovered["c2"].outcome.status == ToolOutcomeStatus.UNCERTAIN
    assert recovered["c2"].outcome.error.code == "execution_state_uncertain"


@pytest.mark.asyncio
async def test_tool_step_checkpoint_saves_assistant_turn_before_call_intents():
    operations = []

    class Store:
        async def record_tool_call_created(self, **kwargs):
            operations.append(("created", kwargs))

    class SessionService:
        store = Store()

        async def save_progress(self, state, messages):
            operations.append(("saved", list(messages)))

    run = RunState(run_id="run-1", session_id="s-1")
    run.messages = [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "write", "arguments": '{"x":1}'}}
            ],
        },
    ]
    executor = SimpleNamespace(
        registry=SimpleNamespace(
            get=lambda _name: SimpleNamespace(policy=SimpleNamespace(action="write", scope="internal"))
        )
    )

    assert await _checkpoint_tool_step(
        SessionService(), SessionState(session_id="s-1", started_at=datetime.now(UTC)), run, executor
    )

    assert [kind for kind, _payload in operations] == ["saved", "created"]
    assert operations[1][1]["tool_call_id"] == "c1"


def test_tool_result_sse_exposes_source_refs_outside_result_content():
    source = ToolSourceRef(
        provider="slack",
        kind="message",
        ref="C1:1.0",
        title="Decision",
        url="https://example.slack.com/archives/C1/p1",
    )
    page = ToolSourceRef(
        provider="web",
        kind="page",
        ref="https://example.com/docs",
        title="Documentation",
        url="https://example.com/docs",
    )

    (event,) = agent_events_to_sse(
        ToolCompleted(
            tool_id="call-1",
            name="slack_thread",
            result="thread text",
            preview="thread",
            duration_ms=1,
            is_error=False,
            data=None,
            display_name="Slack Thread",
            source_refs=(source, page),
            outcome=ToolOutcome(
                status=ToolOutcomeStatus.SUCCEEDED,
                effect=ToolEffect(operation="read", target="thread:C1:1.0"),
                receipt="receipt-1",
            ),
        )
    )
    payload = json.loads(event.to_sse()["data"])

    assert isinstance(event, ToolCallResultEvent)
    assert payload["content"] == "thread text"
    assert payload["source_refs"] == [source.to_dict(), page.to_dict()]
    assert payload["data"] is None
    assert payload["outcome"] == {
        "status": "succeeded",
        "effect": {"operation": "read", "target": "thread:C1:1.0"},
        "receipt": "receipt-1",
    }


@pytest.mark.asyncio
async def test_run_agent_loop_collects_each_tool_source_ref_for_episode_provenance():
    source = ToolSourceRef(provider="slack", kind="message", ref="C1:1.0", title="Decision")
    duplicate = ToolSourceRef(provider="slack", kind="message", ref="C1:1.0", title="Duplicate")
    second = ToolSourceRef(provider="web", kind="page", ref="https://example.com", title="Example")
    run = RunState(run_id="run-1", session_id="session-1")

    class _Agent:
        async def stream(self, _messages):
            yield ToolCompleted(
                tool_id="call-1",
                name="slack_thread",
                result="thread text",
                preview="thread",
                duration_ms=1,
                is_error=False,
                data=None,
                display_name="Slack Thread",
                source_refs=(source, duplicate, second),
            )
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    await run_agent_loop(SimpleNamespace(run=run), _Agent(), SessionBus(session_id="session-1"))

    assert run.source_refs == [source.to_dict(), second.to_dict()]


@pytest.mark.asyncio
async def test_run_source_refs_keep_more_than_50_unique_refs_across_tool_completions():
    run = RunState(run_id="run-1", session_id="session-1")
    first_batch = tuple(
        ToolSourceRef(provider="slack", kind="message", ref=f"C1:{index}", title=f"Message {index}")
        for index in range(30)
    )
    duplicate = ToolSourceRef(provider="slack", kind="message", ref="C1:0", title="Duplicate")
    second_batch = (
        duplicate,
        *(
            ToolSourceRef(provider="slack", kind="message", ref=f"C1:{index}", title=f"Message {index}")
            for index in range(30, 60)
        ),
    )

    class _Agent:
        async def stream(self, _messages):
            for call_id, source_refs in (("call-1", first_batch), ("call-2", second_batch)):
                yield ToolCompleted(
                    tool_id=call_id,
                    name="slack_search",
                    result="messages",
                    preview="messages",
                    duration_ms=1,
                    is_error=False,
                    data=None,
                    display_name="Slack Search",
                    source_refs=source_refs,
                )
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    await run_agent_loop(SimpleNamespace(run=run), _Agent(), SessionBus(session_id="session-1"))

    assert len(run.source_refs) == 60
    assert [ref["ref"] for ref in run.source_refs] == [f"C1:{index}" for index in range(60)]
    assert run.source_refs[0]["title"] == "Message 0"


def test_run_source_refs_use_constant_time_dedupe_and_initialize_supplied_refs():
    initial = [
        {
            "provider": "demo",
            "kind": "document",
            "ref": f"doc-{index}",
            "title": f"Document {index}",
        }
        for index in range(75)
    ]
    initial.append(
        {"provider": "demo", "kind": "document", "ref": "doc-0", "title": "Duplicate"},
    )
    run = RunState(run_id="run-1", session_id="session-1", source_refs=initial)

    class NoScanList(list):
        def __iter__(self):
            raise AssertionError("add_source_ref scanned the ordered refs list")

    assert len(run.source_refs) == 75
    assert run.source_refs[0] == initial[0]
    assert run.source_refs[-1]["ref"] == "doc-74"
    run.source_refs = NoScanList(run.source_refs)
    for index in range(75, 2075):
        run.add_source_ref(
            {
                "provider": "demo",
                "kind": "document",
                "ref": f"doc-{index}",
                "title": f"Document {index}",
            }
        )
    run.add_source_ref({"provider": "demo", "kind": "document", "ref": "doc-2074", "title": "Replay"})

    assert len(run.source_refs) == 2075
    assert run.source_refs[-1]["ref"] == "doc-2074"


def test_reasoning_sse_preserves_nested_scope():
    (event,) = agent_events_to_sse(
        ReasoningDelta(depth=1, parent_id="call-research", message_id="reasoning-1", content="internal thought")
    )

    assert isinstance(event, ReasoningMessageContentEvent)
    assert event.depth == 1
    assert event.parent_id == "call-research"

    data = json.loads(event.to_sse()["data"])
    assert data["depth"] == 1
    assert data["parent_id"] == "call-research"


def test_text_boundary_sse_preserves_nested_scope():
    (start,) = agent_events_to_sse(TextStarted(depth=1, parent_id="call-research", message_id="text-1"))
    (content,) = agent_events_to_sse(
        TextDelta(depth=1, parent_id="call-research", message_id="text-1", content="hello")
    )
    (end,) = agent_events_to_sse(TextEnded(depth=1, parent_id="call-research", message_id="text-1", content="hello"))

    for event in (start, content, end):
        data = json.loads(event.to_sse()["data"])
        assert data["depth"] == 1
        assert data["parent_id"] == "call-research"


def test_keepalive_is_typed_data_event_with_latest_seq():
    chunk = _keepalive(session_id="sess-1", latest_seq=42)

    assert "event: stream_keepalive" in chunk
    assert "data: " in chunk

    payload = json.loads(chunk.split("data: ", 1)[1])
    assert payload["type"] == "stream_keepalive"
    assert payload["session_id"] == "sess-1"
    assert payload["seq"] == 42
    assert payload["latest_seq"] == 42

    event = KeepaliveEvent(session_id="sess-1", latest_seq=42)
    assert json.loads(event.to_sse()["data"])["latest_seq"] == 42


def test_compaction_events_can_target_subagent_trace():
    start = CompactionStartedEvent(
        run_id="run-1",
        scope="agent",
        parent_tool_call_id="call-research",
    )
    done = CompactionFinishedEvent(
        run_id="run-1",
        messages_before=42,
        messages_after=9,
        scope="agent",
        parent_tool_call_id="call-research",
    )

    start_payload = json.loads(start.to_sse()["data"])
    done_payload = json.loads(done.to_sse()["data"])

    assert start_payload["type"] == "compaction_started"
    assert start_payload["scope"] == "agent"
    assert start_payload["parent_tool_call_id"] == "call-research"
    assert done_payload["type"] == "compaction_finished"
    assert done_payload["scope"] == "agent"
    assert done_payload["parent_tool_call_id"] == "call-research"
    assert done_payload["messages_before"] == 42
    assert done_payload["messages_after"] == 9


def test_compaction_agent_scope_requires_parent_tool_call():
    with pytest.raises(ValueError, match="agent compaction requires parent_tool_call_id"):
        CompactionStartedEvent(run_id="run-1", scope="agent")


def test_compaction_run_scope_rejects_parent_tool_call():
    with pytest.raises(ValueError, match="run compaction cannot include parent_tool_call_id"):
        CompactionFinishedEvent(
            run_id="run-1",
            messages_before=42,
            messages_after=9,
            scope="run",
            parent_tool_call_id="call-research",
        )


def test_task_lifecycle_events_include_parent_tool_call():
    start = TaskStartedEvent(
        run_id="run-1",
        task_id="call-research",
        parent_tool_call_id="call-research",
        name="Research",
        summary="look up event systems",
        depth=1,
    )
    done = TaskFinishedEvent(
        run_id="run-1",
        task_id="call-research",
        parent_tool_call_id="call-research",
        name="Research Event Systems",
        status="completed",
        summary="done",
        depth=1,
    )

    start_payload = json.loads(start.to_sse()["data"])
    done_payload = json.loads(done.to_sse()["data"])

    assert start_payload["type"] == "task_started"
    assert start_payload["task_id"] == "call-research"
    assert start_payload["parent_tool_call_id"] == "call-research"
    assert done_payload["type"] == "task_finished"
    assert done_payload["name"] == "Research Event Systems"
    assert done_payload["status"] == "completed"


def test_todo_updated_event_round_trips_payload():
    event = TodoUpdatedEvent(
        run_id="run-1",
        tool_call_id="call-todos",
        explanation="Split the implementation.",
        items=[
            {"content": "Research prior art", "status": "completed"},
            {"content": "Implement server tool", "status": "in_progress"},
            {"content": "Polish desktop UI", "status": "pending"},
        ],
    )

    payload = json.loads(event.to_sse()["data"])

    assert payload["type"] == "todo_updated"
    assert payload["run_id"] == "run-1"
    assert payload["tool_call_id"] == "call-todos"
    assert payload["items"][1] == {
        "content": "Implement server tool",
        "status": "in_progress",
    }


@pytest.mark.asyncio
async def test_research_child_reasoning_is_not_emitted_to_parent(monkeypatch):
    prompt_cache_keys = []

    class FakeLLM:
        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None, prompt_cache_key=None):
            prompt_cache_keys.append(prompt_cache_key)
            yield ReasoningContentDelta("internal research thought")
            yield make_text_response("child answer", model=model)

    monkeypatch.setattr(spawner_module, "llm_client", FakeLLM())

    emitted = []

    async def emit(event):
        emitted.append(event)

    executor = make_executor()
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(emit=emit),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )

    spawn = create_spawn_fn(executor=executor, model="test-model", max_depth=3, current_depth=0)
    result = await spawn(
        ctx,
        "research task",
        system_prompt="research system",
        tools=[],
        parent_id="call-research",
        timeout=1,
    )

    assert result.text == "child answer"
    assert [event.type.value for event in emitted] == ["task_started", "task_finished"]
    assert len(prompt_cache_keys) == 1
    assert prompt_cache_keys[0].startswith("test::")


@pytest.mark.asyncio
async def test_full_subagent_tool_calls_stay_on_child_bus(monkeypatch):
    """A FULL-isolation foreground subagent streams its tool calls ONLY to its own
    child session bus; the parent trace gets lifecycle events only (the parent
    renders FULL agents as drill-in leaves). To watch a FULL agent's tool calls
    live you open its session — they are never forwarded to the parent trace."""
    from ntrp.tools.core import ToolResult, tool
    from ntrp.tools.core.context import ChildSession
    from ntrp.tools.core.types import ToolAction, ToolPolicy, ToolScope
    from tests.helpers import make_tool_response

    class FakeLLM:
        def __init__(self):
            self.n = 0

        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None, prompt_cache_key=None):
            self.n += 1
            if self.n == 1:
                yield ReasoningContentDelta("internal thought")
                yield make_tool_response("ping", {})
            else:
                yield make_text_response("child answer", model=model)

    monkeypatch.setattr(spawner_module, "llm_client", FakeLLM())

    async def ping_exec(execution, *args, **kwargs):
        return ToolResult(content="pong", preview="pong")

    ping_tool = tool(
        display_name="Ping",
        description="ping",
        policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL),
        execute=ping_exec,
    )

    parent_emitted = []
    child_emitted = []

    async def parent_emit(event):
        parent_emitted.append(event)

    async def child_emit(event):
        child_emitted.append(event)

    child_finished: list = []

    async def child_io_factory(_params):
        async def _aclose():
            return None

        async def _finish(status):
            child_finished.append(status)

        return ChildSession(io=IOBridge(emit=child_emit), finish=_finish, aclose=_aclose)

    executor = make_executor({"ping": ping_tool})
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3, child_io_factory=child_io_factory),
        io=IOBridge(emit=parent_emit),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )

    spawn = create_spawn_fn(executor=executor, model="test-model", max_depth=3, current_depth=0)
    result = await spawn(
        ctx,
        "workflow agent task",
        system_prompt="sys",
        parent_id="call-wf",
        lifecycle_id="call-wf:agent1",
        timeout=2,
    )

    assert result.text == "child answer"
    # Parent trace sees ONLY lifecycle — no tool calls, no reasoning leak.
    types = [e.type.value for e in parent_emitted]
    assert types == ["task_started", "task_finished"]
    assert not any(e.type.value == "TOOL_CALL_START" for e in parent_emitted)
    # The child's own bus carries the full detail — drill-in renders it live.
    assert any(e.type.value == "TOOL_CALL_START" for e in child_emitted)
    # The finish event rolls up the per-agent tool-call count (one ping call).
    assert parent_emitted[-1].tool_count == 1
    # And the spawner closes the child's run framing on a terminal status, so the
    # viewed session collapses live → done instead of streaming forever.
    assert child_finished == ["completed"]


@pytest.mark.asyncio
async def test_child_io_factory_frames_full_run_lifecycle_on_child_bus():
    """The production contract: a spawned child session's OWN bus carries the
    standard run lifecycle — RunStarted when it opens, RunFinished when it ends —
    so the client renders it live then collapses to done, exactly like a
    top-level thread. (The desktop gates live-vs-done on this lifecycle, not on
    content.) finish() is idempotent, so the terminal frame fires exactly once."""
    from unittest.mock import AsyncMock

    from ntrp.services.chat import make_child_io_factory
    from ntrp.tools.core.context import ChildIOParams

    buses = BusRegistry()
    factory = make_child_io_factory(
        buses,
        SimpleNamespace(get_active_run=lambda _sid: None),
        record_approval=AsyncMock(),
        resolve_approval=AsyncMock(),
        approval_timeout_seconds=300,
    )

    child = await factory(ChildIOParams(session_id="parent::abc", run_id="run-1", pending_approvals={}))
    child_bus = buses.get("parent::abc")

    # Opening the child = a live run: RunStarted lands on its OWN bus, first,
    # carrying the same run_id the runtime snapshot surfaces for the session.
    assert [type(r.event).__name__ for r in child_bus._recent] == ["RunStartedEvent"]
    assert child_bus._recent[0].event.run_id == "run-1"
    assert child_bus._recent[0].event.session_id == "parent::abc"

    await child.finish("completed")
    await child.finish("completed")  # idempotent — no second terminal frame
    assert [type(r.event).__name__ for r in child_bus._recent] == ["RunStartedEvent", "RunFinishedEvent"]
    assert child_bus._recent[1].event.run_id == "run-1"


@pytest.mark.asyncio
async def test_child_io_factory_emits_run_cancelled_on_cancel():
    """A cancelled subagent collapses the viewed child session via RunCancelled,
    not RunFinished — so the desktop reads it as cancelled, not completed."""
    from unittest.mock import AsyncMock

    from ntrp.services.chat import make_child_io_factory
    from ntrp.tools.core.context import ChildIOParams

    buses = BusRegistry()
    factory = make_child_io_factory(
        buses,
        SimpleNamespace(get_active_run=lambda _sid: None),
        record_approval=AsyncMock(),
        resolve_approval=AsyncMock(),
        approval_timeout_seconds=300,
    )
    child = await factory(ChildIOParams(session_id="p::x", run_id="r1", pending_approvals={}))
    await child.finish("cancelled")
    assert [type(r.event).__name__ for r in buses.get("p::x")._recent] == ["RunStartedEvent", "RunCancelledEvent"]


@pytest.mark.asyncio
async def test_child_io_factory_aclose_keeps_bus_while_run_active():
    """aclose() must NOT evict a child bus while its run is still active — it uses
    the SAME liveness predicate as the SSE endpoint (run_registry.get_active_run),
    so a connecting viewer can't have the bus reset out from under it. The old
    `is_active=lambda: False` raced that window and reset the bus seq."""
    from unittest.mock import AsyncMock

    from ntrp.services.chat import make_child_io_factory
    from ntrp.tools.core.context import ChildIOParams

    active = {"on": True}
    buses = BusRegistry()
    factory = make_child_io_factory(
        buses,
        SimpleNamespace(get_active_run=lambda _sid: object() if active["on"] else None),
        record_approval=AsyncMock(),
        resolve_approval=AsyncMock(),
        approval_timeout_seconds=300,
    )
    child = await factory(ChildIOParams(session_id="p::live", run_id="r1", pending_approvals={}))
    assert buses.get("p::live") is not None

    await child.aclose()  # run still active, no subscribers → bus KEPT
    assert buses.get("p::live") is not None

    active["on"] = False
    await child.aclose()  # run done, no subscribers → bus evicted
    assert buses.get("p::live") is None


@pytest.mark.asyncio
async def test_catchup_replay_emits_structural_events_only():
    """Catch-up replay (snapshot/durable) emits STRUCTURAL events only — never
    the ephemeral deltas (token text, tool args, reasoning) — even with
    stream=True. A client joining an ongoing run lands on the settled current
    state instead of re-streaming the whole token/tool-call history (the "full
    replay" churn). The live tail still carries deltas for what's streaming now.
    Boundaries are passed through verbatim, never synthesized."""
    buses = BusRegistry()
    bus = buses.get_or_create("sess-1")
    await bus.emit(TextMessageStartEvent(message_id="text-1"))
    await bus.emit(TextMessageContentEvent(message_id="text-1", delta="hello"))
    await bus.emit(TextMessageEndEvent(message_id="text-1", content="hello"))

    stream = _event_stream("sess-1", buses, RunRegistry(), stream=True)
    try:
        chunks = [await anext(stream), await anext(stream)]
    finally:
        await stream.aclose()

    payloads = [json.loads(chunk.split("data: ", 1)[1].strip()) for chunk in chunks]
    # CONTENT delta dropped on replay; START + END (carrying full content) kept.
    assert [payload["type"] for payload in payloads] == [
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_END",
    ]
    assert payloads[1]["content"] == "hello"
    assert all(payload["message_id"] == "text-1" for payload in payloads)


@pytest.mark.asyncio
async def test_event_stream_filters_snapshot_text_deltas_when_stream_false():
    buses = BusRegistry()
    bus = buses.get_or_create("sess-1")
    await bus.emit(TextMessageStartEvent(message_id="text-1"))
    await bus.emit(TextMessageContentEvent(message_id="text-1", delta="hello"))
    await bus.emit(TextMessageEndEvent(message_id="text-1", content="hello"))

    stream = _event_stream("sess-1", buses, RunRegistry(), stream=False)
    try:
        chunks = [await anext(stream), await anext(stream)]
    finally:
        await stream.aclose()

    payloads = [json.loads(chunk.split("data: ", 1)[1].strip()) for chunk in chunks]
    assert [payload["type"] for payload in payloads] == [
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_END",
    ]
    assert all(payload["type"] != "TEXT_MESSAGE_CONTENT" for payload in payloads)


@pytest.mark.asyncio
async def test_event_stream_emits_stream_reset_on_replay_gap():
    buses = BusRegistry()
    bus = buses.get_or_create("sess-1")
    await bus.emit(ThinkingEvent(status="saved-a"))
    await bus.emit(ThinkingEvent(status="saved-b"))
    bus.mark_checkpoint()
    await bus.emit(ThinkingEvent(status="live-tail"))

    stream = _event_stream("sess-1", buses, RunRegistry(), stream=True, after_seq=1)
    try:
        reset_chunk = await anext(stream)
        replay_chunk = await anext(stream)
    finally:
        await stream.aclose()

    reset_payload = json.loads(reset_chunk.split("data: ", 1)[1].strip())
    replay_payload = json.loads(replay_chunk.split("data: ", 1)[1].strip())

    assert "event: stream_reset" in reset_chunk
    assert reset_payload["type"] == "stream_reset"
    assert reset_payload["reason"] == "replay_gap"
    assert reset_payload["session_id"] == "sess-1"
    assert reset_payload["seq"] == 2
    assert replay_payload["type"] == "thinking"
    assert replay_payload["status"] == "live-tail"
    assert replay_payload["seq"] == 3


@pytest.mark.asyncio
async def test_event_stream_emits_stream_reset_on_future_cursor():
    buses = BusRegistry()
    bus = buses.get_or_create("sess-1")
    await bus.emit(ThinkingEvent(status="old-generation"))
    bus.mark_checkpoint()

    stream = _event_stream("sess-1", buses, RunRegistry(), stream=True, after_seq=44)
    try:
        reset_chunk = await anext(stream)
    finally:
        await stream.aclose()

    reset_payload = json.loads(reset_chunk.split("data: ", 1)[1].strip())

    assert "event: stream_reset" in reset_chunk
    assert reset_payload["type"] == "stream_reset"
    assert reset_payload["reason"] == "future_cursor"
    assert reset_payload["session_id"] == "sess-1"
    assert reset_payload["seq"] == 1


@pytest.mark.asyncio
async def test_event_stream_replays_pending_approval():
    buses = BusRegistry()
    bus = buses.get_or_create("sess-1")
    await bus.emit(ApprovalNeededEvent(tool_id="tool-1", name="edit_file"))

    registry = RunRegistry()
    run = registry.create_run("sess-1")
    run.status = RunStatus.RUNNING
    run.pending_approvals["tool-1"] = asyncio.get_running_loop().create_future()

    stream = _event_stream("sess-1", buses, registry, stream=True, after_seq=0)
    try:
        chunk = await anext(stream)
    finally:
        await stream.aclose()

    payload = json.loads(chunk.split("data: ", 1)[1].strip())
    assert payload["type"] == "approval_needed"
    assert payload["tool_id"] == "tool-1"
    assert payload["replay"] is True


@pytest.mark.asyncio
async def test_event_stream_skips_resolved_approval_replay():
    buses = BusRegistry()
    bus = buses.get_or_create("sess-1")
    await bus.emit(ApprovalNeededEvent(tool_id="tool-1", name="edit_file"))
    await bus.emit(ThinkingEvent(status="tail"))

    registry = RunRegistry()
    run = registry.create_run("sess-1")
    run.status = RunStatus.RUNNING
    future = asyncio.get_running_loop().create_future()
    future.set_result({"approved": True, "result": ""})
    run.pending_approvals["tool-1"] = future

    class BrieflyStaleStore:
        async def get_latest_session_event_seq(self, session_id: str) -> int:
            return 0

        async def get_latest_session_checkpoint_seq(self, session_id: str) -> int:
            return 0

        async def list_session_events(self, session_id: str, *, after_seq: int, limit: int) -> list:
            return []

        async def list_pending_tool_approvals(self, session_id: str) -> list[dict]:
            return [{"tool_call_id": "tool-1"}]

    stream = _event_stream(
        "sess-1",
        buses,
        registry,
        stream=True,
        after_seq=0,
        event_store=BrieflyStaleStore(),
    )
    try:
        chunk = await anext(stream)
    finally:
        await stream.aclose()

    payload = json.loads(chunk.split("data: ", 1)[1].strip())
    assert payload["type"] == "thinking"
    assert payload["status"] == "tail"
    assert payload["seq"] == 2


@pytest.mark.asyncio
async def test_event_stream_delivers_slow_consumer_reset_even_at_checkpoint_cursor():
    buses = BusRegistry()
    bus = buses.get_or_create("sess-1")
    bus.subscriber_queue_size = 2

    stream = _event_stream("sess-1", buses, RunRegistry(), stream=True)
    try:
        first_chunk_task = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)

        await bus.emit(ThinkingEvent(status="one"))
        first_chunk = await first_chunk_task

        await bus.emit(ThinkingEvent(status="two"))
        await bus.emit(ThinkingEvent(status="three"))
        await bus.emit(ThinkingEvent(status="four"))

        reset_chunk = await anext(stream)
    finally:
        await stream.aclose()

    first_payload = json.loads(first_chunk.split("data: ", 1)[1].strip())
    reset_payload = json.loads(reset_chunk.split("data: ", 1)[1].strip())

    assert first_payload["status"] == "one"
    assert "event: stream_reset" in reset_chunk
    assert reset_payload["type"] == "stream_reset"
    assert reset_payload["reason"] == "slow_consumer"
    assert reset_payload["session_id"] == "sess-1"
    assert reset_payload["seq"] == 0


@pytest.mark.asyncio
async def test_event_stream_delivers_slow_consumer_reset_with_size_one_queue():
    buses = BusRegistry()
    bus = buses.get_or_create("sess-1")
    bus.subscriber_queue_size = 1

    stream = _event_stream("sess-1", buses, RunRegistry(), stream=True)
    try:
        first_chunk_task = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)

        await bus.emit(ThinkingEvent(status="one"))
        first_chunk = await first_chunk_task

        await bus.emit(ThinkingEvent(status="two"))
        await bus.emit(ThinkingEvent(status="three"))

        reset_chunk = await anext(stream)
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
    finally:
        await stream.aclose()

    first_payload = json.loads(first_chunk.split("data: ", 1)[1].strip())
    reset_payload = json.loads(reset_chunk.split("data: ", 1)[1].strip())

    assert first_payload["status"] == "one"
    assert reset_payload["type"] == "stream_reset"
    assert reset_payload["reason"] == "slow_consumer"


@pytest.mark.asyncio
async def test_automation_event_stream_replays_after_seq():
    buses = BusRegistry()
    bus = buses.get_or_create("automation:events")
    await bus.emit(AutomationProgressEvent(task_id="loop-a", status="old"))
    await bus.emit(AutomationProgressEvent(task_id="loop-a", status="new"))

    stream = _automation_event_stream(buses, after_seq=1)
    try:
        replay_chunk = await anext(stream)
    finally:
        await stream.aclose()

    replay_payload = json.loads(replay_chunk.split("data: ", 1)[1].strip())

    assert "event: automation_progress" in replay_chunk
    assert replay_payload["type"] == "automation_progress"
    assert replay_payload["task_id"] == "loop-a"
    assert replay_payload["status"] == "new"
    assert replay_payload["seq"] == 2
    assert replay_payload["replay"] is True


@pytest.mark.asyncio
async def test_automation_event_stream_without_cursor_does_not_replay_old_events(monkeypatch):
    monkeypatch.setattr("ntrp.server.routers.automation.KEEPALIVE_INTERVAL", 0)
    buses = BusRegistry()
    bus = buses.get_or_create("automation:events")
    await bus.emit(AutomationProgressEvent(task_id="loop-a", status="old"))

    stream = _automation_event_stream(buses)
    try:
        first_chunk = await anext(stream)
    finally:
        await stream.aclose()

    payload = json.loads(first_chunk.split("data: ", 1)[1].strip())

    assert payload["type"] == "stream_keepalive"
    assert payload["latest_seq"] == 1


@pytest.mark.asyncio
async def test_automation_event_stream_closes_after_slow_consumer_reset_with_size_one_queue():
    buses = BusRegistry()
    bus = buses.get_or_create("automation:events")
    bus.subscriber_queue_size = 1

    stream = _automation_event_stream(buses)
    try:
        first_chunk_task = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)

        await bus.emit(AutomationProgressEvent(task_id="loop-a", status="one"))
        first_chunk = await first_chunk_task

        await bus.emit(AutomationProgressEvent(task_id="loop-a", status="two"))
        await bus.emit(AutomationProgressEvent(task_id="loop-a", status="three"))

        reset_chunk = await anext(stream)
        next_chunk_task = asyncio.create_task(anext(stream))
        await asyncio.sleep(0.05)
        assert next_chunk_task.done()
        with pytest.raises(StopAsyncIteration):
            await next_chunk_task
    finally:
        await stream.aclose()

    first_payload = json.loads(first_chunk.split("data: ", 1)[1].strip())
    reset_payload = json.loads(reset_chunk.split("data: ", 1)[1].strip())

    assert first_payload["status"] == "one"
    assert reset_payload["type"] == "stream_reset"
    assert reset_payload["reason"] == "slow_consumer"


@pytest.mark.asyncio
async def test_chat_event_stream_replays_tail_after_10k_event_reconnect():
    buses = BusRegistry()
    bus = buses.get_or_create("stress-session")
    total = RECENT_BUFFER_MAX + 25

    for i in range(total):
        await bus.emit(ThinkingEvent(status=f"event-{i}"))

    after_seq = total - 5
    stream = _event_stream("stress-session", buses, RunRegistry(), stream=True, after_seq=after_seq)
    try:
        chunks = [await anext(stream) for _ in range(5)]
    finally:
        await stream.aclose()

    payloads = [json.loads(chunk.split("data: ", 1)[1].strip()) for chunk in chunks]
    assert [payload["seq"] for payload in payloads] == list(range(after_seq + 1, total + 1))
    assert [payload["status"] for payload in payloads] == [f"event-{i}" for i in range(after_seq, total)]


@pytest.mark.asyncio
async def test_chat_event_stream_resets_when_10k_event_reconnect_cursor_is_evicted():
    buses = BusRegistry()
    bus = buses.get_or_create("stress-gap-session")
    total = RECENT_BUFFER_MAX + 25

    for i in range(total):
        await bus.emit(ThinkingEvent(status=f"event-{i}"))

    stream = _event_stream("stress-gap-session", buses, RunRegistry(), stream=True, after_seq=1)
    try:
        reset_chunk = await anext(stream)
    finally:
        await stream.aclose()

    payload = json.loads(reset_chunk.split("data: ", 1)[1].strip())
    assert payload["type"] == "stream_reset"
    assert payload["reason"] == "replay_gap"


@pytest.mark.asyncio
async def test_automation_event_stream_replays_tail_after_10k_event_reconnect():
    buses = BusRegistry()
    bus = buses.get_or_create("automation:events")
    total = RECENT_BUFFER_MAX + 25

    for i in range(total):
        await bus.emit(AutomationProgressEvent(task_id="loop-a", status=f"event-{i}"))

    after_seq = total - 5
    stream = _automation_event_stream(buses, after_seq=after_seq)
    try:
        chunks = [await anext(stream) for _ in range(5)]
    finally:
        await stream.aclose()

    payloads = [json.loads(chunk.split("data: ", 1)[1].strip()) for chunk in chunks]
    assert [payload["seq"] for payload in payloads] == list(range(after_seq + 1, total + 1))
    assert [payload["status"] for payload in payloads] == [f"event-{i}" for i in range(after_seq, total)]


@pytest.mark.asyncio
async def test_automation_event_stream_emits_stream_reset_on_future_cursor():
    buses = BusRegistry()
    bus = buses.get_or_create("automation:events")
    await bus.emit(AutomationProgressEvent(task_id="loop-a", status="old"))

    stream = _automation_event_stream(buses, after_seq=44)
    try:
        reset_chunk = await anext(stream)
    finally:
        await stream.aclose()

    reset_payload = json.loads(reset_chunk.split("data: ", 1)[1].strip())

    assert "event: stream_reset" in reset_chunk
    assert reset_payload["type"] == "stream_reset"
    assert reset_payload["reason"] == "replay_gap"
    assert reset_payload["session_id"] == "automation:events"
    assert reset_payload["seq"] == 1


@pytest.mark.asyncio
async def test_run_agent_loop_emits_text_end_before_run_cancelled():
    run = RunState(run_id="run-1", session_id="sess-1")
    bus = SessionBus(session_id="sess-1")

    class CancellingAgent:
        async def stream(self, messages):
            yield TextStarted(message_id="text-1")
            yield TextDelta(message_id="text-1", content="hello")
            run.cancelled = True
            yield TextEnded(message_id="text-1", content="hello")

    await run_agent_loop(SimpleNamespace(run=run), CancellingAgent(), bus)

    assert [record.event.type.value for record in bus._recent] == [
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "run_cancelled",
    ]


@pytest.mark.asyncio
async def test_run_agent_loop_closes_open_text_on_cooperative_cancel():
    run = RunState(run_id="run-1", session_id="sess-1")

    class CancelAfterSecondContentBus(SessionBus):
        def __init__(self):
            super().__init__(session_id="sess-1")
            self.content_count = 0

        async def emit(self, event):
            await super().emit(event)
            if isinstance(event, TextMessageContentEvent):
                self.content_count += 1
                if self.content_count == 2:
                    run.cancelled = True

    class CooperativeCancellingAgent:
        async def stream(self, messages):
            yield TextStarted(depth=1, parent_id="call-research", message_id="text-1")
            yield TextDelta(depth=1, parent_id="call-research", message_id="text-1", content="hello")
            yield TextDelta(depth=1, parent_id="call-research", message_id="text-1", content=" world")
            yield TextDelta(depth=1, parent_id="call-research", message_id="text-1", content=" dropped")

    bus = CancelAfterSecondContentBus()

    await run_agent_loop(SimpleNamespace(run=run), CooperativeCancellingAgent(), bus)

    assert [record.event.type.value for record in bus._recent] == [
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "run_cancelled",
    ]
    end = bus._recent[3].event
    assert isinstance(end, TextMessageEndEvent)
    assert end.message_id == "text-1"
    assert end.content == "hello world"
    assert end.depth == 1
    assert end.parent_id == "call-research"


@pytest.mark.asyncio
async def test_run_agent_loop_closes_text_before_backgrounding():
    run = RunState(run_id="run-1", session_id="sess-1")
    bus = SessionBus(session_id="sess-1")

    class BackgroundingAgent:
        async def stream(self, messages):
            yield TextStarted(message_id="text-1")
            yield TextDelta(message_id="text-1", content="hello")
            run.backgrounded = True
            yield TextEnded(message_id="text-1", content="hello")

    result, bg_gen = await run_agent_loop(SimpleNamespace(run=run), BackgroundingAgent(), bus)

    assert result is None
    assert bg_gen is not None
    assert [record.event.type.value for record in bus._recent] == [
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
    ]


@pytest.mark.asyncio
async def test_run_agent_loop_collects_current_tool_sources_before_background_handoff():
    run = RunState(run_id="run-1", session_id="sess-1")
    bus = SessionBus(session_id="sess-1")
    source = ToolSourceRef(provider="web", kind="page", ref="https://example.com", title="Example")

    class BackgroundingAgent:
        async def stream(self, messages):
            run.backgrounded = True
            yield ToolCompleted(
                tool_id="call-1",
                name="web_fetch",
                result="body",
                preview="body",
                duration_ms=1,
                is_error=False,
                data=None,
                display_name="Web Fetch",
                source_refs=(source,),
            )
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    result, bg_gen = await run_agent_loop(SimpleNamespace(run=run), BackgroundingAgent(), bus)

    assert result is None
    assert bg_gen is not None
    assert run.source_refs == [source.to_dict()]
    assert [record.event.type.value for record in bus._recent] == []
    assert isinstance(await anext(bg_gen), Result)


@pytest.mark.asyncio
async def test_run_chat_keeps_backgrounded_run_active_until_drain_finishes(monkeypatch):
    from ntrp.services import chat as chat_service

    registry = RunRegistry()
    run = registry.create_run("sess-bg")
    session_state = SessionState(session_id="sess-bg", started_at=datetime.now(UTC))
    blocker = asyncio.Future()

    class Store:
        async def get_background_agent_result(self, session_id, task_id):
            return None

    class RecordingSessionService:
        store = Store()

        async def save(self, session_state, messages, metadata=None):
            return None

        async def save_progress(self, session_state, messages):
            return None

        async def record_chat_run_status(self, *args, **kwargs):
            return None

        async def update_goal(self, *args, **kwargs):
            return None

        async def update_chat_idempotency_key(self, *args, **kwargs):
            return None

    class BackgroundingAgent:
        def __init__(self):
            self.hooks = SimpleNamespace(on_response=None, on_step_finish=None, get_pending_messages=None)
            self.tools = []
            self._last_response = None

        async def stream(self, messages):
            yield TextStarted(message_id="text-1")
            yield TextDelta(message_id="text-1", content="hello")
            run.backgrounded = True
            yield TextEnded(message_id="text-1", content="hello")
            await blocker

    monkeypatch.setattr(chat_service, "create_agent", lambda **_kwargs: BackgroundingAgent())
    ctx = ChatContext(
        run=run,
        session_state=session_state,
        is_init=False,
        executor=SimpleNamespace(registry={}),
        tools=[],
        config=SimpleNamespace(approval_timeout_seconds=300, compactor=None, model=""),
        available_integrations=[],
        integration_errors={},
        session_service=RecordingSessionService(),
        run_registry=registry,
    )
    bus = SessionBus(session_id="sess-bg")

    await run_chat(ctx, bus, BusRegistry())

    assert registry.get_active_run("sess-bg") is run
    assert registry.get_accepting_run("sess-bg") is None
    status = registry.get_status(datetime.now(UTC))
    assert status["active_runs"][0]["status"] == "backgrounded"
    event = next(record.event for record in bus._recent if record.event.type.value == "run_backgrounded")
    assert event.session_id == "sess-bg"

    if run.drain_task:
        run.cancelled = True
        run.drain_task.cancel()
        with suppress(asyncio.CancelledError):
            await run.drain_task


@pytest.mark.asyncio
async def test_run_agent_loop_closes_text_when_task_cancelled_during_content_flush():
    run_registry = RunRegistry()
    run = run_registry.create_run("sess-1")

    class CancellingContentFlushBus(SessionBus):
        async def emit(self, event):
            await super().emit(event)
            if isinstance(event, TextMessageContentEvent):
                run_registry.cancel_run(run.run_id)
                await asyncio.sleep(0)

    class AgentWithOpenText:
        async def stream(self, messages):
            yield TextStarted(depth=1, parent_id="call-research", message_id="text-1")
            yield TextDelta(depth=1, parent_id="call-research", message_id="text-1", content="hello")
            yield TextEnded(depth=1, parent_id="call-research", message_id="text-1", content="hello")

    bus = CancellingContentFlushBus(session_id="sess-1")
    task = asyncio.create_task(run_agent_loop(SimpleNamespace(run=run), AgentWithOpenText(), bus))
    run.task = task

    await task

    assert [record.event.type.value for record in bus._recent] == [
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "run_cancelled",
    ]
    end = bus._recent[2].event
    assert isinstance(end, TextMessageEndEvent)
    assert end.message_id == "text-1"
    assert end.content == "hello"
    assert end.depth == 1
    assert end.parent_id == "call-research"


@pytest.mark.asyncio
async def test_run_agent_loop_hard_task_cancel_emits_terminal_cancelled():
    run = RunState(run_id="run-1", session_id="sess-1")
    bus = SessionBus(session_id="sess-1")
    released = asyncio.Event()

    class AgentWithBlockingStream:
        async def stream(self, messages):
            yield TextStarted(depth=1, parent_id="call-research", message_id="text-1")
            yield TextDelta(depth=1, parent_id="call-research", message_id="text-1", content="partial")
            await released.wait()

    task = asyncio.create_task(run_agent_loop(SimpleNamespace(run=run), AgentWithBlockingStream(), bus))

    async def wait_for_streamed_content():
        while len(bus._recent) < 2:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_streamed_content(), timeout=1)

    task.cancel()
    await task

    event_types = [record.event.type.value for record in bus._recent]
    assert event_types == [
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "run_cancelled",
    ]
    assert event_types.count("run_cancelled") == 1
    end = bus._recent[2].event
    assert isinstance(end, TextMessageEndEvent)
    assert end.message_id == "text-1"
    assert end.content == "partial"
    assert end.depth == 1
    assert end.parent_id == "call-research"
    assert run.cancelled is True


@pytest.mark.asyncio
async def test_run_chat_emits_cancelled_when_task_cancelled_before_agent_loop():
    registry = RunRegistry()
    run = registry.create_run("sess-1")
    session_state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))

    class NoopSessionService:
        async def save(self, session_state, messages, metadata=None):
            return None

    class CancellingInitialEmitBus(SessionBus):
        async def emit(self, event):
            await super().emit(event)
            if event.type.value == "RUN_STARTED":
                task = asyncio.current_task()
                assert task is not None
                task.cancel()
                await asyncio.sleep(0)

    bus = CancellingInitialEmitBus(session_id="sess-1")
    queue = bus.subscribe()
    registry = RunRegistry()
    registry._runs[run.run_id] = run
    ctx = ChatContext(
        run=run,
        session_state=session_state,
        is_init=False,
        executor=SimpleNamespace(),
        tools=[],
        config=SimpleNamespace(approval_timeout_seconds=300),
        available_integrations=[],
        integration_errors={},
        session_service=NoopSessionService(),
        run_registry=registry,
    )

    await run_chat(ctx, bus, BusRegistry())

    events = []
    while not queue.empty():
        record = queue.get_nowait()
        assert record is not None
        events.append(record.event.type.value)

    assert events.count("run_cancelled") == 1
    assert events == ["RUN_STARTED", "run_cancelled"]
    assert run.cancelled is True
    assert run.cancel_terminal_emitted is True
    assert run.status == RunStatus.CANCELLED
    assert registry.get_active_run("sess-1") is None


@pytest.mark.asyncio
async def test_run_chat_persists_budget_stop_reason(monkeypatch):
    from ntrp.services import chat as chat_service

    registry = RunRegistry()
    run = registry.create_run("sess-1")
    session_state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))

    class Store:
        async def get_background_agent_result(self, session_id, task_id):
            return None

    class RecordingSessionService:
        def __init__(self):
            self.store = Store()
            self.statuses = []

        async def save(self, session_state, messages, metadata=None):
            return None

        async def save_progress(self, session_state, messages):
            return None

        async def record_chat_run_status(self, run_id, status, *, stop_reason=None, last_seq=None):
            self.statuses.append((run_id, status, stop_reason, last_seq))

    class FakeAgent:
        def __init__(self):
            self.hooks = SimpleNamespace(on_response=None, on_step_finish=None, get_pending_messages=None)
            self.tools = []

        async def stream(self, messages):
            yield Result(text="", stop_reason=StopReason.MAX_COST, steps=0, usage=Usage())

    service = RecordingSessionService()
    monkeypatch.setattr(chat_service, "create_agent", lambda **_kwargs: FakeAgent())
    ctx = ChatContext(
        run=run,
        session_state=session_state,
        is_init=False,
        executor=SimpleNamespace(),
        tools=[],
        config=SimpleNamespace(approval_timeout_seconds=300),
        available_integrations=[],
        integration_errors={},
        session_service=service,
        run_registry=registry,
    )

    await run_chat(ctx, SessionBus(session_id="sess-1"), BusRegistry())

    assert service.statuses[-1][1] == RunStatus.COMPLETED.value
    assert service.statuses[-1][2] == StopReason.MAX_COST.value


@pytest.mark.asyncio
async def test_run_chat_records_durable_message_count_for_trimmed_loop(monkeypatch):
    from ntrp.services import chat as chat_service

    registry = RunRegistry()
    run = registry.create_run("sess-1")
    run.loop_task_id = "loop-1"
    run.history_prefix = [{"role": "user", "content": f"old-{i}"} for i in range(3)]
    run.messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "tick", "is_meta": True}]
    session_state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))

    class Store:
        async def get_background_agent_result(self, session_id, task_id):
            return None

    class RecordingSessionService:
        def __init__(self):
            self.store = Store()
            self.saved_metadata = None

        async def save(self, session_state, messages, metadata=None):
            self.saved_metadata = metadata

        async def save_progress(self, session_state, messages):
            return None

        async def record_chat_run_status(self, *args, **kwargs):
            return None

        async def update_goal(self, *args, **kwargs):
            return None

    class FakeAgent:
        def __init__(self):
            self.hooks = SimpleNamespace(on_response=None, on_step_finish=None, get_pending_messages=None)
            self.tools = []
            self._last_response = None

        async def stream(self, messages):
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=0, usage=Usage(prompt_tokens=1))

    service = RecordingSessionService()
    monkeypatch.setattr(chat_service, "create_agent", lambda **_kwargs: FakeAgent())
    ctx = ChatContext(
        run=run,
        session_state=session_state,
        is_init=False,
        executor=SimpleNamespace(),
        tools=[],
        config=SimpleNamespace(approval_timeout_seconds=300),
        available_integrations=[],
        integration_errors={},
        session_service=service,
        run_registry=registry,
    )

    await run_chat(ctx, SessionBus(session_id="sess-1"), BusRegistry())

    assert service.saved_metadata["last_message_count"] == 5


@pytest.mark.asyncio
async def test_run_chat_final_save_failure_emits_error_not_finished(monkeypatch):
    from ntrp.services import chat as chat_service

    registry = RunRegistry()
    run = registry.create_run("sess-1")
    session_state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))

    class Store:
        async def get_background_agent_result(self, session_id, task_id):
            return None

    class FailingFinalSaveService:
        def __init__(self):
            self.store = Store()
            self.statuses = []

        async def save(self, session_state, messages, metadata=None):
            raise RuntimeError("final save down")

        async def save_progress(self, session_state, messages):
            return None

        async def record_chat_run_status(
            self,
            run_id,
            status,
            *,
            stop_reason=None,
            last_seq=None,
            error_code=None,
            error_message=None,
        ):
            self.statuses.append({"status": status, "error_code": error_code, "error_message": error_message})

    class FakeAgent:
        def __init__(self):
            self.hooks = SimpleNamespace(on_response=None, on_step_finish=None, get_pending_messages=None)
            self.tools = []
            self._last_response = None

        async def stream(self, messages):
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=0, usage=Usage())

    service = FailingFinalSaveService()
    monkeypatch.setattr(chat_service, "create_agent", lambda **_kwargs: FakeAgent())
    bus = SessionBus(session_id="sess-1")
    ctx = ChatContext(
        run=run,
        session_state=session_state,
        is_init=False,
        executor=SimpleNamespace(),
        tools=[],
        config=SimpleNamespace(approval_timeout_seconds=300),
        available_integrations=[],
        integration_errors={},
        session_service=service,
        run_registry=registry,
    )

    await run_chat(ctx, bus, BusRegistry())

    event_types = [record.event.type.value for record in bus._recent]
    run_error = next(record.event for record in bus._recent if record.event.type.value == "RUN_ERROR")
    assert "RUN_FINISHED" not in event_types
    assert run_error.code == "run_finalization_failed"
    assert "final state" in run_error.message
    assert registry.get_active_run("sess-1") is None
    assert registry.get_run(run.run_id).status == RunStatus.ERROR
    assert service.statuses[-1]["status"] == RunStatus.ERROR.value
    assert service.statuses[-1]["error_code"] == "run_finalization_failed"


@pytest.mark.asyncio
async def test_run_chat_completed_outbox_failure_does_not_reclassify_run(monkeypatch):
    from ntrp.services import chat as chat_service

    registry = RunRegistry()
    run = registry.create_run("sess-1")
    session_state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))

    class Store:
        async def get_background_agent_result(self, session_id, task_id):
            return None

    class RecordingSessionService:
        def __init__(self):
            self.store = Store()
            self.statuses = []

        async def save(self, session_state, messages, metadata=None):
            return None

        async def save_progress(self, session_state, messages):
            return None

        async def record_chat_run_status(
            self,
            run_id,
            status,
            *,
            stop_reason=None,
            last_seq=None,
            error_code=None,
            error_message=None,
        ):
            self.statuses.append({"status": status, "error_code": error_code, "error_message": error_message})

    class FakeAgent:
        def __init__(self):
            self.hooks = SimpleNamespace(on_response=None, on_step_finish=None, get_pending_messages=None)
            self.tools = []
            self._last_response = None

        async def stream(self, messages):
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=0, usage=Usage())

    async def failing_enqueue(_event):
        raise RuntimeError("outbox down")

    service = RecordingSessionService()
    monkeypatch.setattr(chat_service, "create_agent", lambda **_kwargs: FakeAgent())
    bus = SessionBus(session_id="sess-1")
    ctx = ChatContext(
        run=run,
        session_state=session_state,
        is_init=False,
        executor=SimpleNamespace(),
        tools=[],
        config=SimpleNamespace(approval_timeout_seconds=300),
        available_integrations=[],
        integration_errors={},
        session_service=service,
        run_registry=registry,
        enqueue_run_completed=failing_enqueue,
    )

    await run_chat(ctx, bus, BusRegistry())

    event_types = [record.event.type.value for record in bus._recent]
    thinking = next(record.event for record in bus._recent if record.event.type.value == "thinking")
    assert thinking.session_id == "sess-1"
    assert thinking.run_id == run.run_id
    assert "RUN_ERROR" not in event_types
    assert "RUN_FINISHED" in event_types
    assert service.statuses[-1]["status"] == RunStatus.COMPLETED.value
    assert registry.get_run(run.run_id).status == RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_run_chat_completed_bus_failure_does_not_reclassify_run(monkeypatch):
    from ntrp.services import chat as chat_service

    registry = RunRegistry()
    run = registry.create_run("sess-1")
    session_state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))

    class Store:
        async def get_background_agent_result(self, session_id, task_id):
            return None

    class RecordingSessionService:
        def __init__(self):
            self.store = Store()
            self.statuses = []

        async def save(self, session_state, messages, metadata=None):
            return None

        async def save_progress(self, session_state, messages):
            return None

        async def record_chat_run_status(
            self,
            run_id,
            status,
            *,
            stop_reason=None,
            last_seq=None,
            error_code=None,
            error_message=None,
        ):
            self.statuses.append({"status": status, "error_code": error_code})

    class FailingFinishedBus(SessionBus):
        async def emit(self, event):
            if event.type.value == "RUN_FINISHED":
                raise RuntimeError("subscriber fanout failed")
            await super().emit(event)

    class FakeAgent:
        def __init__(self):
            self.hooks = SimpleNamespace(on_response=None, on_step_finish=None, get_pending_messages=None)
            self.tools = []
            self._last_response = None

        async def stream(self, messages):
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=0, usage=Usage())

    service = RecordingSessionService()
    monkeypatch.setattr(chat_service, "create_agent", lambda **_kwargs: FakeAgent())

    await run_chat(
        ChatContext(
            run=run,
            session_state=session_state,
            is_init=False,
            executor=SimpleNamespace(),
            tools=[],
            config=SimpleNamespace(approval_timeout_seconds=300),
            available_integrations=[],
            integration_errors={},
            session_service=service,
            run_registry=registry,
        ),
        FailingFinishedBus(session_id="sess-1"),
        BusRegistry(),
    )

    assert service.statuses[-1]["status"] == RunStatus.COMPLETED.value
    assert all(entry["status"] != RunStatus.ERROR.value for entry in service.statuses)
    assert registry.get_active_run("sess-1") is None
    assert registry.get_run(run.run_id).status == RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_run_chat_emits_live_token_usage_after_model_response(monkeypatch):
    from ntrp.services import chat as chat_service

    registry = RunRegistry()
    run = registry.create_run("sess-1")
    run.messages = [{"role": "user", "content": "hi"}]
    session_state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))

    class Store:
        async def get_background_agent_result(self, session_id, task_id):
            return None

    class RecordingSessionService:
        def __init__(self):
            self.store = Store()

        async def save(self, session_state, messages, metadata=None):
            return None

        async def save_progress(self, session_state, messages):
            return None

        async def record_chat_run_status(self, *args, **kwargs):
            return None

        async def update_goal(self, *args, **kwargs):
            return None

    class FakeAgent:
        def __init__(self):
            self.hooks = SimpleNamespace(on_response=None, on_step_finish=None, get_pending_messages=None)
            self.tools = []
            self._last_response = None

        async def stream(self, messages):
            response = make_text_response("done")
            object.__setattr__(
                response,
                "usage",
                Usage(prompt_tokens=10, completion_tokens=2, cache_read_tokens=3, cache_write_tokens=4),
            )
            self._last_response = response
            messages.append({"role": "assistant", "content": "done"})
            if self.hooks.on_response:
                await self.hooks.on_response(response)
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=0, usage=response.usage)

    monkeypatch.setattr(chat_service, "create_agent", lambda **_kwargs: FakeAgent())
    ctx = ChatContext(
        run=run,
        session_state=session_state,
        is_init=False,
        executor=SimpleNamespace(),
        tools=[],
        config=SimpleNamespace(approval_timeout_seconds=300),
        available_integrations=[],
        integration_errors={},
        session_service=RecordingSessionService(),
        run_registry=registry,
    )
    bus = SessionBus(session_id="sess-1")

    await run_chat(ctx, bus, BusRegistry())

    usage_events = [record.event for record in bus._recent if record.event.type.value == "token_usage"]
    assert len(usage_events) == 1
    event = usage_events[0]
    assert event.run_id == run.run_id
    assert event.usage == {"prompt": 10, "completion": 2, "total": 19, "cache_read": 3, "cache_write": 4}
    assert event.cost == 0.0
    assert event.message_count == 2
    finished_events = [record.event for record in bus._recent if record.event.type.value == "RUN_FINISHED"]
    assert finished_events[-1].context_input_tokens == 17


@pytest.mark.asyncio
async def test_active_goal_dispatches_hidden_continuation_after_user_turn(monkeypatch):
    from ntrp.services import chat as chat_service

    registry = RunRegistry()
    run = registry.create_run("sess-1")
    run.messages = [{"role": "user", "content": "is this active?", "client_id": "user-1"}]
    run.client_id = "user-1"
    run.input_message_index = 0
    session_state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))
    dispatched = []

    class Store:
        async def get_background_agent_result(self, session_id, task_id):
            return None

    class GoalSessionService:
        store = Store()

        async def save(self, session_state, messages, metadata=None):
            return None

        async def save_progress(self, session_state, messages):
            return None

        async def record_chat_run_status(self, *args, **kwargs):
            return None

        async def update_goal(self, *args, **kwargs):
            return None

        async def get_goal(self, session_id):
            return {"goal_id": "goal-1", "status": "active", "objective": "Keep going"}

    class FakeAgent:
        def __init__(self):
            self.hooks = SimpleNamespace(on_response=None, on_step_finish=None, get_pending_messages=None)
            self.tools = []
            self._last_response = None

        async def stream(self, messages):
            messages.append({"role": "assistant", "content": "Yes."})
            yield Result(text="Yes.", stop_reason=StopReason.END_TURN, steps=0, usage=Usage())

    async def dispatch(session_id, message, client_id=None, skip_approvals=False):
        dispatched.append((session_id, message, client_id, skip_approvals))

    monkeypatch.setattr(chat_service, "create_agent", lambda **_kwargs: FakeAgent())
    ctx = ChatContext(
        run=run,
        session_state=session_state,
        is_init=False,
        executor=SimpleNamespace(),
        tools=[],
        config=SimpleNamespace(approval_timeout_seconds=300),
        available_integrations=[],
        integration_errors={},
        session_service=GoalSessionService(),
        run_registry=registry,
        goal_id="goal-1",
        dispatch_session_message=dispatch,
    )

    await run_chat(ctx, SessionBus(session_id="sess-1"), BusRegistry())

    assert len(dispatched) == 1
    assert dispatched[0][0] == "sess-1"
    assert dispatched[0][1].startswith("<goal_context>")
    assert "Continue working toward the active session goal." in dispatched[0][1]
    assert "<objective>\nKeep going\n</objective>" in dispatched[0][1]
    assert dispatched[0][2].startswith("goal:goal-1:")
    assert dispatched[0][3] is True


@pytest.mark.asyncio
async def test_goal_meta_run_dispatches_followup_even_without_tool_activity(monkeypatch):
    from ntrp.services import chat as chat_service

    registry = RunRegistry()
    run = registry.create_run("sess-1")
    run.messages = [
        {"role": "user", "content": "old visible turn", "client_id": "user-1"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "old-tool", "name": "Bash", "arguments": "{}"}],
        },
        {"role": "tool", "content": "old result", "tool_call_id": "old-tool"},
        {"role": "user", "content": "Continue", "client_id": "goal:goal-1:1", "is_meta": True},
    ]
    run.client_id = "goal:goal-1:1"
    run.input_message_index = 3
    session_state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))
    dispatched = []

    class Store:
        async def get_background_agent_result(self, session_id, task_id):
            return None

    class GoalSessionService:
        store = Store()

        async def save(self, session_state, messages, metadata=None):
            return None

        async def save_progress(self, session_state, messages):
            return None

        async def record_chat_run_status(self, *args, **kwargs):
            return None

        async def update_goal(self, *args, **kwargs):
            return None

        async def get_goal(self, session_id):
            return {"goal_id": "goal-1", "status": "active", "objective": "Keep going"}

    class FakeAgent:
        def __init__(self):
            self.hooks = SimpleNamespace(on_response=None, on_step_finish=None, get_pending_messages=None)
            self.tools = []
            self._last_response = None

        async def stream(self, messages):
            messages.append({"role": "assistant", "content": "Still working."})
            yield Result(text="Still working.", stop_reason=StopReason.END_TURN, steps=0, usage=Usage())

    async def dispatch(*args, **kwargs):
        dispatched.append((args, kwargs))

    monkeypatch.setattr(chat_service, "create_agent", lambda **_kwargs: FakeAgent())
    ctx = ChatContext(
        run=run,
        session_state=session_state,
        is_init=False,
        executor=SimpleNamespace(),
        tools=[],
        config=SimpleNamespace(approval_timeout_seconds=300),
        available_integrations=[],
        integration_errors={},
        session_service=GoalSessionService(),
        run_registry=registry,
        goal_id="goal-1",
        dispatch_session_message=dispatch,
    )

    await run_chat(ctx, SessionBus(session_id="sess-1"), BusRegistry())

    assert len(dispatched) == 1
    assert dispatched[0][0][0] == "sess-1"
    assert dispatched[0][0][1].startswith("<goal_context>")
    assert dispatched[0][0][2].startswith("goal:goal-1:")
    assert dispatched[0][0][3] is True


@pytest.mark.asyncio
async def test_run_chat_does_not_overwrite_error_status(monkeypatch):
    from ntrp.services import chat as chat_service

    registry = RunRegistry()
    run = registry.create_run("sess-1")
    session_state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))

    class Store:
        async def get_background_agent_result(self, session_id, task_id):
            return None

    class RecordingSessionService:
        def __init__(self):
            self.store = Store()
            self.statuses = []

        async def save(self, session_state, messages, metadata=None):
            return None

        async def record_chat_run_status(self, run_id, status, *, stop_reason=None, last_seq=None):
            self.statuses.append((status, stop_reason))

    service = RecordingSessionService()
    monkeypatch.setattr(chat_service, "create_agent", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    ctx = ChatContext(
        run=run,
        session_state=session_state,
        is_init=False,
        executor=SimpleNamespace(),
        tools=[],
        config=SimpleNamespace(approval_timeout_seconds=300),
        available_integrations=[],
        integration_errors={},
        session_service=service,
        run_registry=registry,
    )

    await run_chat(ctx, SessionBus(session_id="sess-1"), BusRegistry())

    assert service.statuses[-1] == (RunStatus.ERROR.value, "boom")
    assert all(status != RunStatus.COMPLETED.value for status, _ in service.statuses)


@pytest.mark.asyncio
async def test_run_chat_surfaces_context_length_provider_error(monkeypatch):
    from ntrp.services import chat as chat_service

    registry = RunRegistry()
    run = registry.create_run("sess-1")
    session_state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))

    class ProviderError(Exception):
        body = {
            "error": {
                "type": "invalid_request_error",
                "code": "context_length_exceeded",
                "message": "Your input exceeds the context window of this model. Please adjust your input and try again.",
            }
        }

    class Store:
        async def get_background_agent_result(self, session_id, task_id):
            return None

    class RecordingSessionService:
        def __init__(self):
            self.store = Store()
            self.statuses = []

        async def save(self, session_state, messages, metadata=None):
            return None

        async def record_chat_run_status(
            self,
            run_id,
            status,
            *,
            stop_reason=None,
            last_seq=None,
            error_code=None,
            error_message=None,
        ):
            self.statuses.append(
                {
                    "status": status,
                    "stop_reason": stop_reason,
                    "last_seq": last_seq,
                    "error_code": error_code,
                    "error_message": error_message,
                }
            )

    async def failing_agent_loop(ctx, agent, bus):
        raise ProviderError()

    monkeypatch.setattr(
        chat_service, "create_agent", lambda **_kwargs: SimpleNamespace(hooks=SimpleNamespace(), tools=[])
    )
    monkeypatch.setattr(chat_service, "run_agent_loop", failing_agent_loop)
    service = RecordingSessionService()
    bus = SessionBus(session_id="sess-1")
    ctx = ChatContext(
        run=run,
        session_state=session_state,
        is_init=False,
        executor=SimpleNamespace(),
        tools=[],
        config=SimpleNamespace(approval_timeout_seconds=300),
        available_integrations=[],
        integration_errors={},
        session_service=service,
        run_registry=registry,
    )

    await run_chat(ctx, bus, BusRegistry())

    run_error = next(record.event for record in bus._recent if record.event.type.value == "RUN_ERROR")
    assert run_error.code == "context_length_exceeded"
    assert "context window" in run_error.message
    assert registry.get_active_run("sess-1") is None
    assert registry.get_run(run.run_id).status == RunStatus.ERROR
    assert service.statuses[-1]["status"] == RunStatus.ERROR.value
    assert service.statuses[-1]["error_code"] == "context_length_exceeded"
    assert "context window" in service.statuses[-1]["error_message"]
    assert all(entry["status"] != RunStatus.COMPLETED.value for entry in service.statuses)


@pytest.mark.asyncio
async def test_run_chat_final_save_failure_preserves_provider_error(monkeypatch):
    from ntrp.services import chat as chat_service

    registry = RunRegistry()
    run = registry.create_run("sess-1")
    session_state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))

    class ProviderError(Exception):
        body = {
            "error": {
                "type": "invalid_request_error",
                "code": "context_length_exceeded",
                "message": "Your input exceeds the context window of this model. Please adjust your input and try again.",
            }
        }

    class Store:
        async def get_background_agent_result(self, session_id, task_id):
            return None

    class FailingFinalSaveService:
        def __init__(self):
            self.store = Store()
            self.statuses = []

        async def save(self, session_state, messages, metadata=None):
            raise RuntimeError("final save down")

        async def record_chat_run_status(
            self,
            run_id,
            status,
            *,
            stop_reason=None,
            last_seq=None,
            error_code=None,
            error_message=None,
        ):
            self.statuses.append(
                {
                    "status": status,
                    "error_code": error_code,
                    "error_message": error_message,
                }
            )

    async def failing_agent_loop(ctx, agent, bus):
        raise ProviderError()

    monkeypatch.setattr(
        chat_service, "create_agent", lambda **_kwargs: SimpleNamespace(hooks=SimpleNamespace(), tools=[])
    )
    monkeypatch.setattr(chat_service, "run_agent_loop", failing_agent_loop)
    service = FailingFinalSaveService()
    bus = SessionBus(session_id="sess-1")
    ctx = ChatContext(
        run=run,
        session_state=session_state,
        is_init=False,
        executor=SimpleNamespace(),
        tools=[],
        config=SimpleNamespace(approval_timeout_seconds=300),
        available_integrations=[],
        integration_errors={},
        session_service=service,
        run_registry=registry,
    )

    await run_chat(ctx, bus, BusRegistry())

    run_errors = [record.event for record in bus._recent if record.event.type.value == "RUN_ERROR"]
    assert [event.code for event in run_errors] == ["context_length_exceeded"]
    assert service.statuses[-1]["status"] == RunStatus.ERROR.value
    assert service.statuses[-1]["error_code"] == "context_length_exceeded"


@pytest.mark.asyncio
async def test_run_chat_provider_error_status_write_failure_is_not_silently_completed(monkeypatch):
    from ntrp.services import chat as chat_service

    registry = RunRegistry()
    run = registry.create_run("sess-1")
    session_state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))

    class ProviderError(Exception):
        body = {
            "error": {
                "type": "invalid_request_error",
                "code": "context_length_exceeded",
                "message": "Your input exceeds the context window of this model.",
            }
        }

    class Store:
        async def get_background_agent_result(self, session_id, task_id):
            return None

    class FailingStatusService:
        store = Store()

        async def save(self, session_state, messages, metadata=None):
            return None

        async def record_chat_run_status(self, run_id, status, **kwargs):
            if status == RunStatus.ERROR.value:
                raise RuntimeError("status db down")
            return None

    async def failing_agent_loop(ctx, agent, bus):
        raise ProviderError()

    monkeypatch.setattr(
        chat_service, "create_agent", lambda **_kwargs: SimpleNamespace(hooks=SimpleNamespace(), tools=[])
    )
    monkeypatch.setattr(chat_service, "run_agent_loop", failing_agent_loop)
    bus = SessionBus(session_id="sess-1")
    ctx = ChatContext(
        run=run,
        session_state=session_state,
        is_init=False,
        executor=SimpleNamespace(),
        tools=[],
        config=SimpleNamespace(approval_timeout_seconds=300),
        available_integrations=[],
        integration_errors={},
        session_service=FailingStatusService(),
        run_registry=registry,
    )

    with pytest.raises(RuntimeError, match="Failed to persist terminal status"):
        await run_chat(ctx, bus, BusRegistry())

    run_errors = [record.event for record in bus._recent if record.event.type.value == "RUN_ERROR"]
    assert [event.code for event in run_errors] == ["context_length_exceeded"]
    assert registry.get_run(run.run_id).status == RunStatus.ERROR


def test_safe_error_classifies_context_window_exception_class():
    from ntrp.services import chat as chat_service

    class ContextWindowExceededError(Exception):
        pass

    code, message, debug_id = chat_service._safe_error(ContextWindowExceededError())

    assert code == "context_length_exceeded"
    assert "context window" in message
    assert debug_id.startswith("err_")


@pytest.mark.asyncio
async def test_run_chat_compacts_and_retries_context_length_provider_error(monkeypatch):
    from ntrp.services import chat as chat_service

    registry = RunRegistry()
    run = registry.create_run("sess-1")
    run.messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "latest"},
    ]
    session_state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))

    class ProviderError(Exception):
        body = {
            "error": {
                "type": "invalid_request_error",
                "code": "context_length_exceeded",
                "message": "Your input exceeds the context window of this model.",
            }
        }

    class RetryCompactor:
        def __init__(self):
            self.calls = []

        def should_compact(self, messages, model, last_input_tokens):
            return False

        async def maybe_compact(self, messages, model, last_input_tokens, *, rehydration_state=None):
            self.calls.append((list(messages), model, last_input_tokens))
            return [
                {"role": "system", "content": "system"},
                {"role": "assistant", "content": "<session_handoff>\nsummary"},
                {"role": "user", "content": "latest"},
            ]

    class Store:
        async def get_background_agent_result(self, session_id, task_id):
            return None

    class RecordingSessionService:
        def __init__(self):
            self.store = Store()
            self.statuses = []
            self.saved = []

        async def save(self, session_state, messages, metadata=None):
            self.saved.append((list(messages), metadata))

        async def record_chat_run_status(
            self,
            run_id,
            status,
            *,
            stop_reason=None,
            last_seq=None,
            error_code=None,
            error_message=None,
        ):
            self.statuses.append(
                {
                    "status": status,
                    "stop_reason": stop_reason,
                    "last_seq": last_seq,
                    "error_code": error_code,
                    "error_message": error_message,
                }
            )

    attempts = 0

    async def flaky_agent_loop(ctx, agent, bus):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ProviderError()
        ctx.run.messages.append({"role": "assistant", "content": "ok"})
        return "ok", None

    monkeypatch.setattr(
        chat_service, "create_agent", lambda **_kwargs: SimpleNamespace(hooks=SimpleNamespace(), tools=[])
    )
    monkeypatch.setattr(chat_service, "run_agent_loop", flaky_agent_loop)
    compactor = RetryCompactor()
    service = RecordingSessionService()
    bus = SessionBus(session_id="sess-1")
    ctx = ChatContext(
        run=run,
        session_state=session_state,
        is_init=False,
        executor=SimpleNamespace(),
        tools=[],
        config=SimpleNamespace(
            approval_timeout_seconds=300,
            compactor=compactor,
            model="gpt-5.2",
        ),
        available_integrations=[],
        integration_errors={},
        session_service=service,
        run_registry=registry,
    )

    await run_chat(ctx, bus, BusRegistry())

    assert attempts == 2
    assert compactor.calls
    assert run.messages[-1]["content"] == "ok"
    assert not any(record.event.type.value == "RUN_ERROR" for record in bus._recent)
    assert any(record.event.type.value == "RUN_FINISHED" for record in bus._recent)
    assert service.statuses[-1]["status"] == RunStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_context_retry_compacts_loop_prefix_into_persisted_summary(monkeypatch):
    from ntrp.services import chat as chat_service

    registry = RunRegistry()
    run = registry.create_run("sess-1")
    run.history_prefix = [{"role": "user", "content": "old-prefix"}]
    run.messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "tail"},
    ]
    session_state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))

    class ProviderError(Exception):
        body = {
            "error": {
                "type": "invalid_request_error",
                "code": "context_length_exceeded",
                "message": "Your input exceeds the context window of this model.",
            }
        }

    class RetryCompactor:
        def __init__(self):
            self.seen = []

        def should_compact(self, messages, model, last_input_tokens):
            return False

        async def maybe_compact(self, messages, model, last_input_tokens, *, rehydration_state=None):
            self.seen.append(list(messages))
            return [
                {"role": "system", "content": "system"},
                {"role": "assistant", "content": "<session_handoff>\nold-prefix summary"},
                {"role": "user", "content": "tail"},
            ]

    class Store:
        async def get_background_agent_result(self, session_id, task_id):
            return None

    class RecordingSessionService:
        def __init__(self):
            self.store = Store()
            self.saved = []

        async def save(self, session_state, messages, metadata=None):
            self.saved.append((list(messages), metadata))

        async def record_chat_run_status(self, *args, **kwargs):
            return None

    attempts = 0

    async def flaky_agent_loop(ctx, agent, bus):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ProviderError()
        ctx.run.messages.append({"role": "assistant", "content": "ok"})
        return "ok", None

    monkeypatch.setattr(
        chat_service, "create_agent", lambda **_kwargs: SimpleNamespace(hooks=SimpleNamespace(), tools=[])
    )
    monkeypatch.setattr(chat_service, "run_agent_loop", flaky_agent_loop)
    compactor = RetryCompactor()
    service = RecordingSessionService()
    registry = RunRegistry()
    registry._runs[run.run_id] = run
    ctx = ChatContext(
        run=run,
        session_state=session_state,
        is_init=False,
        executor=SimpleNamespace(),
        tools=[],
        config=SimpleNamespace(
            approval_timeout_seconds=300,
            compactor=compactor,
            model="gpt-5.2",
        ),
        available_integrations=[],
        integration_errors={},
        session_service=service,
        run_registry=registry,
    )

    await run_chat(ctx, SessionBus(session_id="sess-1"), BusRegistry())

    assert compactor.seen[0] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old-prefix"},
        {"role": "user", "content": "tail"},
    ]
    assert run.history_prefix == []
    assert service.saved[0][0] == [
        {"role": "system", "content": "system"},
        {"role": "assistant", "content": "<session_handoff>\nold-prefix summary"},
        {"role": "user", "content": "tail"},
    ]


@pytest.mark.asyncio
async def test_cancelled_run_finally_drops_pending_without_persisting():
    registry = RunRegistry()
    run = registry.create_run("sess-1")
    run.messages = [{"role": "user", "content": "old request"}]
    run.queue_injection({"role": "user", "content": "dead follow-up", "client_id": "cid-dead"})
    session_state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))

    class RecordingSessionService:
        def __init__(self):
            self.saved: list[list[dict]] = []

        async def save(self, session_state, messages, metadata=None):
            self.saved.append(list(messages))

    class CancellingInitialEmitBus(SessionBus):
        async def emit(self, event):
            await super().emit(event)
            if event.type.value == "RUN_STARTED":
                task = asyncio.current_task()
                assert task is not None
                task.cancel()
                await asyncio.sleep(0)

    session_service = RecordingSessionService()
    bus = CancellingInitialEmitBus(session_id="sess-1")
    ctx = ChatContext(
        run=run,
        session_state=session_state,
        is_init=False,
        executor=SimpleNamespace(),
        tools=[],
        config=SimpleNamespace(approval_timeout_seconds=300),
        available_integrations=[],
        integration_errors={},
        session_service=session_service,
        run_registry=registry,
    )

    await run_chat(ctx, bus, BusRegistry())

    assert session_service.saved == []
    assert run.pending_injection_count == 0
    assert run.messages == [{"role": "user", "content": "old request"}]


@pytest.mark.asyncio
async def test_cancelled_run_finally_does_not_clear_newer_replay_events():
    registry = RunRegistry()
    run = registry.create_run("sess-1")
    session_state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))

    class NoopSessionService:
        async def save(self, session_state, messages, metadata=None):
            return None

    class CancellingInitialEmitBus(SessionBus):
        async def emit(self, event):
            await super().emit(event)
            if event.type.value == "RUN_STARTED":
                task = asyncio.current_task()
                assert task is not None
                task.cancel()
                await asyncio.sleep(0)

    bus = CancellingInitialEmitBus(session_id="sess-1")
    await bus.emit(ThinkingEvent(status="newer run replay"))
    ctx = ChatContext(
        run=run,
        session_state=session_state,
        is_init=False,
        executor=SimpleNamespace(),
        tools=[],
        config=SimpleNamespace(approval_timeout_seconds=300),
        available_integrations=[],
        integration_errors={},
        session_service=NoopSessionService(),
        run_registry=registry,
    )

    await run_chat(ctx, bus, BusRegistry())

    assert [record.event.type.value for record in bus._recent] == [
        "thinking",
        "RUN_STARTED",
        "run_cancelled",
    ]


@pytest.mark.asyncio
async def test_background_result_after_cancel_is_ignored_for_cancelled_run(monkeypatch):
    from ntrp.services import chat as chat_service

    registry = RunRegistry()
    run = registry.create_run("sess-1")
    session_state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))
    stream_started = asyncio.Event()
    release = asyncio.Event()

    class NoopStore:
        async def record_background_agent_started(self, **kwargs):
            return None

        async def record_background_agent_finished(self, **kwargs):
            return None

    class NoopSessionService:
        store = NoopStore()

        async def save(self, session_state, messages, metadata=None):
            return None

    class FakeAgent:
        def __init__(self):
            self.hooks = SimpleNamespace(on_response=None, on_step_finish=None, get_pending_messages=None)
            self.tools = []

        async def stream(self, messages):
            stream_started.set()
            await release.wait()
            if False:
                yield None

    monkeypatch.setattr(chat_service, "create_agent", lambda **_kwargs: FakeAgent())
    ctx = ChatContext(
        run=run,
        session_state=session_state,
        is_init=False,
        executor=SimpleNamespace(),
        tools=[],
        config=SimpleNamespace(approval_timeout_seconds=300),
        available_integrations=[],
        integration_errors={},
        session_service=NoopSessionService(),
        run_registry=registry,
    )

    task = asyncio.create_task(run_chat(ctx, SessionBus(session_id="sess-1"), BusRegistry()))
    await asyncio.wait_for(stream_started.wait(), timeout=1)
    run.cancelled = True

    await registry.get_background_registry("sess-1").inject([{"role": "user", "content": "late background"}])

    assert run.pending_injection_count == 0
    task.cancel()
    await asyncio.wait_for(task, timeout=1)


@pytest.mark.asyncio
async def test_backgrounded_drain_cancel_does_not_save_merged_output():
    run = RunState(run_id="run-1", session_id="sess-1", backgrounded=True)
    session_state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))
    started = asyncio.Event()
    release = asyncio.Event()

    class RecordingSessionService:
        def __init__(self):
            self.saved: list[list[dict]] = []
            self.statuses: list[tuple[str, str | None]] = []

        async def load(self, session_id=None):
            return SessionData(state=session_state, messages=[{"role": "user", "content": "newer"}])

        async def save(self, session_state, messages, metadata=None):
            self.saved.append(list(messages))

        async def record_chat_run_status(self, run_id, status, *, stop_reason=None, last_seq=None):
            self.statuses.append((status, stop_reason))

    async def gen():
        started.set()
        await release.wait()
        if False:
            yield None

    service = RecordingSessionService()
    registry = RunRegistry()
    registry._runs[run.run_id] = run
    ctx = ChatContext(
        run=run,
        session_state=session_state,
        is_init=False,
        executor=SimpleNamespace(registry=SimpleNamespace(get=lambda _name: None)),
        tools=[],
        config=SimpleNamespace(),
        available_integrations=[],
        integration_errors={},
        session_service=service,
        run_registry=RunRegistry(),
    )
    task = asyncio.create_task(
        _drain_backgrounded(
            gen(),
            SimpleNamespace(tools=[]),
            ctx,
            BackgroundTaskRegistry(session_id="sess-1"),
            UsageTracker(),
            SessionBus(session_id="sess-1"),
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    run.cancelled = True
    task.cancel()
    await asyncio.wait_for(task, timeout=1)

    assert ctx.session_service.saved == []
    assert ctx.session_service.statuses[-1] == (RunStatus.CANCELLED.value, "cancelled")


@pytest.mark.asyncio
async def test_backgrounded_drain_persists_budget_stop_reason():
    run = RunState(run_id="run-1", session_id="sess-1", backgrounded=True)
    session_state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))
    source = ToolSourceRef(provider="slack", kind="message", ref="C1:1.0", title="Decision")
    duplicate = ToolSourceRef(provider="slack", kind="message", ref="C1:1.0", title="Duplicate")

    class RecordingSessionService:
        def __init__(self):
            self.statuses: list[tuple[str, str | None]] = []

        async def load(self, session_id=None):
            return SessionData(state=session_state, messages=[])

        async def save(self, session_state, messages, metadata=None):
            return None

        async def record_chat_run_status(self, run_id, status, *, stop_reason=None, last_seq=None):
            self.statuses.append((status, stop_reason))

    async def gen():
        yield ToolCompleted(
            tool_id="call-1",
            name="slack_thread",
            result="thread text",
            preview="thread",
            duration_ms=1,
            is_error=False,
            data=None,
            display_name="Slack Thread",
            source_refs=(source, duplicate),
        )
        yield Result(text="", stop_reason=StopReason.MAX_COST, steps=0, usage=Usage())

    service = RecordingSessionService()
    ctx = ChatContext(
        run=run,
        session_state=session_state,
        is_init=False,
        executor=SimpleNamespace(registry=SimpleNamespace(get=lambda _name: None)),
        tools=[],
        config=SimpleNamespace(),
        available_integrations=[],
        integration_errors={},
        session_service=service,
        run_registry=RunRegistry(),
    )

    await _drain_backgrounded(
        gen(),
        SimpleNamespace(tools=[]),
        ctx,
        BackgroundTaskRegistry(session_id="sess-1"),
        UsageTracker(),
        SessionBus(session_id="sess-1"),
    )

    assert service.statuses[-1] == (RunStatus.COMPLETED.value, StopReason.MAX_COST.value)
    assert run.source_refs == [source.to_dict()]


@pytest.mark.asyncio
async def test_backgrounded_drain_stream_failure_records_error_not_completed():
    run = RunState(run_id="run-1", session_id="sess-1", backgrounded=True)
    session_state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))

    class RecordingSessionService:
        def __init__(self):
            self.statuses: list[dict] = []
            self.saved: list[list[dict]] = []

        async def load(self, session_id=None):
            return SessionData(state=session_state, messages=[])

        async def save(self, session_state, messages, metadata=None):
            self.saved.append(list(messages))

        async def record_chat_run_status(
            self,
            run_id,
            status,
            *,
            stop_reason=None,
            last_seq=None,
            error_code=None,
            error_message=None,
        ):
            self.statuses.append(
                {
                    "status": status,
                    "stop_reason": stop_reason,
                    "error_code": error_code,
                    "error_message": error_message,
                }
            )

    async def gen():
        raise RuntimeError("background stream exploded")
        yield  # pragma: no cover

    service = RecordingSessionService()
    registry = RunRegistry()
    registry._runs[run.run_id] = run
    ctx = ChatContext(
        run=run,
        session_state=session_state,
        is_init=False,
        executor=SimpleNamespace(registry=SimpleNamespace(get=lambda _name: None)),
        tools=[],
        config=SimpleNamespace(),
        available_integrations=[],
        integration_errors={},
        session_service=service,
        run_registry=registry,
    )

    await _drain_backgrounded(
        gen(),
        SimpleNamespace(tools=[]),
        ctx,
        BackgroundTaskRegistry(session_id="sess-1"),
        UsageTracker(),
        SessionBus(session_id="sess-1"),
    )

    assert service.saved == [[]]
    assert service.statuses[-1]["status"] == RunStatus.ERROR.value
    assert service.statuses[-1]["error_code"] == "background_drain_failed"
    assert "backgrounded run failed" in service.statuses[-1]["error_message"]
    assert all(entry["status"] != RunStatus.COMPLETED.value for entry in service.statuses)
    assert run.status == RunStatus.ERROR


@pytest.mark.asyncio
async def test_backgrounded_drain_final_save_failure_records_error():
    run = RunState(run_id="run-1", session_id="sess-1", backgrounded=True)
    session_state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))

    class FailingSaveSessionService:
        def __init__(self):
            self.statuses = []

        async def load(self, session_id=None):
            return SessionData(state=session_state, messages=[])

        async def save(self, session_state, messages, metadata=None):
            raise RuntimeError("background save down")

        async def record_chat_run_status(
            self,
            run_id,
            status,
            *,
            stop_reason=None,
            last_seq=None,
            error_code=None,
            error_message=None,
        ):
            self.statuses.append({"status": status, "error_code": error_code, "error_message": error_message})

    async def gen():
        yield Result(text="done", stop_reason=StopReason.END_TURN, steps=0, usage=Usage())

    service = FailingSaveSessionService()
    registry = RunRegistry()
    registry._runs[run.run_id] = run
    ctx = ChatContext(
        run=run,
        session_state=session_state,
        is_init=False,
        executor=SimpleNamespace(registry=SimpleNamespace(get=lambda _name: None)),
        tools=[],
        config=SimpleNamespace(),
        available_integrations=[],
        integration_errors={},
        session_service=service,
        run_registry=registry,
    )

    await _drain_backgrounded(
        gen(),
        SimpleNamespace(tools=[]),
        ctx,
        BackgroundTaskRegistry(session_id="sess-1"),
        UsageTracker(),
        SessionBus(session_id="sess-1"),
    )

    assert service.statuses[-1]["status"] == RunStatus.ERROR.value
    assert service.statuses[-1]["error_code"] == "run_finalization_failed"
    assert "final state" in service.statuses[-1]["error_message"]
    assert run.status == RunStatus.ERROR


@pytest.mark.asyncio
async def test_run_agent_loop_retries_text_end_when_emit_is_cancelled():
    run = RunState(run_id="run-1", session_id="sess-1")

    class CancellingFirstEndBus(SessionBus):
        def __init__(self):
            super().__init__(session_id="sess-1")
            self.cancelled_end_once = False

        async def emit(self, event):
            if isinstance(event, TextMessageEndEvent) and not self.cancelled_end_once:
                self.cancelled_end_once = True
                run.cancelled = True
                raise asyncio.CancelledError
            await super().emit(event)

    class AgentWithTextEnd:
        async def stream(self, messages):
            yield TextStarted(depth=1, parent_id="call-research", message_id="text-1")
            yield TextDelta(depth=1, parent_id="call-research", message_id="text-1", content="partial")
            yield TextEnded(depth=2, parent_id="call-final", message_id="text-final", content="explicit final")

    bus = CancellingFirstEndBus()

    await run_agent_loop(SimpleNamespace(run=run), AgentWithTextEnd(), bus)

    assert [record.event.type.value for record in bus._recent] == [
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "run_cancelled",
    ]
    end = bus._recent[2].event
    assert isinstance(end, TextMessageEndEvent)
    assert end.message_id == "text-final"
    assert end.content == "explicit final"
    assert end.depth == 2
    assert end.parent_id == "call-final"
    assert bus.cancelled_end_once is True


@pytest.mark.asyncio
async def test_automation_event_stream_uses_typed_keepalive(monkeypatch):
    monkeypatch.setattr("ntrp.server.routers.automation.KEEPALIVE_INTERVAL", 0)
    buses = BusRegistry()
    stream = _automation_event_stream(buses)
    try:
        chunk = await anext(stream)
    finally:
        await stream.aclose()

    assert "event: stream_keepalive" in chunk
    payload = json.loads(chunk.split("data: ", 1)[1])
    assert payload["type"] == "stream_keepalive"
    assert payload["session_id"] == "automation:events"


@pytest.mark.asyncio
async def test_automation_event_stream_resumes_after_durable_cursor(monkeypatch):
    monkeypatch.setattr("ntrp.server.routers.automation.KEEPALIVE_INTERVAL", 0)

    class EventStore:
        async def get_latest_session_event_seq(self, session_id):
            assert session_id == "automation:events"
            return 7

    recorded = []

    async def record_events(records):
        recorded.extend(records)

    buses = BusRegistry(record_events=record_events)
    stream = _automation_event_stream(buses, event_store=EventStore())
    try:
        chunk = await anext(stream)
        bus = buses.get("automation:events")
    finally:
        await stream.aclose()
        await buses.close_all()

    assert bus is not None
    assert bus.next_seq == 8
    assert '"latest_seq": 7' in chunk
    assert recorded == []


def test_foreground_child_suppression_drops_nested_text_keeps_tool_args():
    """The parent-stream filter (_SUPPRESSED_NESTED_SSE) must drop a sub-agent's
    token text — the firehose, already dropped client-side via !event.depth —
    while keeping its tool-call args, which feed the nested row's label and are
    emitted atomically (never part of the volume problem)."""
    from ntrp.agent import TextDelta, TextEnded, TextStarted, ToolStarted
    from ntrp.core.spawner import _SUPPRESSED_NESTED_SSE

    def forwarded(event) -> tuple[str, ...]:
        return tuple(type(e).__name__ for e in agent_events_to_sse(event) if not isinstance(e, _SUPPRESSED_NESTED_SSE))

    assert forwarded(TextStarted(depth=1, parent_id="p", message_id="t")) == ()
    assert forwarded(TextDelta(depth=1, parent_id="p", message_id="t", content="x")) == ()
    assert forwarded(TextEnded(depth=1, parent_id="p", message_id="t", content="x")) == ()

    tool = forwarded(ToolStarted(tool_id="tl", name="bash", display_name="Bash", args={"command": "nl -ba x"}))
    assert "ToolCallStartEvent" in tool
    assert "ToolCallArgsEvent" in tool
    assert "ToolCallEndEvent" in tool


@pytest.mark.asyncio
async def test_durable_replay_serves_sparse_ledger_without_reset():
    """REGRESSION: session_events is a sparse ledger — ephemeral deltas (token
    text, tool args, reasoning) are not persisted, so seqs have holes. After a
    server restart the in-memory buffer is empty, so a resuming client relies on
    durable replay. The old contiguity check treated every hole as a gap and
    returned None → a bogus replay_gap reset → reload loop. With a sparse store
    and an empty buffer, the stream must REPLAY the persisted rows, not reset."""
    from ntrp.server.bus import StreamRecord

    buses = BusRegistry()

    # Persisted rows are sparse: seqs 5, 8, 10 (4/6/7/9 were dropped deltas).
    # Checkpoint 3, latest persisted 10 → bus seeds next_seq=11, replay_upper=10.
    sparse = [
        StreamRecord(seq=5, session_id="sess-1", event=TextMessageEndEvent(message_id="m", content="hi")),
        StreamRecord(seq=8, session_id="sess-1", event=ThinkingEvent(status="tail")),
        StreamRecord(seq=10, session_id="sess-1", event=TaskFinishedEvent(run_id="r", task_id="t", status="completed")),
    ]

    class SparseStore:
        async def get_latest_session_event_seq(self, session_id: str) -> int:
            return 10

        async def get_latest_session_checkpoint_seq(self, session_id: str) -> int:
            return 3

        async def list_session_events(self, session_id: str, *, after_seq: int, limit: int) -> list:
            return [r for r in sparse if r.seq > after_seq]

    stream = _event_stream(
        "sess-1",
        buses,
        RunRegistry(),
        stream=True,
        after_seq=3,
        event_store=SparseStore(),
    )
    chunks: list[str] = []
    try:
        for _ in range(3):
            chunks.append(await anext(stream))
    finally:
        await stream.aclose()

    payloads = [json.loads(c.split("data: ", 1)[1].strip()) for c in chunks]
    types = [p["type"] for p in payloads]
    # No reset — the sparse rows replay straight through, in order.
    assert "stream_reset" not in types
    seqs = [p["seq"] for p in payloads]
    assert seqs == [5, 8, 10]


@pytest.mark.asyncio
async def test_catchup_replay_collapses_tool_call_chain_keeps_live_tail():
    """REGRESSION (user report: 'full replay of tool calls when I open a tab
    with an ongoing research subagent'). A long tool-call chain in the buffer
    must replay as its STRUCTURAL skeleton (start/end/result) with the ephemeral
    ARGS deltas dropped — not the full re-stream. Events emitted AFTER the
    client subscribes (the live tail) still carry args, so what's actively
    streaming renders normally."""
    buses = BusRegistry()
    bus = buses.get_or_create("sess-1")
    # Buffered (pre-join) tool-call chain.
    await bus.emit(ToolCallStartEvent(tool_call_id="t1", tool_call_name="bash", display_name="Bash"))
    await bus.emit(ToolCallArgsEvent(tool_call_id="t1", delta='{"command":'))
    await bus.emit(ToolCallArgsEvent(tool_call_id="t1", delta='"ls"}'))
    await bus.emit(ToolCallEndEvent(tool_call_id="t1"))
    await bus.emit(ToolCallResultEvent(tool_call_id="t1", content="a\nb", preview="2 lines", name="bash"))

    stream = _event_stream("sess-1", buses, RunRegistry(), stream=True)
    try:
        replayed = [json.loads((await anext(stream)).split("data: ", 1)[1].strip()) for _ in range(3)]
        # Live tail: a new args delta emitted after subscription must come through.
        await bus.emit(ToolCallArgsEvent(tool_call_id="t2", delta='{"q":"x"}'))
        live = json.loads((await anext(stream)).split("data: ", 1)[1].strip())
    finally:
        await stream.aclose()

    assert [p["type"] for p in replayed] == [
        "TOOL_CALL_START",
        "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
    ]
    assert all(p.get("replay") for p in replayed)
    assert all(p["type"] != "TOOL_CALL_ARGS" for p in replayed)
    # Live tail keeps full deltas.
    assert live["type"] == "TOOL_CALL_ARGS"
    assert live.get("replay") is not True
