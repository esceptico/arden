import asyncio
import json

from arden.execution.command_envelope import CancelToolCommand, ExecuteToolCommand, ExecutorToolContext
from arden.execution.commands import ExecutorCommand, ExecutorCommandLog
from arden.execution.devices import ExecutorDevice, ExecutorDeviceStore
from arden.execution.leases import ExecutorLease, LeaseStore
from arden.execution.models import InvocationRecord, InvocationStatus
from arden.execution.results import DeviceResultEnvelope, validate_device_result
from arden.execution.store import InvocationStore
from arden.logging import get_logger

_logger = get_logger(__name__)

COMMAND_EXECUTE_TOOL = "execute_tool"
COMMAND_CANCEL_TOOL = "cancel_tool"


class StaleLeaseError(Exception):
    """The submitting executor no longer holds the current lease."""


class ExecutorGateway:
    """Server side of the executor protocol.

    Owns dispatch (durable command log + live wakeup), result acceptance
    (lease fencing + idempotent completion), and in-process waiters that let
    an execution backend await a terminal invocation.
    """

    def __init__(
        self,
        devices: ExecutorDeviceStore,
        leases: LeaseStore,
        commands: ExecutorCommandLog,
        invocations: InvocationStore,
    ):
        self.devices = devices
        self.leases = leases
        self.commands = commands
        self.invocations = invocations
        self._wakeups: dict[str, asyncio.Event] = {}
        self._waiters: dict[str, asyncio.Future[InvocationRecord]] = {}
        # executor_id -> lease_id of the stream that owns the connection.
        self._connected: dict[str, str] = {}

    # -- connection lifecycle --

    async def connect(self, device: ExecutorDevice) -> ExecutorLease:
        lease = await self.leases.acquire(device.executor_id)
        await self.devices.touch(device.executor_id)
        self._connected[device.executor_id] = lease.lease_id
        return lease

    def disconnect(self, executor_id: str, lease_id: str | None = None) -> None:
        """Drop a connection. With a lease_id, only the stream that still owns
        the connection may drop it — a superseded stream's late teardown must
        not un-mark the live one (reconnect race)."""
        if lease_id is not None and self._connected.get(executor_id) != lease_id:
            return
        self._connected.pop(executor_id, None)

    def is_connected(self, executor_id: str) -> bool:
        return executor_id in self._connected

    def stream_owner(self, executor_id: str) -> str | None:
        return self._connected.get(executor_id)

    def connected_executor(self) -> str | None:
        return next(iter(self._connected), None)

    async def revoke(self, executor_id: str) -> None:
        """Kill switch: revoke the credential, fence every lease, drop the
        connection, and wake its stream so it terminates immediately."""
        await self.devices.revoke(executor_id)
        await self.leases.release_all(executor_id)
        self._connected.pop(executor_id, None)
        self._wakeup(executor_id).set()

    async def heartbeat(self, device: ExecutorDevice, lease_id: str, *, acked_seq: int | None = None) -> ExecutorLease:
        lease = await self.leases.renew(lease_id)
        if lease is None or lease.executor_id != device.executor_id:
            raise StaleLeaseError(lease_id)
        await self.devices.touch(device.executor_id)
        if acked_seq is not None:
            await self.commands.ack(device.executor_id, acked_seq)
        return lease

    async def reconcile_open_invocations(self) -> int:
        """Settle invocations orphaned by a server restart.

        Their waiters were in-process futures and died with the process, so no
        outcome can ever be consumed: a `requested` invocation was never picked
        up and becomes `cancelled`; a `running` one may or may not have executed
        on the device and becomes `uncertain`. Each also gets a cancel_tool
        command to every enrolled device, so a reconnecting executor aborts the
        stale work (or refuses to start it — accept_started reports the
        cancel_requested flag) instead of running side effects nobody awaits."""
        open_records = await self.invocations.open_invocations()
        if not open_records:
            return 0
        devices = [d for d in await self.devices.list_devices() if d.revoked_at is None]
        for record in open_records:
            cause = await self.invocations.cancellation_cause_for_session(record.session_id) or "server_restart"
            await self.invocations.request_cancel(record.invocation_id)
            for device in devices:
                await self.commands.append(
                    device.executor_id,
                    CancelToolCommand(version=1, type=COMMAND_CANCEL_TOOL, invocation_id=record.invocation_id),
                )
            status = (
                InvocationStatus.CANCELLED
                if record.status == InvocationStatus.REQUESTED
                else InvocationStatus.UNCERTAIN
            )
            await self.invocations.complete(
                record.invocation_id,
                status=status,
                result_payload=DeviceResultEnvelope(
                    content=f"Device execution was orphaned after {cause}.",
                    preview="Device execution interrupted",
                ).serialized(),
                error_code=cause,
            )
        _logger.info("Reconciled %d orphaned tool invocation(s) after restart", len(open_records))
        return len(open_records)

    # -- dispatch --

    async def dispatch(
        self,
        executor_id: str,
        invocation: InvocationRecord,
        *,
        context: ExecutorToolContext,
    ) -> ExecutorCommand:
        try:
            arguments = json.loads(invocation.arguments_json)
        except json.JSONDecodeError as exc:
            raise ValueError("executor invocation arguments are not valid JSON") from exc
        command_payload = ExecuteToolCommand(
            version=1,
            type=COMMAND_EXECUTE_TOOL,
            invocation_id=invocation.invocation_id,
            tool_call_id=invocation.tool_call_id,
            tool_name=invocation.tool_name,
            arguments=arguments,
            context=context,
            run_id=invocation.run_id,
            session_id=invocation.session_id,
            deadline_at=invocation.deadline_at.isoformat() if invocation.deadline_at else None,
        )
        command = await self.commands.append(
            executor_id,
            command_payload,
        )
        self._wakeup(executor_id).set()
        return command

    async def cancel(self, executor_id: str, invocation_id: str) -> None:
        await self.invocations.request_cancel(invocation_id)
        await self.commands.append(
            executor_id,
            CancelToolCommand(version=1, type=COMMAND_CANCEL_TOOL, invocation_id=invocation_id),
        )
        self._wakeup(executor_id).set()

    # -- results --

    async def accept_started(self, device: ExecutorDevice, lease_id: str, invocation_id: str) -> InvocationRecord:
        await self._require_current_lease(device, lease_id)
        return await self.invocations.mark_running(invocation_id)

    async def accept_result(
        self,
        device: ExecutorDevice,
        lease_id: str,
        *,
        invocation_id: str,
        status: InvocationStatus,
        result: DeviceResultEnvelope,
        error_code: str | None = None,
    ) -> InvocationRecord:
        await self._require_current_lease(device, lease_id)
        validate_device_result(status, result, error_code)
        record = await self.invocations.complete(
            invocation_id,
            status=status,
            result_payload=result.serialized(),
            error_code=error_code,
        )
        waiter = self._waiters.pop(invocation_id, None)
        if waiter is not None and not waiter.done():
            waiter.set_result(record)
        return record

    # -- waiting --

    def waiter(self, invocation_id: str) -> asyncio.Future[InvocationRecord]:
        future = self._waiters.get(invocation_id)
        if future is None:
            future = asyncio.get_running_loop().create_future()
            self._waiters[invocation_id] = future
        return future

    def drop_waiter(self, invocation_id: str) -> None:
        self._waiters.pop(invocation_id, None)

    # -- stream support --

    def _wakeup(self, executor_id: str) -> asyncio.Event:
        event = self._wakeups.get(executor_id)
        if event is None:
            event = asyncio.Event()
            self._wakeups[executor_id] = event
        return event

    async def pending_commands(self, executor_id: str, cursor_seq: int) -> list[ExecutorCommand]:
        return await self.commands.after(executor_id, cursor_seq)

    async def wait_for_commands(self, executor_id: str, timeout: float) -> None:
        event = self._wakeup(executor_id)
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except TimeoutError:
            return
        finally:
            event.clear()

    async def _require_current_lease(self, device: ExecutorDevice, lease_id: str) -> None:
        if not await self.leases.is_current(lease_id, device.executor_id):
            raise StaleLeaseError(lease_id)
