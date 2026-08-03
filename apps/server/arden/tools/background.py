from pydantic import BaseModel, Field

from arden.events.sse import BackgroundTaskEvent
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope


class CancelAgentInput(BaseModel):
    session_id: str = Field(
        min_length=1,
        max_length=200,
        description="Session id of the running agent to stop — the id research() returned.",
    )


async def approve_cancel_agent(_execution: ToolExecution, args: CancelAgentInput) -> ApprovalInfo:
    return ApprovalInfo(
        description="Stop a running agent",
        preview=f"Agent session: {args.session_id}",
        diff=None,
    )


async def cancel_agent(execution: ToolExecution, args: CancelAgentInput) -> ToolResult:
    registry = execution.ctx.background_tasks
    task_id = registry.task_for_session(args.session_id)
    if task_id is None:
        running = registry.live_child_sessions()
        listing = "\n".join(f"- {sid}" for sid in running)
        return ToolResult.failure(
            code="not_found",
            message=(
                f"No agent of yours is running in {args.session_id}."
                + (f" Currently running:\n{listing}" if running else " Nothing is running.")
            ),
            preview="Not found",
            recovery_action=(
                "Retry with one of the listed sessions. A finished agent needs no cancel — "
                "session_read shows what it did. Only agents you spawned can be stopped here; "
                "the user stops their own chats from the app."
            ),
        )

    command = registry.cancel(task_id)
    if emit := execution.ctx.io.emit:
        await emit(BackgroundTaskEvent(task_id=task_id, command=command, status="cancelled"))
    # An agent's own spawns run in ITS session, so stopping it must stop them too —
    # same cascade the /chat/child-agents/{id}/cancel route performs. Mirror
    # every cancel durably like that route does: an in-memory cancel alone
    # reads as still-running after a restart.
    cancelled = [(registry.session_id, task_id)]
    if (run_registry := execution.ctx.run_registry) is not None:
        cancelled.extend(run_registry.cancel_subtree(args.session_id))
    if (session_service := execution.ctx.services.get("session")) is not None:
        if (store := getattr(session_service, "store", None)) is not None:
            for cancelled_session, cancelled_task in cancelled:
                await store.request_background_agent_cancel(cancelled_session, cancelled_task)
    cascaded = len(cancelled) - 1
    tail = f" Also stopped {cascaded} agent(s) it had spawned." if cascaded else ""
    return ToolResult(
        content=f"Cancelled the agent in {args.session_id}.{tail}",
        preview=f"Cancelled · {args.session_id}",
    )


cancel_agent_tool = tool(
    display_name="CancelAgent",
    display_description="Stop a running agent.",
    description=(
        "Stop an agent you spawned, addressed by its session id. Anything it spawned stops too. "
        "Requires approval. A finished agent needs no cancel — its result arrives automatically, "
        "and session_read shows its work."
    ),
    input_model=CancelAgentInput,
    policy=ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL, requires_approval=True, deferred=True),
    approval=approve_cancel_agent,
    execute=cancel_agent,
)
