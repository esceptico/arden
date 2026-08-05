from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from arden.automation.models import Automation
from arden.automation.triggers import build_trigger
from arden.core.agent_types import SPAWN_SURFACE_GUIDANCE
from arden.events.triggers import EVENT_APPROACHING
from arden.tools.core import EmptyInput, ToolResult, tool
from arden.tools.core.collections import format_timestamp
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope

ALL_TOOLS_DESCRIPTION = (
    "Whether the runs may use every tool, or only read-only ones. Default false: the runs read but "
    "never write or execute. Set true only when the prompt genuinely needs to act — approvals still "
    "apply to each consequential call."
)

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
    "Each automation gets its own dedicated channel for results; use loop_create to repeat work in this chat. "
    "Runs are read-only by default. Set all_tools=true when the prompt must act; auto_approve=true only skips "
    "approvals, it never widens what the run may use. "
    "Always provide one stable idempotency_key and reuse it unchanged after an ambiguous retry; standalone "
    "automations use idempotency_scope='global'."
    f" {SPAWN_SURFACE_GUIDANCE}"
)

LIST_AUTOMATIONS_DESCRIPTION = "List all automations by stable ref with their trigger, status, and next run."

LIST_AUTOMATION_RUNS_DESCRIPTION = (
    "List automation runs since an inclusive timestamp. Returns the configured trigger, status, result, and channel "
    "for understanding what ran and why. Read a returned channel when its run needs more context."
)

UPDATE_AUTOMATION_DESCRIPTION = (
    "Update an existing automation. Only provide the fields you want to change. "
    "Use automation_list to find refs. "
    "Trigger fields (trigger_type, at, days, every, event_type, lead_minutes, start, end, and for "
    "trigger_type='message': channels, from_user, contains) are merged with the current trigger — only "
    "provide what should change. "
    "Set enabled=false to pause or enabled=true to resume."
)

DELETE_AUTOMATION_DESCRIPTION = "Delete an automation by its exact ref. Use automation_list to find refs."

GET_AUTOMATION_RESULT_DESCRIPTION = "Get the last execution result of an automation by its exact ref."

RUN_AUTOMATION_DESCRIPTION = (
    "Trigger an immediate execution of an automation. "
    "The automation runs in the background — use automation_result to check the outcome. "
    "Use automation_list to find refs."
)


# --- Helpers ---


def _triggers_label(triggers: list) -> str:
    return " | ".join(t.label for t in triggers)


def _automation_not_found(automation_ref: str) -> ToolResult:
    return ToolResult.failure(
        code="not_found",
        message=f"Automation not found: {automation_ref}",
        preview="Not found",
        recovery_action="Call automation_list and retry with an exact returned automation_ref.",
    )


def _automation_unavailable() -> ToolResult:
    return ToolResult.failure(
        code="not_configured",
        message="Automation service unavailable.",
        preview="Unavailable",
        recovery_action="Enable the automation service before retrying.",
    )


class AutomationListRunsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    since: AwareDatetime = Field(description="Inclusive ISO timestamp, usually today's local midnight.")
    automation_ref: str | None = Field(default=None, min_length=1, max_length=200)
    offset: int = Field(default=0, ge=0, le=10_000)
    limit: int = Field(default=100, ge=1, le=200)


async def _resolve_parent_context(
    execution: "ToolExecution",
    explicit_parent_ref: str | None,
    idempotency_scope: str | None,
) -> tuple[Automation | None, str | None]:
    """Resolve an explicit public ref or the current loop's internal owner; pull
    parent_fire_at from that parent's last_run_at when run/attempt scopes
    need it but the caller didn't supply one.

    Raises ValueError when run/attempt scope is requested but the parent
    cannot be resolved — silently collapsing to global scope would break
    the agent's idempotency intent without any signal.
    """
    svc = execution.ctx.services.get("automation")
    if explicit_parent_ref is not None:
        if svc is None:
            raise ValueError("automation service is required to resolve parent_automation_ref")
        try:
            parent = await svc.get_by_ref(explicit_parent_ref)
        except KeyError as exc:
            raise ValueError(f"parent_automation_ref {explicit_parent_ref!r} was not found") from exc
    elif execution.ctx.run.loop_task_id is not None:
        if svc is None:
            raise ValueError("automation service is required to resolve the current loop parent")
        try:
            parent = await svc.get(execution.ctx.run.loop_task_id)
        except KeyError as exc:
            raise ValueError("the current loop parent is unavailable") from exc
    else:
        parent = None
    if parent is None or idempotency_scope not in {"run", "attempt"}:
        return parent, None
    fire_at = format_timestamp(parent.last_run_at) if parent.last_run_at else None
    return parent, fire_at


def _format_automation_list(automations: list[Automation]) -> str:
    lines = []
    for a in sorted(automations, key=lambda item: item.automation_ref):
        status = "enabled" if a.enabled else "disabled"
        next_run = format_timestamp(a.next_run_at) if a.next_run_at else "—"
        last_run = format_timestamp(a.last_run_at) if a.last_run_at else "never"
        label = _automation_label(a)
        builtin_tag = " [builtin]" if a.builtin else ""

        lines.append(
            f"[{a.automation_ref}] {label}{builtin_tag}\n"
            f"  {_triggers_label(a.triggers)} · {status}\n"
            f"  next: {next_run} · last: {last_run}" + (f"\n  model: {a.model}" if a.model else "")
        )
    return "\n\n".join(lines)


def _automation_label(automation: Automation) -> str:
    """Never promote executable prompt text into a display label."""
    return automation.name


# --- Input Models ---


class AutomationCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
            "Skip per-tool approvals. This never widens the toolset: without all_tools the automation stays read-only."
        ),
    )
    all_tools: bool = Field(default=False, description=ALL_TOOLS_DESCRIPTION)
    parent_automation_ref: str | None = Field(
        default=None,
        description=(
            "Exact parent ref from automation_list. Defaults to the current loop when "
            "this tool is called from inside a loop iteration."
        ),
    )
    idempotency_key: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "Required stable creation key. Reuse the exact key after an ambiguous retry; "
            "do not version or rotate it to bypass an existing automation."
        ),
    )
    idempotency_scope: Literal["run", "attempt", "global"] = Field(
        default="global",
        description=(
            "Scope for the idempotency claim. Use 'global' for standalone automations; "
            "'run' / 'attempt' are scoped to the calling automation's fire."
        ),
    )
    attempt_n: int | None = Field(
        default=None,
        description="Required retry-attempt number when idempotency_scope='attempt'.",
        ge=0,
    )

    @model_validator(mode="after")
    def validate_attempt_scope(self):
        if self.idempotency_scope == "attempt" and self.attempt_n is None:
            raise ValueError("attempt_n is required when idempotency_scope='attempt'")
        if self.idempotency_scope != "attempt" and self.attempt_n is not None:
            raise ValueError("attempt_n is only valid when idempotency_scope='attempt'")
        return self


class AutomationUpdateInput(BaseModel):
    # Without this a stale field name (the removed `tool_scope`) is accepted and
    # ignored, so the model believes it set something it did not.
    model_config = ConfigDict(extra="forbid")

    automation_ref: str = Field(description="Exact automation_ref returned by automation_list")
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
        description="Skip per-tool approvals. This never widens the toolset.",
    )
    all_tools: bool | None = Field(default=None, description=ALL_TOOLS_DESCRIPTION)
    enabled: bool | None = Field(default=None, description="Enable or disable the automation")


class AutomationDeleteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    automation_ref: str = Field(description="Exact automation_ref returned by automation_list")


class AutomationResultInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    automation_ref: str = Field(description="Exact automation_ref returned by automation_list")


class AutomationRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    automation_ref: str = Field(description="Exact automation_ref returned by automation_list")


# --- Tools ---


async def approve_automation_create(execution: ToolExecution, args: AutomationCreateInput) -> ApprovalInfo | None:
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
        lines.append("Auto-approve: yes (skips approval for granted tools; does not grant access)")
    lines.append("Tools: every tool" if args.all_tools else "Tools: read-only")
    lines.append("Results: dedicated automation channel")
    try:
        inferred_parent, _ = await _resolve_parent_context(
            execution, args.parent_automation_ref, args.idempotency_scope
        )
        parent_conflict: str | None = None
    except ValueError as exc:
        inferred_parent = None
        parent_conflict = str(exc)
    if inferred_parent:
        lines.append(f"Parent: {inferred_parent.automation_ref}")
    if parent_conflict:
        lines.append(f"Parent invalid: {parent_conflict}")
    if args.idempotency_scope:
        attempt = f" · attempt={args.attempt_n}" if args.attempt_n is not None else ""
        lines.append(f"Idempotency: {args.idempotency_scope} · key={args.idempotency_key}{attempt}")
    lines.append("")
    lines.append("Prompt:")
    lines.append(args.prompt)

    return ApprovalInfo(
        description=f"Create automation: {args.name}",
        preview="\n".join(lines),
        diff=None,
    )


async def automation_create(execution: ToolExecution, args: AutomationCreateInput) -> ToolResult:
    svc = execution.ctx.services["automation"]
    try:
        parent, parent_fire_at = await _resolve_parent_context(
            execution, args.parent_automation_ref, args.idempotency_scope
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
            parent_automation_id=parent.task_id if parent is not None else None,
            idempotency_key=args.idempotency_key,
            idempotency_scope=args.idempotency_scope,
            parent_fire_at=parent_fire_at,
            attempt_n=args.attempt_n,
            tool_scope="all" if args.all_tools else "read_only",
        )
    except ValueError as e:
        return ToolResult.failure(
            code="invalid_arguments",
            message=str(e),
            preview="Invalid automation",
            recovery_action="Correct the schedule, trigger, or target fields and retry.",
        )

    if automation is None:
        task_id = svc.idempotent_task_id(
            args.idempotency_scope,
            args.idempotency_key,
            parent.task_id if parent is not None else None,
            parent_fire_at,
            args.attempt_n,
        )
        if area is not None:
            task_id = f"area:{area.area_id}:{task_id}"
        existing = await svc.store.get(task_id)
        if existing is not None:
            lines = [
                f"Automation already exists: {_automation_label(existing)}",
                f"Ref: {existing.automation_ref}",
            ]
            lines.append("No changes made. Reuse this ref; call automation_update to change it.")
            return ToolResult(content="\n".join(lines), preview=f"Already exists ({existing.automation_ref})")
        return ToolResult.failure(
            code="write_conflict",
            message=(
                f"Idempotency key {args.idempotency_key!r} is already claimed, but its automation is unavailable."
            ),
            preview="Idempotency conflict",
            recovery_action="Call automation_list, then retry once with the same key; do not rotate the key.",
        )

    lines = [
        f"Created automation: {_automation_label(automation)}",
        f"Ref: {automation.automation_ref}",
        f"Trigger: {_triggers_label(automation.triggers)}",
    ]
    if automation.model:
        lines.append(f"Model: {automation.model}")
    if automation.parent_automation_id:
        if parent is None:
            raise RuntimeError("created automation lost its resolved parent")
        lines.append(f"Parent: {parent.automation_ref}")
    if automation.next_run_at:
        lines.append(f"Next run: {format_timestamp(automation.next_run_at)}")

    return ToolResult(content="\n".join(lines), preview=f"Created ({automation.automation_ref})")


async def automation_list(execution: ToolExecution, args: EmptyInput) -> ToolResult:
    automations = await execution.ctx.services["automation"].list_all()
    if not automations:
        return ToolResult(content="No automations.", preview="0 automations")

    content = _format_automation_list(automations)
    return ToolResult(content=content, preview=f"{len(automations)} automations")


async def automation_list_runs(execution: ToolExecution, args: AutomationListRunsInput) -> ToolResult:
    service = execution.ctx.services.get("automation")
    if service is None:
        return _automation_unavailable()
    if args.automation_ref is not None:
        try:
            await service.get_by_ref(args.automation_ref)
        except KeyError:
            return _automation_not_found(args.automation_ref)
    automations = {automation.automation_ref: automation for automation in await service.list_all()}
    rows = await service.store.list_runs_since(
        args.since,
        automation_ref=args.automation_ref,
        limit=args.limit + 1,
        offset=args.offset,
    )
    has_more = len(rows) > args.limit
    visible = rows[: args.limit]
    entries = []
    lines = []
    for row in visible:
        automation_ref = str(row["automation_ref"])
        automation = automations.get(automation_ref)
        name = _automation_label(automation) if automation is not None else "Deleted automation"
        trigger = _triggers_label(automation.triggers) if automation is not None else "deleted"
        channel = row.get("chat_session_id") or (automation.thread_id if automation is not None else None)
        summary = row.get("error") or row.get("result") or ""
        entries.append(
            {
                "automation_ref": automation_ref,
                "name": name,
                "started_at": row["started_at"],
                "ended_at": row["ended_at"],
                "status": row["status"],
                "configured_trigger": trigger,
                "result": row["result"],
                "error": row["error"],
                "channel": channel,
            }
        )
        suffix = f" - {summary[:300]}" if summary else ""
        channel_text = f" - channel: {channel}" if channel else ""
        lines.append(f"- {row['started_at']} - {name} - {row['status']} - trigger: {trigger}{channel_text}{suffix}")
    if not lines:
        lines.append("No automation runs in this time window.")
    next_offset = args.offset + len(visible)
    if has_more:
        lines.append(f"More runs exist; retry with offset={next_offset}.")
    return ToolResult(
        content="Automation runs\n" + "\n".join(lines),
        preview=f"{len(visible)} automation runs" + (" (capped)" if has_more else ""),
        data={
            "since": args.since.isoformat(),
            "automation_ref": args.automation_ref,
            "offset": args.offset,
            "entries": entries,
            "has_more": has_more,
            "next_offset": next_offset if has_more else None,
        },
    )


async def approve_automation_update(execution: ToolExecution, args: AutomationUpdateInput) -> ApprovalInfo | None:
    try:
        automation = await execution.ctx.services["automation"].get_by_ref(args.automation_ref)
    except KeyError:
        return None

    changes = []
    fields = {
        "name": args.name,
        "description": args.description,
        "prompt": args.prompt,
        "enabled": args.enabled,
        "auto_approve": args.auto_approve,
        "tools": None if args.all_tools is None else ("every tool" if args.all_tools else "read-only"),
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
        description=f"Update: {label} ({args.automation_ref})",
        preview="\n".join(changes) if changes else "No changes",
        diff=None,
    )


async def automation_update(execution: ToolExecution, args: AutomationUpdateInput) -> ToolResult:
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
        current = await automation_service.get_by_ref(args.automation_ref)
        automation = await automation_service.update(
            current.task_id,
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
            # None leaves the scope alone — and a custodian's fine-grained key
            # is never reachable from here, only "all" or "read_only".
            tool_scope=None if args.all_tools is None else ("all" if args.all_tools else "read_only"),
        )
    except KeyError:
        return _automation_not_found(args.automation_ref)
    except ValueError as e:
        return ToolResult.failure(
            code="invalid_arguments",
            message=str(e),
            preview="Invalid update",
            recovery_action="Call automation_list, inspect the current trigger, and retry with valid fields.",
        )

    label = _automation_label(automation)
    lines = [
        f"Updated automation: {label}",
        f"Ref: {automation.automation_ref}",
        f"Trigger: {_triggers_label(automation.triggers)}",
        f"Enabled: {automation.enabled}",
    ]
    if automation.next_run_at:
        lines.append(f"Next run: {format_timestamp(automation.next_run_at)}")

    return ToolResult(content="\n".join(lines), preview=f"Updated ({automation.automation_ref})")


async def approve_automation_delete(execution: ToolExecution, args: AutomationDeleteInput) -> ApprovalInfo | None:
    try:
        automation = await execution.ctx.services["automation"].get_by_ref(args.automation_ref)
    except KeyError:
        return None
    return ApprovalInfo(
        description=f"Delete automation {args.automation_ref}",
        preview=f"Name: {_automation_label(automation)}\nPrompt: {automation.prompt}",
        diff=f"- automation {args.automation_ref}",
    )


async def automation_delete(execution: ToolExecution, args: AutomationDeleteInput) -> ToolResult:
    try:
        automation = await execution.ctx.services["automation"].get_by_ref(args.automation_ref)
        await execution.ctx.services["automation"].delete(automation.task_id)
    except KeyError:
        return _automation_not_found(args.automation_ref)
    except ValueError:
        return ToolResult.failure(
            code="write_conflict",
            message="The automation could not be deleted in its current state.",
            preview="Cannot delete",
            recovery_action="Call automation_list and retry with a current automation_ref.",
        )

    return ToolResult(content=f"Deleted: {_automation_label(automation)} ({args.automation_ref})", preview="Deleted")


async def automation_result(execution: ToolExecution, args: AutomationResultInput) -> ToolResult:
    try:
        automation = await execution.ctx.services["automation"].get_by_ref(args.automation_ref)
    except KeyError:
        return _automation_not_found(args.automation_ref)

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
    return ToolResult(content=header + automation.last_result, preview=f"Result ({automation.automation_ref})")


async def approve_automation_run(execution: ToolExecution, args: AutomationRunInput) -> ApprovalInfo | None:
    try:
        automation = await execution.ctx.services["automation"].get_by_ref(args.automation_ref)
    except KeyError:
        return None
    return ApprovalInfo(
        description=f"Run now: {_automation_label(automation)}",
        preview=f"Automation: {args.automation_ref}\nPrompt: {automation.prompt}",
        diff=None,
    )


async def automation_run(execution: ToolExecution, args: AutomationRunInput) -> ToolResult:
    try:
        service = execution.ctx.services["automation"]
        automation = await service.get_by_ref(args.automation_ref)
        await service.run_now(automation.task_id)
    except KeyError:
        return _automation_not_found(args.automation_ref)
    except RuntimeError:
        return ToolResult.failure(
            code="temporarily_unavailable",
            message="The automation could not be started.",
            preview="Unavailable",
            retryable=True,
            recovery_action="Check automation_result or automation_list before retrying.",
        )

    return ToolResult(
        content=f"Automation {args.automation_ref} started. Use automation_result to check the outcome.",
        preview="Started",
    )


automation_create_tool = tool(
    display_name="CreateAutomation",
    display_description="Create a task that runs automatically on a schedule or event.",
    description=CREATE_AUTOMATION_DESCRIPTION,
    input_model=AutomationCreateInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        permissions=frozenset({"automation"}),
        deferred=True,
        destructive=False,
        open_world=False,
        idempotent=True,
    ),
    approval=approve_automation_create,
    execute=automation_create,
)

automation_list_tool = tool(
    display_name="ListAutomations",
    display_description="List configured automations.",
    description=LIST_AUTOMATIONS_DESCRIPTION,
    policy=ToolPolicy(
        action=ToolAction.READ, scope=ToolScope.INTERNAL, permissions=frozenset({"automation"}), deferred=True
    ),
    execute=automation_list,
)

automation_list_runs_tool = tool(
    display_name="ListAutomationRuns",
    display_description="List recent automation executions.",
    description=LIST_AUTOMATION_RUNS_DESCRIPTION,
    input_model=AutomationListRunsInput,
    policy=ToolPolicy(
        action=ToolAction.READ, scope=ToolScope.INTERNAL, permissions=frozenset({"automation"}), deferred=True
    ),
    execute=automation_list_runs,
)

automation_update_tool = tool(
    display_name="UpdateAutomation",
    display_description="Change an automation's trigger, model, permissions, or status.",
    description=UPDATE_AUTOMATION_DESCRIPTION,
    input_model=AutomationUpdateInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        permissions=frozenset({"automation"}),
        deferred=True,
        destructive=True,
        open_world=False,
        idempotent=False,
    ),
    approval=approve_automation_update,
    execute=automation_update,
)

automation_delete_tool = tool(
    display_name="DeleteAutomation",
    display_description="Delete an automation.",
    description=DELETE_AUTOMATION_DESCRIPTION,
    input_model=AutomationDeleteInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        permissions=frozenset({"automation"}),
        deferred=True,
        destructive=True,
        open_world=False,
        idempotent=True,
    ),
    approval=approve_automation_delete,
    execute=automation_delete,
)

automation_result_tool = tool(
    display_name="AutomationResult",
    description=GET_AUTOMATION_RESULT_DESCRIPTION,
    input_model=AutomationResultInput,
    policy=ToolPolicy(
        action=ToolAction.READ, scope=ToolScope.INTERNAL, permissions=frozenset({"automation"}), deferred=True
    ),
    execute=automation_result,
)

automation_run_tool = tool(
    display_name="RunAutomation",
    display_description="Run an automation now.",
    description=RUN_AUTOMATION_DESCRIPTION,
    input_model=AutomationRunInput,
    policy=ToolPolicy(
        action=ToolAction.EXECUTE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        permissions=frozenset({"automation"}),
        deferred=True,
        destructive=True,
        open_world=True,
        idempotent=False,
    ),
    approval=approve_automation_run,
    execute=automation_run,
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


class LoopScheduleWakeupInput(BaseModel):
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
            recovery_action="Call loop_create from an active chat instead.",
        )
    return task_id, None


async def loop_schedule_wakeup(execution: ToolExecution, args: LoopScheduleWakeupInput) -> ToolResult:
    task_id, err = _loop_task_id_or_error(execution)
    if err:
        return err
    svc = execution.ctx.services.get("automation")
    if svc is None:
        return _automation_unavailable()
    next_run = datetime.now(UTC) + timedelta(seconds=args.delay_seconds)
    if not await svc.scheduler.reschedule_run(task_id, next_run):
        return ToolResult.failure(
            code="automation_unavailable",
            message="The current loop is missing or disabled.",
            preview="Loop unavailable",
            recovery_action="Create or enable the loop before scheduling its next iteration.",
        )
    return ToolResult(
        content=f"Next iteration scheduled in {args.delay_seconds}s ({format_timestamp(next_run)}).",
        preview=f"Wake in {args.delay_seconds}s",
    )


async def approve_loop_schedule_wakeup(_execution: ToolExecution, args: LoopScheduleWakeupInput) -> ApprovalInfo:
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


loop_schedule_wakeup_tool = tool(
    display_name="ScheduleWakeup",
    display_description="Schedule the current loop's next iteration.",
    description=SCHEDULE_WAKEUP_DESCRIPTION,
    input_model=LoopScheduleWakeupInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        permissions=frozenset({"automation"}),
        deferred=True,
        destructive=True,
        open_world=False,
        idempotent=False,
    ),
    approval=approve_loop_schedule_wakeup,
    execute=loop_schedule_wakeup,
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
        deferred=True,
        destructive=True,
        open_world=False,
        idempotent=True,
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
    "the agent calls loop_schedule_wakeup to adjust the next interval, or loop_done "
    "to terminate when the goal is reached. "
    "Optional max_iterations caps the loop. "
    "Optional stop_when is a natural-language predicate the agent checks each iteration."
    f" {SPAWN_SURFACE_GUIDANCE}"
)


class LoopCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    parent_automation_ref: str | None = Field(
        default=None,
        description="Exact parent ref from automation_list. Defaults to the calling loop when invoked inside one.",
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


async def approve_loop_create(execution: ToolExecution, args: LoopCreateInput) -> ApprovalInfo | None:
    lines = [f"Every: {args.every}", ""]
    if args.max_iterations:
        lines.insert(1, f"Max iterations: {args.max_iterations}")
    if args.max_age_days:
        lines.insert(-1, f"Auto-expires: after {args.max_age_days} day(s)")
    if args.stop_when:
        lines.insert(-1, f"Stop when: {args.stop_when}")
    try:
        inferred_parent, _ = await _resolve_parent_context(
            execution, args.parent_automation_ref, args.idempotency_scope
        )
        parent_conflict: str | None = None
    except ValueError as exc:
        inferred_parent = None
        parent_conflict = str(exc)
    if inferred_parent:
        lines.insert(-1, f"Parent: {inferred_parent.automation_ref}")
    if parent_conflict:
        lines.insert(-1, f"Parent invalid: {parent_conflict}")
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


async def loop_create(execution: ToolExecution, args: LoopCreateInput) -> ToolResult:
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
        parent, parent_fire_at = await _resolve_parent_context(
            execution, args.parent_automation_ref, args.idempotency_scope
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
            parent_automation_id=parent.task_id if parent is not None else None,
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
        f"Created loop {loop.automation_ref}",
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
        if parent is None:
            raise RuntimeError("created loop lost its resolved parent")
        lines.append(f"Parent: {parent.automation_ref}")
    if loop.next_run_at:
        lines.append(f"First run: {format_timestamp(loop.next_run_at)}")
    return ToolResult(content="\n".join(lines), preview=f"Loop · every {args.every}")


loop_create_tool = tool(
    display_name="CreateLoop",
    display_description="Create repeated work that stops when a condition is met.",
    description=CREATE_LOOP_DESCRIPTION,
    input_model=LoopCreateInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        permissions=frozenset({"automation"}),
        deferred=True,
        destructive=False,
        open_world=False,
        idempotent=False,
    ),
    approval=approve_loop_create,
    execute=loop_create,
)
