import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

import arden.database as database
from arden.execution import (
    COMMAND_CANCEL_TOOL,
    COMMAND_EXECUTE_TOOL,
    ExecutorCommandLog,
    ExecutorDeviceStore,
    ExecutorGateway,
    InvocationConflictError,
    InvocationStatus,
    InvocationStore,
    LeaseStore,
    StaleLeaseError,
)
from arden.execution.results import DeviceResultEnvelope
from arden.server.app import app
from arden.settings import hash_api_key
from arden.skills.device_store import DeviceSkillStore
from arden.skills.registry import SkillMeta, SkillRegistry


@pytest_asyncio.fixture
async def gateway(tmp_path):
    conn = await database.connect(tmp_path / "sessions.db")
    devices = ExecutorDeviceStore(conn)
    await devices.init_schema()
    leases = LeaseStore(conn, ttl_seconds=60)
    await leases.init_schema()
    commands = ExecutorCommandLog(conn)
    await commands.init_schema()
    invocations = InvocationStore(conn)
    await invocations.init_schema()
    yield ExecutorGateway(devices, leases, commands, invocations)
    await conn.close()


async def _enrolled(gateway: ExecutorGateway):
    device, token = await gateway.devices.enroll(name="mac", capabilities=["filesystem", "shell"])
    return device, token


async def _client_invocation(gateway: ExecutorGateway, invocation_id: str = "inv-1"):
    return await gateway.invocations.create(
        invocation_id=invocation_id,
        run_id="run-1",
        session_id="sess-1",
        tool_call_id="call-1",
        tool_name="file_read",
        placement="client",
        arguments_json='{"path": "/tmp/x"}',
    )


# -- devices --


@pytest.mark.asyncio
async def test_enroll_and_authenticate(gateway):
    device, token = await _enrolled(gateway)
    assert (await gateway.devices.authenticate(token)) == device
    assert await gateway.devices.authenticate("wrong-token") is None


@pytest.mark.asyncio
async def test_revoked_device_no_longer_authenticates(gateway):
    device, token = await _enrolled(gateway)
    await gateway.devices.revoke(device.executor_id)
    assert await gateway.devices.authenticate(token) is None


# -- happy path --


@pytest.mark.asyncio
async def test_dispatch_execute_result_roundtrip(gateway):
    device, _ = await _enrolled(gateway)
    lease = await gateway.connect(device)
    invocation = await _client_invocation(gateway)

    await gateway.dispatch(device.executor_id, invocation)
    commands = await gateway.pending_commands(device.executor_id, 0)
    assert len(commands) == 1
    assert commands[0].command_type == COMMAND_EXECUTE_TOOL
    assert commands[0].payload["tool_name"] == "file_read"
    assert commands[0].payload["arguments"] == {"path": "/tmp/x"}

    await gateway.accept_started(device, lease.lease_id, "inv-1")
    waiter = gateway.waiter("inv-1")
    record = await gateway.accept_result(
        device,
        lease.lease_id,
        invocation_id="inv-1",
        status=InvocationStatus.SUCCEEDED,
        result=DeviceResultEnvelope(content="file body", preview="Read"),
    )
    assert record.status == InvocationStatus.SUCCEEDED
    assert (await asyncio.wait_for(waiter, 1)).invocation_id == "inv-1"


# -- replay / cursor --


@pytest.mark.asyncio
async def test_cursor_replay_after_reconnect(gateway):
    device, _ = await _enrolled(gateway)
    await gateway.connect(device)
    first = await gateway.dispatch(device.executor_id, await _client_invocation(gateway, "inv-1"))
    second = await gateway.dispatch(
        device.executor_id,
        await gateway.invocations.create(
            invocation_id="inv-2",
            run_id="run-1",
            session_id="sess-1",
            tool_call_id="call-2",
            tool_name="file_read",
            placement="client",
            arguments_json="{}",
        ),
    )

    replayed = await gateway.pending_commands(device.executor_id, first.seq)
    assert [c.seq for c in replayed] == [second.seq]

    lease = await gateway.connect(device)
    await gateway.heartbeat(device, lease.lease_id, acked_seq=second.seq)
    assert await gateway.pending_commands(device.executor_id, 0) == []


# -- duplicate delivery / idempotency --


@pytest.mark.asyncio
async def test_duplicate_result_submission_is_accepted(gateway):
    device, _ = await _enrolled(gateway)
    lease = await gateway.connect(device)
    await _client_invocation(gateway)
    await gateway.accept_started(device, lease.lease_id, "inv-1")

    for _ in range(2):
        record = await gateway.accept_result(
            device,
            lease.lease_id,
            invocation_id="inv-1",
            status=InvocationStatus.SUCCEEDED,
            result=DeviceResultEnvelope(content="ok", preview="Done"),
        )
        assert record.status == InvocationStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_conflicting_result_submission_is_rejected(gateway):
    device, _ = await _enrolled(gateway)
    lease = await gateway.connect(device)
    await _client_invocation(gateway)
    await gateway.accept_result(
        device,
        lease.lease_id,
        invocation_id="inv-1",
        status=InvocationStatus.SUCCEEDED,
        result=DeviceResultEnvelope(content="ok", preview="Done"),
    )

    with pytest.raises(InvocationConflictError):
        await gateway.accept_result(
            device,
            lease.lease_id,
            invocation_id="inv-1",
            status=InvocationStatus.FAILED,
            result=DeviceResultEnvelope(content="boom", preview="Failed"),
            error_code="tool_error",
        )


# -- lease fencing --


@pytest.mark.asyncio
async def test_stale_lease_cannot_submit_results(gateway):
    device, _ = await _enrolled(gateway)
    old_lease = await gateway.connect(device)
    await gateway.connect(device)  # reconnect supersedes the first lease
    await _client_invocation(gateway)

    with pytest.raises(StaleLeaseError):
        await gateway.accept_result(
            device,
            old_lease.lease_id,
            invocation_id="inv-1",
            status=InvocationStatus.SUCCEEDED,
            result=DeviceResultEnvelope(content="ok", preview="Done"),
        )


@pytest.mark.asyncio
async def test_heartbeat_rejects_stale_lease(gateway):
    device, _ = await _enrolled(gateway)
    old_lease = await gateway.connect(device)
    await gateway.connect(device)

    with pytest.raises(StaleLeaseError):
        await gateway.heartbeat(device, old_lease.lease_id)


@pytest.mark.asyncio
async def test_lease_from_another_device_is_stale(gateway):
    device_a, _ = await _enrolled(gateway)
    device_b, _ = await gateway.devices.enroll(name="other", capabilities=[])
    lease_a = await gateway.connect(device_a)
    await _client_invocation(gateway)

    with pytest.raises(StaleLeaseError):
        await gateway.accept_result(
            device_b,
            lease_a.lease_id,
            invocation_id="inv-1",
            status=InvocationStatus.SUCCEEDED,
            result=DeviceResultEnvelope(content="ok", preview="Done"),
        )


# -- cancellation --


@pytest.mark.asyncio
async def test_cancel_flags_invocation_and_appends_command(gateway):
    device, _ = await _enrolled(gateway)
    lease = await gateway.connect(device)
    invocation = await _client_invocation(gateway)
    await gateway.dispatch(device.executor_id, invocation)
    await gateway.accept_started(device, lease.lease_id, "inv-1")

    await gateway.cancel(device.executor_id, "inv-1")
    record = await gateway.invocations.get("inv-1")
    assert record is not None and record.cancel_requested

    commands = await gateway.pending_commands(device.executor_id, 0)
    assert commands[-1].command_type == COMMAND_CANCEL_TOOL

    done = await gateway.accept_result(
        device,
        lease.lease_id,
        invocation_id="inv-1",
        status=InvocationStatus.UNCERTAIN,
        result=DeviceResultEnvelope(content="interrupted", preview="Interrupted"),
        error_code="cancelled",
    )
    assert done.status == InvocationStatus.UNCERTAIN


# -- HTTP surface --


class _Config:
    api_key_hash = hash_api_key("test-key")


class _Runtime:
    connected = True

    def __init__(self, gateway, device_skills=None):
        self.config = _Config()
        self.executor_gateway = gateway
        self.stores = SimpleNamespace(device_skills=device_skills) if device_skills else None
        self.skill_registry = SkillRegistry()

    async def hydrate_device_skills(self):
        entries = []
        for skill in await self.stores.device_skills.all_loaded():
            meta = SkillMeta(name=skill.name, description=skill.description, path=Path(skill.name), location="device")
            entries.append((meta, skill.body or ""))
        self.skill_registry.set_device_skills(entries)


@pytest.fixture
def http(tmp_path):
    async def build():
        conn = await database.connect(tmp_path / "sessions.db")
        devices = ExecutorDeviceStore(conn)
        await devices.init_schema()
        leases = LeaseStore(conn, ttl_seconds=60)
        await leases.init_schema()
        commands = ExecutorCommandLog(conn)
        await commands.init_schema()
        invocations = InvocationStore(conn)
        await invocations.init_schema()
        device_skills = DeviceSkillStore(conn)
        await device_skills.init_schema()
        return conn, ExecutorGateway(devices, leases, commands, invocations), device_skills

    conn, http_gateway, device_skills = asyncio.run(build())
    had_runtime = hasattr(app.state, "runtime")
    previous = getattr(app.state, "runtime", None)
    app.state.runtime = _Runtime(http_gateway, device_skills)
    client = TestClient(app)
    yield client, http_gateway
    if had_runtime:
        app.state.runtime = previous
    else:
        delattr(app.state, "runtime")
    asyncio.run(conn.close())


def test_enroll_requires_api_key(http):
    client, _ = http
    response = client.post("/executor/enroll", json={"name": "mac"})
    assert response.status_code == 401

    response = client.post(
        "/executor/enroll",
        json={"name": "mac", "capabilities": ["filesystem"]},
        headers={"Authorization": "Bearer test-key"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["executor_id"].startswith("exec_")
    assert body["token"]


def test_executor_endpoints_reject_bad_device_token(http):
    client, _ = http
    response = client.post(
        "/executor/heartbeat",
        json={"lease_id": "lease_x"},
        headers={"Authorization": "Bearer not-a-device-token"},
    )
    assert response.status_code == 401


def test_result_flow_over_http(http):
    client, http_gateway = http
    enroll = client.post(
        "/executor/enroll",
        json={"name": "mac", "capabilities": ["filesystem"]},
        headers={"Authorization": "Bearer test-key"},
    )
    token = enroll.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # TestClient cannot consume an infinite SSE stream without hanging its
    # portal; the live stream is exercised by the executor pilot tests.
    device = asyncio.run(http_gateway.devices.authenticate(token))
    assert device is not None
    lease_id = asyncio.run(http_gateway.connect(device)).lease_id

    asyncio.run(
        http_gateway.invocations.create(
            invocation_id="inv-http",
            run_id="run-1",
            session_id="sess-1",
            tool_call_id="call-1",
            tool_name="file_read",
            placement="client",
            arguments_json="{}",
        )
    )

    started = client.post(
        "/executor/started",
        json={"lease_id": lease_id, "invocation_id": "inv-http"},
        headers=headers,
    )
    assert started.status_code == 200

    result = client.post(
        "/executor/results",
        json={
            "lease_id": lease_id,
            "invocation_id": "inv-http",
            "status": "succeeded",
            "result": {"content": "ok", "preview": "Done"},
        },
        headers=headers,
    )
    assert result.status_code == 200
    assert result.json()["status"] == "succeeded"

    duplicate = client.post(
        "/executor/results",
        json={
            "lease_id": lease_id,
            "invocation_id": "inv-http",
            "status": "succeeded",
            "result": {"content": "ok", "preview": "Done"},
        },
        headers=headers,
    )
    assert duplicate.status_code == 200

    conflict = client.post(
        "/executor/results",
        json={
            "lease_id": lease_id,
            "invocation_id": "inv-http",
            "status": "failed",
            "result": {"content": "boom", "preview": "Failed"},
            "error_code": "tool_error",
        },
        headers=headers,
    )
    assert conflict.status_code == 409


# -- reconnect race / revocation kill switch --


@pytest.mark.asyncio
async def test_stale_stream_teardown_does_not_unmark_live_connection(gateway):
    device, _ = await _enrolled(gateway)
    old_lease = await gateway.connect(device)
    new_lease = await gateway.connect(device)  # fast reconnect supersedes

    # The old stream's late finally block must be a no-op.
    gateway.disconnect(device.executor_id, old_lease.lease_id)
    assert gateway.is_connected(device.executor_id)
    assert gateway.stream_owner(device.executor_id) == new_lease.lease_id

    gateway.disconnect(device.executor_id, new_lease.lease_id)
    assert not gateway.is_connected(device.executor_id)


@pytest.mark.asyncio
async def test_revoke_fences_lease_and_terminates_stream_ownership(gateway):
    device, token = await _enrolled(gateway)
    lease = await gateway.connect(device)
    await _client_invocation(gateway)
    await gateway.accept_started(device, lease.lease_id, "inv-1")

    await gateway.revoke(device.executor_id)

    assert not gateway.is_connected(device.executor_id)
    assert gateway.stream_owner(device.executor_id) is None
    assert await gateway.devices.authenticate(token) is None
    with pytest.raises(StaleLeaseError):
        await gateway.accept_result(
            device,
            lease.lease_id,
            invocation_id="inv-1",
            status=InvocationStatus.SUCCEEDED,
            result=DeviceResultEnvelope(content="ok", preview="Done"),
        )
    with pytest.raises(StaleLeaseError):
        await gateway.heartbeat(device, lease.lease_id)


def test_skill_advertisement_flow_over_http(http):
    client, http_gateway = http
    enroll = client.post(
        "/executor/enroll",
        json={"name": "mac", "capabilities": []},
        headers={"Authorization": "Bearer test-key"},
    )
    token = enroll.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    device = asyncio.run(http_gateway.devices.authenticate(token))
    lease_id = asyncio.run(http_gateway.connect(device)).lease_id

    skill_md = "---\nname: mac-notes\ndescription: Search local notes\n---\n\n# Steps\n"
    advertised = client.post(
        "/executor/skills",
        json={
            "lease_id": lease_id,
            "skills": [{"name": "mac-notes", "description": "Search local notes", "sha256": "abc"}],
        },
        headers=headers,
    )
    assert advertised.status_code == 200
    assert advertised.json()["need"] == ["mac-notes"]

    stored = client.post(
        "/executor/skills/bodies",
        json={"lease_id": lease_id, "skills": [{"name": "mac-notes", "sha256": "abc", "body": skill_md}]},
        headers=headers,
    )
    assert stored.status_code == 200
    assert stored.json()["stored"] == ["mac-notes"]

    registry = app.state.runtime.skill_registry
    assert registry.get("mac-notes") is not None
    assert registry.load_body("mac-notes") == "# Steps"

    # Second advertisement with the same sha needs nothing; removal propagates.
    again = client.post(
        "/executor/skills",
        json={"lease_id": lease_id, "skills": [{"name": "mac-notes", "description": "x", "sha256": "abc"}]},
        headers=headers,
    )
    assert again.json()["need"] == []
    cleared = client.post(
        "/executor/skills",
        json={"lease_id": lease_id, "skills": []},
        headers=headers,
    )
    assert cleared.status_code == 200
    assert app.state.runtime.skill_registry.get("mac-notes") is None


def test_skill_advertisement_rejects_stale_lease(http):
    client, http_gateway = http
    enroll = client.post(
        "/executor/enroll",
        json={"name": "mac", "capabilities": []},
        headers={"Authorization": "Bearer test-key"},
    )
    token = enroll.json()["token"]
    device = asyncio.run(http_gateway.devices.authenticate(token))
    old_lease = asyncio.run(http_gateway.connect(device)).lease_id
    asyncio.run(http_gateway.connect(device))  # supersede

    response = client.post(
        "/executor/skills",
        json={"lease_id": old_lease, "skills": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


# -- restart reconciliation --


@pytest.mark.asyncio
async def test_reconcile_settles_orphaned_invocations(gateway):
    device, _token = await _enrolled(gateway)
    requested = await _client_invocation(gateway, "inv-requested")
    running = await _client_invocation(gateway, "inv-running")
    await gateway.invocations.mark_running(running.invocation_id)

    reconciled = await gateway.reconcile_open_invocations()

    assert reconciled == 2
    settled_requested = await gateway.invocations.get(requested.invocation_id)
    settled_running = await gateway.invocations.get(running.invocation_id)
    # Never picked up -> cancelled; possibly executed on the device -> uncertain.
    assert settled_requested.status == InvocationStatus.CANCELLED
    assert settled_running.status == InvocationStatus.UNCERTAIN
    assert settled_requested.cancel_requested is True
    assert settled_running.cancel_requested is True
    assert settled_requested.error_code == "server_restart"
    # A reconnecting device replaying stale execute_tool commands finds the
    # abort instruction in the same log.
    commands = await gateway.pending_commands(device.executor_id, 0)
    cancel_targets = {c.invocation_id for c in commands if c.command_type == COMMAND_CANCEL_TOOL}
    assert cancel_targets == {"inv-requested", "inv-running"}


@pytest.mark.asyncio
async def test_reconcile_preserves_durable_child_cancel_cause(gateway):
    await gateway.invocations.record_session_cancellation(
        session_id="sess-1",
        actor="user",
        cause="user_cancelled",
        idempotency_key="cancel-1",
    )
    invocation = await _client_invocation(gateway, "inv-cancelled-child")

    await gateway.reconcile_open_invocations()

    settled = await gateway.invocations.get(invocation.invocation_id)
    assert settled is not None
    assert settled.status == InvocationStatus.CANCELLED
    assert settled.error_code == "user_cancelled"


@pytest.mark.asyncio
async def test_reconcile_with_nothing_open_is_a_no_op(gateway):
    assert await gateway.reconcile_open_invocations() == 0


@pytest.mark.asyncio
async def test_late_result_for_reconciled_invocation_conflicts(gateway):
    device, _token = await _enrolled(gateway)
    lease = await gateway.connect(device)
    running = await _client_invocation(gateway, "inv-late")
    await gateway.invocations.mark_running(running.invocation_id)
    await gateway.reconcile_open_invocations()

    with pytest.raises(InvocationConflictError):
        await gateway.accept_result(
            device,
            lease.lease_id,
            invocation_id="inv-late",
            status=InvocationStatus.SUCCEEDED,
            result=DeviceResultEnvelope(content="too late", preview="Done"),
        )
