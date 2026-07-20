"""Read-only tools that expose recent chat sessions to the agent.

Built for cross-session pattern detection — e.g. an audit automation that
runs weekly, scans recent sessions, and proposes automations/skills based
on what the user actually repeats. The agent gets enough to identify
patterns without dumping every byte of history into context.

Three tools:
- `list_recent_sessions` — index of sessions: id, name, when, message count.
- `read_session` — messages for one session, with content trimmed by default
  so a single huge session can't blow the context window.
- `create_session` — spawn a new session (defaults to a channel) the agent
  can post into for channel-aware automations.
"""

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from ntrp.agent.types.tools import ToolSourceRef, normalize_source_refs
from ntrp.tools.core import ToolResult, tool
from ntrp.tools.core.collections import format_timestamp
from ntrp.tools.core.context import ToolExecution
from ntrp.tools.core.types import ToolAction, ToolPolicy, ToolScope

# How aggressively to trim each message's content when summarizing a session.
# Tool calls and large tool results are the worst offenders — a single bash
# output can be tens of KB. The default keeps the structure intact while
# capping each line, and `full_content=True` opts in to untrimmed bodies.
_DEFAULT_CONTENT_CHARS = 400
_MAX_CONTENT_CHARS = 4_000
_DEFAULT_LIST_LIMIT = 20
_MAX_LIST_LIMIT = 100
_DEFAULT_MESSAGE_LIMIT = 50
_MAX_MESSAGE_LIMIT = 200


def _active_area_id(execution: ToolExecution) -> str | None:
    return execution.ctx.session_state.area_id


class ListRecentSessionsInput(BaseModel):
    limit: int = Field(
        default=_DEFAULT_LIST_LIMIT,
        ge=1,
        le=_MAX_LIST_LIMIT,
        description=f"Max sessions to return (default {_DEFAULT_LIST_LIMIT}, max {_MAX_LIST_LIMIT}). Most recent first.",
    )
    within_days: int | None = Field(
        default=None,
        ge=1,
        le=365,
        description=(
            "Only return sessions with activity in the last N days. Omit to "
            "use limit alone. Useful for periodic audits ('last 7 days')."
        ),
    )


class SearchTranscriptsInput(BaseModel):
    query: str = Field(
        description=(
            "Full-text query across chat transcripts (FTS5 syntax: bare words "
            'are AND-ed; quote "exact phrases"). Searches the readable message '
            "text, not JSON. Returns ranked snippets with session_id + seq so "
            "you can read_session(around_seq=...) for context."
        )
    )
    limit: int = Field(
        default=_DEFAULT_LIST_LIMIT,
        ge=1,
        le=_MAX_LIST_LIMIT,
        description=f"Max hits to return (default {_DEFAULT_LIST_LIMIT}, max {_MAX_LIST_LIMIT}). Best match first.",
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Pagination offset — skip this many hits. Use limit+offset to page through results.",
    )
    session_id: str | None = Field(
        default=None,
        description="Restrict the search to one session. Omit to search across all transcripts.",
    )
    within_days: int | None = Field(
        default=None,
        ge=1,
        le=3650,
        description="Only match messages from the last N days. Omit for all time.",
    )


class CreateSessionInput(BaseModel):
    name: str = Field(description="Short human-readable label for the new session (e.g. 'ops-alerts').")
    session_type: Literal["chat", "channel"] = Field(
        default="channel",
        description=(
            "Session kind. 'channel' (default) = topic stream the agent posts into "
            "from automations; 'chat' = ad-hoc conversation."
        ),
    )


class ReadSessionInput(BaseModel):
    session_id: str = Field(description="The session_id from list_recent_sessions.")
    limit: int = Field(
        default=_DEFAULT_MESSAGE_LIMIT,
        ge=1,
        le=_MAX_MESSAGE_LIMIT,
        description=(
            f"Max messages to return (default {_DEFAULT_MESSAGE_LIMIT}, max "
            f"{_MAX_MESSAGE_LIMIT}). Messages are returned oldest-first."
        ),
    )
    content_chars: int = Field(
        default=_DEFAULT_CONTENT_CHARS,
        ge=50,
        le=_MAX_CONTENT_CHARS,
        description=(
            f"Truncate each message's content body to this many chars "
            f"(default {_DEFAULT_CONTENT_CHARS}, max {_MAX_CONTENT_CHARS}). "
            "Keeps a long session readable without blowing context."
        ),
    )
    role_filter: str | None = Field(
        default=None,
        description=(
            "Comma-separated roles to include, e.g. 'user' for just the "
            "prompts, or 'user,assistant' to drop tool noise. Omit for all."
        ),
    )
    after_seq: int | None = Field(
        default=None,
        description=(
            "Pagination cursor: return messages AFTER this seq (exclusive), "
            "oldest-first. Use the last seq from the previous page to walk "
            "forward through a long session. Omit for the most recent page."
        ),
    )
    before_seq: int | None = Field(
        default=None,
        description=(
            "Pagination cursor: return the page of messages ENDING just "
            "before this seq (exclusive). Use the first seq from the current "
            "page to walk backward. Mutually exclusive with after_seq."
        ),
    )
    around_seq: int | None = Field(
        default=None,
        description=(
            "Center the page on this seq (e.g. a hit's seq from search_transcripts) to read the surrounding context."
        ),
    )


# --- Helpers ---


def _content_to_text(raw) -> str:
    """Normalize a stored message content into plain text. Messages can be
    a string, a list of content blocks (text/image/tool_result), or a dict —
    flatten to a single string the agent can scan."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts: list[str] = []
        for block in raw:
            if isinstance(block, dict):
                if block.get("type") == "text" and block.get("text"):
                    parts.append(block["text"])
                elif block.get("type") == "tool_use" and block.get("name"):
                    parts.append(f"[tool_use: {block['name']}]")
                elif block.get("type") == "tool_result":
                    inner = block.get("content")
                    parts.append(f"[tool_result: {_content_to_text(inner)[:120]}]")
                elif block.get("type") == "image":
                    parts.append("[image]")
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    if isinstance(raw, dict):
        return _content_to_text(raw.get("content"))
    return str(raw)


def _truncate(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _parse_when(raw) -> datetime | None:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    if isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


def _format_when(raw) -> str:
    dt = _parse_when(raw)
    if dt is None:
        return str(raw)
    return format_timestamp(dt)


def _session_ref(session_id: str, title: str) -> ToolSourceRef:
    return ToolSourceRef(provider="ntrp", kind="session", ref=session_id, title=title or session_id)


def _message_ref(session_id: str, seq: object, title: str) -> ToolSourceRef | None:
    if not isinstance(seq, int):
        return None
    return ToolSourceRef(provider="ntrp", kind="message", ref=f"{session_id}:{seq}", title=title)


# --- Executors ---


async def list_recent_sessions(execution: ToolExecution, args: ListRecentSessionsInput) -> ToolResult:
    svc = execution.ctx.services.get("session")
    if svc is None:
        return ToolResult(content="Session service unavailable.", preview="Unavailable", is_error=True)

    sessions = await svc.list_sessions(
        limit=args.limit * 2 if args.within_days else args.limit,
        area_id=_active_area_id(execution),
    )

    if args.within_days is not None:
        cutoff = datetime.now(UTC) - timedelta(days=args.within_days)
        sessions = [s for s in sessions if (when := _parse_when(s.get("last_activity"))) is not None and when >= cutoff]
    sessions.sort(
        key=lambda session: (
            _parse_when(session.get("last_activity") or session.get("started_at")) or datetime.min.replace(tzinfo=UTC),
            str(session.get("session_id", "")),
        ),
        reverse=True,
    )
    has_more = len(sessions) > args.limit
    sessions = sessions[: args.limit]

    if not sessions:
        return ToolResult(content="No sessions found.", preview="0 sessions")

    lines: list[str] = []
    for s in sessions:
        sid = s.get("session_id", "?")
        name = (s.get("name") or "(untitled)").strip()
        when = _format_when(s.get("last_activity") or s.get("started_at"))
        count = s.get("message_count", 0)
        lines.append(f"- {sid} · {name} · {when} · {count} msgs")

    if has_more:
        lines.append(f"Showing {len(sessions)} sessions; more exist. Increase limit or narrow within_days.")

    return ToolResult(
        content="\n".join(lines),
        preview=f"{len(sessions)} sessions" + (" (capped)" if has_more else ""),
        data={"items": sessions, "count": len(sessions), "has_more": has_more},
        source_refs=normalize_source_refs(
            _session_ref(str(session.get("session_id", "")), str(session.get("name") or session.get("session_id", "")))
            for session in sessions
        ),
    )


async def create_session(execution: ToolExecution, args: CreateSessionInput) -> ToolResult:
    svc = execution.ctx.services.get("session")
    if svc is None:
        return ToolResult(content="Session service unavailable.", preview="Unavailable", is_error=True)

    origin = execution.ctx.run.loop_task_id
    state = await svc.provision(
        name=args.name,
        session_type=args.session_type,
        origin_automation_id=origin,
        area_id=_active_area_id(execution),
    )

    lines = [
        f"Created {args.session_type}: {state.name}",
        f"ID: {state.session_id}",
    ]
    if origin:
        lines.append(f"Origin automation: {origin}")
    return ToolResult(
        content="\n".join(lines),
        preview=f"Created ({state.session_id})",
        source_refs=(_session_ref(state.session_id, state.name or state.session_id),),
    )


async def read_session(execution: ToolExecution, args: ReadSessionInput) -> ToolResult:
    svc = execution.ctx.services.get("session")
    if svc is None:
        return ToolResult(content="Session service unavailable.", preview="Unavailable", is_error=True)

    roles: set[str] | None = None
    if args.role_filter:
        roles = {r.strip().lower() for r in args.role_filter.split(",") if r.strip()}

    if args.after_seq is not None and args.before_seq is not None:
        return ToolResult(
            content="Pass only one of after_seq / before_seq.",
            preview="Bad cursor",
            is_error=True,
        )

    try:
        page = await svc.list_messages(
            args.session_id,
            limit=args.limit,
            after_seq=args.after_seq,
            before_seq=args.before_seq,
            around_seq=args.around_seq,
            area_id=_active_area_id(execution),
        )
    except Exception as e:
        return ToolResult(
            content=f"Failed to read session {args.session_id}: {e}",
            preview="Read failed",
            is_error=True,
        )

    raw_messages = page.get("messages") if isinstance(page, dict) else page
    if not raw_messages:
        return ToolResult(
            content=f"No messages in session {args.session_id}.",
            preview="0 messages",
        )

    lines: list[str] = []
    kept = 0
    first_seq: int | None = None
    last_seq: int | None = None
    source_refs: list[ToolSourceRef] = [_session_ref(args.session_id, f"Session {args.session_id}")]
    for row in raw_messages:
        # Each row is {seq, role, created_at, message: {...}}. The body lives
        # in the nested message; role is mirrored at the top level.
        msg = row.get("message", row) if isinstance(row, dict) else {}
        seq = row.get("seq") if isinstance(row, dict) else None
        role = (row.get("role") or msg.get("role") or "").lower()
        if roles is not None and role not in roles:
            continue
        body = _truncate(_content_to_text(msg.get("content")), args.content_chars)
        tool_name = msg.get("name") or msg.get("tool_name")
        prefix = f"{role}" + (f"({tool_name})" if tool_name and role == "tool" else "")
        seq_tag = f"#{seq} " if seq is not None else ""
        lines.append(f"{seq_tag}[{prefix}] {body}" if body else f"{seq_tag}[{prefix}]")
        if seq is not None:
            first_seq = seq if first_seq is None else first_seq
            last_seq = seq
        if ref := _message_ref(args.session_id, seq, f"Session {args.session_id} message {seq}"):
            source_refs.append(ref)
        kept += 1

    if not kept:
        return ToolResult(
            content=f"No messages matched filter in session {args.session_id}.",
            preview="0 matched",
        )

    footer: list[str] = []
    if isinstance(page, dict):
        if page.get("has_more_after") and last_seq is not None:
            footer.append(f"More after: read_session(after_seq={last_seq})")
        if page.get("has_more_before") and first_seq is not None:
            footer.append(f"More before: read_session(before_seq={first_seq})")
    body_text = "\n".join(lines)
    if footer:
        body_text += "\n\n" + "\n".join(footer)

    return ToolResult(
        content=body_text,
        preview=f"{kept} of {len(raw_messages)} messages",
        source_refs=normalize_source_refs(source_refs),
    )


async def search_transcripts(execution: ToolExecution, args: SearchTranscriptsInput) -> ToolResult:
    svc = execution.ctx.services.get("session")
    if svc is None:
        return ToolResult(content="Session service unavailable.", preview="Unavailable", is_error=True)

    since: str | None = None
    if args.within_days is not None:
        since = (datetime.now(UTC) - timedelta(days=args.within_days)).isoformat()

    try:
        result = await svc.search_messages(
            args.query,
            limit=args.limit,
            offset=args.offset,
            session_id=args.session_id,
            since=since,
            area_id=_active_area_id(execution),
        )
    except Exception as e:
        return ToolResult(
            content=f"Search failed: {e}",
            preview="Search failed",
            is_error=True,
        )

    hits = result.get("hits", [])
    if not hits:
        return ToolResult(content=f"No matches for {args.query!r}.", preview="0 hits")

    lines: list[str] = []
    for h in hits:
        sid = h.get("session_id", "?")
        name = (h.get("session_name") or "(untitled)").strip()
        when = _format_when(h.get("created_at"))
        role = (h.get("role") or "").lower()
        seq = h.get("seq")
        snippet = (h.get("snippet") or "").replace("\n", " ").strip()
        lines.append(f"- {sid} #{seq} · {name} · {when} · [{role}] {snippet}")

    footer = ""
    if result.get("has_more"):
        footer = f"\n\nMore results: search_transcripts(offset={args.offset + args.limit})"
    return ToolResult(
        content="\n".join(lines) + footer,
        preview=f"{len(hits)} hit{'s' if len(hits) != 1 else ''}",
        source_refs=normalize_source_refs(
            ref
            for hit in hits
            if (
                ref := _message_ref(
                    str(hit.get("session_id", "")),
                    hit.get("seq"),
                    f"{hit.get('session_name') or hit.get('session_id', '')} message {hit.get('seq')}",
                )
            )
        ),
    )


# --- Tool registration ---

LIST_RECENT_SESSIONS_DESCRIPTION = (
    "List recent chat sessions (most recent first) with id, name, last "
    "activity, and message count. Read-only. Use to find sessions worth "
    "inspecting before calling read_session. Useful for cross-session "
    "pattern detection (audit automations, propose-* skills running in "
    "a scheduled context with no current conversation)."
)

READ_SESSION_DESCRIPTION = (
    "Read messages from a specific session by session_id. Content is "
    "truncated per message to keep context manageable; raise "
    "content_chars (up to 4000) for fuller bodies. Use role_filter='user' "
    "to scan only user prompts when looking for patterns. Each line is "
    "tagged with its #seq; paginate a long session with after_seq / "
    "before_seq cursors, or center on a search hit with around_seq. "
    "Read-only."
)


CREATE_SESSION_DESCRIPTION = (
    "Spawn a new session the agent can target. Defaults to session_type='channel' "
    "— a topic stream automations can post into (e.g. 'ops-alerts', 'morning-brief'). "
    "Use 'chat' for ad-hoc conversations. When invoked from inside a loop iteration, "
    "the new session is stamped with origin_automation_id so the source is auditable. "
    "Cheap; no approval needed (sessions are easily archived/deleted)."
)


list_recent_sessions_tool = tool(
    display_name="ListRecentSessions",
    description=LIST_RECENT_SESSIONS_DESCRIPTION,
    input_model=ListRecentSessionsInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, permissions=frozenset({"session"})),
    execute=list_recent_sessions,
)

read_session_tool = tool(
    display_name="ReadSession",
    description=READ_SESSION_DESCRIPTION,
    input_model=ReadSessionInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, permissions=frozenset({"session"})),
    execute=read_session,
)

SEARCH_TRANSCRIPTS_DESCRIPTION = (
    "Full-text search across chat transcripts, ranked by relevance. Searches "
    "the readable message text (not JSON/images). Filter to one chat with "
    "session_id, bound by time with within_days, page with limit+offset. Each "
    "hit gives session_id + seq — follow up with read_session(session_id, "
    "around_seq=seq) to read the surrounding conversation. Read-only."
)

search_transcripts_tool = tool(
    display_name="SearchTranscripts",
    description=SEARCH_TRANSCRIPTS_DESCRIPTION,
    input_model=SearchTranscriptsInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, permissions=frozenset({"session"})),
    execute=search_transcripts,
)

create_session_tool = tool(
    display_name="CreateSession",
    description=CREATE_SESSION_DESCRIPTION,
    input_model=CreateSessionInput,
    policy=ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL, permissions=frozenset({"session"})),
    execute=create_session,
)
