import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

import arden.database as database
from arden.context.models import SessionState
from arden.context.store import SessionStore
from arden.events.sse import ConnectionNeededEvent
from arden.integrations.base import IntegrationConnectionDescriptor, IntegrationConnectionError
from arden.integrations.connection_request import IntegrationConnectionRequestEnvelope
from arden.server.routers.chat import _connection_descriptor_from_payload
from arden.server.routers.chat import router as chat_router
from arden.server.routers.session import _connection_snapshot
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


def _descriptor(*, account_ref: str | None = None) -> IntegrationConnectionDescriptor:
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
        tool_names=("email_search", "email_read", "email_send"),
        account_ref=account_ref,
    )


def _make_execution(io: IOBridge, tool_id: str = "call-1") -> ToolExecution:
    ctx = ToolContext(
        session_state=SessionState(session_id="session-1", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1"),
        io=io,
        background_tasks=BackgroundTaskRegistry(session_id="session-1"),
    )
    return ToolExecution(tool_id=tool_id, tool_name="connection_request", ctx=ctx)


async def _empty_connection_suspension(**_kwargs):
    return None


async def _persist_connection(**_kwargs):
    return None


def _durable_io(
    *,
    emit=None,
    pending_connections=None,
    approval_timeout_seconds=300,
) -> IOBridge:
    return IOBridge(
        emit=emit,
        pending_connections=pending_connections,
        record_connection=_persist_connection,
        resolve_connection=_persist_connection,
        get_suspension=_empty_connection_suspension,
        consume_suspension=_persist_connection,
        approval_timeout_seconds=approval_timeout_seconds,
    )


@pytest.mark.asyncio
async def test_request_connection_emits_event_and_waits_for_matching_resolution():
    emitted = []
    pending: dict[str, asyncio.Future] = {}

    async def emit(event):
        emitted.append(event)

    execution = _make_execution(_durable_io(emit=emit, pending_connections=pending))
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

    execution = _make_execution(_durable_io(emit=emit, pending_connections=pending))
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

    first = _make_execution(_durable_io(emit=emit, pending_connections=pending), tool_id="call-1")
    task = asyncio.create_task(first.request_connection(_descriptor(), source="suggestion"))
    for _ in range(20):
        if pending:
            break
        await asyncio.sleep(0)
    pending["call-1"].set_result({"approved": False, "result": "not now"})
    assert await task is False

    second = ToolExecution(tool_id="call-2", tool_name="connection_request", ctx=first.ctx)
    assert await second.request_connection(_descriptor(), source="suggestion") is False
    assert len(emitted) == 1


@pytest.mark.asyncio
async def test_request_connection_fails_closed_without_interactive_client():
    execution = _make_execution(_durable_io())

    assert await execution.request_connection(_descriptor(), source="suggestion") is False


@pytest.mark.asyncio
async def test_request_connection_timeout_returns_false_and_clears_pending_state():
    async def emit(_event):
        return None

    pending: dict[str, asyncio.Future] = {}
    execution = _make_execution(
        _durable_io(
            emit=emit,
            pending_connections=pending,
            approval_timeout_seconds=0.001,
        )
    )

    accepted = await execution.request_connection(_descriptor(), source="recovery")

    assert accepted is False
    assert pending == {}


@pytest.mark.asyncio
async def test_request_connection_requires_every_persistence_callback_before_prompting():
    emitted = []
    pending: dict[str, asyncio.Future] = {}

    async def emit(event):
        emitted.append(event)

    execution = _make_execution(
        IOBridge(
            emit=emit,
            pending_connections=pending,
            record_connection=_persist_connection,
            resolve_connection=_persist_connection,
            get_suspension=_empty_connection_suspension,
        )
    )

    with pytest.raises(RuntimeError, match="Connection consume persistence callback is not configured"):
        await execution.request_connection(_descriptor(), source="suggestion")

    assert emitted == []
    assert pending == {}


@pytest.mark.asyncio
async def test_request_connection_propagates_lookup_failure_without_prompting():
    emitted = []
    pending: dict[str, asyncio.Future] = {}

    async def emit(event):
        emitted.append(event)

    async def fail_lookup(**_kwargs):
        raise RuntimeError("lookup failed")

    execution = _make_execution(
        IOBridge(
            emit=emit,
            pending_connections=pending,
            record_connection=_persist_connection,
            resolve_connection=_persist_connection,
            get_suspension=fail_lookup,
            consume_suspension=_persist_connection,
        )
    )

    with pytest.raises(RuntimeError, match="lookup failed"):
        await execution.request_connection(_descriptor(), source="suggestion")

    assert emitted == []
    assert pending == {}


@pytest.mark.asyncio
async def test_request_connection_propagates_record_failure_before_prompting():
    emitted = []
    pending: dict[str, asyncio.Future] = {}

    async def emit(event):
        emitted.append(event)

    async def fail_record(**_kwargs):
        raise RuntimeError("record failed")

    execution = _make_execution(
        IOBridge(
            emit=emit,
            pending_connections=pending,
            record_connection=fail_record,
            resolve_connection=_persist_connection,
            get_suspension=_empty_connection_suspension,
            consume_suspension=_persist_connection,
        )
    )

    with pytest.raises(RuntimeError, match="record failed"):
        await execution.request_connection(_descriptor(), source="suggestion")

    assert emitted == []
    assert pending == {}


@pytest.mark.asyncio
async def test_request_connection_propagates_resolve_failure_and_clears_live_future():
    pending: dict[str, asyncio.Future] = {}
    consume_calls = 0

    async def emit(_event):
        return None

    async def fail_resolve(**_kwargs):
        raise RuntimeError("resolve failed")

    async def consume(**_kwargs):
        nonlocal consume_calls
        consume_calls += 1

    execution = _make_execution(
        IOBridge(
            emit=emit,
            pending_connections=pending,
            record_connection=_persist_connection,
            resolve_connection=fail_resolve,
            get_suspension=_empty_connection_suspension,
            consume_suspension=consume,
        )
    )
    task = asyncio.create_task(execution.request_connection(_descriptor(), source="suggestion"))
    for _ in range(20):
        if pending:
            break
        await asyncio.sleep(0)
    pending["call-1"].set_result({"approved": True, "result": "connected"})

    with pytest.raises(RuntimeError, match="resolve failed"):
        await task

    assert pending == {}
    assert consume_calls == 0


@pytest.mark.asyncio
async def test_connection_consume_failure_is_recovered_after_restart_without_reprompting():
    durable_state = {"suspension": None, "fail_consume": True}
    emitted = []

    async def emit(event):
        emitted.append(event)

    async def get_suspension(**_kwargs):
        return durable_state["suspension"]

    async def record_connection(**_kwargs):
        durable_state["suspension"] = {
            "kind": "integration_connection",
            "status": "pending",
            "resolution": None,
        }

    async def resolve_connection(*, status, result_feedback, **_kwargs):
        durable_state["suspension"] = {
            "kind": "integration_connection",
            "status": status,
            "resolution": {
                "approved": status == "approved",
                "result": result_feedback,
            },
        }

    async def consume_suspension(**_kwargs):
        if durable_state["fail_consume"]:
            durable_state["fail_consume"] = False
            raise RuntimeError("consume failed")
        durable_state["suspension"] = None

    first_pending: dict[str, asyncio.Future] = {}
    first = _make_execution(
        IOBridge(
            emit=emit,
            pending_connections=first_pending,
            record_connection=record_connection,
            resolve_connection=resolve_connection,
            get_suspension=get_suspension,
            consume_suspension=consume_suspension,
        )
    )
    first_task = asyncio.create_task(first.request_connection(_descriptor(), source="suggestion"))
    for _ in range(20):
        if first_pending:
            break
        await asyncio.sleep(0)
    first_pending["call-1"].set_result({"approved": True, "result": "connected"})

    with pytest.raises(RuntimeError, match="consume failed"):
        await first_task

    assert first_pending == {}
    assert durable_state["suspension"]["status"] == "approved"

    restarted_pending: dict[str, asyncio.Future] = {}
    restarted = _make_execution(
        IOBridge(
            emit=emit,
            pending_connections=restarted_pending,
            record_connection=record_connection,
            resolve_connection=resolve_connection,
            get_suspension=get_suspension,
            consume_suspension=consume_suspension,
        )
    )

    assert await restarted.request_connection(_descriptor(), source="suggestion") is True
    assert durable_state["suspension"] is None
    assert restarted_pending == {}
    assert len(emitted) == 1


@pytest.mark.asyncio
async def test_request_connection_cancellation_clears_live_future_without_resolving():
    pending: dict[str, asyncio.Future] = {}
    resolve_calls = 0

    async def emit(_event):
        return None

    async def resolve(**_kwargs):
        nonlocal resolve_calls
        resolve_calls += 1

    execution = _make_execution(
        IOBridge(
            emit=emit,
            pending_connections=pending,
            record_connection=_persist_connection,
            resolve_connection=resolve,
            get_suspension=_empty_connection_suspension,
            consume_suspension=_persist_connection,
        )
    )
    task = asyncio.create_task(execution.request_connection(_descriptor(), source="suggestion"))
    for _ in range(20):
        if pending:
            break
        await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert pending == {}
    assert resolve_calls == 0


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
        account_ref="me@example.test",
    )

    payload = json.loads(event.to_sse()["data"])

    assert payload["type"] == "connection_needed"
    assert payload["integration_id"] == "gmail"
    assert payload["account_ref"] == "me@example.test"
    assert "token" not in json.dumps(payload).lower()
    assert "credential" not in json.dumps(payload).lower()


@pytest.mark.asyncio
async def test_connection_suspension_persists_and_resolves_separately_from_approvals(session_store):
    await session_store.record_integration_connection_requested(
        run_id="run-1",
        session_id="session-1",
        tool_call_id="call-1",
        descriptor=_descriptor(account_ref="me@example.test"),
        source="suggestion",
        detail="Need the inbox",
        expires_at=None,
    )

    rows = await session_store.list_pending_integration_connections("session-1", run_id="run-1")

    assert len(rows) == 1
    assert rows[0]["kind"] == "integration_connection"
    assert rows[0]["payload"]["integration_id"] == "gmail"
    assert rows[0]["payload"]["account_ref"] == "me@example.test"
    assert await session_store.list_pending_tool_approvals("session-1", run_id="run-1") == []

    assert await session_store.resolve_integration_connection(
        run_id="run-1",
        tool_call_id="call-1",
        status="approved",
        result_feedback="connected",
    )
    assert await session_store.list_pending_integration_connections("session-1", run_id="run-1") == []


def test_connection_request_envelope_rejects_unknown_durable_fields():
    payload = IntegrationConnectionRequestEnvelope.from_descriptor(
        _descriptor(),
        source="recovery",
        detail="Reconnect Gmail",
    ).model_dump(mode="json")
    payload["legacy_provider_payload"] = {}

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _connection_descriptor_from_payload(payload)

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _connection_snapshot({"tool_call_id": "call-1", "payload": payload})


def test_connection_request_envelope_does_not_coerce_identity_fields():
    payload = IntegrationConnectionRequestEnvelope.from_descriptor(
        _descriptor(),
        source="recovery",
        detail="Reconnect Gmail",
    ).model_dump(mode="json")
    payload["integration_id"] = 42

    with pytest.raises(ValidationError, match="valid string"):
        _connection_descriptor_from_payload(payload)


class _ConnectionRegistry:
    def __init__(self, state: str):
        self.state = state

    def get_connection(self, _integration_id: str):
        return IntegrationConnectionDescriptor(**{**_descriptor().__dict__, "state": self.state})


def _connection_router_client(
    *,
    verified_state: str = "connected",
    expected_account_ref: str | None = None,
):
    registry = RunRegistry()
    run = registry.create_run("session-1")
    loop = asyncio.new_event_loop()
    future = loop.create_future()
    run.pending_connections["call-1"] = future
    run.pending_connection_descriptors["call-1"] = _descriptor(account_ref=expected_account_ref)

    class _Runtime:
        run_registry = registry
        session_service = None
        integrations = _ConnectionRegistry(verified_state)

        class _ConnectionService:
            async def verify_connection(inner_self, integration_id: str, *, account_ref: str | None = None):
                runtime.verified_account_ref = account_ref
                descriptor = runtime.integrations.get_connection(integration_id)
                if descriptor.state != "connected":
                    raise IntegrationConnectionError(
                        integration_id=integration_id,
                        reason=descriptor.state,
                        detail="Gmail is not connected",
                    )

        connection_service = _ConnectionService()
        verified_account_ref = None
        reload_calls = 0

        async def reload_config(self):
            self.reload_calls += 1

    runtime = _Runtime()
    app = FastAPI()
    app.include_router(chat_router)
    app.dependency_overrides[get_runtime] = lambda: runtime
    return app, run, future, loop, runtime


def test_connection_result_verifies_then_resolves_live_request():
    app, run, future, loop, runtime = _connection_router_client()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/connections/result",
                json={
                    "run_id": run.run_id,
                    "tool_id": "call-1",
                    "result": "connected",
                    "approved": True,
                    "account_ref": "me@example.test",
                },
            )
        assert response.status_code == 200
        assert future.result()["approved"] is True
        assert runtime.verified_account_ref == "me@example.test"
        assert runtime.reload_calls == 0
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
        assert response.status_code == 424
        assert response.json()["detail"] == "Gmail is not connected"
        assert not future.done()
    finally:
        loop.close()


def test_connection_result_rejects_a_different_recovery_account():
    app, run, future, loop, runtime = _connection_router_client(expected_account_ref="broken@example.test")
    try:
        with TestClient(app) as client:
            response = client.post(
                "/connections/result",
                json={
                    "run_id": run.run_id,
                    "tool_id": "call-1",
                    "result": "connected",
                    "approved": True,
                    "account_ref": "BROKEN@example.test",
                },
            )
        assert response.status_code == 424
        assert "broken@example.test" in response.json()["detail"]
        assert runtime.verified_account_ref is None
        assert not future.done()
    finally:
        loop.close()


def test_connection_result_verifies_the_expected_recovery_account():
    app, run, future, loop, runtime = _connection_router_client(expected_account_ref="me@example.test")
    try:
        with TestClient(app) as client:
            response = client.post(
                "/connections/result",
                json={
                    "run_id": run.run_id,
                    "tool_id": "call-1",
                    "result": "connected",
                    "approved": True,
                    "account_ref": "me@example.test",
                },
            )
        assert response.status_code == 200
        assert runtime.verified_account_ref == "me@example.test"
        assert future.done()
    finally:
        loop.close()
