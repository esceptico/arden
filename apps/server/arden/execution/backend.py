import json

from arden.execution.gateway import ExecutorGateway
from arden.execution.models import TERMINAL_STATUSES
from arden.execution.results import tool_result_from_record
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

        record = await self._invocations.create(
            invocation_id=invocation.invocation_id,
            run_id=execution.ctx.run.run_id,
            session_id=execution.ctx.session_id,
            tool_call_id=invocation.invocation_id,
            tool_name=invocation.tool_name,
            placement=ToolPlacement.CLIENT,
            arguments_json=json.dumps(invocation.arguments),
        )
        if record.status in TERMINAL_STATUSES:
            # Duplicate execution of an already-settled invocation (e.g. a
            # replayed agent step): return the recorded outcome, never re-run.
            return tool_result_from_record(record)

        area = execution.ctx.area
        context = {"default_cwd": area.default_cwd if area else None}
        waiter = self._gateway.waiter(invocation.invocation_id)
        await self._gateway.dispatch(executor_id, record, context=context)
        try:
            record = await waiter
        finally:
            self._gateway.drop_waiter(invocation.invocation_id)
        return tool_result_from_record(record)
