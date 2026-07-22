from pydantic import BaseModel, Field

from arden.events.sse import GoalUpdatedEvent
from arden.tools.core import EmptyInput, ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope

BLOCK_ATTEMPT_KIND = "block_attempt"
BLOCKED_CONFIRMATION_ATTEMPTS = 3


def _format_goal(goal: dict | None) -> str:
    if not goal:
        return "No active goal for this session."
    lines = [
        f"Goal: {goal['objective']}",
        f"Status: {goal['status']}",
    ]
    if goal.get("blocked_reason"):
        lines.append(f"Blocked: {goal['blocked_reason']}")
    evidence = goal.get("evidence") or []
    if evidence:
        lines.append("Evidence:")
        lines.extend(f"- {item.get('text', '')}" for item in evidence[-5:])
    return "\n".join(lines)


def _normalize_blocked_reason(reason: str) -> str:
    return " ".join(reason.casefold().split())


def _consecutive_block_attempts(goal: dict, reason: str) -> int:
    expected = _normalize_blocked_reason(reason)
    count = 0
    for item in reversed(goal.get("evidence") or []):
        if item.get("kind") != BLOCK_ATTEMPT_KIND:
            break
        if _normalize_blocked_reason(str(item.get("blocked_reason") or "")) != expected:
            break
        count += 1
    return count


def _block_attempt_evidence(reason: str, evidence: str | None) -> str:
    if evidence:
        return f"Blocker reported: {reason}\nEvidence: {evidence}"
    return f"Blocker reported: {reason}"


def _goal_failure(*, missing_goal: bool = False) -> ToolResult:
    if missing_goal:
        return ToolResult.failure(
            code="not_found",
            message="No active goal exists for this session.",
            preview="No goal",
            recovery_action="Create a goal before trying to update its status.",
        )
    return ToolResult.failure(
        code="not_configured",
        message="Session goal service is unavailable.",
        preview="Goal unavailable",
        recovery_action="Retry from a persisted session with goal support enabled.",
    )


async def _emit_goal_updated(execution: ToolExecution, goal: dict) -> None:
    if execution.ctx.io.emit:
        await execution.ctx.io.emit(GoalUpdatedEvent(session_id=execution.ctx.session_id, goal=goal))


async def get_goal(execution: ToolExecution, args: EmptyInput) -> ToolResult:
    svc = execution.ctx.services.get("session")
    if not svc:
        return _goal_failure()
    goal = await svc.get_goal(execution.ctx.session_id)
    return ToolResult(content=_format_goal(goal), preview=goal["status"] if goal else "No goal")


async def complete_goal(execution: ToolExecution, args: EmptyInput) -> ToolResult:
    svc = execution.ctx.services.get("session")
    if not svc:
        return _goal_failure()
    goal = await svc.update_goal(execution.ctx.session_id, status="complete")
    if not goal:
        return _goal_failure(missing_goal=True)
    await _emit_goal_updated(execution, goal)
    return ToolResult(
        content=(
            "Goal marked complete. Now send the user a visible concise completion report in chat. "
            "Include what changed, exact verification performed, and any remaining risks."
        ),
        preview="Goal complete",
    )


class BlockGoalInput(BaseModel):
    reason: str = Field(max_length=20_000, description="Why progress cannot continue without user/system input.")
    evidence: str | None = Field(default=None, max_length=20_000, description="Optional evidence for the blocker.")


async def block_goal(execution: ToolExecution, args: BlockGoalInput) -> ToolResult:
    svc = execution.ctx.services.get("session")
    if not svc:
        return _goal_failure()
    get_goal = getattr(svc, "get_goal", None)
    if not get_goal:
        return _goal_failure()
    current = await get_goal(execution.ctx.session_id)
    if not current:
        return _goal_failure(missing_goal=True)
    attempts = _consecutive_block_attempts(current, args.reason) + 1
    terminal_blocked = attempts >= BLOCKED_CONFIRMATION_ATTEMPTS
    goal = await svc.update_goal(
        execution.ctx.session_id,
        status="blocked" if terminal_blocked else "active",
        blocked_reason=args.reason if terminal_blocked else None,
        evidence=_block_attempt_evidence(args.reason, args.evidence),
        evidence_kind=BLOCK_ATTEMPT_KIND,
        evidence_blocked_reason=args.reason,
    )
    if not goal:
        return _goal_failure(missing_goal=True)
    await _emit_goal_updated(execution, goal)
    if not terminal_blocked:
        remaining = BLOCKED_CONFIRMATION_ATTEMPTS - attempts
        return ToolResult(
            content=(
                f"Blocker noted ({attempts}/{BLOCKED_CONFIRMATION_ATTEMPTS}). Goal remains active; "
                f"{remaining} same-blocker report{'s' if remaining != 1 else ''} before terminal blocked. "
                "Continue automatically and try any viable next step. Call block_goal again only if this same "
                "missing input still prevents progress."
            ),
            preview="Goal still active",
        )
    return ToolResult(content=_format_goal(goal), preview="Goal blocked")


get_goal_tool = tool(
    display_name="Get Goal",
    description="Read the durable goal for the current session.",
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, permissions=frozenset({"session"})),
    execute=get_goal,
)

complete_goal_tool = tool(
    display_name="Complete Goal",
    description=(
        "Mark the current session goal complete after the completion audit passes. "
        "This tool takes no input. Put evidence and verification in the visible assistant report after the tool succeeds."
    ),
    policy=ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL, permissions=frozenset({"session"})),
    execute=complete_goal,
)

block_goal_tool = tool(
    display_name="Block Goal",
    description=(
        "Report that the current session goal may be blocked on missing user/system input. "
        "The first two consecutive matching reports keep the goal active so automatic continuation can retry; "
        "the third matching report marks the goal blocked. After a terminal blocked result, send the user a "
        "concise blocked report with the reason and next required input."
    ),
    input_model=BlockGoalInput,
    policy=ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL, permissions=frozenset({"session"})),
    execute=block_goal,
)
