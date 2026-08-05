import asyncio
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from pydantic import BaseModel, Field

import arden.database as database
from arden.context.models import SessionState
from arden.execution import (
    COMMAND_CANCEL_TOOL,
    ExecutorCommandLog,
    ExecutorDeviceStore,
    ExecutorGateway,
    InvocationStatus,
    InvocationStore,
    LeaseStore,
)
from arden.execution.backend import ClientExecutionBackend
from arden.execution.results import DeviceResultEnvelope
from arden.tools.core.base import ToolResult
from arden.tools.core.background_tasks import BackgroundTaskRegistry
from arden.tools.core.context import IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.execution import ExecutionRouter, ToolInvocation
from arden.tools.core.function import tool
from arden.tools.core.registry import ToolRegistry
from arden.tools.core.types import ToolAction, ToolPlacement, ToolPolicy, ToolScope


class _Input(BaseModel):
    path: str = Field(default="/tmp/x")


async def _server_side_refusal(execution, args):
    return ToolResult.failure(code="no_client_execution", message="refused", preview="No device executor")


def _client_tool():
    return tool(
        description="read a device file",
        input_model=_Input,
        policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, placement=ToolPlacement.CLIENT),
        execute=_server_side_refusal,
    )


def _make_execution(registry: ToolRegistry, tool_call_id: str = "call-1") -> ToolExecution:
    ctx = ToolContext(
        session_state=SessionState(session_id="sess-1", started_at=datetime.now(UTC)),
        registry=registry,
        run=RunContext(run_id="run-1", current_depth=0, max_depth=3),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="sess-1"),
    )
    return ToolExecution(tool_id=tool_call_id, tool_name="read_device_file", ctx=ctx)


@pytest_asyncio.fixture
async def rig(tmp_path):
    conn = await database.connect(tmp_path / "sessions.db")
    devices = ExecutorDeviceStore(conn)
    await devices.init_schema()
    leases = LeaseStore(conn, ttl_seconds=60)
    await leases.init_schema()
    commands = ExecutorCommandLog(conn)
    await commands.init_schema()
    invocations = InvocationStore(conn)
    await invocations.init_schema()
    gateway = ExecutorGateway(devices, leases, commands, invocations)
    registry = ToolRegistry()
    registry.register("read_device_file", _client_tool(), source="_system")
    router = ExecutionRouter(registry, client_backend=ClientExecutionBackend(gateway, invocations))
    yield gateway, router, registry
    await conn.close()


@pytest.mark.asyncio
async def test_no_executor_connected_fails_cleanly(rig):
    _gateway, router, registry = rig
    result = await router.execute(
        ToolInvocation(invocation_id="call-1", tool_name="read_device_file", arguments={"path": "/tmp/x"}),
        _make_execution(registry),
    )
    assert result.is_error
    assert result.outcome.error.code == "no_executor_connected"
    assert result.outcome.error.retryable


@pytest.mark.asyncio
async def test_client_tool_routes_to_device_and_returns_result(rig):
    gateway, router, registry = rig
    device, _ = await gateway.devices.enroll(name="mac", capabilities=["filesystem"])
    lease = await gateway.connect(device)

    async def fake_executor():
        # Wait until the command shows up, execute "the tool", post the result.
        for _ in range(100):
            commands = await gateway.pending_commands(device.executor_id, 0)
            if commands:
                command = commands[0]
                assert command.payload.arguments == {"path": "/etc/hosts"}
                await gateway.accept_started(device, lease.lease_id, command.invocation_id)
                await gateway.accept_result(
                    device,
                    lease.lease_id,
                    invocation_id=command.invocation_id,
                    status=InvocationStatus.SUCCEEDED,
                    result=DeviceResultEnvelope(content="127.0.0.1 localhost", preview="Read hosts"),
                )
                return
            await asyncio.sleep(0.01)
        raise AssertionError("command never dispatched")

    task = asyncio.create_task(fake_executor())
    result = await router.execute(
        ToolInvocation(invocation_id="call-1", tool_name="read_device_file", arguments={"path": "/etc/hosts"}),
        _make_execution(registry),
    )
    await task
    assert not result.is_error
    assert result.content == "127.0.0.1 localhost"
    assert result.preview == "Read hosts"


@pytest.mark.asyncio
async def test_oversized_client_arguments_fail_before_device_dispatch(rig):
    gateway, router, registry = rig
    device, _ = await gateway.devices.enroll(name="mac", capabilities=[])
    await gateway.connect(device)

    result = await router.execute(
        ToolInvocation(
            invocation_id="call-too-large",
            tool_name="read_device_file",
            arguments={"payload": "x" * (1_048_577)},
        ),
        _make_execution(registry),
    )

    assert result.is_error
    assert result.outcome.error.code == "invalid_executor_command"
    assert await gateway.pending_commands(device.executor_id, 0) == []


@pytest.mark.asyncio
async def test_client_file_discovery_updates_are_carried_to_the_next_device_call(rig):
    gateway, router, registry = rig
    device, _ = await gateway.devices.enroll(name="mac", capabilities=["filesystem"])
    lease = await gateway.connect(device)
    execution = _make_execution(registry)

    async def settle_first_call():
        for _ in range(100):
            commands = await gateway.pending_commands(device.executor_id, 0)
            if commands:
                await gateway.accept_result(
                    device,
                    lease.lease_id,
                    invocation_id=commands[0].invocation_id,
                    status=InvocationStatus.FAILED,
                    result=DeviceResultEnvelope(
                        content="missing",
                        preview="Not found",
                        file_discovery={
                            "version": 1,
                            "observed": ({"path": "/workspace", "kind": "directory"},),
                            "discovered_roots": (),
                            "missed_roots": ("/workspace",),
                        },
                    ),
                    error_code="not_found",
                )
                return
            await asyncio.sleep(0.01)
        raise AssertionError("command never dispatched")

    first = asyncio.create_task(settle_first_call())
    await router.execute(
        ToolInvocation(invocation_id="call-1", tool_name="read_device_file", arguments={"path": "/workspace/nope"}),
        execution,
    )
    await first
    assert execution.ctx.run.file_discovery_state() == {
        "observed_paths": {"/workspace": "directory"},
        "miss_counts": {"/workspace": 1},
    }

    async def settle_second_call():
        for _ in range(100):
            commands = await gateway.pending_commands(device.executor_id, 1)
            if commands:
                assert commands[0].payload.context.file_discovery.model_dump() == {
                    "observed_paths": {"/workspace": "directory"},
                    "miss_counts": {"/workspace": 1},
                }
                await gateway.accept_result(
                    device,
                    lease.lease_id,
                    invocation_id=commands[0].invocation_id,
                    status=InvocationStatus.SUCCEEDED,
                    result=DeviceResultEnvelope(content="ok", preview="Read"),
                )
                return
            await asyncio.sleep(0.01)
        raise AssertionError("second command never dispatched")

    second = asyncio.create_task(settle_second_call())
    result = await router.execute(
        ToolInvocation(invocation_id="call-2", tool_name="read_device_file", arguments={"path": "/workspace"}),
        execution,
    )
    await second
    assert result.content == "ok"


@pytest.mark.asyncio
async def test_failed_device_result_maps_to_tool_failure(rig):
    gateway, router, registry = rig
    device, _ = await gateway.devices.enroll(name="mac", capabilities=[])
    lease = await gateway.connect(device)

    async def fake_executor():
        for _ in range(100):
            commands = await gateway.pending_commands(device.executor_id, 0)
            if commands:
                await gateway.accept_result(
                    device,
                    lease.lease_id,
                    invocation_id=commands[0].invocation_id,
                    status=InvocationStatus.FAILED,
                    result=DeviceResultEnvelope(content="Could not read /nope", preview="Read failed"),
                    error_code="not_found",
                )
                return
            await asyncio.sleep(0.01)

    task = asyncio.create_task(fake_executor())
    result = await router.execute(
        ToolInvocation(invocation_id="call-1", tool_name="read_device_file", arguments={"path": "/nope"}),
        _make_execution(registry),
    )
    await task
    assert result.is_error
    assert result.outcome.error.code == "not_found"
    assert "Could not read /nope" in result.content


@pytest.mark.asyncio
async def test_replayed_settled_invocation_returns_recorded_result_without_rerun(rig):
    gateway, router, registry = rig
    device, _ = await gateway.devices.enroll(name="mac", capabilities=[])
    await gateway.connect(device)

    backend = router.client_backend
    await backend._invocations.create(
        invocation_id="call-1",
        run_id="run-1",
        session_id="sess-1",
        tool_call_id="call-1",
        tool_name="read_device_file",
        placement=ToolPlacement.CLIENT,
        arguments_json="{}",
    )
    await backend._invocations.complete(
        "call-1",
        status=InvocationStatus.SUCCEEDED,
        result_payload=DeviceResultEnvelope(content="cached body", preview="Read").serialized(),
    )

    result = await router.execute(
        ToolInvocation(invocation_id="call-1", tool_name="read_device_file", arguments={}),
        _make_execution(registry),
    )
    assert result.content == "cached body"
    assert await gateway.pending_commands(device.executor_id, 0) == []


@pytest.mark.asyncio
async def test_router_without_client_backend_falls_through_to_refusing_handler(rig):
    _, _, registry = rig
    router = ExecutionRouter(registry)
    result = await router.execute(
        ToolInvocation(invocation_id="call-1", tool_name="read_device_file", arguments={"path": "/x"}),
        _make_execution(registry),
    )
    assert result.is_error
    assert result.outcome.error.code == "no_client_execution"


@pytest.mark.asyncio
async def test_upstream_cancellation_drops_the_waiter(rig):
    gateway, router, registry = rig
    device, _ = await gateway.devices.enroll(name="mac", capabilities=[])
    await gateway.connect(device)

    task = asyncio.create_task(
        router.execute(
            ToolInvocation(invocation_id="call-1", tool_name="read_device_file", arguments={}),
            _make_execution(registry),
        )
    )
    for _ in range(100):
        if await gateway.pending_commands(device.executor_id, 0):
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "call-1" not in gateway._waiters


def _schema_names(schemas):
    return {schema["function"]["name"] for schema in schemas}


@pytest.mark.asyncio
async def test_client_tools_hidden_without_connected_executor(rig):
    from arden.tools.executor import ToolExecutor

    gateway, router, registry = rig
    executor = ToolExecutor().with_registry(registry)
    executor.router = router

    # Backend wired but no executor connected: hidden.
    assert "read_device_file" not in _schema_names(executor.get_tools())

    device, _ = await gateway.devices.enroll(name="mac", capabilities=[])
    await gateway.connect(device)
    assert "read_device_file" in _schema_names(executor.get_tools())

    gateway.disconnect(device.executor_id)
    assert "read_device_file" not in _schema_names(executor.get_tools())


def test_client_tools_hidden_without_client_backend(rig):
    from arden.tools.executor import ToolExecutor

    _gateway, _router, registry = rig
    executor = ToolExecutor().with_registry(registry)
    assert "read_device_file" not in _schema_names(executor.get_tools())


@pytest.mark.asyncio
async def test_upstream_cancellation_requests_device_abort(rig):
    gateway, router, registry = rig
    device, _ = await gateway.devices.enroll(name="mac", capabilities=[])
    await gateway.connect(device)

    task = asyncio.create_task(
        router.execute(
            ToolInvocation(invocation_id="call-1", tool_name="read_device_file", arguments={}),
            _make_execution(registry),
        )
    )
    for _ in range(100):
        if await gateway.pending_commands(device.executor_id, 0):
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The caller gave up — the device must be told to abort, not left running.
    record = await gateway.invocations.get("call-1")
    assert record.cancel_requested is True
    commands = await gateway.pending_commands(device.executor_id, 0)
    assert any(c.command_type == COMMAND_CANCEL_TOOL and c.invocation_id == "call-1" for c in commands)


@pytest.mark.asyncio
async def test_caller_timeout_becomes_a_durable_deadline(rig):
    gateway, router, registry = rig
    device, _ = await gateway.devices.enroll(name="mac", capabilities=[])
    await gateway.connect(device)

    task = asyncio.create_task(
        router.execute(
            ToolInvocation(
                invocation_id="call-1",
                tool_name="read_device_file",
                arguments={},
                timeout_seconds=120,
            ),
            _make_execution(registry),
        )
    )
    try:
        for _ in range(100):
            if await gateway.pending_commands(device.executor_id, 0):
                break
            await asyncio.sleep(0.01)
        record = await gateway.invocations.get("call-1")
        assert record.deadline_at is not None
        remaining = (record.deadline_at - datetime.now(UTC)).total_seconds()
        assert 100 < remaining <= 120
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
