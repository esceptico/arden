import asyncio
from datetime import UTC, datetime

import pytest

from arden.agent.types.tools import ToolSourceRef
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

    async def cancel_chat_idempotency_key(self, **kwargs):
        raise RuntimeError("store down")

    async def record_chat_compaction(self, **kwargs):
        raise RuntimeError("store down")

    async def set_goal(self, session_id, objective, *, token_budget=None):
        raise RuntimeError("store down")

    async def get_goal(self, session_id):
        raise RuntimeError("store down")

    async def update_goal(self, session_id, **kwargs):
        raise RuntimeError("store down")

    async def clear_goal(self, session_id):
        raise RuntimeError("store down")

    async def set_todo_override(self, session_id, items, explanation=None):
        raise RuntimeError("store down")

    async def get_todo_override(self, session_id):
        raise RuntimeError("store down")

    async def clear_todo_override(self, session_id):
        raise RuntimeError("store down")

    async def set_session_todos(self, session_id, items, explanation=None):
        raise RuntimeError("store down")

    async def get_session_todos(self, session_id):
        raise RuntimeError("store down")

    async def clear_session_todos(self, session_id):
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
async def test_session_service_chat_idempotency_cancellation_propagates_store_failures():
    service = SessionService(_FailingStore())

    with pytest.raises(RuntimeError, match="store down"):
        await service.cancel_chat_idempotency_key(
            client_id="cid-1",
            session_id="sess-1",
            run_id="run-1",
        )


@pytest.mark.asyncio
async def test_session_service_metadata_writes_never_turn_store_failures_into_empty_results():
    service = SessionService(_FailingStore())
    calls = (
        service.record_chat_compaction(
            compaction_id="compact-1",
            session_id="sess-1",
            boundary_seq=1,
            messages_before=2,
            messages_after=1,
        ),
        service.set_goal("sess-1", "ship"),
        service.get_goal("sess-1"),
        service.update_goal("sess-1", objective="ship now"),
        service.clear_goal("sess-1"),
        service.set_todo_override("sess-1", []),
        service.get_todo_override("sess-1"),
        service.clear_todo_override("sess-1"),
        service.set_session_todos("sess-1", []),
        service.get_session_todos("sess-1"),
        service.clear_session_todos("sess-1"),
    )

    for call in calls:
        with pytest.raises(RuntimeError, match="store down"):
            await call


@pytest.mark.asyncio
async def test_session_service_event_delivery_failure_is_explicit():
    async def fail_event(_event):
        raise RuntimeError("event bus down")

    service = SessionService(_SlowStore(), event_sink=fail_event)
    state = SessionState(session_id="sess-1", started_at=datetime.now(UTC))

    with pytest.raises(RuntimeError, match="event bus down"):
        await service.announce_created(state)


@pytest.mark.asyncio
async def test_session_service_serializes_typed_run_evidence_at_store_boundary():
    class EvidenceStore:
        async def record_run_evidence(self, **kwargs):
            self.kwargs = kwargs
            return {"sources": kwargs["source_refs"]}

    store = EvidenceStore()
    service = SessionService(store)
    source = ToolSourceRef(provider="gmail", kind="message", ref="message-1", title="Message 1")

    result = await service.record_run_evidence(
        run_id="run-1",
        session_id="session-1",
        source_refs=(source,),
    )

    assert store.kwargs["source_refs"] == [source.to_dict()]
    assert result == {"sources": [source.to_dict()]}

    with pytest.raises(TypeError, match="ToolSourceRef"):
        await service.record_run_evidence(
            run_id="run-1",
            session_id="session-1",
            source_refs=({"provider": "gmail"},),
        )
