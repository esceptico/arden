import asyncio
import contextlib
import json
from datetime import UTC, datetime, timedelta

from arden.constants import OFFLOAD_THRESHOLD
from arden.core.tool_result_files import RESULTS_BASE
from arden.execution.gateway import ExecutorGateway
from arden.execution.models import TERMINAL_STATUSES, InvocationRecord
from arden.execution.results import result_payload, tool_result_from_payload
from arden.execution.store import InvocationStore
from arden.tools.core.base import ToolResult
from arden.tools.core.context import ToolExecution
from arden.tools.core.execution import ToolInvocation
from arden.tools.core.types import ToolPlacement


class ClientExecutionBackend:
    """Executes client-placed tools on a connected executor device.

    Creates the durable invocation, dispatches over the executor gateway,
    and awaits the terminal outcome. Timeouts and cancellation are owned by
    the caller (ArdenToolExecutor) exactly as for in-process tools.
    """

    def __init__(self, gateway: ExecutorGateway, invocations: InvocationStore):
        self._gateway = gateway
        self._invocations = invocations

    def available(self) -> bool:
        return self._gateway.connected_executor() is not None

    async def execute(self, invocation: ToolInvocation, execution: ToolExecution) -> ToolResult:
        executor_id = self._gateway.connected_executor()
        if executor_id is None:
            return ToolResult.failure(
                code="no_executor_connected",
                message=(
                    f"Tool {invocation.tool_name!r} runs on the user's device, and no device executor "
                    "is currently connected."
                ),
                preview="No device connected",
                retryable=True,
                recovery_action="Retry once the desktop app is running, or continue without this tool.",
            )

        deadline_at = (
            datetime.now(UTC) + timedelta(seconds=invocation.timeout_seconds)
            if invocation.timeout_seconds is not None
            else None
        )
        record = await self._invocations.create(
            invocation_id=invocation.invocation_id,
            run_id=execution.ctx.run.run_id,
            session_id=execution.ctx.session_id,
            tool_call_id=invocation.invocation_id,
            tool_name=invocation.tool_name,
            placement=ToolPlacement.CLIENT,
            arguments_json=json.dumps(invocation.arguments),
            deadline_at=deadline_at,
        )
        if record.status in TERMINAL_STATUSES:
            # Duplicate execution of an already-settled invocation (e.g. a
            # replayed agent step): return the recorded outcome, never re-run.
            return self._settle(record, execution)

        waiter = self._gateway.waiter(invocation.invocation_id)
        await self._gateway.dispatch(executor_id, record, context=self._context(execution))
        try:
            record = await waiter
        except asyncio.CancelledError:
            # The caller gave up (run cancel or wait_for timeout) — tell the
            # device to abort instead of letting it run to completion for a
            # result nobody is waiting on. Shielded: this cleanup must survive
            # the cancellation that triggered it.
            with contextlib.suppress(Exception):
                await asyncio.shield(self._gateway.cancel(executor_id, invocation.invocation_id))
            raise
        finally:
            self._gateway.drop_waiter(invocation.invocation_id)
        return self._settle(record, execution)

    def _context(self, execution: ToolExecution) -> dict:
        area = execution.ctx.area
        observations = {
            resource_id: {"version": observation.version, "content_read": observation.content_read}
            for resource_id, observation in execution.ctx.run.resource_observations().items()
        }
        return {
            "default_cwd": area.default_cwd if area else None,
            "offload_dir": f"{RESULTS_BASE}/",
            "offload_threshold": OFFLOAD_THRESHOLD,
            "resource_observations": observations,
        }

    def _settle(self, record: InvocationRecord, execution: ToolExecution) -> ToolResult:
        payload = result_payload(record)
        for observation in payload.get("observations") or []:
            if not isinstance(observation, dict) or not observation.get("id"):
                continue
            execution.ctx.run.observe_resource(
                str(observation["id"]),
                version=str(observation["version"]) if observation.get("version") is not None else None,
                container_version=None,
                content_read=bool(observation.get("content_read")),
            )
        return tool_result_from_payload(record, payload)
