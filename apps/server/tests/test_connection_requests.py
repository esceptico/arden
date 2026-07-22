import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

import arden.database as database
from arden.context.models import SessionState
from arden.context.store import SessionStore
from arden.events.sse import ConnectionNeededEvent
from arden.integrations.base import IntegrationConnectionDescriptor, IntegrationConnectionError
from arden.server.routers.chat import router as chat_router
from arden.server.runtime import get_runtime
from arden.server.state import RunRegistry
from arden.tools.core.context import (
    BackgroundTaskRegistry,
    IOBridge,
    RunContext,
    ToolContext,
    ToolExecution,
)
from arden.tools.core.registry import ToolRegistry


@pytest_asyncio.fixture
async def session_store(tmp_path: Path):
    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    store = SessionStore(conn, read_conn)
    await store.init_schema()
    yield store
    await read_conn.close()
    await conn.close()


def _descriptor() -> IntegrationConnectionDescriptor:
    return IntegrationConnectionDescriptor(
        integration_id="gmail",
        connection_id="google",
        label="Gmail",
        capability="Search, read, and send email",
        action="oauth",
        settings_tab="integrations",
        state="auth_required",
        detail="Reconnect Gmail",
        required_scopes=("gmail.readonly",),
        tool_names=("emails", "read_email", "send_email"),
    )


def _make_execution(io: IOBridge, tool_id: str = "call-1") -> ToolExecution:
    ctx = ToolContext(
        session_state=SessionState(session_id="session-1", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1"),
        io=io,
        background_tasks=BackgroundTaskRegistry(session_id="session-1"),
    )
    return ToolExecution(tool_id=tool_id, tool_name="request_connection", ctx=ctx)


@pytest.mark.asyncio
async def test_request_connection_emits_event_and_waits_for_matching_resolution():
    emitted = []
    pending: dict[str, asyncio.Future] = {}

    async def emit(event):
        emitted.append(event)

    execution = _make_execution(IOBridge(emit=emit, pending_connections=pending))
    task = asyncio.create_task(
        execution.request_connection(_descriptor(), source="suggestion", detail="Need the inbox")
    )
    for _ in range(20):
        if pending and emitted:
            break
        await asyncio.sleep(0)

    assert list(pending) == ["call-1"]
    assert len(emitted) == 1
    event = emitted[0]
    assert isinstance(event, ConnectionNeededEvent)
    assert event.run_id == "run-1"
    assert event.integration_id == "gmail"
    assert event.connection_id == "google"
    assert event.source == "suggestion"
    assert event.detail == "Need the inbox"
    assert event.required_scopes == ["gmail.readonly"]

    pending["call-1"].set_result(
        {"type": "connection_response", "tool_id": "call-1", "result": "connected", "approved": True}
    )

    assert await task is True
    assert pending == {}


@pytest.mark.asyncio
async def test_request_connection_decline_returns_false():
    pending: dict[str, asyncio.Future] = {}

    async def emit(_event):
        return None

    execution = _make_execution(IOBridge(emit=emit, pending_connections=pending))
    task = asyncio.create_task(execution.request_connection(_descriptor(), source="recovery"))
    for _ in range(20):
        if pending:
            break
        await asyncio.sleep(0)
    pending["call-1"].set_result(
        {"type": "connection_response", "tool_id": "call-1", "result": "not now", "approved": False}
    )

    assert await task is False


@pytest.mark.asyncio
async def test_declined_connection_is_not_prompted_again_in_same_run():
    emitted = []
    pending: dict[str, asyncio.Future] = {}

    async def emit(event):
        emitted.append(event)

    first = _make_execution(IOBridge(emit=emit, pending_connections=pending), tool_id="call-1")
    task = asyncio.create_task(first.request_connection(_descriptor(), source="suggestion"))
    for _ in range(20):
        if pending:
            break
        await asyncio.sleep(0)
    pending["call-1"].set_result({"approved": False, "result": "not now"})
    assert await task is False

    second = ToolExecution(tool_id="call-2", tool_name="request_connection", ctx=first.ctx)
    assert await second.request_connection(_descriptor(), source="suggestion") is False
    assert len(emitted) == 1


@pytest.mark.asyncio
async def test_request_connection_fails_closed_without_interactive_client():
    execution = _make_execution(IOBridge())

    assert await execution.request_connection(_descriptor(), source="suggestion") is False


@pytest.mark.asyncio
async def test_request_connection_timeout_returns_false_and_clears_pending_state():
    async def emit(_event):
        return None

    pending: dict[str, asyncio.Future] = {}
    execution = _make_execution(
        IOBridge(
            emit=emit,
            pending_connections=pending,
            approval_timeout_seconds=0.001,
        )
    )

    accepted = await execution.request_connection(_descriptor(), source="recovery")

    assert accepted is False
    assert pending == {}


@pytest.mark.asyncio
async def test_approval_auto_mode_does_not_resolve_pending_connection():
    registry = RunRegistry()
    run = registry.create_run("session-1")
    future = asyncio.get_running_loop().create_future()
    run.pending_connections["call-1"] = future

    resolved = run.set_skip_approvals(True)

    assert resolved == 0
    assert not future.done()


def test_connection_event_serialization_contains_no_secret_fields():
    event = ConnectionNeededEvent(
        tool_id="call-1",
        integration_id="gmail",
        connection_id="google",
        label="Gmail",
        reason="auth_required",
        detail="Reconnect Gmail",
        capability="Search, read, and send email",
        action="oauth",
        settings_tab="integrations",
        required_scopes=["gmail.readonly"],
        source="recovery",
    )

    payload = json.loads(event.to_sse()["data"])

    assert payload["type"] == "connection_needed"
    assert payload["integration_id"] == "gmail"
    assert "token" not in json.dumps(payload).lower()
    assert "credential" not in json.dumps(payload).lower()


@pytest.mark.asyncio
async def test_connection_suspension_persists_and_resolves_separately_from_approvals(session_store):
    await session_store.record_integration_connection_requested(
        run_id="run-1",
        session_id="session-1",
        tool_call_id="call-1",
        descriptor=_descriptor(),
        source="suggestion",
        detail="Need the inbox",
        expires_at=None,
    )

    rows = await session_store.list_pending_integration_connections("session-1", run_id="run-1")

    assert len(rows) == 1
    assert rows[0]["kind"] == "integration_connection"
    assert rows[0]["payload"]["integration_id"] == "gmail"
    assert await session_store.list_pending_tool_approvals("session-1", run_id="run-1") == []

    assert await session_store.resolve_integration_connection(
        run_id="run-1",
        tool_call_id="call-1",
        status="approved",
        result_feedback="connected",
    )
    assert await session_store.list_pending_integration_connections("session-1", run_id="run-1") == []


class _ConnectionRegistry:
    def __init__(self, state: str):
        self.state = state

    def get_connection(self, _integration_id: str):
        return IntegrationConnectionDescriptor(**{**_descriptor().__dict__, "state": self.state})


def _connection_router_client(*, verified_state: str = "connected"):
    registry = RunRegistry()
    run = registry.create_run("session-1")
    loop = asyncio.new_event_loop()
    future = loop.create_future()
    run.pending_connections["call-1"] = future
    run.pending_connection_descriptors["call-1"] = _descriptor()

    class _Runtime:
        run_registry = registry
        session_service = None
        integrations = _ConnectionRegistry("auth_required")

        class _ConnectionService:
            async def verify_connection(inner_self, integration_id: str):
                descriptor = runtime.integrations.get_connection(integration_id)
                if descriptor.state != "connected":
                    raise IntegrationConnectionError(
                        integration_id=integration_id,
                        reason=descriptor.state,
                        detail="Gmail is not connected",
                    )

        connection_service = _ConnectionService()

        async def reload_config(self):
            self.integrations.state = verified_state

    runtime = _Runtime()
    app = FastAPI()
    app.include_router(chat_router)
    app.dependency_overrides[get_runtime] = lambda: runtime
    return app, run, future, loop, runtime


def test_connection_result_verifies_then_resolves_live_request():
    app, run, future, loop, _runtime = _connection_router_client()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/connections/result",
                json={"run_id": run.run_id, "tool_id": "call-1", "result": "connected", "approved": True},
            )
        assert response.status_code == 200
        assert future.result()["approved"] is True
    finally:
        loop.close()


def test_connection_result_keeps_request_pending_when_verification_fails():
    app, run, future, loop, _runtime = _connection_router_client(verified_state="auth_required")
    try:
        with TestClient(app) as client:
            response = client.post(
                "/connections/result",
                json={"run_id": run.run_id, "tool_id": "call-1", "result": "", "approved": True},
            )
        assert response.status_code == 409
        assert response.json()["detail"] == "Gmail is not connected"
        assert not future.done()
    finally:
        loop.close()
