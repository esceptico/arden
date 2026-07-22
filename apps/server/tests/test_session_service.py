import asyncio
from datetime import UTC, datetime

import pytest

from arden.context.models import SessionState
from arden.services.session import SessionService


class _SlowStore:
    def __init__(self):
        self.in_save = False
        self.overlapped = False

    async def save_session(self, session_state, messages, metadata=None):
        if self.in_save:
            self.overlapped = True
        self.in_save = True
        await asyncio.sleep(0.01)
        self.in_save = False


class _FailingStore:
    async def save_session(self, session_state, messages, metadata=None):
        raise RuntimeError("store down")

    async def update_progress(self, session_state, messages):
        raise RuntimeError("store down")

    async def record_chat_run_started(self, run_id, session_id, metadata=None):
        raise RuntimeError("store down")

    async def record_chat_run_status(self, run_id, status, **kwargs):
        raise RuntimeError("store down")

    async def record_chat_queued_message(self, **kwargs):
        raise RuntimeError("store down")

    async def mark_chat_queued_message_ingested(self, client_id, *, ingested_seq=None):
        raise RuntimeError("store down")

    async def mark_chat_queued_message_cancelled(self, client_id):
        raise RuntimeError("store down")


@pytest.mark.asyncio
async def test_session_service_serializes_saves_for_same_session():
    store = _SlowStore()
    service = SessionService(store)
    state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))

    await asyncio.gather(
        service.save(state, [{"role": "user", "content": "one"}]),
        service.save(state, [{"role": "user", "content": "two"}]),
    )

    assert store.overlapped is False


@pytest.mark.asyncio
async def test_session_service_save_propagates_store_failures():
    service = SessionService(_FailingStore())
    state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))

    with pytest.raises(RuntimeError, match="store down"):
        await service.save(state, [{"role": "user", "content": "one"}])


@pytest.mark.asyncio
async def test_session_service_save_progress_propagates_store_failures():
    service = SessionService(_FailingStore())
    state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))

    with pytest.raises(RuntimeError, match="store down"):
        await service.save_progress(state, [{"role": "user", "content": "one"}])


@pytest.mark.asyncio
async def test_session_service_chat_run_started_propagates_store_failures():
    service = SessionService(_FailingStore())

    with pytest.raises(RuntimeError, match="store down"):
        await service.record_chat_run_started("run-1", "sess-1")


@pytest.mark.asyncio
async def test_session_service_chat_run_status_propagates_store_failures():
    service = SessionService(_FailingStore())

    with pytest.raises(RuntimeError, match="store down"):
        await service.record_chat_run_status("run-1", "completed")


@pytest.mark.asyncio
async def test_session_service_queued_chat_message_propagates_store_failures():
    service = SessionService(_FailingStore())

    with pytest.raises(RuntimeError, match="store down"):
        await service.record_chat_queued_message(
            client_id="cid-1",
            session_id="sess-1",
            run_id="run-1",
            message={"role": "user", "content": "queued"},
        )


@pytest.mark.asyncio
async def test_session_service_queued_message_ingested_propagates_store_failures():
    service = SessionService(_FailingStore())

    with pytest.raises(RuntimeError, match="store down"):
        await service.mark_chat_queued_message_ingested("cid-1", ingested_seq=10)


@pytest.mark.asyncio
async def test_session_service_queued_message_cancelled_propagates_store_failures():
    service = SessionService(_FailingStore())

    with pytest.raises(RuntimeError, match="store down"):
        await service.mark_chat_queued_message_cancelled("cid-1")
