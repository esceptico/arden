"""Tools that drive the Arden app itself rather than the outside world.

`session_send_message` delivers a message to a session — another chat, or an agent this
run spawned; `session_rename` and `session_archive` curate the sidebar;
`app_request_attention` raises a needs-you item on Home; `app_open` takes the
user somewhere in the UI. They exist because the agent can already *read* every
session (`session_list`/`session_read`) but had no way to act on one,
and no way to reach the user outside its own reply.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from arden.areas.agent import NOTIFY_ASK_TTL_HOURS
from arden.areas.asks import nominate_focus
from arden.areas.models import Ask
from arden.events.destinations import (
    AppDestination,
    AreaDestination,
    AutomationDestination,
    HomeDestination,
    MemoryDestination,
    SessionDestination,
    SettingsDestination,
)
from arden.events.sse import AreasChangedEvent, NavigationRequestedEvent
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import APPROVAL_WAIVED, ApprovalInfo, ApprovalWaived, ToolAction, ToolPolicy, ToolScope
from arden.tools.sessions import area_filter, session_ref

_PREVIEW_CHARS = 1_200
_RECENT_ID_HINT_LIMIT = 10


class SessionSendMessageInput(BaseModel):
    session_id: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "Target session id: another chat from session_list, or the "
            "session background() returned for an agent you spawned. Must not be "
            "the session you are running in — use loop_create for another turn here."
        ),
    )
    message: str = Field(
        min_length=1,
        max_length=20_000,
        description=(
            "What to deliver. To a chat: write it as if the user typed it there — it has "
            "none of your context, so state the task, the target and any ids in full. "
            "To a running agent: the steering instruction it reads at its next step."
        ),
    )


class AppFollowupTaskInput(BaseModel):
    session_id: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "Session id of an agent this session spawned — the id research()/background() "
            "returned. Must not be the session you are running in."
        ),
    )
    task: str = Field(
        min_length=1,
        max_length=20_000,
        description=(
            "The new task. The agent keeps its session history, but state the goal, "
            "targets, and any ids in full — it has none of your later context."
        ),
    )


class SessionRenameInput(BaseModel):
    session_id: str = Field(min_length=1, max_length=200, description="Session id from session_list.")
    name: str = Field(
        min_length=1,
        max_length=120,
        description="New human-readable title, 2-6 words, no trailing punctuation.",
    )


class SessionArchiveInput(BaseModel):
    session_ids: list[str] = Field(
        min_length=1,
        max_length=50,
        description=(
            "Session ids from session_list to archive. A single "
            "session is a one-element list; an archival sweep passes the "
            "whole batch at once."
        ),
    )


class AppRequestAttentionInput(BaseModel):
    text: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "One-line headline the user reads on Home. State the thing, not the meta "
            "('Invoice #4021 is 12 days overdue', not 'I found something')."
        ),
    )
    kind: Literal["notify", "question", "review"] = Field(
        description=(
            "notify = FYI, no decision needed (expires quietly after 72h). "
            "question = you are blocked on the user's judgment. "
            "review = you are proposing an action and want approve/reject."
        )
    )
    why_now: str = Field(
        min_length=1,
        max_length=300,
        description="Why this needs the user today rather than whenever. One sentence.",
    )
    what_next: str = Field(
        min_length=1,
        max_length=300,
        description="What happens after they act. One sentence.",
    )
    key: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9][a-z0-9_-]*$",
        description=(
            "Stable slug identifying THIS concern (e.g. 'invoice-4021'). Re-raising the "
            "same key refreshes the item instead of nagging again; a key the user "
            "already decided stays silent."
        ),
    )


class ToolAutomationDestination(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["automation"]
    automation_ref: str | None = Field(default=None, min_length=1, max_length=200)


class ToolAreaDestination(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["area"]
    area_ref: str = Field(min_length=1, max_length=200)


class AreaListInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


ToolAppDestination = Annotated[
    HomeDestination
    | SessionDestination
    | SettingsDestination
    | ToolAutomationDestination
    | MemoryDestination
    | ToolAreaDestination,
    Field(discriminator="kind"),
]


class AppOpenInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination: ToolAppDestination = Field(
        description=(
            'Where to send the user. One of: {"kind":"home"}, '
            '{"kind":"session","session_id":"..."}, {"kind":"settings","tab":"models"}, '
            '{"kind":"automation","automation_ref":"..."}, {"kind":"memory","path":"..."}, '
            '{"kind":"area","area_ref":"..."}. automation_ref, tab and path may be omitted '
            "to open the surface itself; a memory path opens that wiki page."
        )
    )
    label: str = Field(
        min_length=1,
        max_length=160,
        description=(
            "What the user sees if they are not already here — an imperative phrase "
            "naming the destination, e.g. 'Open the Ops area' or 'Review the email "
            "digest automation'."
        ),
    )


async def _unknown_session(execution: ToolExecution, session_id: str) -> ToolResult:
    """Name the sessions that DO exist so a bad id self-corrects in one turn."""
    svc = execution.ctx.services["session"]
    recent = await svc.list_sessions(limit=_RECENT_ID_HINT_LIMIT, area_id=area_filter(execution))
    known = ", ".join(str(row.get("session_id")) for row in recent) or "none"
    return ToolResult.failure(
        code="not_found",
        message=f"No session {session_id}. Recent session ids: {known}.",
        preview="Unknown session",
        recovery_action="Call session_list and retry with an exact session_id.",
    )


async def _unknown_area(execution: ToolExecution, area_ref: str) -> ToolResult:
    areas = await execution.ctx.services["session"].list_areas()
    known = ", ".join(str(area["area_ref"]) for area in areas) or "none"
    return ToolResult.failure(
        code="not_found",
        message=f"No area {area_ref}. Areas: {known}.",
        preview="Unknown area",
        recovery_action="Call area_list and retry with an exact area_ref.",
    )


async def area_list(execution: ToolExecution, _args: AreaListInput) -> ToolResult:
    areas = await execution.ctx.services["session"].list_areas()
    if not areas:
        return ToolResult(content="No Areas.", preview="0 Areas")
    return ToolResult(
        content="\n".join(f"- {area['area_ref']} · {area['name']}" for area in areas),
        preview=f"{len(areas)} Area{'s' if len(areas) != 1 else ''}",
    )


def _archived_session(session_id: str, recovery_action: str) -> ToolResult:
    """An archived session is out of the sidebar, so the user is not watching it.
    Acting on one either lands where nobody looks or drags it back into view."""
    return ToolResult.failure(
        code="conflict",
        message=f"Session {session_id} is archived — the user does not see it in the sidebar.",
        preview="Archived session",
        recovery_action=recovery_action,
    )


async def approve_session_send_message(
    execution: ToolExecution, args: SessionSendMessageInput
) -> ApprovalInfo | ApprovalWaived:
    if execution.ctx.background_tasks.task_for_session(args.session_id) is not None:
        return APPROVAL_WAIVED
    return ApprovalInfo(
        description="Send a prompt to another chat",
        preview=f"To: {args.session_id}\n\n{args.message[:_PREVIEW_CHARS]}",
        diff=None,
    )


async def session_send_message(execution: ToolExecution, args: SessionSendMessageInput) -> ToolResult:
    if args.session_id == execution.ctx.session_id:
        return ToolResult.failure(
            code="invalid_arguments",
            message="session_send_message cannot target its own session.",
            preview="Same session",
            recovery_action=(
                "Use loop_create to give yourself another turn in this chat, or target a different session_id."
            ),
        )

    registry = execution.ctx.background_tasks
    if (task_id := registry.task_for_session(args.session_id)) is not None:
        if registry.queue_steering(task_id, args.message):
            return ToolResult(
                content=f"Queued for the agent in {args.session_id}; it reads this at its next step.",
                preview=f"Steered {args.session_id}",
            )
        # It finished between the approval check and here. Falling through to
        # dispatch would start a fresh run on an approval nobody granted.
        return ToolResult.failure(
            code="conflict",
            message=f"The agent in {args.session_id} finished before the message landed.",
            preview="Agent finished",
            recovery_action=(
                "Its result is delivered automatically; session_read(session_id=...) for detail. "
                "Call session_send_message again only if you want a new run in that session."
            ),
        )

    svc = execution.ctx.services["session"]
    data = await svc.load(args.session_id)
    if data is None:
        return await _unknown_session(execution, args.session_id)
    if await svc.is_archived(args.session_id):
        return _archived_session(
            args.session_id,
            "Ask the user to restore it from Settings → Archive, or target a session that is still in the sidebar.",
        )

    await execution.ctx.services["app_control"].dispatch(
        args.session_id,
        args.message,
        client_id=f"session_send_message:{execution.tool_id}",
    )
    label = data.state.name or args.session_id
    return ToolResult(
        content=(
            f"Delivered to {label} ({args.session_id}). It will be picked up in that "
            "chat's current or next run; the reply appears there, not here."
        ),
        preview=f"Sent to {label}",
        source_refs=(session_ref(args.session_id, label),),
    )


async def approve_app_followup_task(
    execution: ToolExecution, args: AppFollowupTaskInput
) -> ApprovalInfo | ApprovalWaived:
    # Same line send_message draws: queueing into a live agent is free; waking
    # an idle one starts a fresh run — that costs money, so the user decides.
    if execution.ctx.background_tasks.task_for_session(args.session_id) is not None:
        return APPROVAL_WAIVED
    return ApprovalInfo(
        description="Wake an idle agent with a new task",
        preview=f"To: {args.session_id}\n\n{args.task[:_PREVIEW_CHARS]}",
        diff=None,
    )


async def app_followup_task(execution: ToolExecution, args: AppFollowupTaskInput) -> ToolResult:
    ctx = execution.ctx
    if args.session_id == ctx.session_id:
        return ToolResult.failure(
            code="invalid_arguments",
            message="app_followup_task cannot target its own session.",
            preview="Same session",
            recovery_action="Use loop_create for another turn here, or target an agent session you spawned.",
        )

    registry = ctx.background_tasks
    if (task_id := registry.task_for_session(args.session_id)) is not None:
        if registry.queue_followup(task_id, args.task):
            return ToolResult(
                content=(
                    f"Task queued for the running agent in {args.session_id}; it picks it up at its "
                    "next step and its report is delivered here automatically."
                ),
                preview=f"Tasked {args.session_id}",
            )
        # It finished between the approval check and here. Waking it now would
        # start a run on a waiver granted for a queue, not a dispatch.
        return ToolResult.failure(
            code="conflict",
            message=f"The agent in {args.session_id} finished before the task landed.",
            preview="Agent finished",
            recovery_action="Call app_followup_task again — it will wake the idle agent, with the user's approval.",
        )

    svc = ctx.services["session"]
    data = await svc.load(args.session_id)
    if data is None:
        return await _unknown_session(execution, args.session_id)
    if data.state.parent_session_id != ctx.session_id:
        live = registry.live_child_sessions()
        listing = "\n".join(f"- {sid}" for sid in live)
        return ToolResult.failure(
            code="invalid_arguments",
            message=(
                f"{args.session_id} is not an agent of this session."
                + (f" Your live agents:\n{listing}" if live else " You have no live agents.")
            ),
            preview="Not your agent",
            recovery_action=(
                "Target a session id research()/background() returned to you; use session_send_message for any other chat."
            ),
        )
    if await svc.is_archived(args.session_id):
        return _archived_session(
            args.session_id,
            "Spawn a fresh agent instead — background(task=...) or research(task=...).",
        )

    await ctx.services["app_control"].dispatch(
        args.session_id,
        f"<app_followup_task>\n{args.task}\n</app_followup_task>",
        client_id=f"bg:followup:{execution.tool_id}",
    )
    label = data.state.name or args.session_id
    return ToolResult(
        content=(
            f"Woke the idle agent {label} ({args.session_id}) with the task. It runs in its own "
            "session with its prior context; session_read(session_id=...) shows the outcome."
        ),
        preview=f"Woke {label}",
        source_refs=(session_ref(args.session_id, label),),
    )


async def session_rename(execution: ToolExecution, args: SessionRenameInput) -> ToolResult:
    svc = execution.ctx.services["session"]
    data = await svc.load(args.session_id)
    if data is None:
        return await _unknown_session(execution, args.session_id)
    if await svc.is_archived(args.session_id):
        return _archived_session(
            args.session_id,
            "Leave archived chats alone; renaming one would pull it back into the sidebar.",
        )

    previous = data.state.name or "(untitled)"
    await svc.rename(args.session_id, args.name)
    if execution.ctx.run_registry is not None:
        execution.ctx.run_registry.sync_session_name(args.session_id, args.name)
    data.state.name = args.name
    await svc.announce_row(data.state, len(data.messages))
    return ToolResult(
        content=f"Renamed {args.session_id}: {previous} → {args.name}.",
        preview=f"Renamed to {args.name}",
        source_refs=(session_ref(args.session_id, args.name),),
    )


async def session_archive(execution: ToolExecution, args: SessionArchiveInput) -> ToolResult:
    registry = execution.ctx.run_registry
    svc = execution.ctx.services["session"]
    lines: list[str] = []
    archived = 0

    # Each id stands alone: a sweep must not lose 49 good archives to one
    # bad id, so skips are reported, never fatal.
    for session_id in dict.fromkeys(args.session_ids):
        if session_id == execution.ctx.session_id:
            lines.append(f"- {session_id} · skipped — this is the session you are running in")
            continue
        if registry is not None and registry.get_active_run(session_id) is not None:
            lines.append(f"- {session_id} · skipped — live run in progress")
            continue
        data = await svc.load(session_id)
        if data is None:
            lines.append(f"- {session_id} · skipped — no such session")
            continue
        if await svc.is_archived(session_id):
            lines.append(f"- {session_id} · skipped — already archived")
            continue
        await svc.archive(session_id)
        archived += 1
        lines.append(f"- {session_id} · archived — {data.state.name or '(untitled)'}")

    summary = f"Archived {archived} of {len(args.session_ids)}."
    if archived == 0:
        return ToolResult.failure(
            code="conflict",
            message="\n".join([summary, *lines]),
            preview="Nothing archived",
            recovery_action="Check each skip reason; ids come from session_list.",
        )
    return ToolResult(
        content="\n".join([f"{summary} Restore from Settings → Archive.", *lines]),
        preview=summary,
        data={"archived": archived, "requested": len(args.session_ids)},
    )


async def app_request_attention(execution: ToolExecution, args: AppRequestAttentionInput) -> ToolResult:
    app = execution.ctx.services["app_control"]
    area_id = execution.ctx.session_state.area_id
    session_id = execution.ctx.session_id
    now = datetime.now(UTC)
    ask = Ask(
        id=f"tool:{session_id}:{args.key}",
        area_key=area_id,
        text=args.text,
        kind=args.kind,
        source="agent_tool",
        actions=[{"verb": "open_session", "ref": session_id}],
        state="active",
        created_at=now.isoformat(),
        provenance=f"run:{execution.ctx.run.run_id}",
        why_now=args.why_now,
        what_next=args.what_next,
        expires_at=(now + timedelta(hours=NOTIFY_ASK_TTL_HOURS)).isoformat() if args.kind == "notify" else None,
        stable_key=args.key,
        reply_session_id=session_id,
    )
    raised = app.asks.upsert_agent_nomination(ask)
    await app.emit(AreasChangedEvent(keys=[area_id] if area_id else []))
    if not raised:
        return ToolResult(
            content=(
                f"Refreshed the existing item for '{args.key}' — the user has already seen "
                "or decided this one; it will not notify again."
            ),
            preview="refreshed",
        )
    if not any(focus.id == ask.id for focus in nominate_focus(app.asks.list())):
        return ToolResult(
            content=(
                f"Stored, but Home is not showing '{args.key}' yet — a higher-ranked item holds "
                "its lane. It surfaces once that one is resolved."
            ),
            preview=f"{args.kind} queued",
        )
    return ToolResult(content=f"Raised on Home: {args.text}", preview=f"{args.kind} raised")


async def _resolve_destination(
    execution: ToolExecution, destination: ToolAppDestination
) -> AppDestination | ToolResult:
    """A navigation prompt that lands nowhere is worse than a refusal: the user
    sees a dead card and the agent believes it succeeded. Only ref-carrying
    destinations can be wrong — home/settings/memory are fixed surfaces."""
    match destination:
        case SessionDestination():
            if await execution.ctx.services["session"].load(destination.session_id) is None:
                return await _unknown_session(execution, destination.session_id)
        case ToolAreaDestination():
            area = await execution.ctx.services["session"].get_area_by_ref(destination.area_ref)
            if area is None:
                return await _unknown_area(execution, destination.area_ref)
            return AreaDestination(kind="area", area_id=area["area_id"])
        case ToolAutomationDestination(automation_ref=str() as automation_ref):
            svc = execution.ctx.services.get("automation")
            if svc is None:
                return ToolResult.failure(
                    code="not_found",
                    message=f"No automation {automation_ref} — automations are not available here.",
                    preview="No automations",
                    recovery_action="Open the automations surface itself: destination={'kind':'automation'}.",
                )
            try:
                automation = await svc.get_by_ref(automation_ref)
            except KeyError:
                return ToolResult.failure(
                    code="not_found",
                    message=f"No automation {automation_ref}.",
                    preview="Unknown automation",
                    recovery_action="Call automation_list and retry with an exact automation_ref.",
                )
            return AutomationDestination(kind="automation", task_id=automation.task_id)
        case ToolAutomationDestination():
            return AutomationDestination(kind="automation")
        case MemoryDestination(path=str() as path):
            svc = execution.ctx.services.get("wiki")
            if svc is None:
                return ToolResult.failure(
                    code="not_found",
                    message=f"No wiki page {path} — the wiki is not available here.",
                    preview="No wiki",
                    recovery_action="Open the memory surface itself: destination={'kind':'memory'}.",
                )
            if not any(record.resource.path == path for record in svc.list_pages()):
                return ToolResult.failure(
                    code="not_found",
                    message=f"No wiki page at {path}.",
                    preview="Unknown page",
                    recovery_action="Call read_wiki to list pages and retry with an exact path.",
                )
    return destination


async def app_open(execution: ToolExecution, args: AppOpenInput) -> ToolResult:
    destination = await _resolve_destination(execution, args.destination)
    if isinstance(destination, ToolResult):
        return destination
    await execution.ctx.services["app_control"].emit(
        NavigationRequestedEvent(
            origin_session_id=execution.ctx.session_id,
            destination=destination.model_dump(exclude_none=True),
            label=args.label,
        )
    )
    return ToolResult(content=f"Asked the app to open: {args.label}.", preview=args.label)


SEND_MESSAGE_DESCRIPTION = (
    "Deliver a message to a session — the single address for anything you can talk to. "
    "Delivers guidance promptly; it does NOT wake a finished agent — app_followup_task does. "
    "Two behaviours, chosen from the target:\n\n"
    "- An agent you spawned that is still running (the session id background() returned): the "
    "message is queued as steering and the agent reads it at its next step. No approval.\n"
    "- Any other chat: the target's agent picks it up immediately if a run is live there, "
    "otherwise a fresh run starts. Always requires the user's approval, like bash. Cannot "
    "target the session you are running in, or an archived one — the user would never see the reply.\n\n"
    "Fire and forget either way: nothing is waited for and no answer comes back here. The chat's "
    "reply lands in that chat; the agent's result is delivered to you automatically when it finishes. "
    "If you need an answer inside this turn, use research() instead.\n\n"
    "Find ids with session_list; inspect what a session did with session_read."
)

FOLLOWUP_TASK_DESCRIPTION = (
    "Assign a new task to an agent you spawned, addressed by its session id. Works whether the "
    "agent is running or finished:\n\n"
    "- Still running: the task is queued as a <app_followup_task> the agent reads at its next step, "
    "and its report covers it — delivered here automatically. No approval.\n"
    "- Finished/idle: the agent is WOKEN — a fresh hidden run starts in its session with the task "
    "as input, keeping its context. Requires the user's approval, like session_send_message to a chat.\n\n"
    "This is the difference from session_send_message: session_send_message delivers guidance promptly but never "
    "wakes a finished agent; app_followup_task assigns work and wakes an idle one. Fire and forget — "
    "nothing is waited for here. A woken agent's reply lands in its session; session_read shows it."
)

RENAME_SESSION_DESCRIPTION = (
    "Rename a chat session. Use when a session's auto-generated title no longer "
    "matches what it became. Cheap and reversible; no approval needed. The sidebar "
    "row updates live, so archived chats are refused."
)

ARCHIVE_SESSION_DESCRIPTION = (
    "Archive chat sessions — they leave the sidebar and move to Settings → Archive, "
    "where the user can restore them. Reversible; no approval needed. Takes a batch "
    "of session_ids; each id succeeds or is skipped independently (the session you "
    "are running in, one with a live run, an unknown id, or one already archived is "
    "skipped, never fatal), and the result reports every outcome. The archival "
    "chain: session_list(order='oldest', within_days=…) → drop rows you must "
    "keep ([channel], [agent], anything running) → session_archive(session_ids=[…]) "
    "once with the whole batch."
)


REQUEST_ATTENTION_DESCRIPTION = (
    "Raise a needs-you item on the user's Home deck from this session. Use for "
    "something that genuinely needs the user and that they would not otherwise see — "
    "you are blocked on their judgment, you want an action approved, or a finding "
    "matters today. Ordinary progress belongs in your reply, not here.\n\n"
    "The item shows the headline, why_now and what_next, and its primary action opens "
    "this session. Home surfaces one item per lane and at most four at once, and every "
    "chat outside an Area shares a single lane — so an item raised here competes with "
    "the ones raised from every other plain chat, questions first, then reviews, then "
    "notifies. The result tells you whether yours is the one on Home.\n\n"
    "Re-raising the same `key` refreshes the existing item silently. Once the user "
    "has decided on that key, further raises stay silent."
)

OPEN_IN_APP_DESCRIPTION = (
    "Take the user to a place in the Arden app: a chat, an area room, the automations "
    "panel, memory, settings, or Home.\n\n"
    "If the user is currently looking at this session, the app navigates immediately. "
    "Otherwise they get a dismissible prompt carrying `label` that navigates when "
    "clicked — you never yank someone out of an unrelated screen.\n\n"
    "Use when the answer to the user's request IS somewhere else in the app. Do not "
    "use it to punctuate a normal reply."
)


session_send_message_tool = tool(
    display_name="SendMessage",
    display_description="Message a chat or a running agent.",
    description=SEND_MESSAGE_DESCRIPTION,
    input_model=SessionSendMessageInput,
    policy=ToolPolicy(
        action=ToolAction.EXECUTE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        allow_approval_bypass=False,
        permissions=frozenset({"session", "app_control"}),
        deferred=True,
        destructive=True,
        open_world=True,
        idempotent=False,
    ),
    approval=approve_session_send_message,
    execute=session_send_message,
)

app_followup_task_tool = tool(
    display_name="FollowupTask",
    display_description="Assign a task to one of your agents.",
    description=FOLLOWUP_TASK_DESCRIPTION,
    input_model=AppFollowupTaskInput,
    policy=ToolPolicy(
        action=ToolAction.EXECUTE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        allow_approval_bypass=False,
        permissions=frozenset({"session", "app_control"}),
        deferred=True,
        destructive=True,
        open_world=True,
        idempotent=False,
    ),
    approval=approve_app_followup_task,
    execute=app_followup_task,
)

session_rename_tool = tool(
    display_name="RenameSession",
    display_description="Rename a chat session.",
    description=RENAME_SESSION_DESCRIPTION,
    input_model=SessionRenameInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        permissions=frozenset({"session"}),
        deferred=True,
        destructive=True,
        open_world=False,
        idempotent=True,
    ),
    execute=session_rename,
)

session_archive_tool = tool(
    display_name="ArchiveSession",
    display_description="Archive a chat session.",
    description=ARCHIVE_SESSION_DESCRIPTION,
    input_model=SessionArchiveInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        permissions=frozenset({"session"}),
        deferred=True,
        destructive=True,
        open_world=False,
        idempotent=True,
    ),
    execute=session_archive,
)

app_request_attention_tool = tool(
    display_name="RequestAttention",
    display_description="Raise a needs-you item on Home.",
    description=REQUEST_ATTENTION_DESCRIPTION,
    input_model=AppRequestAttentionInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        permissions=frozenset({"app_control"}),
        deferred=True,
        destructive=True,
        open_world=False,
        idempotent=False,
    ),
    execute=app_request_attention,
)

app_open_tool = tool(
    display_name="OpenInApp",
    display_description="Open a place in the Arden app.",
    description=OPEN_IN_APP_DESCRIPTION,
    input_model=AppOpenInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        permissions=frozenset({"app_control", "session"}),
        deferred=True,
        destructive=False,
        open_world=False,
        idempotent=False,
    ),
    execute=app_open,
)

area_list_tool = tool(
    display_name="ListAreas",
    display_description="List Areas and their stable references.",
    description="List active Areas by name and stable area_ref. Use the ref with app_open.",
    input_model=AreaListInput,
    policy=ToolPolicy(
        action=ToolAction.READ,
        scope=ToolScope.INTERNAL,
        permissions=frozenset({"session"}),
        deferred=True,
    ),
    execute=area_list,
)
