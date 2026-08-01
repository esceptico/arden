import asyncio
import json

from arden.execution.commands import ExecutorCommand, ExecutorCommandLog
from arden.execution.devices import ExecutorDevice, ExecutorDeviceStore
from arden.execution.leases import ExecutorLease, LeaseStore
from arden.execution.models import InvocationRecord, InvocationStatus
from arden.execution.store import InvocationStore

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
        self._connected: set[str] = set()

    # -- connection lifecycle --

    async def connect(self, device: ExecutorDevice) -> ExecutorLease:
        lease = await self.leases.acquire(device.executor_id)
        await self.devices.touch(device.executor_id)
        self._connected.add(device.executor_id)
        return lease

    def disconnect(self, executor_id: str) -> None:
        self._connected.discard(executor_id)

    def is_connected(self, executor_id: str) -> bool:
        return executor_id in self._connected

    def connected_executor(self) -> str | None:
        return next(iter(self._connected), None)

    async def heartbeat(self, device: ExecutorDevice, lease_id: str, *, acked_seq: int | None = None) -> ExecutorLease:
        lease = await self.leases.renew(lease_id)
        if lease is None or lease.executor_id != device.executor_id:
            raise StaleLeaseError(lease_id)
        await self.devices.touch(device.executor_id)
        if acked_seq is not None:
            await self.commands.ack(device.executor_id, acked_seq)
        return lease

    # -- dispatch --

    async def dispatch(
        self,
        executor_id: str,
        invocation: InvocationRecord,
        *,
        context: dict | None = None,
    ) -> ExecutorCommand:
        command = await self.commands.append(
            executor_id,
            COMMAND_EXECUTE_TOOL,
            {
                "invocation_id": invocation.invocation_id,
                "tool_call_id": invocation.tool_call_id,
                "tool_name": invocation.tool_name,
                "arguments": json.loads(invocation.arguments_json),
                "context": context or {},
                "run_id": invocation.run_id,
                "session_id": invocation.session_id,
                "deadline_at": invocation.deadline_at.isoformat() if invocation.deadline_at else None,
            },
            invocation_id=invocation.invocation_id,
        )
        self._wakeup(executor_id).set()
        return command

    async def cancel(self, executor_id: str, invocation_id: str) -> None:
        await self.invocations.request_cancel(invocation_id)
        await self.commands.append(
            executor_id,
            COMMAND_CANCEL_TOOL,
            {"invocation_id": invocation_id},
            invocation_id=invocation_id,
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
        result_payload: str,
        error_code: str | None = None,
    ) -> InvocationRecord:
        await self._require_current_lease(device, lease_id)
        record = await self.invocations.complete(
            invocation_id,
            status=status,
            result_payload=result_payload,
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
