import asyncio
import json
from pathlib import Path

import pytest
import pytest_asyncio

import arden.database as database
from arden.automation.scheduler import AUTOMATION_BUS_KEY, Scheduler
from arden.automation.service import AutomationService
from arden.automation.store import AutomationStore
from arden.context.store import SessionStore
from arden.events.sse import EventType
from arden.server.bus import BusRegistry
from arden.services.session import SessionService


@pytest_asyncio.fixture
async def store(tmp_path: Path):
    conn = await database.connect(tmp_path / "automation.db")
    s = AutomationStore(conn)
    await s.init_schema()
    yield s
    await conn.close()


@pytest_asyncio.fixture
async def session_service(tmp_path: Path):
    conn = await database.connect(tmp_path / "sessions.db")
    s = SessionStore(conn)
    await s.init_schema()
    yield SessionService(s)
    await conn.close()


@pytest_asyncio.fixture
async def service(store: AutomationStore, session_service: SessionService):
    sched = Scheduler(store=store, build_deps=lambda: None)
    return AutomationService(store=store, scheduler=sched, session_service=session_service)


@pytest.mark.asyncio
async def test_service_accepts_session_service(store: AutomationStore, session_service: SessionService):
    sched = Scheduler(store=store, build_deps=lambda: None)
    svc = AutomationService(store=store, scheduler=sched, session_service=session_service)
    assert svc.session_service is session_service


@pytest.mark.asyncio
async def test_create_provisions_channel(service: AutomationService, session_service: SessionService):
    auto = await service.create(
        name="scan offers",
        description="check the offers feed",
        trigger_type="time",
        every="1h",
    )

    assert auto is not None
    assert auto.thread_id is not None
    assert auto.read_history is True
    # description is the authoritative prompt for session-bound automations.
    assert auto.description == "check the offers feed"

    data = await session_service.load(auto.thread_id)
    assert data is not None
    assert data.state.session_type == "channel"
    assert data.state.origin_automation_id == auto.task_id


@pytest.mark.asyncio
async def test_create_emits_session_created_on_automation_bus(store: AutomationStore, session_service: SessionService):
    registry = BusRegistry()
    bus = registry.get_or_create(AUTOMATION_BUS_KEY)
    session_service.set_event_sink(bus.emit)
    sched = Scheduler(store=store, build_deps=lambda: None)
    svc = AutomationService(store=store, scheduler=sched, session_service=session_service)

    queue = bus.subscribe()

    auto = await svc.create(
        name="scan offers",
        description="check the offers feed",
        trigger_type="time",
        every="1h",
    )
    assert auto is not None

    record = queue.get_nowait()
    assert record.event.type == EventType.SESSION_CREATED
    session = record.event.session
    assert session["session_id"] == auto.thread_id
    assert session["session_type"] == "channel"
    assert session["origin_automation_id"] == auto.task_id
    assert session["name"] == "scan offers"
    assert session["message_count"] == 0
    assert session["started_at"]
    assert session["last_activity"]


@pytest.mark.asyncio
async def test_channel_content_save_emits_session_activity(session_service: SessionService):
    registry = BusRegistry()
    bus = registry.get_or_create(AUTOMATION_BUS_KEY)
    session_service.set_event_sink(bus.emit)

    channel = await session_service.provision(name="feed", session_type="channel", origin_automation_id="t1")
    # Subscribe after creation so we only observe the activity delta.
    queue = bus.subscribe()
    await session_service.save_progress(channel, [{"role": "assistant", "content": "hi"}])

    record = queue.get_nowait()
    assert record.event.type == EventType.SESSION_ACTIVITY
    assert record.event.session["session_id"] == channel.session_id
    assert record.event.session["message_count"] == 1


@pytest.mark.asyncio
async def test_chat_session_saves_do_not_emit_activity(session_service: SessionService):
    # Ordinary chat sessions must not flood the global bus — the user is
    # already watching their own chat over its per-session stream.
    registry = BusRegistry()
    bus = registry.get_or_create(AUTOMATION_BUS_KEY)
    session_service.set_event_sink(bus.emit)

    chat = await session_service.provision(name="my chat", session_type="chat")
    queue = bus.subscribe()
    await session_service.save_progress(chat, [{"role": "assistant", "content": "hi"}])

    with pytest.raises(asyncio.QueueEmpty):
        queue.get_nowait()


@pytest.mark.asyncio
async def test_session_created_reaches_automation_event_stream(store: AutomationStore, session_service: SessionService):
    # Full server transport path: provision() -> SessionService sink -> bus
    # -> the real /automations/events stream generator -> SSE wire frame.
    from arden.server.routers.automation import _automation_event_stream

    registry = BusRegistry()
    bus = registry.get_or_create(AUTOMATION_BUS_KEY)
    session_service.set_event_sink(bus.emit)
    sched = Scheduler(store=store, build_deps=lambda: None)
    svc = AutomationService(store=store, scheduler=sched, session_service=session_service)

    auto = await svc.create(
        name="watch inbox",
        description="check the inbox",
        trigger_type="time",
        every="1h",
    )
    assert auto is not None

    # The one event emitted on create is the session_created announcement;
    # replay from the start to read it back off the stream.
    stream = _automation_event_stream(registry, after_seq=0)
    try:
        chunk = await anext(stream)
    finally:
        await stream.aclose()

    payload = json.loads(chunk.split("data: ", 1)[1].strip())
    assert payload["type"] == "session_created"
    assert payload["session"]["session_id"] == auto.thread_id
    assert payload["session"]["origin_automation_id"] == auto.task_id
    assert payload["session"]["session_type"] == "channel"


@pytest.mark.asyncio
async def test_create_with_explicit_thread_id_skips_channel(
    service: AutomationService, session_service: SessionService
):
    chat_state = session_service.create(name="existing chat")
    await session_service.save(chat_state, [])

    auto = await service.create(
        name="bound to chat",
        description="run in the existing chat",
        trigger_type="time",
        every="1h",
        thread_id=chat_state.session_id,
    )

    assert auto is not None
    assert auto.thread_id == chat_state.session_id

    data = await session_service.load(chat_state.session_id)
    assert data is not None
    assert data.state.session_type == "chat"
    assert data.state.origin_automation_id is None
