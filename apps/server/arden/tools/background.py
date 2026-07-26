from pydantic import BaseModel, Field

from arden.constants import BACKGROUND_AGENT_TIMEOUT
from arden.core.agent_types import SPAWN_SURFACE_GUIDANCE
from arden.events.sse import BackgroundTaskEvent
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope

BACKGROUND_SYSTEM_PROMPT = (
    "You are a background agent. Complete the given task using available read-only tools, "
    "then return a concise, self-contained report. Be thorough but focused. "
    "Include any relevant source names, IDs, links, or evidence directly in your final report. "
    "Do not refer to separate files, hidden messages, or context outside your final report. "
    "You are read-only — report what you find, the caller decides what to do with it."
)

BACKGROUND_DESCRIPTION = (
    "Spawn a background agent that runs independently and delivers its result automatically when done. "
    "The agent has read-only tool access (search, read, web, memory). "
    "Use for long-running tasks: deep research, multi-source investigation, data gathering. "
    "It gets its own session: the id comes back here, send_message(session_id=...) steers it, "
    "read_session(session_id=...) shows its work, cancel_agent(session_id=...) stops it. "
    "The result arrives as a hidden meta message in this conversation — do not poll, and do not "
    "inspect the filesystem for it."
    f" {SPAWN_SURFACE_GUIDANCE}"
)


class BackgroundInput(BaseModel):
    task: str = Field(description="What the background agent should do.")


async def background(execution: ToolExecution, args: BackgroundInput) -> ToolResult:
    ctx = execution.ctx

    if not ctx.spawn_fn:
        return ToolResult.failure(
            code="not_configured",
            message="Background agent spawning is unavailable.",
            preview="Background unavailable",
            recovery_action="Run this from a session with agent spawning enabled.",
        )

    tools = ctx.registry.get_schemas(read_only=True, capabilities=ctx.capabilities)

    spawn = await ctx.spawn_fn(
        ctx,
        task=args.task,
        system_prompt=BACKGROUND_SYSTEM_PROMPT,
        tools=tools,
        timeout=BACKGROUND_AGENT_TIMEOUT,
        parent_id=execution.tool_id,
        agent_type="background_research",
        wait=False,
        kind="background",
    )

    if spawn.status == "failed":
        # The only failure the detached path returns is the per-session concurrency cap.
        return ToolResult.failure(
            code="conflict",
            message=spawn.text,
            preview="At the agent limit",
            recovery_action="Wait for a running agent to finish, then spawn again.",
        )

    content = (
        f"{spawn.text}\n"
        f"Its session is {spawn.child_session_id} — steer it with send_message(session_id=...), "
        "inspect its work with read_session(session_id=...). "
        "The result is delivered here automatically; do not poll."
    )
    return ToolResult(content=content, preview=content[:80], data=spawn.child_agent_data() or None)


class CancelAgentInput(BaseModel):
    session_id: str = Field(
        min_length=1,
        max_length=200,
        description="Session id of the running agent to stop — the id background() returned.",
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
                "read_session shows what it did. Only agents you spawned can be stopped here; "
                "the user stops their own chats from the app."
            ),
        )

    command = registry.cancel(task_id)
    if emit := execution.ctx.io.emit:
        await emit(BackgroundTaskEvent(task_id=task_id, command=command, status="cancelled"))
    # An agent's own spawns run in ITS session, so stopping it must stop them too —
    # same cascade the /chat/child-agents/{id}/cancel route performs.
    cascaded = 0
    if (run_registry := execution.ctx.run_registry) is not None:
        cascaded = len(run_registry.cancel_subtree(args.session_id))
    tail = f" Also stopped {cascaded} agent(s) it had spawned." if cascaded else ""
    return ToolResult(
        content=f"Cancelled the agent in {args.session_id}.{tail}",
        preview=f"Cancelled · {args.session_id}",
    )


background_tool = tool(
    display_name="Background",
    display_description="Run a task in the background.",
    description=BACKGROUND_DESCRIPTION,
    input_model=BackgroundInput,
    policy=ToolPolicy(action=ToolAction.EXECUTE, scope=ToolScope.INTERNAL),
    execute=background,
    kind="agent",
)

cancel_agent_tool = tool(
    display_name="CancelAgent",
    display_description="Stop a running agent.",
    description=(
        "Stop an agent you spawned, addressed by its session id. Anything it spawned stops too. "
        "Requires approval. A finished agent needs no cancel — its result arrives automatically, "
        "and read_session shows its work."
    ),
    input_model=CancelAgentInput,
    policy=ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL, requires_approval=True),
    approval=approve_cancel_agent,
    execute=cancel_agent,
)
