"""Tools that drive the Arden app itself rather than the outside world.

`send_to_session` delivers a prompt into another chat; `rename_session` and
`archive_session` curate the sidebar; `request_attention` raises a needs-you
item on Home; `open_in_app` takes the user somewhere in the UI. They exist
because the agent can already *read* every session
(`list_recent_sessions`/`read_session`) but had no way to act on one, and no
way to reach the user outside its own reply.
"""

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from arden.areas.agent import NOTIFY_ASK_TTL_HOURS
from arden.areas.asks import nominate_focus
from arden.areas.models import Ask
from arden.context.store import AREA_FILTER_UNSET
from arden.events.destinations import AppDestination
from arden.events.sse import AreasChangedEvent, NavigationRequestedEvent
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope
from arden.tools.sessions import session_ref

_PREVIEW_CHARS = 1_200
_RECENT_ID_HINT_LIMIT = 10


class SendToSessionInput(BaseModel):
    session_id: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "Target session id from list_recent_sessions. Must not be the session "
            "you are running in — use create_loop to give yourself another turn."
        ),
    )
    message: str = Field(
        min_length=1,
        max_length=20_000,
        description=(
            "The prompt to deliver, written as if the user typed it into that chat. "
            "It has none of your context — state the task, the target, and any ids in full."
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
    session_id: str = Field(
        min_length=1,
        max_length=200,
        description="Session id from list_recent_sessions. Must not be the session you are running in.",
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
    recent = await svc.list_sessions(limit=_RECENT_ID_HINT_LIMIT, area_id=AREA_FILTER_UNSET)
    known = ", ".join(str(row.get("session_id")) for row in recent) or "none"
    return ToolResult.failure(
        code="not_found",
        message=f"No session {session_id}. Recent session ids: {known}.",
        preview="Unknown session",
        recovery_action="Call list_recent_sessions and retry with an exact session_id.",
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


async def approve_send_to_session(execution: ToolExecution, args: SendToSessionInput) -> ApprovalInfo:
    return ApprovalInfo(
        description="Send a prompt to another chat",
        preview=f"To: {args.session_id}\n\n{args.message[:_PREVIEW_CHARS]}",
        diff=None,
    )


async def send_to_session(execution: ToolExecution, args: SendToSessionInput) -> ToolResult:
    if args.session_id == execution.ctx.session_id:
        return ToolResult.failure(
            code="invalid_arguments",
            message="send_to_session cannot target its own session.",
            preview="Same session",
            recovery_action=(
                "Use create_loop to give yourself another turn in this chat, or target a different session_id."
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
        client_id=f"send_to_session:{execution.tool_id}",
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

    await svc.rename(args.session_id, args.name)
    if execution.ctx.run_registry is not None:
        execution.ctx.run_registry.sync_session_name(args.session_id, args.name)
    data.state.name = args.name
    await svc.announce_row(data.state, len(data.messages))
    return ToolResult(
        content=f"Renamed {args.session_id} to {args.name}.",
        preview=f"Renamed to {args.name}",
        source_refs=(session_ref(args.session_id, args.name),),
    )


async def archive_session(execution: ToolExecution, args: ArchiveSessionInput) -> ToolResult:
    if args.session_id == execution.ctx.session_id:
        return ToolResult.failure(
            code="invalid_arguments",
            message="archive_session cannot archive the session it is running in.",
            preview="Same session",
            recovery_action="Target a different session_id; the user archives this one themselves.",
        )

    registry = execution.ctx.run_registry
    if registry is not None and registry.get_active_run(args.session_id) is not None:
        return ToolResult.failure(
            code="conflict",
            message=f"Session {args.session_id} has a live run.",
            preview="Run in progress",
            recovery_action="Wait for that session's run to finish, or cancel it first.",
        )

    svc = execution.ctx.services["session"]
    data = await svc.load(args.session_id)
    if data is None:
        return await _unknown_session(execution, args.session_id)
    if await svc.is_archived(args.session_id):
        return _archived_session(args.session_id, "Nothing to do — it is already out of the sidebar.")

    await svc.archive(args.session_id)
    label = data.state.name or args.session_id
    return ToolResult(
        content=f"Archived {label} ({args.session_id}). Restore from Settings → Archive.",
        preview=f"Archived {label}",
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


async def open_in_app(execution: ToolExecution, args: OpenInAppInput) -> ToolResult:
    await execution.ctx.services["app_control"].emit(
        NavigationRequestedEvent(
            origin_session_id=execution.ctx.session_id,
            destination=args.destination.model_dump(exclude_none=True),
            label=args.label,
        )
    )
    return ToolResult(content=f"Asked the app to open: {args.label}.", preview=args.label)


SEND_TO_SESSION_DESCRIPTION = (
    "Deliver a prompt into another chat session. The target's agent picks it up "
    "immediately if a run is live there, otherwise a fresh run starts.\n\n"
    "Fire and forget: this returns as soon as the message is delivered. There is no "
    "waiting, polling, or result handed back here — the target's answer lands in the "
    "target chat and surfaces to the user through its own unread signal. If you need "
    "an answer inside this turn, use background() or research() instead.\n\n"
    "Always requires the user's approval, like bash. Cannot target the session you "
    "are running in, or an archived one — the user would never see the reply."
)

RENAME_SESSION_DESCRIPTION = (
    "Rename a chat session. Use when a session's auto-generated title no longer "
    "matches what it became. Cheap and reversible; no approval needed. The sidebar "
    "row updates live, so archived chats are refused."
)

ARCHIVE_SESSION_DESCRIPTION = (
    "Archive a chat session — it leaves the sidebar and moves to Settings → Archive, "
    "where the user can restore it. Reversible; no approval needed. Refuses to "
    "archive the session you are running in, one with a live run, or one that is "
    "already archived."
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


send_to_session_tool = tool(
    display_name="SendToSession",
    display_description="Send a prompt to another chat.",
    description=SEND_TO_SESSION_DESCRIPTION,
    input_model=SendToSessionInput,
    policy=ToolPolicy(
        action=ToolAction.EXECUTE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        allow_approval_bypass=False,
        permissions=frozenset({"session", "app_control"}),
    ),
    approval=approve_send_to_session,
    execute=send_to_session,
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
    policy=ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL, permissions=frozenset({"app_control"})),
    execute=open_in_app,
)
