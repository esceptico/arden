import json

from pydantic import BaseModel, Field

from arden.events.sse import BackgroundTaskEvent
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import BACKGROUND_RESULT_READ_MAX_CHARS, ToolExecution
from arden.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope


class AgentCancelInput(BaseModel):
    session_id: str = Field(
        min_length=1,
        max_length=200,
        description="Session id of the running agent to stop — the id research() returned.",
    )


class AgentResultReadInput(BaseModel):
    task_id: str = Field(
        min_length=1,
        max_length=200,
        description="Task id from a background_agent_result completion event.",
    )
    offset: int = Field(default=0, ge=0, description="Zero-based character offset.")
    limit: int = Field(
        default=BACKGROUND_RESULT_READ_MAX_CHARS,
        ge=1,
        le=BACKGROUND_RESULT_READ_MAX_CHARS,
        description="Maximum characters to return.",
    )


async def agent_result_read(execution: ToolExecution, args: AgentResultReadInput) -> ToolResult:
    result = await execution.ctx.background_tasks.read_background_result(args.task_id)
    if result is None:
        return ToolResult.failure(
            code="not_found",
            message=f"No completed background-agent result exists for task {args.task_id} in this session.",
            preview="Result not found",
            recovery_action="Use the task_id from a background_agent_result event in this session.",
        )
    if args.offset > len(result):
        return ToolResult.failure(
            code="invalid_arguments",
            message=f"Offset {args.offset} is past the result length of {len(result)} characters.",
            preview="Invalid offset",
            recovery_action=f"Retry with offset between 0 and {len(result)}.",
        )

    end = min(len(result), args.offset + args.limit)
    has_more = end < len(result)
    payload = {
        "task_id": args.task_id,
        "offset": args.offset,
        "content": result[args.offset : end],
        "next_offset": end if has_more else None,
        "total_chars": len(result),
        "has_more": has_more,
    }
    return ToolResult(
        content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        preview=f"Result characters {args.offset}-{end} of {len(result)}",
        data={key: value for key, value in payload.items() if key != "content"},
    )


agent_result_read_tool = tool(
    display_name="Read Agent Result",
    display_description="Read an exact page from a completed background agent result.",
    description=(
        "Read the exact durable result of a completed background agent by task id. "
        "Use next_offset to page until has_more is false. Offsets and limits count Unicode characters."
    ),
    input_model=AgentResultReadInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, offload=False),
    execute=agent_result_read,
)


async def approve_agent_cancel(_execution: ToolExecution, args: AgentCancelInput) -> ApprovalInfo:
    return ApprovalInfo(
        description="Stop a running agent",
        preview=f"Agent session: {args.session_id}",
        diff=None,
    )


async def agent_cancel(execution: ToolExecution, args: AgentCancelInput) -> ToolResult:
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
        await session_service.store.request_background_agent_cancel_cascade(
            session_id=registry.session_id,
            task_id=task_id,
            actor="agent",
            cause="user_cancelled",
            idempotency_key=f"agent-cancel:{execution.ctx.run.run_id}:{execution.tool_id}",
        )
    cascaded = len(cancelled) - 1
    tail = f" Also stopped {cascaded} agent(s) it had spawned." if cascaded else ""
    return ToolResult(
        content=f"Cancelled the agent in {args.session_id}.{tail}",
        preview=f"Cancelled · {args.session_id}",
    )


agent_cancel_tool = tool(
    display_name="CancelAgent",
    display_description="Stop a running agent.",
    description=(
        "Stop an agent you spawned, addressed by its session id. Anything it spawned stops too. "
        "Requires approval. A finished agent needs no cancel — its result arrives automatically, "
        "and session_read shows its work."
    ),
    input_model=AgentCancelInput,
    policy=ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL, requires_approval=True, deferred=True),
    approval=approve_agent_cancel,
    execute=agent_cancel,
)
