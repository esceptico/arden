import asyncio
import json
from contextlib import suppress
from datetime import UTC, datetime
from hashlib import sha256

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from arden.automation.models import Automation
from arden.context.models import SessionData, SessionState
from arden.context.store import SessionStore
from arden.core.factory import AgentConfig
from arden.events.sse import (
    MessageIngestedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ThinkingEvent,
    ToolCallStartEvent,
)
from arden.server.app import _iteration_client_id, app
from arden.server.bus import BusRegistry, SessionBus, StreamRecord
from arden.server.deps import get_bus_registry, require_run_registry
from arden.server.routers.chat import (
    _effective_after_seq,
    _event_stream,
    cancel_inject,
    cancel_subagent,
    submit_tool_result,
)
from arden.server.runtime import get_runtime
from arden.server.schemas import ChatRequest, ToolResultRequest
from arden.server.state import RunRegistry, RunState, RunStatus
from arden.services.chat import (
    ChatDeps,
    _handle_background_result,
    expand_skill_command,
)
from arden.services.session import SessionService
from arden.skills.registry import SkillRegistry


class _RuntimeStub:
    def __init__(self, store: SessionStore):
        self.session_service = type("SessionServiceStub", (), {"store": store})()


class _EmptyEventStore:
    async def get_latest_session_event_seq(self, session_id: str) -> int:
        return 0

    async def get_latest_session_checkpoint_seq(self, session_id: str) -> int:
        return 0


def _parse_sse_chunk(chunk: str) -> tuple[int, str, dict]:
    lines = chunk.splitlines()
    assert lines[0].startswith("id: ")
    assert lines[1].startswith("event: ")
    payload = json.loads(chunk.split("data: ", 1)[1].strip())
    seq = int(lines[0].split(": ", 1)[1])
    assert payload["seq"] == seq
    return seq, lines[1].split(": ", 1)[1], payload


@pytest.mark.asyncio
async def test_tools_result_resolves_durable_approval(tmp_path):
    import arden.database as database

    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    store = SessionStore(conn, read_conn)
    await store.init_schema()
    registry = RunRegistry()
    run = registry.create_run("s-1")
    future = asyncio.get_running_loop().create_future()
    run.pending_approvals["call-1"] = future
    await store.record_tool_approval_requested(
        run_id=run.run_id,
        session_id="s-1",
        tool_call_id="call-1",
        tool_name="bash",
        action="execute",
        scope="internal",
    )

    try:
        response = await submit_tool_result(
            ToolResultRequest(run_id=run.run_id, tool_id="call-1", result="ok", approved=True),
            run_registry=registry,
            runtime=_RuntimeStub(store),
        )

        row = await store.get_tool_approval(run_id=run.run_id, tool_call_id="call-1")
        assert response == {"status": "ok"}
        assert row is not None
        assert row["status"] == "approved"
        assert row["result_feedback"] == "ok"
        assert future.result()["approved"] is True
    finally:
        await read_conn.close()
        await conn.close()


@pytest.mark.asyncio
async def test_cancel_subagent_route_cancels_registered_child_task():
    registry = RunRegistry()
    run = registry.create_run("s-1")
    child = asyncio.create_task(asyncio.sleep(60))
    registry.register_subagent(run.run_id, "call-research", child)

    try:
        response = await cancel_subagent("call-research", run.run_id, registry)

        assert response == {
            "status": "cancelling",
            "found": True,
            "cancel_requested": True,
        }
        assert registry.get_run(run.run_id).subagents["call-research"].cancel_requested is True
        assert child.cancelled() or child.cancelling()
        retry = await cancel_subagent("call-research", run.run_id, registry)
        assert retry == {
            "status": "cancelling",
            "found": True,
            "cancel_requested": False,
        }
    finally:
        child.cancel()
        with suppress(asyncio.CancelledError):
            await child


@pytest.mark.asyncio
async def test_tools_result_resolves_durable_approval_without_active_future(tmp_path):
    import arden.database as database

    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    store = SessionStore(conn, read_conn)
    await store.init_schema()
    registry = RunRegistry()
    run = registry.create_run("s-1")
    await store.record_tool_approval_requested(
        run_id=run.run_id,
        session_id="s-1",
        tool_call_id="call-1",
        tool_name="bash",
        action="execute",
        scope="internal",
    )

    try:
        response = await submit_tool_result(
            ToolResultRequest(run_id=run.run_id, tool_id="call-1", result="no", approved=False),
            run_registry=registry,
            runtime=_RuntimeStub(store),
        )

        row = await store.get_tool_approval(run_id=run.run_id, tool_call_id="call-1")
        assert response == {"status": "ok"}
        assert row is not None
        assert row["status"] == "rejected"
        assert row["result_feedback"] == "no"
    finally:
        await read_conn.close()
        await conn.close()


@pytest.mark.asyncio
async def test_tools_result_resolves_durable_approval_without_active_run(tmp_path):
    import arden.database as database

    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    store = SessionStore(conn, read_conn)
    await store.init_schema()
    registry = RunRegistry()
    await store.record_tool_approval_requested(
        run_id="run-1",
        session_id="s-1",
        tool_call_id="call-1",
        tool_name="bash",
        action="execute",
        scope="internal",
    )

    try:
        response = await submit_tool_result(
            ToolResultRequest(run_id="run-1", tool_id="call-1", result="ok", approved=True),
            run_registry=registry,
            runtime=_RuntimeStub(store),
        )

        row = await store.get_tool_approval(run_id="run-1", tool_call_id="call-1")
        assert response == {"status": "ok"}
        assert row is not None
        assert row["status"] == "approved"
        assert row["result_feedback"] == "ok"
    finally:
        await read_conn.close()
        await conn.close()


@pytest.mark.asyncio
async def test_tools_result_resumes_run_after_offline_approval(tmp_path):
    import arden.database as database

    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    store = SessionStore(conn, read_conn)
    await store.init_schema()
    await store.record_tool_approval_requested(
        run_id="run-1",
        session_id="s-1",
        tool_call_id="call-1",
        tool_name="bash",
        action="execute",
        scope="internal",
    )
    resumed = []
    runtime = _RuntimeStub(store)

    async def resume(run_id, session_id):
        resumed.append((run_id, session_id))

    runtime.resume_suspended_chat_run = resume

    try:
        response = await submit_tool_result(
            ToolResultRequest(run_id="run-1", tool_id="call-1", result="ok", approved=True),
            run_registry=RunRegistry(),
            runtime=runtime,
        )

        assert response == {"status": "ok"}
        assert resumed == [("run-1", "s-1")]
    finally:
        await read_conn.close()
        await conn.close()


@pytest.mark.asyncio
async def test_tools_result_wakes_active_future_when_durable_update_fails():
    class FailingStore:
        async def get_tool_approval(self, **kwargs):
            return {"status": "pending"}

        async def resolve_tool_approval(self, **kwargs):
            raise RuntimeError("db unavailable")

    registry = RunRegistry()
    run = registry.create_run("s-1")
    future = asyncio.get_running_loop().create_future()
    run.pending_approvals["call-1"] = future

    response = await submit_tool_result(
        ToolResultRequest(run_id=run.run_id, tool_id="call-1", result="ok", approved=True),
        run_registry=registry,
        runtime=_RuntimeStub(FailingStore()),
    )

    assert response == {"status": "ok"}
    assert future.done()
    assert future.result() == {
        "type": "tool_response",
        "tool_id": "call-1",
        "result": "ok",
        "approved": True,
    }


@pytest.mark.asyncio
async def test_tools_result_durable_fallback_conflicts_for_terminal_approval(tmp_path):
    import arden.database as database

    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    store = SessionStore(conn, read_conn)
    await store.init_schema()
    registry = RunRegistry()
    run = registry.create_run("s-1")
    await store.record_tool_approval_requested(
        run_id=run.run_id,
        session_id="s-1",
        tool_call_id="call-1",
        tool_name="bash",
        action="execute",
        scope="internal",
    )
    assert await store.expire_tool_approval(
        run_id=run.run_id,
        tool_call_id="call-1",
        result_feedback="Approval timed out",
    )

    try:
        with pytest.raises(HTTPException) as exc:
            await submit_tool_result(
                ToolResultRequest(run_id=run.run_id, tool_id="call-1", result="ok", approved=True),
                run_registry=registry,
                runtime=_RuntimeStub(store),
            )

        row = await store.get_tool_approval(run_id=run.run_id, tool_call_id="call-1")
        assert exc.value.status_code == 409
        assert row is not None
        assert row["status"] == "expired"
        assert row["result_feedback"] == "Approval timed out"
    finally:
        await read_conn.close()
        await conn.close()


@pytest.mark.asyncio
async def test_tools_result_active_future_conflicts_for_terminal_durable_approval(tmp_path):
    import arden.database as database

    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    store = SessionStore(conn, read_conn)
    await store.init_schema()
    registry = RunRegistry()
    run = registry.create_run("s-1")
    future = asyncio.get_running_loop().create_future()
    run.pending_approvals["call-1"] = future
    await store.record_tool_approval_requested(
        run_id=run.run_id,
        session_id="s-1",
        tool_call_id="call-1",
        tool_name="bash",
        action="execute",
        scope="internal",
    )
    assert await store.expire_tool_approval(
        run_id=run.run_id,
        tool_call_id="call-1",
        result_feedback="Approval timed out",
    )

    try:
        with pytest.raises(HTTPException) as exc:
            await submit_tool_result(
                ToolResultRequest(run_id=run.run_id, tool_id="call-1", result="ok", approved=True),
                run_registry=registry,
                runtime=_RuntimeStub(store),
            )

        row = await store.get_tool_approval(run_id=run.run_id, tool_call_id="call-1")
        assert exc.value.status_code == 409
        assert row is not None
        assert row["status"] == "expired"
        assert not future.done()
    finally:
        await read_conn.close()
        await conn.close()


@pytest.mark.asyncio
async def test_tools_result_conflicts_if_future_resolves_during_durable_update():
    class ResolvingStore:
        def __init__(self, future):
            self.future = future

        async def get_tool_approval(self, **kwargs):
            self.future.cancel()
            return {"status": "pending"}

        async def resolve_tool_approval(self, **kwargs):
            raise AssertionError("durable row should not resolve after future is already done")

    registry = RunRegistry()
    run = registry.create_run("s-1")
    future = asyncio.get_running_loop().create_future()
    run.pending_approvals["call-1"] = future

    with pytest.raises(HTTPException) as exc:
        await submit_tool_result(
            ToolResultRequest(run_id=run.run_id, tool_id="call-1", result="ok", approved=True),
            run_registry=registry,
            runtime=_RuntimeStub(ResolvingStore(future)),
        )

    assert exc.value.status_code == 409
    assert future.cancelled()


def test_message_ingested_event_serialization():
    event = MessageIngestedEvent(client_id="abc-123", run_id="cool-otter")
    sse = event.to_sse_string()
    assert "event: message_ingested" in sse
    payload = json.loads(sse.split("data: ", 1)[1].strip())
    assert payload["type"] == "message_ingested"
    assert payload["client_id"] == "abc-123"
    assert payload["run_id"] == "cool-otter"
    # AG-UI: every event ships with a timestamp (Unix ms)
    assert isinstance(payload["timestamp"], int) and payload["timestamp"] > 0


def test_chat_request_accepts_client_id():
    req = ChatRequest(message="hi", client_id="abc-123")
    assert req.client_id == "abc-123"


def test_chat_request_client_id_optional():
    req = ChatRequest(message="hi")
    assert req.client_id is None


def test_chat_request_rejects_malformed_context_block():
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", context=[{"type": "context", "content": "missing content_type"}])


@pytest.mark.asyncio
async def test_event_stream_replays_explicit_text_boundaries():
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

    parsed = [_parse_sse_chunk(chunk) for chunk in chunks]
    # Catch-up replay is structural-only: the CONTENT delta (seq 2) is dropped,
    # the START/END boundaries (carrying full content) replay verbatim.
    assert [seq for seq, _event_name, _payload in parsed] == [1, 3]
    payloads = [payload for _seq, _event_name, payload in parsed]
    assert [payload["session_id"] for payload in payloads] == ["sess-1", "sess-1"]
    assert [payload["type"] for payload in payloads] == [
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_END",
    ]
    assert [payload["message_id"] for payload in payloads] == ["text-1", "text-1"]


@pytest.mark.asyncio
async def test_event_stream_replay_honors_after_seq_without_old_duplicates():
    buses = BusRegistry()
    bus = buses.get_or_create("sess-1")
    await bus.emit(ThinkingEvent(status="old"))
    await bus.emit(ThinkingEvent(status="new-1"))
    await bus.emit(ThinkingEvent(status="new-2"))

    stream = _event_stream("sess-1", buses, RunRegistry(), stream=True, after_seq=1)
    try:
        chunks = [await anext(stream), await anext(stream)]
    finally:
        await stream.aclose()

    parsed = [_parse_sse_chunk(chunk) for chunk in chunks]
    assert [seq for seq, _event_name, _payload in parsed] == [2, 3]
    assert [payload["session_id"] for _seq, _event_name, payload in parsed] == ["sess-1", "sess-1"]
    assert [payload["status"] for _seq, _event_name, payload in parsed] == ["new-1", "new-2"]


@pytest.mark.asyncio
async def test_event_stream_replays_persisted_events_after_bus_recreation(tmp_path):
    import arden.database as database

    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    store = SessionStore(conn, read_conn)
    await store.init_schema()
    await store.record_session_event(
        StreamRecord(seq=1, session_id="sess-1", event=TextMessageStartEvent(message_id="a-1"))
    )
    await store.record_session_event(
        StreamRecord(seq=2, session_id="sess-1", event=TextMessageContentEvent(message_id="a-1", delta="old"))
    )
    await store.record_session_event(
        StreamRecord(seq=3, session_id="sess-1", event=TextMessageEndEvent(message_id="a-1", content="old"))
    )
    await store.record_session_event(
        StreamRecord(
            seq=4,
            session_id="sess-1",
            event=ToolCallStartEvent(tool_call_id="tool-1", tool_call_name="read_file"),
        )
    )
    buses = BusRegistry()

    stream = _event_stream("sess-1", buses, RunRegistry(), stream=True, after_seq=0, event_store=store)
    try:
        chunks = [await anext(stream), await anext(stream), await anext(stream)]
    finally:
        await stream.aclose()
        await read_conn.close()
        await conn.close()

    parsed = [_parse_sse_chunk(chunk) for chunk in chunks]
    # Durable replay after bus recreation. Catch-up replay is structural-only,
    # so the (artificially persisted) CONTENT delta at seq 2 is dropped while
    # the cursor still advances past it; START/END/TOOL_CALL_START replay.
    assert [seq for seq, _event_name, _payload in parsed] == [1, 3, 4]
    assert [event_name for _seq, event_name, _payload in parsed] == [
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_END",
        "TOOL_CALL_START",
    ]
    assert all(payload["replay"] is True for _seq, _event_name, payload in parsed)
    assert buses.get_or_create("sess-1").next_seq == 5
    assert buses.get_or_create("sess-1").checkpoint_seq == 0


@pytest.mark.asyncio
async def test_event_stream_uses_persisted_checkpoint_as_cursor_boundary(tmp_path):
    import arden.database as database

    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    store = SessionStore(conn, read_conn)
    await store.init_schema()
    await store.record_chat_run_started("run-1", "sess-1")
    await store.record_chat_run_status("run-1", "running", last_seq=7)
    await store.record_session_event(
        StreamRecord(seq=7, session_id="sess-1", event=ThinkingEvent(status="checkpoint-evidence")),
    )
    buses = BusRegistry()

    stream = _event_stream("sess-1", buses, RunRegistry(), stream=True, after_seq=7, event_store=store)
    next_chunk = asyncio.create_task(anext(stream))
    try:
        for _ in range(100):
            await asyncio.sleep(0)
            bus = buses.get("sess-1")
            if bus is not None and bus._subscribers:
                break

        bus = buses.get("sess-1")
        assert bus is not None
        assert bus.next_seq == 8
        assert bus.checkpoint_seq == 7

        await bus.emit(ThinkingEvent(status="live-after-checkpoint"))
        chunk = await asyncio.wait_for(next_chunk, timeout=1)
    finally:
        if not next_chunk.done():
            next_chunk.cancel()
            with suppress(asyncio.CancelledError):
                await next_chunk
        await stream.aclose()
        await read_conn.close()
        await conn.close()

    seq, event_name, payload = _parse_sse_chunk(chunk)
    assert seq == 8
    assert event_name == "thinking"
    assert payload["status"] == "live-after-checkpoint"
    assert "replay" not in payload


@pytest.mark.asyncio
async def test_event_stream_reset_advances_cursor_to_persisted_checkpoint(tmp_path):
    import arden.database as database

    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    store = SessionStore(conn, read_conn)
    await store.init_schema()
    await store.record_chat_run_started("run-1", "sess-1")
    await store.record_chat_run_status("run-1", "running", last_seq=7)
    await store.record_session_event(
        StreamRecord(seq=7, session_id="sess-1", event=ThinkingEvent(status="checkpoint-evidence")),
    )
    buses = BusRegistry()

    stream = _event_stream("sess-1", buses, RunRegistry(), stream=True, after_seq=2, event_store=store)
    try:
        chunk = await anext(stream)
    finally:
        await stream.aclose()
        await read_conn.close()
        await conn.close()

    seq, event_name, payload = _parse_sse_chunk(chunk)
    assert seq == 7
    assert event_name == "stream_reset"
    assert payload["type"] == "stream_reset"
    assert payload["reason"] == "replay_gap"
    assert payload["session_id"] == "sess-1"


@pytest.mark.asyncio
async def test_event_stream_seeds_persisted_cursor_without_client_cursor(tmp_path):
    import arden.database as database

    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    store = SessionStore(conn, read_conn)
    await store.init_schema()
    await store.record_chat_run_started("run-1", "sess-1")
    await store.record_chat_run_status("run-1", "running", last_seq=7)
    await store.record_session_event(
        StreamRecord(seq=7, session_id="sess-1", event=ThinkingEvent(status="checkpoint-evidence")),
    )
    buses = BusRegistry(record_events=store.record_session_events)

    stream = _event_stream("sess-1", buses, RunRegistry(), stream=True, event_store=store)
    next_chunk = asyncio.create_task(anext(stream))
    try:
        for _ in range(100):
            await asyncio.sleep(0)
            bus = buses.get("sess-1")
            if bus is not None and bus._subscribers:
                break

        bus = buses.get("sess-1")
        assert bus is not None
        assert bus.next_seq == 8
        assert bus.checkpoint_seq == 7

        await bus.emit(ThinkingEvent(status="live-with-seeded-cursor"))
        chunk = await asyncio.wait_for(next_chunk, timeout=1)
    finally:
        if not next_chunk.done():
            next_chunk.cancel()
            with suppress(asyncio.CancelledError):
                await next_chunk
        await stream.aclose()
        await read_conn.close()
        await conn.close()

    seq, event_name, payload = _parse_sse_chunk(chunk)
    assert seq == 8
    assert event_name == "thinking"
    assert payload["status"] == "live-with-seeded-cursor"


@pytest.mark.asyncio
async def test_event_stream_replays_persisted_raw_events_above_checkpoint(tmp_path):
    import arden.database as database

    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    store = SessionStore(conn, read_conn)
    await store.init_schema()
    await store.record_chat_run_started("run-1", "sess-1")
    await store.record_chat_run_status("run-1", "running", last_seq=2)
    await store.record_session_event(
        StreamRecord(seq=3, session_id="sess-1", event=ThinkingEvent(status="noncanonical-1")),
    )
    await store.record_session_event(
        StreamRecord(seq=4, session_id="sess-1", event=ThinkingEvent(status="noncanonical-2")),
    )
    buses = BusRegistry()

    stream = _event_stream("sess-1", buses, RunRegistry(), stream=True, after_seq=2, event_store=store)
    try:
        chunks = [await anext(stream), await anext(stream)]
    finally:
        await stream.aclose()
        await read_conn.close()
        await conn.close()

    parsed = [_parse_sse_chunk(chunk) for chunk in chunks]
    bus = buses.get_or_create("sess-1")
    assert [seq for seq, _event_name, _payload in parsed] == [3, 4]
    assert [payload["status"] for _seq, _event_name, payload in parsed] == ["noncanonical-1", "noncanonical-2"]
    assert all(payload["replay"] is True for _seq, _event_name, payload in parsed)
    assert bus.next_seq == 5
    assert bus.checkpoint_seq == 2


@pytest.mark.asyncio
async def test_event_stream_resets_instead_of_replaying_persisted_checkpointed_events(tmp_path):
    import arden.database as database

    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    store = SessionStore(conn, read_conn)
    await store.init_schema()
    await store.record_session_event(
        StreamRecord(seq=2, session_id="sess-1", event=ThinkingEvent(status="checkpointed")),
    )
    buses = BusRegistry()
    bus = buses.get_or_create("sess-1")
    await bus.emit(ThinkingEvent(status="old-a"))
    await bus.emit(ThinkingEvent(status="old-b"))
    bus.mark_checkpoint()
    await bus.emit(ThinkingEvent(status="live-tail"))

    stream = _event_stream("sess-1", buses, RunRegistry(), stream=True, after_seq=1, event_store=store)
    try:
        reset_chunk = await anext(stream)
        tail_chunk = await anext(stream)
    finally:
        await stream.aclose()
        await read_conn.close()
        await conn.close()

    reset_seq, _reset_event_name, reset_payload = _parse_sse_chunk(reset_chunk)
    tail_seq, _tail_event_name, tail_payload = _parse_sse_chunk(tail_chunk)
    assert reset_seq == 2
    assert _reset_event_name == "stream_reset"
    assert reset_payload["type"] == "stream_reset"
    assert reset_payload["reason"] == "replay_gap"
    assert "replay" not in reset_payload
    assert tail_seq == 3
    assert tail_payload["status"] == "live-tail"
    assert tail_payload["replay"] is True


@pytest.mark.asyncio
async def test_event_stream_stream_false_filters_text_deltas_but_preserves_sequence_ids():
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

    parsed = [_parse_sse_chunk(chunk) for chunk in chunks]
    assert [seq for seq, _event_name, _payload in parsed] == [1, 3]
    assert [payload["session_id"] for _seq, _event_name, payload in parsed] == ["sess-1", "sess-1"]
    assert [payload["type"] for _seq, _event_name, payload in parsed] == [
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_END",
    ]


def test_chat_events_rejects_negative_after_seq():
    app.dependency_overrides[get_bus_registry] = lambda: BusRegistry()
    app.dependency_overrides[require_run_registry] = lambda: RunRegistry()

    try:
        response = TestClient(app).get("/chat/events/sess-1?after_seq=-1")
    finally:
        app.dependency_overrides.pop(get_bus_registry, None)
        app.dependency_overrides.pop(require_run_registry, None)

    assert response.status_code == 422


def test_effective_after_seq_uses_last_event_id_header():
    assert _effective_after_seq(None, "4") == 4
    assert _effective_after_seq(2, "4") == 4
    assert _effective_after_seq(7, "4") == 7


def test_effective_after_seq_rejects_invalid_last_event_id():
    with pytest.raises(HTTPException) as exc:
        _effective_after_seq(None, "wat")
    assert exc.value.status_code == 400


def test_expand_skill_command_injects_skill_path(tmp_path):
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: demo
description: Demo skill
---

Run <skill_path>/scripts/demo.sh
""",
        encoding="utf-8",
    )

    registry = SkillRegistry()
    registry.load([(tmp_path, "global")])

    expanded, changed = expand_skill_command("/demo now", registry)

    assert changed is True
    assert f'<skill name="demo" path="{skill_dir}">' in expanded
    assert f"Run {skill_dir}/scripts/demo.sh" in expanded
    assert "User request: now" in expanded


class _Config:
    has_any_model = True
    api_key_hash = None


class _Runtime:
    def __init__(self):
        self.run_registry = RunRegistry()
        self.config = _Config()

    async def resolve_session_chat_model(self, session_id):
        return None


@pytest.fixture
def client_with_active_run():
    """Spin up the FastAPI app with a stub Runtime that already has an active run."""
    runtime = _Runtime()
    run = runtime.run_registry.create_run("sess-1")
    run.status = RunStatus.RUNNING

    app.dependency_overrides[get_runtime] = lambda: runtime
    app.dependency_overrides[get_bus_registry] = lambda: BusRegistry()

    yield TestClient(app), run

    app.dependency_overrides.pop(get_runtime, None)
    app.dependency_overrides.pop(get_bus_registry, None)


def test_post_chat_message_stores_client_id_when_run_active(client_with_active_run):
    c, run = client_with_active_run
    resp = c.post(
        "/chat/message",
        json={"message": "follow-up", "session_id": "sess-1", "client_id": "cid-1"},
    )
    assert resp.status_code == 200
    assert len(run.inject_queue) == 1
    entry = run.inject_queue[0]
    assert entry["role"] == "user"
    assert entry["client_id"] == "cid-1"
    assert entry["content"] == "follow-up"


def test_public_loop_shaped_client_id_grants_no_loop_authority(client_with_active_run):
    client, run = client_with_active_run

    response = client.post(
        "/chat/message",
        json={
            "message": "follow-up",
            "session_id": "sess-1",
            "client_id": "loop:real-task:1",
        },
    )

    assert response.status_code == 200
    assert run.loop_task_id is None


def test_duplicate_post_returns_existing_run_without_requeueing(client_with_active_run):
    """A retry of the same client_id POST resolves to the same run_id and
    does NOT re-queue the message."""
    c, run = client_with_active_run
    payload = {"message": "follow-up", "session_id": "sess-1", "client_id": "cid-1"}

    first = c.post("/chat/message", json=payload)
    second = c.post("/chat/message", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["run_id"] == second.json()["run_id"] == run.run_id
    assert first.json()["status"] == "queued"
    assert second.json()["status"] == "accepted"
    # Only the first POST queued the message; the second was deduped.
    assert len(run.inject_queue) == 1


@pytest.mark.asyncio
async def test_active_run_auto_message_resolves_pending_approval():
    from arden.services import chat as chat_service

    registry = RunRegistry()
    run = registry.create_run("sess-1")
    run.status = RunStatus.RUNNING
    future = asyncio.get_running_loop().create_future()
    run.pending_approvals["tool-x"] = future

    result = await chat_service.submit_chat_message(
        registry,
        lambda: None,
        BusRegistry(),
        message="follow-up",
        session_id="sess-1",
        skip_approvals=True,
        client_id="cid-auto",
    )

    assert result["run_id"] == run.run_id
    assert run.approval_controls.skip_approvals is True
    assert future.result() == {
        "type": "tool_response",
        "tool_id": "tool-x",
        "result": "",
        "approved": True,
    }


@pytest.mark.asyncio
async def test_concurrent_first_messages_share_one_top_level_run(monkeypatch):
    from arden.services import chat as chat_service

    registry = RunRegistry()

    class FakeSessionService:
        store = _EmptyEventStore()

        async def load(self, session_id=None):
            state = SessionState(session_id=session_id or "sess-1", started_at=datetime.now(UTC))
            return SessionData(state=state, messages=[])

        async def save_progress(self, session_state, messages):
            return None

    class FakeExecutor:
        def get_tools(self):
            return []

    async def hold_run(ctx, bus, buses):
        await asyncio.sleep(60)

    monkeypatch.setattr(chat_service, "run_chat", hold_run)
    deps = ChatDeps(
        chat_model="gpt-5.2",
        agent_config=AgentConfig(model="gpt-5.2", research_model=None, max_depth=1, deferred_tools=False),
        executor=FakeExecutor(),
        session_service=FakeSessionService(),
        run_registry=registry,
        available_integrations=[],
        integration_errors={},
    )

    try:
        first, second = await asyncio.gather(
            chat_service.submit_chat_message(
                registry, lambda: deps, BusRegistry(), message="first", session_id="sess-1"
            ),
            chat_service.submit_chat_message(
                registry, lambda: deps, BusRegistry(), message="second", session_id="sess-1"
            ),
        )
        run = registry.get_run(first["run_id"])

        assert first["run_id"] == second["run_id"]
        assert run is not None
        assert len([r for r in registry.list_active_runs() if r.session_id == "sess-1"]) == 1
        assert run.messages[-1]["content"] == "first"
        assert run.inject_queue == [{"role": "user", "content": "second"}]
    finally:
        for run in registry.list_active_runs():
            if run.task:
                run.task.cancel()
                with suppress(asyncio.CancelledError):
                    await run.task


@pytest.mark.asyncio
async def test_submit_cancelled_during_setup_does_not_start_task(monkeypatch):
    from arden.services import chat as chat_service

    registry = RunRegistry()

    class FakeSessionService:
        store = _EmptyEventStore()

        async def load(self, session_id=None):
            state = SessionState(session_id=session_id or "sess-1", started_at=datetime.now(UTC))
            return SessionData(state=state, messages=[])

        async def record_chat_run_started(self, run_id, session_id, metadata=None):
            return None

        async def record_chat_run_status(self, run_id, status, **kwargs):
            return None

        async def save_progress(self, session_state, messages):
            active = registry.get_active_run(session_state.session_id)
            assert active is not None
            registry.cancel_run(active.run_id)

    class FakeExecutor:
        def get_tools(self):
            return []

    async def fail_if_started(ctx, bus, buses):
        raise AssertionError("cancelled setup must not start run_chat")

    monkeypatch.setattr(chat_service, "run_chat", fail_if_started)
    deps = ChatDeps(
        chat_model="gpt-5.2",
        agent_config=AgentConfig(model="gpt-5.2", research_model=None, max_depth=1, deferred_tools=False),
        executor=FakeExecutor(),
        session_service=FakeSessionService(),
        run_registry=registry,
        available_integrations=[],
        integration_errors={},
    )

    result = await chat_service.submit_chat_message(
        registry, lambda: deps, BusRegistry(), message="hi", session_id="sess-1"
    )
    run = registry.get_run(result["run_id"])

    assert result["status"] == "cancelled"
    assert run is not None
    assert run.task is None
    assert run.status == RunStatus.CANCELLED


@pytest.mark.asyncio
async def test_closed_run_is_not_accepting_for_final_drain_window():
    registry = RunRegistry()
    run = registry.create_run("sess-1")
    run.status = RunStatus.RUNNING
    run.queue_injection({"role": "user", "content": "queued"})

    assert run.close_injections() == [{"role": "user", "content": "queued"}]
    assert run.queue_injection({"role": "user", "content": "too late"}) is False
    assert registry.get_accepting_run("sess-1") is None


@pytest.mark.asyncio
async def test_submit_missing_session_is_rejected_before_new_session(monkeypatch):
    from arden.services import chat as chat_service

    class MissingSessionService:
        async def load(self, session_id=None):
            return None

    with pytest.raises(chat_service.ChatSessionNotFound):
        await chat_service.submit_chat_message(
            RunRegistry(),
            lambda: None,
            BusRegistry(),
            message="hi",
            session_id="stale",
            session_service=MissingSessionService(),
        )


def _drain_factory(bus: SessionBus, run: RunState):
    """Mirror the closure built inside services.chat.run_chat for testing."""
    from arden.services.chat import _build_get_pending

    return _build_get_pending(bus, run)


@pytest.mark.asyncio
async def test_drain_emits_ingested_for_entries_with_client_id():
    bus = SessionBus(session_id="sess-1")
    run = RunState(run_id="cool-otter", session_id="sess-1")
    get_pending = _drain_factory(bus, run)
    queue = bus.subscribe()

    run.queue_injection({"role": "user", "content": "first", "client_id": "cid-1"})
    run.queue_injection({"role": "user", "content": "second"})  # background task, no client_id
    run.queue_injection({"role": "user", "content": "third", "client_id": "cid-3"})

    drained = await get_pending()

    # client_id is preserved on the entry so the saved message keeps its id
    # (the desktop relies on it to match user messages back to saved rows
    # for edit/branch flows).
    assert drained == [
        {"role": "user", "content": "first", "client_id": "cid-1"},
        {"role": "user", "content": "second"},
        {"role": "user", "content": "third", "client_id": "cid-3"},
    ]
    # Two ingestion events emitted, in order
    events = [queue.get_nowait().event for _ in range(2)]
    assert all(isinstance(e, MessageIngestedEvent) for e in events)
    assert [e.client_id for e in events] == ["cid-1", "cid-3"]
    assert all(e.run_id == "cool-otter" for e in events)
    assert queue.empty()


@pytest.mark.asyncio
async def test_drain_no_events_when_queue_empty():
    bus = SessionBus(session_id="sess-1")
    run = RunState(run_id="cool-otter", session_id="sess-1")
    get_pending = _drain_factory(bus, run)
    queue = bus.subscribe()

    drained = await get_pending()

    assert drained == []
    assert queue.empty()


@pytest.mark.asyncio
async def test_background_result_after_parent_finished_dispatches_meta_run():
    run = RunState(run_id="cool-otter", session_id="sess-1")
    calls = []

    async def dispatch(
        session_id: str,
        message: str,
        client_id: str | None,
        skip_approvals: bool | None,
        images: list[dict] | None,
    ):
        calls.append((session_id, message, client_id, skip_approvals))

    await _handle_background_result(
        run=run,
        session_id="sess-1",
        messages=[
            {
                "role": "user",
                "content": "[background agent bg-1 completed]\n\nResult:\ndone",
                "is_meta": True,
                "client_id": "bg:bg-1:completed",
            }
        ],
        dispatch_session_message=dispatch,
        run_finished=True,
    )

    assert run.inject_queue == []
    assert calls == [
        (
            "sess-1",
            "[background agent bg-1 completed]\n\nResult:\ndone",
            "bg:bg-1:completed",
            True,
        )
    ]


@pytest.mark.asyncio
async def test_background_result_during_parent_run_queues_injection():
    run = RunState(run_id="cool-otter", session_id="sess-1")
    calls = []

    async def dispatch(
        session_id: str,
        message: str,
        client_id: str | None,
        skip_approvals: bool | None,
        images: list[dict] | None,
    ):
        calls.append((session_id, message, client_id, skip_approvals))

    message = {
        "role": "user",
        "content": "[background agent bg-1 completed]\n\nResult:\ndone",
        "is_meta": True,
        "client_id": "bg:bg-1:completed",
    }

    await _handle_background_result(
        run=run,
        session_id="sess-1",
        messages=[message],
        dispatch_session_message=dispatch,
        run_finished=False,
    )

    assert run.inject_queue == [message]
    assert calls == []


@pytest.mark.asyncio
async def test_background_result_during_parent_run_dedups_by_client_id():
    run = RunState(run_id="cool-otter", session_id="sess-1")
    calls = []

    async def dispatch(
        session_id: str,
        message: str,
        client_id: str | None,
        skip_approvals: bool | None,
        images: list[dict] | None,
    ):
        calls.append((session_id, message, client_id, skip_approvals))

    message = {
        "role": "user",
        "content": "[background agent bg-1 completed]\n\nResult:\ndone",
        "is_meta": True,
        "client_id": "bg:bg-1:completed",
    }

    await _handle_background_result(
        run=run,
        session_id="sess-1",
        messages=[message],
        dispatch_session_message=dispatch,
        run_finished=False,
    )
    await _handle_background_result(
        run=run,
        session_id="sess-1",
        messages=[message],
        dispatch_session_message=dispatch,
        run_finished=False,
    )

    assert run.inject_queue == [message]
    assert calls == []


# --- DELETE /chat/inject/{client_id} ---


@pytest.fixture
def client_no_active_run():
    """Spin up the FastAPI app with a stub Runtime that has no active run."""
    runtime = _Runtime()
    # No run created → get_active_run always returns None

    app.dependency_overrides[get_runtime] = lambda: runtime
    app.dependency_overrides[get_bus_registry] = lambda: BusRegistry()

    yield TestClient(app)

    app.dependency_overrides.pop(get_runtime, None)
    app.dependency_overrides.pop(get_bus_registry, None)


def test_delete_inject_returns_200_when_entry_present(client_with_active_run):
    c, run = client_with_active_run
    run.queue_injection({"role": "user", "content": "x", "client_id": "cid-1"})

    resp = c.delete("/chat/inject/cid-1?session_id=sess-1")

    assert resp.status_code == 200
    assert run.pending_injection_count == 0


def test_delete_inject_returns_409_when_already_drained(client_with_active_run):
    c, run = client_with_active_run
    # Active run, but the client_id was already drained → not in queue
    assert run.pending_injection_count == 0

    resp = c.delete("/chat/inject/cid-missing?session_id=sess-1")

    assert resp.status_code == 409


def test_delete_inject_returns_404_when_no_active_run(client_no_active_run):
    resp = client_no_active_run.delete("/chat/inject/cid-x?session_id=sess-none")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cancel_before_enqueue_tombstone_prevents_later_queue(tmp_path):
    import arden.database as database
    from arden.services import chat as chat_service

    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    store = SessionStore(conn, read_conn)
    await store.init_schema()
    service = SessionService(store)
    state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))
    await service.save(state, [])
    registry = RunRegistry()
    run = registry.create_run("sess-1")
    run.status = RunStatus.RUNNING
    runtime = type("Runtime", (), {"session_service": service})()

    try:
        cancelled = await cancel_inject(
            "cid-before-enqueue",
            "sess-1",
            run_registry=registry,
            runtime=runtime,
        )
        result = await chat_service.submit_chat_message(
            registry,
            lambda: None,
            BusRegistry(),
            message="follow-up",
            session_id="sess-1",
            client_id="cid-before-enqueue",
            session_service=service,
        )

        assert cancelled == {"status": "cancelled", "client_id": "cid-before-enqueue"}
        assert result == {"run_id": run.run_id, "session_id": "sess-1", "status": "cancelled"}
        assert run.inject_queue == []
        assert await store.list_chat_queued_messages("sess-1", status="queued") == []
    finally:
        await read_conn.close()
        await conn.close()


@pytest.mark.asyncio
async def test_ingested_queue_retry_does_not_return_stale_queued_status(tmp_path):
    import arden.database as database
    from arden.services import chat as chat_service
    from arden.services.chat import _build_get_pending

    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    store = SessionStore(conn, read_conn)
    await store.init_schema()
    service = SessionService(store)
    state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))
    await service.save(state, [])
    registry = RunRegistry()
    run = registry.create_run("sess-1")
    run.status = RunStatus.RUNNING
    bus = SessionBus(session_id="sess-1")

    try:
        queued = await chat_service.submit_chat_message(
            registry,
            lambda: None,
            BusRegistry(),
            message="follow-up",
            session_id="sess-1",
            client_id="cid-ingested",
            session_service=service,
        )
        assert queued["status"] == "queued"

        await _build_get_pending(bus, run, service)()

        retry = await chat_service.submit_chat_message(
            registry,
            lambda: None,
            BusRegistry(),
            message="follow-up",
            session_id="sess-1",
            client_id="cid-ingested",
            session_service=service,
        )

        assert retry == {"run_id": run.run_id, "session_id": "sess-1", "status": "ingested"}
        assert run.inject_queue == []
    finally:
        await read_conn.close()
        await conn.close()


@pytest.mark.asyncio
async def test_automation_provenance_does_not_break_legacy_chat_replay(tmp_path):
    import arden.database as database
    from arden.services import chat as chat_service

    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    store = SessionStore(conn, read_conn)
    await store.init_schema()
    service = SessionService(store)
    state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))
    await service.save(state, [])
    client_id = "loop:area:proj_1:1"
    payload = {
        "session_id": "sess-1",
        "message": "Inspect the area.",
        "skip_approvals": False,
        "images": [],
        "context": [],
    }
    request_hash = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

    try:
        await store.claim_chat_idempotency_key(
            session_id="sess-1",
            client_id=client_id,
            request_hash=request_hash,
        )
        await store.update_chat_idempotency_key(
            session_id="sess-1",
            client_id=client_id,
            status="completed",
            run_id="run-old",
        )

        result = await chat_service.submit_chat_message(
            RunRegistry(),
            lambda: pytest.fail("replay must not start a second run"),
            BusRegistry(),
            message="Inspect the area.",
            session_id="sess-1",
            client_id=client_id,
            session_service=service,
            automation_id="area:proj_1",
        )

        assert result == {
            "run_id": "run-old",
            "session_id": "sess-1",
            "status": "completed",
        }
    finally:
        await read_conn.close()
        await conn.close()


@pytest.mark.asyncio
async def test_recreated_automation_does_not_reuse_a_legacy_iteration_receipt(tmp_path):
    import arden.database as database
    from arden.services import chat as chat_service

    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    store = SessionStore(conn, read_conn)
    await store.init_schema()
    service = SessionService(store)
    state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))
    await service.save(state, [])
    old_client_id = "loop:area:proj_1:1"
    old_payload = {
        "session_id": "sess-1",
        "message": "Old incarnation prompt.",
        "skip_approvals": False,
        "images": [],
        "context": [],
    }
    old_hash = sha256(json.dumps(old_payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    recreated = Automation(
        task_id="area:proj_1",
        name="Area agent",
        prompt="New incarnation prompt.",
        model=None,
        triggers=[],
        enabled=True,
        created_at=datetime(2026, 7, 29, tzinfo=UTC),
        next_run_at=None,
        last_run_at=None,
        last_result=None,
        running_since=None,
        auto_approve=False,
        iteration_count=0,
        thread_id="sess-1",
        read_history=True,
    )
    new_client_id = _iteration_client_id(recreated)
    registry = RunRegistry()
    active_run = registry.create_run("sess-1")
    active_run.status = RunStatus.RUNNING

    try:
        await store.claim_chat_idempotency_key(
            session_id="sess-1",
            client_id=old_client_id,
            request_hash=old_hash,
        )
        await store.update_chat_idempotency_key(
            session_id="sess-1",
            client_id=old_client_id,
            status="completed",
            run_id="run-old",
        )

        accepted = await chat_service.submit_chat_message(
            registry,
            lambda: pytest.fail("active run must receive an injection"),
            BusRegistry(),
            message=recreated.prompt,
            session_id="sess-1",
            client_id=new_client_id,
            session_service=service,
            automation_id=recreated.task_id,
        )
        replay = await chat_service.submit_chat_message(
            registry,
            lambda: pytest.fail("idempotent replay must not start a run"),
            BusRegistry(),
            message=recreated.prompt,
            session_id="sess-1",
            client_id=new_client_id,
            session_service=service,
            automation_id=recreated.task_id,
        )

        assert new_client_id != old_client_id
        assert accepted == {
            "run_id": active_run.run_id,
            "session_id": "sess-1",
            "status": "queued",
        }
        assert replay == accepted
        assert len(active_run.inject_queue) == 1
    finally:
        await read_conn.close()
        await conn.close()


def test_cancel_returns_404_for_unknown_run(client_no_active_run):
    resp = client_no_active_run.post("/cancel", json={"run_id": "missing"})

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Run not found"


def test_cancel_returns_202_for_running_run(client_with_active_run):
    c, run = client_with_active_run

    resp = c.post("/cancel", json={"run_id": run.run_id})

    assert resp.status_code == 202
    assert resp.json()["status"] == "cancelling"
    assert resp.json()["found"] is True
    assert run.cancelled is True
    assert run.status == RunStatus.RUNNING


@pytest.mark.asyncio
async def test_submit_message_after_cancel_starts_new_run(monkeypatch):
    from arden.services import chat as chat_service

    registry = RunRegistry()
    old_run = registry.create_run("sess-1")
    old_run.status = RunStatus.RUNNING
    registry.cancel_run(old_run.run_id)

    class FakeSessionService:
        store = _EmptyEventStore()

        async def load(self, session_id=None):
            state = SessionState(
                session_id=session_id or "sess-1",
                started_at=datetime.now(UTC),
            )
            return SessionData(state=state, messages=[])

        async def save_progress(self, session_state, messages):
            return None

    class FakeExecutor:
        def get_tools(self):
            return []

    async def noop_run_chat(ctx, bus, buses):
        return None

    monkeypatch.setattr(chat_service, "run_chat", noop_run_chat)
    deps = ChatDeps(
        chat_model="gpt-5.2",
        agent_config=AgentConfig(model="gpt-5.2", research_model=None, max_depth=1, deferred_tools=False),
        executor=FakeExecutor(),
        session_service=FakeSessionService(),
        run_registry=registry,
        available_integrations=[],
        integration_errors={},
    )

    result = await chat_service.submit_chat_message(
        registry,
        lambda: deps,
        BusRegistry(),
        message="follow-up",
        session_id="sess-1",
        client_id="cid-follow-up",
    )

    new_run = registry.get_run(result["run_id"])
    assert result["status"] == "running"
    assert result["run_id"] != old_run.run_id
    assert old_run.pending_injection_count == 0
    assert new_run is not None
    assert new_run.messages[-1]["content"] == "follow-up"
    assert new_run.messages[-1]["client_id"] == "cid-follow-up"
    if new_run.task:
        await asyncio.wait_for(new_run.task, timeout=1)


@pytest.mark.asyncio
async def test_active_run_keeps_steer_transient_in_memory():
    from arden.services import chat as chat_service

    class FakeSessionService:
        def __init__(self):
            self.queued = []

        async def claim_chat_idempotency_key(self, **kwargs):
            return True, {"status": "accepted", "run_id": None, **kwargs}

        async def update_chat_idempotency_key(self, **kwargs):
            return {"status": kwargs.get("status"), "run_id": kwargs.get("run_id")}

        async def record_chat_queued_message(self, **kwargs):
            self.queued.append(kwargs)

    registry = RunRegistry()
    run = registry.create_run("sess-1")
    run.status = RunStatus.RUNNING
    session_service = FakeSessionService()

    result = await chat_service.submit_chat_message(
        registry,
        lambda: None,
        BusRegistry(),
        message="follow-up",
        session_id="sess-1",
        client_id="cid-ledger",
        session_service=session_service,
    )

    assert result["run_id"] == run.run_id
    assert run.pending_injection_count == 1
    assert session_service.queued == []


@pytest.mark.asyncio
async def test_active_run_does_not_write_steer_to_queue_ledger():
    from arden.services import chat as chat_service

    class FakeSessionService:
        async def claim_chat_idempotency_key(self, **kwargs):
            return True, {"status": "accepted", "run_id": None, **kwargs}

        async def record_chat_queued_message(self, **kwargs):
            raise RuntimeError("ledger down")

    registry = RunRegistry()
    run = registry.create_run("sess-1")
    run.status = RunStatus.RUNNING

    result = await chat_service.submit_chat_message(
        registry,
        lambda: None,
        BusRegistry(),
        message="follow-up",
        session_id="sess-1",
        client_id="cid-ledger",
        session_service=FakeSessionService(),
    )

    assert result["status"] == "queued"
    assert run.pending_injection_count == 1


@pytest.mark.asyncio
async def test_active_run_does_not_queue_message_when_idempotency_update_fails():
    from arden.services import chat as chat_service

    class FakeSessionService:
        def __init__(self):
            self.queued = []
            self.cancelled = []

        async def claim_chat_idempotency_key(self, **kwargs):
            return True, {"status": "accepted", "run_id": None, **kwargs}

        async def record_chat_queued_message(self, **kwargs):
            self.queued.append(kwargs)

        async def update_chat_idempotency_key(self, **kwargs):
            raise RuntimeError("idempotency down")

        async def mark_chat_queued_message_cancelled(self, client_id):
            self.cancelled.append(client_id)

    registry = RunRegistry()
    run = registry.create_run("sess-1")
    run.status = RunStatus.RUNNING
    session_service = FakeSessionService()

    with pytest.raises(RuntimeError, match="idempotency down"):
        await chat_service.submit_chat_message(
            registry,
            lambda: None,
            BusRegistry(),
            message="follow-up",
            session_id="sess-1",
            client_id="cid-ledger",
            session_service=session_service,
        )

    assert run.pending_injection_count == 0
    assert session_service.cancelled == []


@pytest.mark.asyncio
async def test_pre_task_setup_failure_clears_prepared_run(monkeypatch):
    from arden.services import chat as chat_service

    registry = RunRegistry()

    class FakeSessionService:
        def __init__(self):
            self.statuses = []
            self.store = _EmptyEventStore()

        async def load(self, session_id=None):
            state = SessionState(session_id=session_id or "sess-1", started_at=datetime.now(UTC), name="Existing")
            return SessionData(state=state, messages=[])

        async def record_chat_run_started(self, run_id, session_id, metadata=None):
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

        async def save_progress(self, session_state, messages):
            raise RuntimeError("progress down")

    class FakeExecutor:
        def get_tools(self):
            return []

    session_service = FakeSessionService()
    deps = ChatDeps(
        chat_model="gpt-5.2",
        agent_config=AgentConfig(model="gpt-5.2", research_model=None, max_depth=1, deferred_tools=False),
        executor=FakeExecutor(),
        session_service=session_service,
        run_registry=registry,
        available_integrations=[],
        integration_errors={},
    )

    with pytest.raises(RuntimeError, match="progress down"):
        await chat_service.submit_chat_message(
            registry,
            lambda: deps,
            BusRegistry(),
            message="hello",
            session_id="sess-1",
        )

    assert registry.get_active_run("sess-1") is None
    assert session_service.statuses[-1] == {
        "status": RunStatus.ERROR.value,
        "error_code": "run_preparation_failed",
    }


@pytest.mark.asyncio
async def test_pre_start_cancelled_task_emits_terminal_fallback():
    from arden.services import chat as chat_service

    registry = RunRegistry()

    class FakeSessionService:
        store = _EmptyEventStore()

        async def load(self, session_id=None):
            state = SessionState(
                session_id=session_id or "sess-1",
                started_at=datetime.now(UTC),
            )
            return SessionData(state=state, messages=[])

        async def save_progress(self, session_state, messages):
            return None

    class FakeExecutor:
        def get_tools(self):
            return []

    deps = ChatDeps(
        chat_model="gpt-5.2",
        agent_config=AgentConfig(model="gpt-5.2", research_model=None, max_depth=1, deferred_tools=False),
        executor=FakeExecutor(),
        session_service=FakeSessionService(),
        run_registry=registry,
        available_integrations=[],
        integration_errors={},
    )
    buses = BusRegistry()

    result = await chat_service.submit_chat_message(
        registry,
        lambda: deps,
        buses,
        message="cancel before start",
        session_id="sess-1",
        client_id="cid-prestart",
    )
    run = registry.get_run(result["run_id"])
    assert run is not None

    registry.cancel_run(run.run_id)

    async def wait_for_terminal_cancel():
        while not run.cancel_terminal_emitted:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_terminal_cancel(), timeout=1)

    bus = buses.get("sess-1")
    assert bus is not None
    assert [record.event.type.value for record in bus._recent] == ["run_cancelled"]
    assert run.cancel_terminal_emitted is True
    assert run.status == RunStatus.CANCELLED


@pytest.mark.asyncio
async def test_submit_chat_message_primes_bus_cursor_from_durable_events(tmp_path, monkeypatch):
    import arden.database as database
    from arden.services import chat as chat_service

    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    store = SessionStore(conn, read_conn)
    await store.init_schema()
    await store.record_session_event(StreamRecord(seq=187, session_id="sess-1", event=ThinkingEvent(status="old")))

    class FakeSessionService:
        def __init__(self, store):
            self.store = store

        async def load(self, session_id=None):
            state = SessionState(session_id=session_id or "sess-1", started_at=datetime.now(UTC))
            return SessionData(state=state, messages=[])

        async def save_progress(self, session_state, messages):
            return None

        async def record_chat_run_started(self, run_id, session_id, *, metadata=None):
            return None

    class FakeExecutor:
        def get_tools(self):
            return []

    async def hold_run(ctx, bus, buses):
        await asyncio.sleep(60)

    monkeypatch.setattr(chat_service, "run_chat", hold_run)

    registry = RunRegistry()
    buses = BusRegistry(record_events=store.record_session_events)
    session_service = FakeSessionService(store)
    deps = ChatDeps(
        chat_model="gpt-5.2",
        agent_config=AgentConfig(model="gpt-5.2", research_model=None, max_depth=1, deferred_tools=False),
        executor=FakeExecutor(),
        session_service=session_service,
        run_registry=registry,
        available_integrations=[],
        integration_errors={},
    )

    try:
        result = await chat_service.submit_chat_message(
            registry,
            lambda: deps,
            buses,
            message="hello",
            session_id="sess-1",
            session_service=session_service,
        )
        bus = buses.get("sess-1")
        assert bus is not None
        assert bus.next_seq == 188
    finally:
        run = registry.get_run(result["run_id"]) if "result" in locals() else None
        if run and run.task:
            run.task.cancel()
            with suppress(asyncio.CancelledError):
                await run.task
        await buses.close_all()
        await read_conn.close()
        await conn.close()


# --- Full chain: agent.stream + real closure + real bus + mid-run inject ---


@pytest.mark.asyncio
async def test_full_chain_inject_during_run_emits_ingested_and_lands_in_messages():
    """Simulate the production chain: an agent is mid-iteration, a user message
    is appended to inject_queue (as POST /chat/message would do), and the next
    iteration's drain must (a) emit a MessageIngestedEvent on the bus and
    (b) extend the message list so the LLM sees the injected text."""
    from arden.agent import AgentHooks, ToolResult
    from arden.services.chat import _build_get_pending
    from tests.test_agent_lib import FakeExecutor, FakeLLM, _make_agent, _msgs, _response, _tc

    bus = SessionBus(session_id="sess-inj")
    run = RunState(run_id="cool-otter", session_id="sess-inj")

    sub = bus.subscribe()

    # LLM produces: tool_call → (drain happens) → text response.
    # We inject between iterations 1 and 2.
    llm = FakeLLM(
        [
            _response(tool_calls=[_tc("c1", "noop", {})]),
            _response(text="acknowledged"),
        ]
    )
    executor = FakeExecutor({"noop": ToolResult(content="ok", preview="ok")})
    agent = _make_agent(
        llm,
        executor,
        hooks=AgentHooks(get_pending_messages=_build_get_pending(bus, run)),
    )

    messages = _msgs()

    # Append BEFORE running so the drain at top of iter 2 sees it.
    # (In production, POST runs concurrently with the agent loop; appending
    # before iter 2's drain is the same observable state.)
    run.queue_injection({"role": "user", "content": "follow-up", "client_id": "cid-XYZ"})

    result = await agent.run(messages)

    # The agent must have observed the injected user turn.
    assert any(m.get("content") == "follow-up" for m in messages), f"injected message not in messages: {messages}"
    assert result.text == "acknowledged"

    # The bus must have received a MessageIngestedEvent with the right client_id.
    received: list = []
    while not sub.empty():
        received.append(sub.get_nowait().event)
    ingested = [e for e in received if isinstance(e, MessageIngestedEvent)]
    assert len(ingested) == 1, f"expected 1 ingestion event, got {len(ingested)}: {received}"
    assert ingested[0].client_id == "cid-XYZ"
    assert ingested[0].run_id == "cool-otter"

    # client_id stays on the message so the saved row can later be
    # referenced by /session/revert (edit flow) and /sessions/{id}/branch.
    # Each LLM provider's preprocessor strips client_id before the actual
    # API call so it never reaches the wire.
    injected_msg = next(m for m in messages if m.get("content") == "follow-up")
    assert injected_msg.get("client_id") == "cid-XYZ"


@pytest.mark.asyncio
async def test_full_chain_inject_during_final_response_continues_loop():
    """User submits while the LLM is producing its end-turn response.
    My agent.py fix should drain pending, continue the loop, emit ingestion event."""
    from arden.agent import AgentHooks
    from arden.services.chat import _build_get_pending
    from tests.test_agent_lib import FakeExecutor, FakeLLM, _make_agent, _msgs, _response

    bus = SessionBus(session_id="sess-inj2")
    run = RunState(run_id="cool-otter", session_id="sess-inj2")

    sub = bus.subscribe()

    # Two final-style responses (no tool calls). After the first, we inject;
    # the agent must continue rather than declaring END_TURN.
    llm = FakeLLM(
        [
            _response(text="first answer"),
            _response(text="second answer"),
        ]
    )
    closure = _build_get_pending(bus, run)

    # Wrap the closure so we can inject between calls.
    call_count = 0

    async def hook():
        nonlocal call_count
        call_count += 1
        # On the 2nd call (which is the post-LLM drain check before declaring end-turn),
        # there's nothing pending yet — append now to simulate user submit during stream.
        if call_count == 2:
            run.queue_injection({"role": "user", "content": "wait!", "client_id": "cid-LATE"})
        return await closure()

    agent = _make_agent(llm, FakeExecutor({}), hooks=AgentHooks(get_pending_messages=hook))

    messages = _msgs()
    result = await agent.run(messages)

    # Agent must have continued past the first response and produced a second.
    assert result.text == "second answer", f"agent did not loop: {result.text}"
    assert any(m.get("content") == "wait!" for m in messages)

    # Ingestion event was emitted.
    received: list = []
    while not sub.empty():
        received.append(sub.get_nowait().event)
    ingested = [e for e in received if isinstance(e, MessageIngestedEvent)]
    assert len(ingested) == 1
    assert ingested[0].client_id == "cid-LATE"
