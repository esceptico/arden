from datetime import UTC, datetime, timedelta
from difflib import get_close_matches
from typing import Literal

from pydantic import BaseModel, Field

from arden.automation.models import Automation
from arden.automation.triggers import build_trigger
from arden.core.agent_types import SPAWN_SURFACE_GUIDANCE
from arden.events.triggers import EVENT_APPROACHING
from arden.tools.core import EmptyInput, ToolResult, tool
from arden.tools.core.collections import format_timestamp
from arden.tools.core.context import ToolExecution
from arden.tools.core.scope import matches_scope
from arden.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope

# --- Descriptions ---

CREATE_AUTOMATION_DESCRIPTION = (
    "Create an automation — a task the agent runs autonomously. "
    "Trigger types: 'time' (runs at a scheduled time or interval), 'event' (runs when an event fires, "
    f"e.g. '{EVENT_APPROACHING}'), 'message' (runs when a new Slack message arrives). "
    "For reacting to Slack messages use trigger_type='message' with 'channels' (one or more channel "
    "names) plus optional 'from_user' and 'contains' keyword filters — this is the correct trigger for "
    "a Slack watcher; do NOT fake it with time/interval polling. Detection is near-real-time (~1 min). "
    "If the user wants a Slack watcher but hasn't named the channel(s), ASK which channels — do not "
    "substitute a scheduled scan. "
    "Time triggers support two modes: schedule ('at' a specific time) or interval ('every' N hours/minutes). "
    "Optional model override per automation (falls back to default chat model when omitted). "
    "Read-only by default, set auto_approve=true for autonomous memory/note writes (skips approvals)."
    f" {SPAWN_SURFACE_GUIDANCE}"
)

LIST_AUTOMATIONS_DESCRIPTION = "List all automations by stable ID with their trigger, status, and next run."

UPDATE_AUTOMATION_DESCRIPTION = (
    "Update an existing automation. Only provide the fields you want to change. "
    "Use list_automations to find IDs. "
    "Trigger fields (trigger_type, at, days, every, event_type, lead_minutes, start, end, and for "
    "trigger_type='message': channels, from_user, contains) are merged with the current trigger — only "
    "provide what should change. "
    "Set enabled=false to pause or enabled=true to resume."
)

DELETE_AUTOMATION_DESCRIPTION = "Delete an automation by its ID. Use list_automations to find IDs."

GET_AUTOMATION_RESULT_DESCRIPTION = "Get the last execution result of an automation by its ID."

RUN_AUTOMATION_DESCRIPTION = (
    "Trigger an immediate execution of an automation. "
    "The automation runs in the background — use get_automation_result to check the outcome. "
    "Use list_automations to find IDs."
)


# --- Helpers ---


def _triggers_label(triggers: list) -> str:
    return " | ".join(t.label for t in triggers)


def _automation_not_found(task_id: str) -> ToolResult:
    return ToolResult.failure(
        code="not_found",
        message=f"Automation not found: {task_id}",
        preview="Not found",
        recovery_action="Call list_automations and retry with an exact returned task_id.",
    )


def _automation_unavailable() -> ToolResult:
    return ToolResult.failure(
        code="not_configured",
        message="Automation service unavailable.",
        preview="Unavailable",
        recovery_action="Enable the automation service before retrying.",
    )


async def _resolve_parent_context(
    execution: "ToolExecution",
    explicit_parent: str | None,
    idempotency_scope: str | None,
) -> tuple[str | None, str | None]:
    """Default parent_automation_id to the calling loop's task_id; pull
    parent_fire_at from that parent's last_run_at when run/attempt scopes
    need it but the caller didn't supply one.

    Raises ValueError when run/attempt scope is requested but the parent
    cannot be resolved — silently collapsing to global scope would break
    the agent's idempotency intent without any signal.
    """
    parent_id = explicit_parent or execution.ctx.run.loop_task_id
    if parent_id is None or idempotency_scope not in {"run", "attempt"}:
        return parent_id, None
    svc = execution.ctx.services.get("automation")
    if svc is None:
        return parent_id, None
    try:
        parent = await svc.get(parent_id)
    except KeyError as exc:
        raise ValueError(
            f"idempotency_scope={idempotency_scope!r} requires parent_automation_id ({parent_id!r}) to exist; not found"
        ) from exc
    fire_at = format_timestamp(parent.last_run_at) if parent.last_run_at else None
    return parent_id, fire_at


def _format_automation_list(automations: list[Automation]) -> str:
    lines = []
    for a in sorted(automations, key=lambda item: item.task_id):
        status = "enabled" if a.enabled else "disabled"
        next_run = format_timestamp(a.next_run_at) if a.next_run_at else "—"
        last_run = format_timestamp(a.last_run_at) if a.last_run_at else "never"
        label = _automation_label(a)
        builtin_tag = " [builtin]" if a.builtin else ""

        lines.append(
            f"[{a.task_id}] {label}{builtin_tag}\n"
            f"  {_triggers_label(a.triggers)} · {status}\n"
            f"  next: {next_run} · last: {last_run}" + (f"\n  model: {a.model}" if a.model else "")
        )
    return "\n\n".join(lines)


def _automation_label(automation: Automation) -> str:
    """Never promote executable prompt text into a display label."""
    return automation.name or automation.description or automation.task_id


# --- Input Models ---


class CreateAutomationInput(BaseModel):
    name: str = Field(description="Short human-readable label (e.g. 'morning briefing', 'pre-meeting prep')")
    prompt: str = Field(description="Full instructions the automation executes on each run.", min_length=1)
    description: str | None = Field(
        default=None,
        max_length=220,
        description="Optional concise display summary. Omit to generate one from the prompt.",
    )
    model: str | None = Field(default=None, description="Optional agent model override for this automation.")
    trigger_type: Literal["time", "event", "message"] = Field(
        description="Trigger type: 'time' (scheduled or interval), 'event' (reacts to events like calendar_approaching), 'message' (reacts to a new Slack message in one or more channels)",
    )
    at: str | None = Field(
        default=None,
        description="Time of day in HH:MM format (24h, local time). For schedule-based time triggers.",
    )
    days: str | None = Field(
        default=None,
        description="Which days to run: 'daily', 'weekdays', or comma-separated days (e.g. 'mon,wed,fri'). Omit for one-shot schedule or always-on interval.",
    )
    every: str | None = Field(
        default=None,
        description="Interval: e.g. '30m', '2h', '1h30m', '1d', '2d12h'. For interval-based time triggers. Cannot be combined with 'at'.",
    )
    start: str | None = Field(
        default=None,
        description="Start of active window in HH:MM (24h). Only for interval mode. Must be set with 'end'.",
    )
    end: str | None = Field(
        default=None,
        description="End of active window in HH:MM (24h). Only for interval mode. Must be set with 'start'.",
    )
    event_type: str | None = Field(
        default=None,
        description=f"Event type to react to (e.g. '{EVENT_APPROACHING}'). Required for trigger_type='event'",
    )
    lead_minutes: int | str | None = Field(
        default=None,
        description="For event_approaching only: trigger when event is this many minutes away (default 60).",
    )
    channels: list[str] | None = Field(
        default=None,
        max_length=100,
        description="For trigger_type='message': Slack channel names to watch (one or more, e.g. ['feel-good-inc', 'eng-bugs']). Required for message triggers.",
    )
    from_user: str | None = Field(
        default=None,
        description="For trigger_type='message': only react to messages from this Slack username/display name. Recommended — without it, anyone in the channel can drive an auto-approve run.",
    )
    contains: list[str] | None = Field(
        default=None,
        max_length=100,
        description="For trigger_type='message': only react when the message text contains any of these keywords (case-insensitive). Optional.",
    )
    auto_approve: bool = Field(
        default=False,
        description=(
            "Skip per-tool approvals within tool_scope. This never grants tools: "
            "without tool_scope the automation remains read-only."
        ),
    )
    tool_scope: list[str] | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description=(
            "Tool-name allowlist patterns (exact names or prefixes like 'slack_*') granting tools ON TOP of the "
            "always-available read-only toolset. List the write/action tools the prompt needs; read tools are "
            "always there. Omit for read-only access."
        ),
    )
    thread_id: str | None = Field(
        default=None,
        description=(
            "Target session/channel the automation posts into when it fires. "
            "Use a channel session_id (see create_session). When omitted, the run "
            "is unattached (no chat surface)."
        ),
    )
    read_history: bool = Field(
        default=False,
        description=(
            "Only meaningful with thread_id. When true the automation reads the "
            "target session's history as iteration context; false means it just "
            "posts a fresh run."
        ),
    )
    parent_automation_id: str | None = Field(
        default=None,
        description=(
            "Explicit parent lineage. Defaults to the current loop's task_id when "
            "this tool is called from inside a loop iteration."
        ),
    )
    idempotency_key: str | None = Field(
        default=None,
        description=(
            "Deduplication key. Combined with idempotency_scope, prevents "
            "creating duplicate automations on retries / repeated tool calls."
        ),
    )
    idempotency_scope: Literal["run", "attempt", "global"] | None = Field(
        default=None,
        description=(
            "Scope for the idempotency claim. Required when idempotency_key is "
            "set. 'global' = no parent; 'run' / 'attempt' = scoped to the "
            "calling automation's fire."
        ),
    )


class UpdateAutomationInput(BaseModel):
    task_id: str = Field(description="The automation ID to update")
    name: str | None = Field(default=None, description="New name")
    description: str | None = Field(default=None, max_length=220, description="New concise display summary")
    prompt: str | None = Field(default=None, description="New full execution instructions", min_length=1)
    model: str | None = Field(default=None, description="New model override")
    trigger_type: Literal["time", "event", "message"] | None = Field(
        default=None,
        description="New trigger type: 'time', 'event', or 'message'. Only set when switching trigger type.",
    )
    at: str | None = Field(default=None, description="New time of day HH:MM (24h). For schedule-based time triggers.")
    days: str | None = Field(
        default=None, description="New days: 'daily', 'weekdays', or comma-separated (e.g. 'mon,wed,fri')"
    )
    every: str | None = Field(
        default=None, description="New interval: e.g. '30m', '2h', '1d', '2d12h'. For interval-based time triggers."
    )
    start: str | None = Field(default=None, description="New active window start HH:MM (interval mode only)")
    end: str | None = Field(default=None, description="New active window end HH:MM (interval mode only)")
    event_type: str | None = Field(default=None, description=f"New event type (e.g. '{EVENT_APPROACHING}')")
    lead_minutes: int | str | None = Field(
        default=None,
        description="New lead time for event_approaching (minutes or duration like '2h30m')",
    )
    channels: list[str] | None = Field(
        default=None,
        max_length=100,
        description="For trigger_type='message': Slack channel names to watch (one or more). Replaces the existing channel set.",
    )
    from_user: str | None = Field(
        default=None,
        description="For trigger_type='message': only react to messages from this Slack username/display name.",
    )
    contains: list[str] | None = Field(
        default=None,
        max_length=100,
        description="For trigger_type='message': only react when the message contains any of these keywords (case-insensitive).",
    )
    auto_approve: bool | None = Field(
        default=None,
        description="Skip per-tool approvals within the existing tool_scope. This never grants tools.",
    )
    tool_scope: list[str] | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description=(
            "Replace the tool-name allowlist patterns for this automation. Patterns grant tools on top of the "
            "always-available read-only toolset — list the write/action tools the prompt needs."
        ),
    )
    enabled: bool | None = Field(default=None, description="Enable or disable the automation")


class DeleteAutomationInput(BaseModel):
    task_id: str = Field(description="The automation ID to delete")


class GetAutomationResultInput(BaseModel):
    task_id: str = Field(description="The automation ID to get results for")


class RunAutomationInput(BaseModel):
    task_id: str = Field(description="The automation ID to run")


# --- Tools ---


async def approve_create_automation(execution: ToolExecution, args: CreateAutomationInput) -> ApprovalInfo | None:
    next_run = None
    if args.trigger_type == "message":
        chans = ", ".join(f"#{c}" for c in (args.channels or [])) or "(no channel)"
        schedule_label = f"On Slack message in {chans}"
        if args.from_user:
            schedule_label += f" from @{args.from_user}"
        if args.contains:
            schedule_label += f" containing {', '.join(args.contains)}"
    else:
        try:
            trigger, next_run = build_trigger(
                args.trigger_type,
                at=args.at,
                days=args.days,
                every=args.every,
                event_type=args.event_type,
                lead_minutes=args.lead_minutes,
                start=args.start,
                end=args.end,
            )
        except ValueError:
            return None
        schedule_label = _triggers_label([trigger])

    # Multi-line preview the frontend can render as a structured card.
    # Order is intentional: name first, then schedule (what people scan
    # for), then auto-approve warning, then the full prompt body so reviewers
    # can read what'll actually run without expanding anything.
    lines: list[str] = [f"Name: {args.name}", f"Schedule: {schedule_label}"]
    if next_run:
        lines.append(f"Next run: {format_timestamp(next_run)}")
    if args.model:
        lines.append(f"Model: {args.model}")
    if args.auto_approve:
        lines.append("Auto-approve: yes (autonomous writes, skips approvals)")
    lines.append(f"Tools: {', '.join(args.tool_scope)}" if args.tool_scope else "Tools: read-only (no scope grant)")
    if args.thread_id:
        mode = "iteration" if args.read_history else "post"
        lines.append(f"Target session: {args.thread_id} ({mode} mode)")
    try:
        inferred_parent, _ = await _resolve_parent_context(execution, args.parent_automation_id, args.idempotency_scope)
        parent_conflict: str | None = None
    except ValueError as exc:
        # Resolver failure means run/attempt scope with a missing parent. Still
        # show the would-be parent on the card so the reviewer can fix it.
        inferred_parent = args.parent_automation_id or execution.ctx.run.loop_task_id
        parent_conflict = str(exc)
    if inferred_parent:
        lines.append(f"Parent: {inferred_parent}")
    if parent_conflict:
        lines.append(f"Parent {inferred_parent!r} missing — will fail on execute")
    if args.idempotency_scope:
        key = args.idempotency_key or "(unset)"
        lines.append(f"Idempotency: {args.idempotency_scope} · key={key}")
    lines.append("")
    lines.append("Prompt:")
    lines.append(args.prompt)

    return ApprovalInfo(
        description=f"Create automation: {args.name}",
        preview="\n".join(lines),
        diff=None,
    )


def _tool_scope_error(execution: ToolExecution, patterns: list[str]) -> ToolResult | None:
    """A scope pattern that matches no registered tool is a typo, not a grant.
    Fail loudly with candidates instead of storing an allowlist that silently
    strips the automation of the tool its author meant."""
    names = tuple(execution.ctx.registry.tools)
    bad = [p for p in patterns if not any(matches_scope((p,), name) for name in names)]
    if not bad:
        return None
    hints = []
    for pattern in bad:
        close = get_close_matches(pattern.rstrip("*"), names, n=3, cutoff=0.5)
        hints.append(f"'{pattern}'" + (f" (did you mean: {', '.join(close)}?)" if close else ""))
    return ToolResult.failure(
        code="invalid_arguments",
        message=f"tool_scope patterns match no registered tool: {'; '.join(hints)}",
        preview="Unknown tools in scope",
        recovery_action="Use exact registered tool names or prefix patterns like 'slack_*', then retry.",
    )


async def create_automation(execution: ToolExecution, args: CreateAutomationInput) -> ToolResult:
    svc = execution.ctx.services["automation"]
    if args.tool_scope and (scope_error := _tool_scope_error(execution, args.tool_scope)):
        return scope_error
    try:
        parent_automation_id, parent_fire_at = await _resolve_parent_context(
            execution, args.parent_automation_id, args.idempotency_scope
        )
    except ValueError as e:
        return ToolResult.failure(
            code="invalid_arguments",
            message=str(e),
            preview="Invalid automation",
            recovery_action="Correct the parent or idempotency fields and retry.",
        )
    message_triggers: list[dict] | None = None
    if args.trigger_type == "message":
        message_trigger: dict = {"type": "message", "source": "slack", "channels": args.channels or []}
        if args.from_user:
            message_trigger["from_user"] = args.from_user
        if args.contains:
            message_trigger["contains"] = args.contains
        message_triggers = [message_trigger]
    # Automations created from inside an Area (an acting Custodian, or the
    # room's assistant) belong to that Area: namespaced id + room channel.
    area = execution.ctx.area
    try:
        automation = await svc.create(
            name=args.name,
            prompt=args.prompt,
            description=args.description,
            area_id=area.area_id if area is not None else None,
            trigger_type=args.trigger_type,
            triggers=message_triggers,
            at=args.at,
            days=args.days,
            every=args.every,
            event_type=args.event_type,
            lead_minutes=args.lead_minutes,
            auto_approve=args.auto_approve,
            start=args.start,
            end=args.end,
            model=args.model,
            thread_id=args.thread_id,
            read_history=args.read_history,
            parent_automation_id=parent_automation_id,
            idempotency_key=args.idempotency_key,
            idempotency_scope=args.idempotency_scope,
            parent_fire_at=parent_fire_at,
            tool_scope=args.tool_scope,
        )
    except ValueError as e:
        return ToolResult.failure(
            code="invalid_arguments",
            message=str(e),
            preview="Invalid automation",
            recovery_action="Correct the schedule, trigger, or target fields and retry.",
        )

    if automation is None:
        return ToolResult(
            content=f"Skipped (idempotency claim conflict): key={args.idempotency_key}",
            preview="Skipped (idempotent)",
        )

    lines = [
        f"Created automation: {_automation_label(automation)}",
        f"ID: {automation.task_id}",
        f"Trigger: {_triggers_label(automation.triggers)}",
    ]
    if automation.model:
        lines.append(f"Model: {automation.model}")
    if automation.thread_id:
        mode = "iteration" if automation.read_history else "post"
        lines.append(f"Target session: {automation.thread_id} ({mode} mode)")
    if automation.parent_automation_id:
        lines.append(f"Parent: {automation.parent_automation_id}")
    if automation.next_run_at:
        lines.append(f"Next run: {format_timestamp(automation.next_run_at)}")

    return ToolResult(content="\n".join(lines), preview=f"Created ({automation.task_id})")


async def list_automations(execution: ToolExecution, args: EmptyInput) -> ToolResult:
    automations = await execution.ctx.services["automation"].list_all()
    if not automations:
        return ToolResult(content="No automations.", preview="0 automations")

    content = _format_automation_list(automations)
    return ToolResult(content=content, preview=f"{len(automations)} automations")


async def approve_update_automation(execution: ToolExecution, args: UpdateAutomationInput) -> ApprovalInfo | None:
    try:
        automation = await execution.ctx.services["automation"].get(args.task_id)
    except KeyError:
        return None

    changes = []
    fields = {
        "name": args.name,
        "description": args.description,
        "prompt": args.prompt,
        "enabled": args.enabled,
        "auto_approve": args.auto_approve,
        "tool_scope": args.tool_scope,
        "model": args.model,
        "trigger_type": args.trigger_type,
        "at": args.at,
        "days": args.days,
        "every": args.every,
        "event_type": args.event_type,
        "lead_minutes": args.lead_minutes,
        "channels": args.channels,
        "from_user": args.from_user,
        "contains": args.contains,
        "start": args.start,
        "end": args.end,
    }
    for key, value in fields.items():
        if value is not None:
            changes.append(f"{key}: {value}")

    label = _automation_label(automation)
    return ApprovalInfo(
        description=f"Update: {label} ({args.task_id})",
        preview="\n".join(changes) if changes else "No changes",
        diff=None,
    )


async def update_automation(execution: ToolExecution, args: UpdateAutomationInput) -> ToolResult:
    if args.tool_scope and (scope_error := _tool_scope_error(execution, args.tool_scope)):
        return scope_error
    message_triggers: list[dict] | None = None
    if args.trigger_type == "message":
        message_trigger: dict = {"type": "message", "source": "slack", "channels": args.channels or []}
        if args.from_user:
            message_trigger["from_user"] = args.from_user
        if args.contains:
            message_trigger["contains"] = args.contains
        message_triggers = [message_trigger]
    try:
        automation_service = execution.ctx.services["automation"]
        automation = await automation_service.update(
            args.task_id,
            name=args.name,
            description=args.description,
            prompt=args.prompt,
            model=args.model,
            trigger_type=args.trigger_type,
            triggers=message_triggers,
            at=args.at,
            days=args.days,
            every=args.every,
            event_type=args.event_type,
            lead_minutes=args.lead_minutes,
            start=args.start,
            end=args.end,
            auto_approve=args.auto_approve,
            enabled=args.enabled,
            tool_scope=args.tool_scope,
        )
    except KeyError:
        return _automation_not_found(args.task_id)
    except ValueError as e:
        return ToolResult.failure(
            code="invalid_arguments",
            message=str(e),
            preview="Invalid update",
            recovery_action="Call list_automations, inspect the current trigger, and retry with valid fields.",
        )

    label = _automation_label(automation)
    lines = [
        f"Updated automation: {label}",
        f"ID: {automation.task_id}",
        f"Trigger: {_triggers_label(automation.triggers)}",
        f"Enabled: {automation.enabled}",
    ]
    if automation.next_run_at:
        lines.append(f"Next run: {format_timestamp(automation.next_run_at)}")

    return ToolResult(content="\n".join(lines), preview=f"Updated ({automation.task_id})")


async def approve_delete_automation(execution: ToolExecution, args: DeleteAutomationInput) -> ApprovalInfo | None:
    try:
        automation = await execution.ctx.services["automation"].get(args.task_id)
    except KeyError:
        return None
    return ApprovalInfo(
        description=f"Delete automation {args.task_id}",
        preview=f"Name: {_automation_label(automation)}\nPrompt: {automation.prompt}",
        diff=f"- automation {args.task_id}",
    )


async def delete_automation(execution: ToolExecution, args: DeleteAutomationInput) -> ToolResult:
    try:
        automation = await execution.ctx.services["automation"].get(args.task_id)
        await execution.ctx.services["automation"].delete(args.task_id)
    except KeyError:
        return _automation_not_found(args.task_id)
    except ValueError:
        return ToolResult.failure(
            code="write_conflict",
            message="The automation could not be deleted in its current state.",
            preview="Cannot delete",
            recovery_action="Call list_automations and retry with a current automation ID.",
        )

    return ToolResult(content=f"Deleted: {_automation_label(automation)} ({args.task_id})", preview="Deleted")


async def get_automation_result(execution: ToolExecution, args: GetAutomationResultInput) -> ToolResult:
    try:
        automation = await execution.ctx.services["automation"].get(args.task_id)
    except KeyError:
        return _automation_not_found(args.task_id)

    if not automation.last_result:
        last_run = format_timestamp(automation.last_run_at) if automation.last_run_at else "never"
        return ToolResult(
            content=f"No result yet for '{_automation_label(automation)}' (last run: {last_run})",
            preview="No result",
        )

    header = (
        f"Automation: {_automation_label(automation)}\n"
        f"Last run: {format_timestamp(automation.last_run_at) if automation.last_run_at else '—'}\n"
        f"---\n"
    )
    return ToolResult(content=header + automation.last_result, preview=f"Result ({automation.task_id})")


async def approve_run_automation(execution: ToolExecution, args: RunAutomationInput) -> ApprovalInfo | None:
    try:
        automation = await execution.ctx.services["automation"].get(args.task_id)
    except KeyError:
        return None
    return ApprovalInfo(
        description=f"Run now: {_automation_label(automation)}",
        preview=f"Automation: {args.task_id}\nPrompt: {automation.prompt}",
        diff=None,
    )


async def run_automation(execution: ToolExecution, args: RunAutomationInput) -> ToolResult:
    try:
        await execution.ctx.services["automation"].run_now(args.task_id)
    except KeyError:
        return _automation_not_found(args.task_id)
    except RuntimeError:
        return ToolResult.failure(
            code="temporarily_unavailable",
            message="The automation could not be started.",
            preview="Unavailable",
            retryable=True,
            recovery_action="Check get_automation_result or list_automations before retrying.",
        )

    return ToolResult(
        content=f"Automation {args.task_id} started. Use get_automation_result to check the outcome.",
        preview="Started",
    )


create_automation_tool = tool(
    display_name="CreateAutomation",
    display_description="Create a task that runs automatically on a schedule or event.",
    description=CREATE_AUTOMATION_DESCRIPTION,
    input_model=CreateAutomationInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        permissions=frozenset({"automation"}),
    ),
    approval=approve_create_automation,
    execute=create_automation,
)

list_automations_tool = tool(
    display_name="ListAutomations",
    display_description="List configured automations.",
    description=LIST_AUTOMATIONS_DESCRIPTION,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, permissions=frozenset({"automation"})),
    execute=list_automations,
)

update_automation_tool = tool(
    display_name="UpdateAutomation",
    display_description="Change an automation's trigger, model, permissions, or status.",
    description=UPDATE_AUTOMATION_DESCRIPTION,
    input_model=UpdateAutomationInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        permissions=frozenset({"automation"}),
    ),
    approval=approve_update_automation,
    execute=update_automation,
)

delete_automation_tool = tool(
    display_name="DeleteAutomation",
    display_description="Delete an automation.",
    description=DELETE_AUTOMATION_DESCRIPTION,
    input_model=DeleteAutomationInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        permissions=frozenset({"automation"}),
    ),
    approval=approve_delete_automation,
    execute=delete_automation,
)

get_automation_result_tool = tool(
    display_name="AutomationResult",
    description=GET_AUTOMATION_RESULT_DESCRIPTION,
    input_model=GetAutomationResultInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, permissions=frozenset({"automation"})),
    execute=get_automation_result,
)

run_automation_tool = tool(
    display_name="RunAutomation",
    display_description="Run an automation now.",
    description=RUN_AUTOMATION_DESCRIPTION,
    input_model=RunAutomationInput,
    policy=ToolPolicy(
        action=ToolAction.EXECUTE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        permissions=frozenset({"automation"}),
    ),
    approval=approve_run_automation,
    execute=run_automation,
)


# --- Self-paced loop tools ---

SCHEDULE_WAKEUP_DESCRIPTION = (
    "Self-paced loop control: schedule the next iteration of THIS loop. "
    "Only usable when the current run was triggered by a loop. "
    "Pass delay_seconds (minimum 60). Use this to back off when the watched condition "
    "isn't ready yet (e.g. 'check again in 5 minutes') or to poll more aggressively "
    "near a transition. If you call this multiple times in one iteration the last "
    "call wins."
)

LOOP_DONE_DESCRIPTION = (
    "Self-paced loop control: mark THIS loop as done and stop further iterations. "
    "Only usable when the current run was triggered by a loop. "
    "Call this when the loop's goal has been reached (e.g. CI turned green, deploy "
    "succeeded, condition met). Pass a short reason so the user understands why "
    "the loop stopped."
)


class ScheduleWakeupInput(BaseModel):
    delay_seconds: int = Field(
        description="Seconds until the next iteration. Minimum 60.",
        ge=60,
    )


class LoopDoneInput(BaseModel):
    reason: str = Field(description="Why the loop is stopping. Shown to the user.")


def _loop_task_id_or_error(execution: ToolExecution) -> tuple[str | None, ToolResult | None]:
    task_id = execution.ctx.run.loop_task_id
    if not task_id:
        return None, ToolResult.failure(
            code="invalid_context",
            message="This tool is only available inside a loop iteration.",
            preview="Not a loop",
            recovery_action="Call create_loop from an active chat instead.",
        )
    return task_id, None


async def schedule_wakeup(execution: ToolExecution, args: ScheduleWakeupInput) -> ToolResult:
    task_id, err = _loop_task_id_or_error(execution)
    if err:
        return err
    svc = execution.ctx.services.get("automation")
    if svc is None:
        return _automation_unavailable()
    next_run = datetime.now(UTC) + timedelta(seconds=args.delay_seconds)
    await svc.store.set_next_run(task_id, next_run)
    return ToolResult(
        content=f"Next iteration scheduled in {args.delay_seconds}s ({format_timestamp(next_run)}).",
        preview=f"Wake in {args.delay_seconds}s",
    )


async def approve_schedule_wakeup(_execution: ToolExecution, args: ScheduleWakeupInput) -> ApprovalInfo:
    return ApprovalInfo(
        description="Schedule the loop's next iteration",
        preview=f"Delay: {args.delay_seconds} seconds",
        diff=None,
    )


async def loop_done(execution: ToolExecution, args: LoopDoneInput) -> ToolResult:
    task_id, err = _loop_task_id_or_error(execution)
    if err:
        return err
    svc = execution.ctx.services.get("automation")
    if svc is None:
        return _automation_unavailable()
    await svc.store.set_enabled(task_id, False)
    return ToolResult(
        content=f"Loop stopped: {args.reason}",
        preview="Loop done",
    )


async def approve_loop_done(_execution: ToolExecution, args: LoopDoneInput) -> ApprovalInfo:
    return ApprovalInfo(description="Stop the current loop", preview=args.reason[:1_500], diff=None)


schedule_wakeup_tool = tool(
    display_name="ScheduleWakeup",
    display_description="Schedule the current loop's next iteration.",
    description=SCHEDULE_WAKEUP_DESCRIPTION,
    input_model=ScheduleWakeupInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        permissions=frozenset({"automation"}),
    ),
    approval=approve_schedule_wakeup,
    execute=schedule_wakeup,
)

loop_done_tool = tool(
    display_name="LoopDone",
    display_description="Stop the current loop after its goal has been reached.",
    description=LOOP_DONE_DESCRIPTION,
    input_model=LoopDoneInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        permissions=frozenset({"automation"}),
    ),
    approval=approve_loop_done,
    execute=loop_done,
)


# --- create_loop ---

CREATE_LOOP_DESCRIPTION = (
    "Create a loop — a repeating prompt scoped to the CURRENT chat session. "
    "Each iteration posts the prompt as a new user turn in this chat. "
    "Use this when the user wants to monitor or babysit something on a cadence: "
    "'watch CI', 'check the deploy every 5 minutes', 'keep an eye on X'. "
    "Two modes: "
    "(a) fixed interval: pass 'every' (e.g. '5m', '1h', '2h30m'). "
    "(b) self-paced: pass 'every' as the initial cadence; inside each iteration, "
    "the agent calls schedule_wakeup to adjust the next interval, or loop_done "
    "to terminate when the goal is reached. "
    "Optional max_iterations caps the loop. "
    "Optional stop_when is a natural-language predicate the agent checks each iteration."
    f" {SPAWN_SURFACE_GUIDANCE}"
)


class CreateLoopInput(BaseModel):
    prompt: str = Field(
        description="What the loop should do on each iteration. Posted as a user message into this chat. Stand-alone.",
        min_length=1,
    )
    every: str = Field(
        description="Initial interval between iterations: '5m', '30m', '1h', '2h30m', '1d'. Minimum 1 minute.",
        min_length=1,
    )
    max_iterations: int | None = Field(
        default=None,
        description="Optional hard cap on how many times the loop runs.",
        ge=1,
    )
    stop_when: str | None = Field(
        default=None,
        description="Optional natural-language predicate. Each iteration checks if this is met; if so, call loop_done.",
    )
    max_age_days: int | None = Field(
        default=None,
        description="Optional hard cap in days from creation. After this many days, the loop disables itself on the next fire even if max_iterations hasn't been hit.",
        ge=1,
    )
    parent_automation_id: str | None = Field(
        default=None,
        description="Explicit parent lineage. Defaults to the calling loop's task_id if invoked inside a loop iteration.",
    )
    idempotency_key: str | None = Field(
        default=None,
        description="Optional dedupe key. Pair with idempotency_scope.",
    )
    idempotency_scope: Literal["run", "attempt", "global"] | None = Field(
        default=None,
        description="Scope for idempotency_key: 'global', 'run', or 'attempt'.",
    )
    attempt_n: int | None = Field(
        default=None,
        description="For idempotency_scope='attempt': retry attempt number.",
        ge=0,
    )


async def approve_create_loop(execution: ToolExecution, args: CreateLoopInput) -> ApprovalInfo | None:
    lines = [f"Every: {args.every}", ""]
    if args.max_iterations:
        lines.insert(1, f"Max iterations: {args.max_iterations}")
    if args.max_age_days:
        lines.insert(-1, f"Auto-expires: after {args.max_age_days} day(s)")
    if args.stop_when:
        lines.insert(-1, f"Stop when: {args.stop_when}")
    try:
        inferred_parent, _ = await _resolve_parent_context(execution, args.parent_automation_id, args.idempotency_scope)
        parent_conflict: str | None = None
    except ValueError as exc:
        inferred_parent = args.parent_automation_id or execution.ctx.run.loop_task_id
        parent_conflict = str(exc)
    if inferred_parent:
        lines.insert(-1, f"Parent: {inferred_parent}")
    if parent_conflict:
        lines.insert(-1, f"Parent {inferred_parent!r} missing — will fail on execute")
    if args.idempotency_scope:
        key = args.idempotency_key or "(unset)"
        lines.insert(-1, f"Idempotency: {args.idempotency_scope} · key={key}")
    lines.append("Prompt:")
    lines.append(args.prompt)
    return ApprovalInfo(
        description="Create loop in this chat",
        preview="\n".join(lines),
        diff=None,
    )


async def create_loop(execution: ToolExecution, args: CreateLoopInput) -> ToolResult:
    session_id = execution.ctx.session_id
    if not session_id:
        return ToolResult.failure(
            code="invalid_context",
            message="No active session is available for the loop.",
            preview="No session",
            recovery_action="Create the loop from an active chat session.",
        )
    svc = execution.ctx.services.get("automation")
    if svc is None:
        return _automation_unavailable()

    try:
        parent_automation_id, parent_fire_at = await _resolve_parent_context(
            execution, args.parent_automation_id, args.idempotency_scope
        )
    except ValueError as e:
        return ToolResult.failure(
            code="invalid_arguments",
            message=str(e),
            preview="Invalid loop",
            recovery_action="Correct the parent or idempotency fields and retry.",
        )
    try:
        loop = await svc.create_loop(
            session_id=session_id,
            prompt=args.prompt,
            every=args.every,
            max_iterations=args.max_iterations,
            stop_when=args.stop_when,
            max_age_days=args.max_age_days,
            parent_automation_id=parent_automation_id,
            idempotency_key=args.idempotency_key,
            idempotency_scope=args.idempotency_scope,
            parent_fire_at=parent_fire_at,
            attempt_n=args.attempt_n,
        )
    except ValueError as e:
        return ToolResult.failure(
            code="invalid_arguments",
            message=str(e),
            preview="Invalid loop",
            recovery_action="Correct the interval, limits, or stop condition and retry.",
        )

    if loop is None:
        return ToolResult(
            content=f"Skipped (idempotency claim conflict): key={args.idempotency_key}",
            preview="Skipped (idempotent)",
        )

    lines = [
        f"Created loop {loop.task_id}",
        f"Every: {args.every}",
        f"Prompt: {loop.prompt}",
    ]
    if loop.max_iterations:
        lines.append(f"Max iterations: {loop.max_iterations}")
    if loop.max_age_days:
        lines.append(f"Auto-expires: after {loop.max_age_days} day(s)")
    if loop.stop_when:
        lines.append(f"Stop when: {loop.stop_when}")
    if loop.parent_automation_id:
        lines.append(f"Parent: {loop.parent_automation_id}")
    if loop.next_run_at:
        lines.append(f"First run: {format_timestamp(loop.next_run_at)}")
    return ToolResult(content="\n".join(lines), preview=f"Loop · every {args.every}")


create_loop_tool = tool(
    display_name="CreateLoop",
    display_description="Create repeated work that stops when a condition is met.",
    description=CREATE_LOOP_DESCRIPTION,
    input_model=CreateLoopInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        permissions=frozenset({"automation"}),
    ),
    approval=approve_create_loop,
    execute=create_loop,
)
