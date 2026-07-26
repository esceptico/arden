"""Tools that drive the Arden app itself rather than the outside world.

`send_message` delivers a message to a session — another chat, or an agent this
run spawned; `rename_session` and `archive_session` curate the sidebar;
`request_attention` raises a needs-you item on Home; `open_in_app` takes the
user somewhere in the UI. They exist because the agent can already *read* every
session (`list_recent_sessions`/`read_session`) but had no way to act on one,
and no way to reach the user outside its own reply.
"""

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from arden.areas.agent import NOTIFY_ASK_TTL_HOURS
from arden.areas.asks import nominate_focus
from arden.areas.models import Ask
from arden.events.destinations import AppDestination, AreaDestination, AutomationDestination, SessionDestination
from arden.events.sse import AreasChangedEvent, NavigationRequestedEvent
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import APPROVAL_WAIVED, ApprovalInfo, ApprovalWaived, ToolAction, ToolPolicy, ToolScope
from arden.tools.sessions import area_filter, session_ref

_PREVIEW_CHARS = 1_200
_RECENT_ID_HINT_LIMIT = 10


class SendMessageInput(BaseModel):
    session_id: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "Target session id: another chat from list_recent_sessions, or the "
            "session background() returned for an agent you spawned. Must not be "
            "the session you are running in — use create_loop for another turn here."
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


class RenameSessionInput(BaseModel):
    session_id: str = Field(min_length=1, max_length=200, description="Session id from list_recent_sessions.")
    name: str = Field(
        min_length=1,
        max_length=120,
        description="New human-readable title, 2-6 words, no trailing punctuation.",
    )


class ArchiveSessionInput(BaseModel):
    session_ids: list[str] = Field(
        min_length=1,
        max_length=50,
        description=(
            "Session ids from list_recent_sessions to archive. A single "
            "session is a one-element list; an archival sweep passes the "
            "whole batch at once."
        ),
    )


class RequestAttentionInput(BaseModel):
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


class OpenInAppInput(BaseModel):
    destination: AppDestination = Field(
        description=(
            'Where to send the user. One of: {"kind":"home"}, '
            '{"kind":"session","session_id":"..."}, {"kind":"settings","tab":"models"}, '
            '{"kind":"automation","task_id":"..."}, {"kind":"memory"}, '
            '{"kind":"area","area_id":"..."}. task_id and tab may be omitted to '
            "open the surface itself."
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
        recovery_action="Call list_recent_sessions and retry with an exact session_id.",
    )


async def _unknown_area(execution: ToolExecution, area_id: str) -> ToolResult:
    areas = await execution.ctx.services["session"].list_areas()
    known = ", ".join(str(area["area_id"]) for area in areas) or "none"
    return ToolResult.failure(
        code="not_found",
        message=f"No area {area_id}. Areas: {known}.",
        preview="Unknown area",
        recovery_action="Retry with an exact area id.",
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


async def approve_send_message(execution: ToolExecution, args: SendMessageInput) -> ApprovalInfo | ApprovalWaived:
    if execution.ctx.background_tasks.task_for_session(args.session_id) is not None:
        return APPROVAL_WAIVED
    return ApprovalInfo(
        description="Send a prompt to another chat",
        preview=f"To: {args.session_id}\n\n{args.message[:_PREVIEW_CHARS]}",
        diff=None,
    )


async def send_message(execution: ToolExecution, args: SendMessageInput) -> ToolResult:
    if args.session_id == execution.ctx.session_id:
        return ToolResult.failure(
            code="invalid_arguments",
            message="send_message cannot target its own session.",
            preview="Same session",
            recovery_action=(
                "Use create_loop to give yourself another turn in this chat, or target a different session_id."
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
                "Its result is delivered automatically; read_session(session_id=...) for detail. "
                "Call send_message again only if you want a new run in that session."
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
        client_id=f"send_message:{execution.tool_id}",
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


async def rename_session(execution: ToolExecution, args: RenameSessionInput) -> ToolResult:
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


async def archive_session(execution: ToolExecution, args: ArchiveSessionInput) -> ToolResult:
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
            recovery_action="Check each skip reason; ids come from list_recent_sessions.",
        )
    return ToolResult(
        content="\n".join([f"{summary} Restore from Settings → Archive.", *lines]),
        preview=summary,
        data={"archived": archived, "requested": len(args.session_ids)},
    )


async def request_attention(execution: ToolExecution, args: RequestAttentionInput) -> ToolResult:
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


async def _invalid_destination(execution: ToolExecution, destination: AppDestination) -> ToolResult | None:
    """A navigation prompt that lands nowhere is worse than a refusal: the user
    sees a dead card and the agent believes it succeeded. Only ref-carrying
    destinations can be wrong — home/settings/memory are fixed surfaces."""
    match destination:
        case SessionDestination():
            if await execution.ctx.services["session"].load(destination.session_id) is None:
                return await _unknown_session(execution, destination.session_id)
        case AreaDestination():
            if await execution.ctx.services["session"].get_area(destination.area_id) is None:
                return await _unknown_area(execution, destination.area_id)
        case AutomationDestination(task_id=str() as task_id):
            svc = execution.ctx.services.get("automation")
            if svc is None:
                return ToolResult.failure(
                    code="not_found",
                    message=f"No automation {task_id} — automations are not available here.",
                    preview="No automations",
                    recovery_action="Open the automations surface itself: destination={'kind':'automation'}.",
                )
            try:
                await svc.get(task_id)
            except KeyError:
                return ToolResult.failure(
                    code="not_found",
                    message=f"No automation {task_id}.",
                    preview="Unknown automation",
                    recovery_action="Call list_automations and retry with an exact task_id.",
                )
    return None


async def open_in_app(execution: ToolExecution, args: OpenInAppInput) -> ToolResult:
    if failure := await _invalid_destination(execution, args.destination):
        return failure
    await execution.ctx.services["app_control"].emit(
        NavigationRequestedEvent(
            origin_session_id=execution.ctx.session_id,
            destination=args.destination.model_dump(exclude_none=True),
            label=args.label,
        )
    )
    return ToolResult(content=f"Asked the app to open: {args.label}.", preview=args.label)


SEND_MESSAGE_DESCRIPTION = (
    "Deliver a message to a session — the single address for anything you can talk to. "
    "Two behaviours, chosen from the target:\n\n"
    "- An agent you spawned that is still running (the session id background() returned): the "
    "message is queued as steering and the agent reads it at its next step. No approval.\n"
    "- Any other chat: the target's agent picks it up immediately if a run is live there, "
    "otherwise a fresh run starts. Always requires the user's approval, like bash. Cannot "
    "target the session you are running in, or an archived one — the user would never see the reply.\n\n"
    "Fire and forget either way: nothing is waited for and no answer comes back here. The chat's "
    "reply lands in that chat; the agent's result is delivered to you automatically when it finishes. "
    "If you need an answer inside this turn, use research() instead.\n\n"
    "Find ids with list_recent_sessions; inspect what a session did with read_session."
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
    "chain: list_recent_sessions(order='oldest', within_days=…) → drop rows you must "
    "keep ([channel], [agent], anything running) → archive_session(session_ids=[…]) "
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


send_message_tool = tool(
    display_name="SendMessage",
    display_description="Message a chat or a running agent.",
    description=SEND_MESSAGE_DESCRIPTION,
    input_model=SendMessageInput,
    policy=ToolPolicy(
        action=ToolAction.EXECUTE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        allow_approval_bypass=False,
        permissions=frozenset({"session", "app_control"}),
    ),
    approval=approve_send_message,
    execute=send_message,
)

rename_session_tool = tool(
    display_name="RenameSession",
    display_description="Rename a chat session.",
    description=RENAME_SESSION_DESCRIPTION,
    input_model=RenameSessionInput,
    policy=ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL, permissions=frozenset({"session"})),
    execute=rename_session,
)

archive_session_tool = tool(
    display_name="ArchiveSession",
    display_description="Archive a chat session.",
    description=ARCHIVE_SESSION_DESCRIPTION,
    input_model=ArchiveSessionInput,
    policy=ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL, permissions=frozenset({"session"})),
    execute=archive_session,
)

request_attention_tool = tool(
    display_name="RequestAttention",
    display_description="Raise a needs-you item on Home.",
    description=REQUEST_ATTENTION_DESCRIPTION,
    input_model=RequestAttentionInput,
    policy=ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL, permissions=frozenset({"app_control"})),
    execute=request_attention,
)

open_in_app_tool = tool(
    display_name="OpenInApp",
    display_description="Open a place in the Arden app.",
    description=OPEN_IN_APP_DESCRIPTION,
    input_model=OpenInAppInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        permissions=frozenset({"app_control", "session"}),
    ),
    execute=open_in_app,
)
