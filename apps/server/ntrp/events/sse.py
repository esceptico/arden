"""
Server-Sent Event types for ntrp's chat stream.

The wire format follows the AG-UI protocol (https://ag-ui-protocol.com)
where applicable. Canonical event types are uppercase per AG-UI spec; the
remaining ntrp-specific events (approval, background tasks, automations,
etc.) ride on the same channel as snake_case named non-canonical events.

Every event includes a `timestamp` (Unix milliseconds) on the wire so
clients can compute per-event timing without a local clock.

Identity and visibility rules:
- `seq` is assigned by `SessionBus` and is only the transport resume cursor.
- `event_id` is optional domain-level idempotency for events that may be
  delivered through more than one path.
- `message_id`, `task_id`, and `run_id` identify domain objects, not stream
  positions.
- `model_visible` and `ui_visible` are independent. Background completions can
  wake the model while staying hidden from the transcript UI.
"""

import json
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from ntrp.agent import (
    ReasoningBlock,
    ReasoningDelta,
    ReasoningEnded,
    ReasoningStarted,
    TextBlock,
    TextDelta,
    TextEnded,
    TextStarted,
    ToolCompleted,
    ToolInputDelta,
    ToolInputEnded,
    ToolInputStarted,
    ToolStarted,
)
from ntrp.agent.types.tools import normalize_source_refs


class EventType(StrEnum):
    # ─── AG-UI canonical events (uppercase) ────────────────────────────
    RUN_STARTED = "RUN_STARTED"
    RUN_FINISHED = "RUN_FINISHED"
    RUN_ERROR = "RUN_ERROR"

    TEXT_MESSAGE_START = "TEXT_MESSAGE_START"
    TEXT_MESSAGE_CONTENT = "TEXT_MESSAGE_CONTENT"
    TEXT_MESSAGE_END = "TEXT_MESSAGE_END"

    TOOL_CALL_START = "TOOL_CALL_START"
    TOOL_CALL_ARGS = "TOOL_CALL_ARGS"
    TOOL_CALL_END = "TOOL_CALL_END"
    TOOL_CALL_RESULT = "TOOL_CALL_RESULT"

    REASONING_START = "REASONING_START"
    REASONING_MESSAGE_START = "REASONING_MESSAGE_START"
    REASONING_MESSAGE_CONTENT = "REASONING_MESSAGE_CONTENT"
    REASONING_MESSAGE_END = "REASONING_MESSAGE_END"
    REASONING_END = "REASONING_END"

    # ─── ntrp-specific events (snake_case, non-canonical) ──────────────
    THINKING = "thinking"
    APPROVAL_NEEDED = "approval_needed"
    INPUT_NEEDED = "input_needed"
    BACKGROUND_TASK = "background_task"
    WORKFLOW_STARTED = "workflow_started"
    WORKFLOW_FINISHED = "workflow_finished"
    RUN_CANCELLED = "run_cancelled"
    RUN_BACKGROUNDED = "run_backgrounded"
    MESSAGE_INGESTED = "message_ingested"
    STREAM_RESET = "stream_reset"
    STREAM_KEEPALIVE = "stream_keepalive"
    TASK_STARTED = "task_started"
    TASK_PROGRESS = "task_progress"
    TASK_FINISHED = "task_finished"
    AUTOMATION_PROGRESS = "automation_progress"
    AUTOMATION_FINISHED = "automation_finished"
    AUTOMATION_SUGGESTIONS_UPDATED = "automation_suggestions_updated"
    COMPACTION_STARTED = "compaction_started"
    COMPACTION_FINISHED = "compaction_finished"
    TOKEN_USAGE = "token_usage"
    GOAL_UPDATED = "goal_updated"
    GOAL_CLEARED = "goal_cleared"
    MEMORY_CHANGED = "memory_changed"
    AREAS_CHANGED = "areas_changed"
    TODO_UPDATED = "todo_updated"
    SESSION_UPDATED = "session_updated"
    SESSION_CREATED = "session_created"
    SESSION_ACTIVITY = "session_activity"


# Token-level delta events are ephemeral transport: their cumulative text is
# recoverable from terminal events. Oversized tool-result raw bodies may be
# recovered through TOOL_CALL_RESULT.raw_ref rather than TOOL_CALL_RESULT.content,
# so token deltas are streamed live and held in the
# in-memory replay buffer but NOT persisted to the durable session_events
# table. Every mature streaming server (Letta, Vercel AI SDK, OpenAI,
# LangGraph) keeps per-token deltas ephemeral and persists only the final
# message/state; persisting them durably is the high-cardinality, low-value
# write that bloats the event log without serving any reachable replay path
# (durable replay is only read for cursors above the per-step checkpoint,
# which the in-memory buffer already covers).
EPHEMERAL_EVENT_TYPES: frozenset["EventType"] = frozenset(
    {
        EventType.TEXT_MESSAGE_CONTENT,
        EventType.TOOL_CALL_ARGS,
        EventType.REASONING_MESSAGE_CONTENT,
    }
)


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class SSEEvent:
    type: EventType
    timestamp: int = field(default_factory=_now_ms)

    def to_sse(self) -> dict:
        data = asdict(self)
        data["type"] = self.type.value
        from ntrp.workflow.models import state_for_event

        state = state_for_event(self)
        if state is not None:
            data["workflow_state"] = state.value
        return {"event": self.type.value, "data": json.dumps(data)}

    def to_sse_string(self) -> str:
        sse = self.to_sse()
        return f"event: {sse['event']}\ndata: {sse['data']}\n\n"


# ─── Run lifecycle ───────────────────────────────────────────────────


@dataclass(frozen=True)
class RunStartedEvent(SSEEvent):
    type: EventType = field(default=EventType.RUN_STARTED, init=False)
    session_id: str = ""
    run_id: str = ""
    integrations: list[str] = field(default_factory=list)
    integration_errors: dict[str, str] = field(default_factory=dict)
    skip_approvals: bool = False
    session_name: str = ""
    # True when the triggering user-turn was system-generated (e.g. a loop
    # tick). The desktop uses this to insert a hidden segment boundary so
    # the agent's response renders as a fresh turn instead of being
    # grouped under the user's previous "Worked" block.
    is_meta_run: bool = False
    meta_client_id: str | None = None


@dataclass(frozen=True)
class SessionUpdatedEvent(SSEEvent):
    type: EventType = field(default=EventType.SESSION_UPDATED, init=False)
    session_id: str = ""
    name: str | None = None


@dataclass(frozen=True)
class MemoryChangedEvent(SSEEvent):
    """The live memory vault absorbed on-disk changes (Obsidian edit, feed write,
    maintenance pass). Rides the global automation bus; clients refetch the listed
    pages if they're showing them."""

    type: EventType = field(default=EventType.MEMORY_CHANGED, init=False)
    paths: list[str] = field(default_factory=list)  # vault-relative page paths
    revision: str | None = None
    review_required: bool = False


@dataclass(frozen=True)
class AreasChangedEvent(SSEEvent):
    """An area's asks/autonomy/registry changed (ask resolved, autonomy toggled,
    area created). Rides the global automation bus so the desktop's areas
    surface refetches the listed keys without a reload."""

    type: EventType = field(default=EventType.AREAS_CHANGED, init=False)
    keys: list[str] = field(default_factory=list)  # affected area keys


@dataclass(frozen=True)
class SessionCreatedEvent(SSEEvent):
    type: EventType = field(default=EventType.SESSION_CREATED, init=False)
    # Full SessionListItem-shaped row so the client can render the new
    # session without a refetch. Carried nested under `session` because the
    # bus overwrites a top-level `session_id` with its own channel key
    # (this rides the global automation bus, not a per-session stream).
    session: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SessionActivityEvent(SSEEvent):
    type: EventType = field(default=EventType.SESSION_ACTIVITY, init=False)
    # Lightweight "this session got new content" delta on the global
    # automation bus, so the sidebar bumps/re-sorts a channel row the user
    # isn't currently viewing. Nested under `session` for the same
    # bus-clobber reason as SessionCreatedEvent.
    session: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RunFinishedEvent(SSEEvent):
    type: EventType = field(default=EventType.RUN_FINISHED, init=False)
    run_id: str = ""
    usage: dict = field(default_factory=dict)
    # Token pressure for the current context window. Unlike `usage`, this is
    # not cumulative across model calls in a multi-step run.
    context_input_tokens: int | None = None
    # Server-side message count after this run. The desktop's budget dial
    # checks it against `max_messages` for the message-pressure arc. 0 when
    # unavailable (cancelled runs etc.).
    message_count: int = 0


@dataclass(frozen=True)
class RunErrorEvent(SSEEvent):
    type: EventType = field(default=EventType.RUN_ERROR, init=False)
    run_id: str = ""
    message: str = ""
    recoverable: bool = False
    code: str = "internal_error"
    debug_id: str | None = None


# ─── Text messages (AG-UI Start / Content / End) ─────────────────────


@dataclass(frozen=True)
class TextMessageStartEvent(SSEEvent):
    type: EventType = field(default=EventType.TEXT_MESSAGE_START, init=False)
    message_id: str = ""
    role: str = "assistant"
    depth: int = 0
    parent_id: str | None = None


@dataclass(frozen=True)
class TextMessageContentEvent(SSEEvent):
    type: EventType = field(default=EventType.TEXT_MESSAGE_CONTENT, init=False)
    message_id: str = ""
    delta: str = ""
    depth: int = 0
    parent_id: str | None = None


@dataclass(frozen=True)
class TextMessageEndEvent(SSEEvent):
    type: EventType = field(default=EventType.TEXT_MESSAGE_END, init=False)
    message_id: str = ""
    content: str = ""  # cumulative final text, optional convenience for clients
    depth: int = 0
    parent_id: str | None = None


# ─── Tool calls (AG-UI Start / Args / End / Result) ──────────────────


def _format_call(name: str, args: dict) -> str:
    if not args:
        return f"{name}()"
    parts = [f"{k}={v!r}" for k, v in sorted(args.items())]
    return f"{name}({', '.join(parts)})"


@dataclass(frozen=True)
class ToolCallStartEvent(SSEEvent):
    type: EventType = field(default=EventType.TOOL_CALL_START, init=False)
    tool_call_id: str = ""
    tool_call_name: str = ""
    parent_message_id: str | None = None
    display_name: str = ""
    description: str = ""  # human-readable preview of the call
    depth: int = 0
    parent_id: str | None = None
    kind: str = "tool"
    # Additive UI rendering hints (see ToolMeta / tool_presentation).
    icon: str | None = None
    noun: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class ToolCallArgsEvent(SSEEvent):
    type: EventType = field(default=EventType.TOOL_CALL_ARGS, init=False)
    tool_call_id: str = ""
    delta: str = ""  # JSON-encoded args (ntrp emits atomically, so single delta)
    depth: int = 0
    parent_id: str | None = None


@dataclass(frozen=True)
class ToolCallEndEvent(SSEEvent):
    type: EventType = field(default=EventType.TOOL_CALL_END, init=False)
    tool_call_id: str = ""
    depth: int = 0
    parent_id: str | None = None


@dataclass(frozen=True)
class ToolCallResultEvent(SSEEvent):
    type: EventType = field(default=EventType.TOOL_CALL_RESULT, init=False)
    tool_call_id: str = ""
    role: str = "tool"
    content: str = ""
    preview: str = ""
    duration_ms: int = 0
    data: dict | None = None
    source_refs: list[dict[str, str]] = field(default_factory=list)
    display_name: str = ""
    name: str = ""  # tool name, convenience
    depth: int = 0
    parent_id: str | None = None
    kind: str = "tool"
    is_error: bool = False
    raw_ref: str | None = None
    content_sha256: str | None = None
    content_bytes: int | None = None
    retention_class: str | None = None


# ─── Reasoning (AG-UI Start / Content / End + outer Start/End) ───────


@dataclass(frozen=True)
class ReasoningStartEvent(SSEEvent):
    type: EventType = field(default=EventType.REASONING_START, init=False)
    message_id: str = ""
    depth: int = 0
    parent_id: str | None = None


@dataclass(frozen=True)
class ReasoningMessageStartEvent(SSEEvent):
    type: EventType = field(default=EventType.REASONING_MESSAGE_START, init=False)
    message_id: str = ""
    role: str = "reasoning"
    depth: int = 0
    parent_id: str | None = None


@dataclass(frozen=True)
class ReasoningMessageContentEvent(SSEEvent):
    type: EventType = field(default=EventType.REASONING_MESSAGE_CONTENT, init=False)
    message_id: str = ""
    delta: str = ""
    depth: int = 0
    parent_id: str | None = None


@dataclass(frozen=True)
class ReasoningMessageEndEvent(SSEEvent):
    type: EventType = field(default=EventType.REASONING_MESSAGE_END, init=False)
    message_id: str = ""
    depth: int = 0
    parent_id: str | None = None


@dataclass(frozen=True)
class ReasoningEndEvent(SSEEvent):
    type: EventType = field(default=EventType.REASONING_END, init=False)
    message_id: str = ""
    depth: int = 0
    parent_id: str | None = None


# ─── ntrp-specific (non-canonical) events ─────────────────────────────


@dataclass(frozen=True)
class ThinkingEvent(SSEEvent):
    type: EventType = field(default=EventType.THINKING, init=False)
    session_id: str = ""
    run_id: str = ""
    status: str = ""


@dataclass(frozen=True)
class ApprovalNeededEvent(SSEEvent):
    type: EventType = field(default=EventType.APPROVAL_NEEDED, init=False)
    tool_id: str = ""
    name: str = ""
    path: str | None = None
    diff: str | None = None
    content_preview: str | None = None


@dataclass(frozen=True)
class InputNeededEvent(SSEEvent):
    type: EventType = field(default=EventType.INPUT_NEEDED, init=False)
    tool_id: str = ""
    name: str = ""
    title: str = ""
    html: str = ""


@dataclass(frozen=True)
class BackgroundTaskEvent(SSEEvent):
    type: EventType = field(default=EventType.BACKGROUND_TASK, init=False)
    event_id: str | None = None
    task_id: str = ""
    session_id: str = ""
    run_id: str | None = None
    child_run_id: str | None = None
    child_session_id: str | None = None
    parent_tool_call_id: str | None = None
    agent_type: str | None = None
    wait: bool | None = None
    command: str = ""
    status: str = ""  # "started", "completed", "failed", "cancelled", "activity"
    detail: str | None = None
    result_ref: str | None = None
    model_visible: bool = False
    ui_visible: bool = True
    terminal: bool = False


@dataclass(frozen=True)
class TaskStartedEvent(SSEEvent):
    type: EventType = field(default=EventType.TASK_STARTED, init=False)
    session_id: str = ""
    run_id: str = ""
    task_id: str = ""
    parent_task_id: str | None = None
    parent_tool_call_id: str | None = None
    child_run_id: str | None = None
    child_session_id: str | None = None
    agent_type: str | None = None
    wait: bool | None = None
    name: str = ""
    summary: str = ""
    depth: int = 0
    workflow_id: str | None = None
    phase: str | None = None


@dataclass(frozen=True)
class TaskProgressEvent(SSEEvent):
    type: EventType = field(default=EventType.TASK_PROGRESS, init=False)
    session_id: str = ""
    run_id: str = ""
    task_id: str = ""
    parent_task_id: str | None = None
    parent_tool_call_id: str | None = None
    child_run_id: str | None = None
    child_session_id: str | None = None
    agent_type: str | None = None
    wait: bool | None = None
    name: str = ""
    status: str = "running"
    summary: str = ""
    depth: int = 0
    workflow_id: str | None = None
    phase: str | None = None


@dataclass(frozen=True)
class TaskFinishedEvent(SSEEvent):
    type: EventType = field(default=EventType.TASK_FINISHED, init=False)
    session_id: str = ""
    run_id: str = ""
    task_id: str = ""
    parent_task_id: str | None = None
    parent_tool_call_id: str | None = None
    child_run_id: str | None = None
    child_session_id: str | None = None
    agent_type: str | None = None
    wait: bool | None = None
    name: str = ""
    status: str = "completed"
    summary: str = ""
    depth: int = 0
    workflow_id: str | None = None
    phase: str | None = None
    tool_count: int | None = None


@dataclass(frozen=True)
class WorkflowStartedEvent(SSEEvent):
    type: EventType = field(default=EventType.WORKFLOW_STARTED, init=False)
    session_id: str = ""
    run_id: str = ""
    workflow_id: str = ""
    parent_tool_call_id: str | None = None
    name: str = ""
    description: str = ""
    # The declared plan: phase titles in order, so the UI can render the whole
    # run as pending segments before the first agent spawns.
    phases: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class WorkflowFinishedEvent(SSEEvent):
    type: EventType = field(default=EventType.WORKFLOW_FINISHED, init=False)
    session_id: str = ""
    run_id: str = ""
    workflow_id: str = ""
    status: str = "completed"
    summary: str = ""
    agent_count: int = 0


@dataclass(frozen=True)
class RunCancelledEvent(SSEEvent):
    type: EventType = field(default=EventType.RUN_CANCELLED, init=False)
    run_id: str = ""


@dataclass(frozen=True)
class RunBackgroundedEvent(SSEEvent):
    type: EventType = field(default=EventType.RUN_BACKGROUNDED, init=False)
    run_id: str = ""
    session_id: str = ""


@dataclass(frozen=True)
class MessageIngestedEvent(SSEEvent):
    type: EventType = field(default=EventType.MESSAGE_INGESTED, init=False)
    client_id: str = ""
    run_id: str = ""


@dataclass(frozen=True)
class StreamResetEvent(SSEEvent):
    type: EventType = field(default=EventType.STREAM_RESET, init=False)
    reason: str = ""


@dataclass(frozen=True)
class KeepaliveEvent(SSEEvent):
    type: EventType = field(default=EventType.STREAM_KEEPALIVE, init=False)
    session_id: str = ""
    latest_seq: int = 0


@dataclass(frozen=True)
class AutomationProgressEvent(SSEEvent):
    type: EventType = field(default=EventType.AUTOMATION_PROGRESS, init=False)
    task_id: str = ""
    status: str = ""


@dataclass(frozen=True)
class AutomationFinishedEvent(SSEEvent):
    type: EventType = field(default=EventType.AUTOMATION_FINISHED, init=False)
    task_id: str = ""
    result: str | None = None


@dataclass(frozen=True)
class AutomationSuggestionsUpdatedEvent(SSEEvent):
    type: EventType = field(default=EventType.AUTOMATION_SUGGESTIONS_UPDATED, init=False)


@dataclass(frozen=True)
class CompactionStartedEvent(SSEEvent):
    type: EventType = field(default=EventType.COMPACTION_STARTED, init=False)
    run_id: str = ""
    scope: Literal["run", "agent"] = "run"
    parent_tool_call_id: str | None = None

    def __post_init__(self) -> None:
        _validate_compaction_owner(self.scope, self.parent_tool_call_id)


@dataclass(frozen=True)
class CompactionFinishedEvent(SSEEvent):
    type: EventType = field(default=EventType.COMPACTION_FINISHED, init=False)
    run_id: str = ""
    messages_before: int = 0
    messages_after: int = 0
    scope: Literal["run", "agent"] = "run"
    parent_tool_call_id: str | None = None

    def __post_init__(self) -> None:
        _validate_compaction_owner(self.scope, self.parent_tool_call_id)


def _validate_compaction_owner(scope: str, parent_tool_call_id: str | None) -> None:
    if scope not in {"run", "agent"}:
        raise ValueError("compaction scope must be 'run' or 'agent'")
    if scope == "agent" and not parent_tool_call_id:
        raise ValueError("agent compaction requires parent_tool_call_id")
    if scope == "run" and parent_tool_call_id is not None:
        raise ValueError("run compaction cannot include parent_tool_call_id")


@dataclass(frozen=True)
class TokenUsageEvent(SSEEvent):
    type: EventType = field(default=EventType.TOKEN_USAGE, init=False)
    run_id: str = ""
    usage: dict = field(default_factory=dict)
    cost: float = 0.0
    message_count: int | None = None
    scope: str = "run"
    task_id: str | None = None
    child_run_id: str | None = None
    workflow_id: str | None = None
    phase: str | None = None


@dataclass(frozen=True)
class GoalUpdatedEvent(SSEEvent):
    type: EventType = field(default=EventType.GOAL_UPDATED, init=False)
    session_id: str = ""
    goal: dict = field(default_factory=dict)


@dataclass(frozen=True)
class GoalClearedEvent(SSEEvent):
    type: EventType = field(default=EventType.GOAL_CLEARED, init=False)
    session_id: str = ""


@dataclass(frozen=True)
class TodoUpdatedEvent(SSEEvent):
    type: EventType = field(default=EventType.TODO_UPDATED, init=False)
    run_id: str = ""
    tool_call_id: str = ""
    explanation: str | None = None
    items: list[dict] = field(default_factory=list)


_EVENT_CLASSES = {
    EventType.RUN_STARTED.value: RunStartedEvent,
    EventType.RUN_FINISHED.value: RunFinishedEvent,
    EventType.RUN_ERROR.value: RunErrorEvent,
    EventType.TEXT_MESSAGE_START.value: TextMessageStartEvent,
    EventType.TEXT_MESSAGE_CONTENT.value: TextMessageContentEvent,
    EventType.TEXT_MESSAGE_END.value: TextMessageEndEvent,
    EventType.TOOL_CALL_START.value: ToolCallStartEvent,
    EventType.TOOL_CALL_ARGS.value: ToolCallArgsEvent,
    EventType.TOOL_CALL_END.value: ToolCallEndEvent,
    EventType.TOOL_CALL_RESULT.value: ToolCallResultEvent,
    EventType.REASONING_START.value: ReasoningStartEvent,
    EventType.REASONING_MESSAGE_START.value: ReasoningMessageStartEvent,
    EventType.REASONING_MESSAGE_CONTENT.value: ReasoningMessageContentEvent,
    EventType.REASONING_MESSAGE_END.value: ReasoningMessageEndEvent,
    EventType.REASONING_END.value: ReasoningEndEvent,
    EventType.THINKING.value: ThinkingEvent,
    EventType.APPROVAL_NEEDED.value: ApprovalNeededEvent,
    EventType.INPUT_NEEDED.value: InputNeededEvent,
    EventType.BACKGROUND_TASK.value: BackgroundTaskEvent,
    EventType.TASK_STARTED.value: TaskStartedEvent,
    EventType.TASK_PROGRESS.value: TaskProgressEvent,
    EventType.TASK_FINISHED.value: TaskFinishedEvent,
    EventType.WORKFLOW_STARTED.value: WorkflowStartedEvent,
    EventType.WORKFLOW_FINISHED.value: WorkflowFinishedEvent,
    EventType.RUN_CANCELLED.value: RunCancelledEvent,
    EventType.RUN_BACKGROUNDED.value: RunBackgroundedEvent,
    EventType.MESSAGE_INGESTED.value: MessageIngestedEvent,
    EventType.STREAM_RESET.value: StreamResetEvent,
    EventType.STREAM_KEEPALIVE.value: KeepaliveEvent,
    EventType.AUTOMATION_PROGRESS.value: AutomationProgressEvent,
    EventType.AUTOMATION_FINISHED.value: AutomationFinishedEvent,
    EventType.AUTOMATION_SUGGESTIONS_UPDATED.value: AutomationSuggestionsUpdatedEvent,
    EventType.COMPACTION_STARTED.value: CompactionStartedEvent,
    EventType.COMPACTION_FINISHED.value: CompactionFinishedEvent,
    EventType.TOKEN_USAGE.value: TokenUsageEvent,
    EventType.GOAL_UPDATED.value: GoalUpdatedEvent,
    EventType.GOAL_CLEARED.value: GoalClearedEvent,
    EventType.TODO_UPDATED.value: TodoUpdatedEvent,
    EventType.SESSION_UPDATED.value: SessionUpdatedEvent,
    EventType.SESSION_CREATED.value: SessionCreatedEvent,
    EventType.SESSION_ACTIVITY.value: SessionActivityEvent,
}


def event_from_payload(payload: dict) -> SSEEvent:
    event_type = payload.get("type")
    cls = _EVENT_CLASSES.get(event_type)
    if cls is None:
        raise ValueError(f"Unknown SSE event type: {event_type}")
    kwargs = {key: value for key, value in payload.items() if key not in {"type", "seq", "session_id", "workflow_state"}}
    return cls(**kwargs)


# ─── Conversion from agent events to AG-UI SSE ────────────────────────


def agent_events_to_sse(event) -> tuple[SSEEvent, ...]:
    """Convert an ntrp.agent event to one or more SSEEvents.

    For tool calls we emit the full AG-UI start/args/end triplet; ntrp
    produces tool calls atomically (the model isn't streaming arguments
    token-by-token), so the args delta carries the full JSON payload.
    """
    base = {"depth": event.depth, "parent_id": event.parent_id}
    match event:
        case TextStarted():
            return (
                TextMessageStartEvent(
                    message_id=event.message_id,
                    role="assistant",
                    **base,
                ),
            )
        case TextEnded():
            return (
                TextMessageEndEvent(
                    message_id=event.message_id,
                    content=event.content,
                    **base,
                ),
            )
        case TextDelta():
            return (
                TextMessageContentEvent(
                    message_id=getattr(event, "message_id", "") or "",
                    delta=event.content,
                    **base,
                ),
            )
        case TextBlock():
            # TextBlock is the cumulative-final text for a step; the streaming
            # TextDelta events already carried every chunk over the wire, so
            # re-emitting here would just duplicate the message client-side.
            # Other (non-SSE) consumers of agent events can still use
            # TextBlock — it's only this conversion that drops it.
            return ()
        case ReasoningBlock():
            message_id = f"reasoning-{uuid4().hex[:10]}"
            content = event.content.strip()
            return (
                ReasoningStartEvent(message_id=message_id, **base),
                ReasoningMessageStartEvent(message_id=message_id, **base),
                ReasoningMessageContentEvent(message_id=message_id, delta=content, **base),
                ReasoningMessageEndEvent(message_id=message_id, **base),
                ReasoningEndEvent(message_id=message_id, **base),
            )
        case ReasoningStarted():
            return (
                ReasoningStartEvent(message_id=event.message_id, **base),
                ReasoningMessageStartEvent(message_id=event.message_id, **base),
            )
        case ReasoningDelta():
            return (ReasoningMessageContentEvent(message_id=event.message_id, delta=event.content, **base),)
        case ReasoningEnded():
            return (
                ReasoningMessageEndEvent(message_id=event.message_id, **base),
                ReasoningEndEvent(message_id=event.message_id, **base),
            )
        case ToolInputStarted():
            return (
                ToolCallStartEvent(
                    tool_call_id=event.tool_id,
                    tool_call_name=event.name,
                    display_name=event.display_name,
                    description="",
                    depth=event.depth,
                    parent_id=event.parent_id,
                    kind=event.kind,
                    icon=event.icon,
                    noun=event.noun,
                    source=event.source,
                ),
            )
        case ToolInputDelta():
            return (
                ToolCallArgsEvent(
                    tool_call_id=event.tool_id,
                    delta=event.delta,
                    depth=event.depth,
                    parent_id=event.parent_id,
                ),
            )
        case ToolInputEnded():
            return (
                ToolCallEndEvent(
                    tool_call_id=event.tool_id,
                    depth=event.depth,
                    parent_id=event.parent_id,
                ),
            )
        case ToolStarted():
            description = _format_call(event.display_name or event.name, event.args)
            args_json = json.dumps(event.args) if event.args else "{}"
            return (
                ToolCallStartEvent(
                    tool_call_id=event.tool_id,
                    tool_call_name=event.name,
                    display_name=event.display_name,
                    description=description,
                    depth=event.depth,
                    parent_id=event.parent_id,
                    kind=event.kind,
                    icon=event.icon,
                    noun=event.noun,
                    source=event.source,
                ),
                ToolCallArgsEvent(
                    tool_call_id=event.tool_id,
                    delta=args_json,
                    depth=event.depth,
                    parent_id=event.parent_id,
                ),
                ToolCallEndEvent(
                    tool_call_id=event.tool_id,
                    depth=event.depth,
                    parent_id=event.parent_id,
                ),
            )
        case ToolCompleted():
            return (
                ToolCallResultEvent(
                    tool_call_id=event.tool_id,
                    name=event.name,
                    content=event.result,
                    preview=event.preview,
                    duration_ms=event.duration_ms,
                    data=event.data,
                    source_refs=[ref.to_dict() for ref in normalize_source_refs(event.source_refs)],
                    display_name=event.display_name,
                    depth=event.depth,
                    parent_id=event.parent_id,
                    kind=event.kind,
                    is_error=event.is_error,
                ),
            )
    return ()


def agent_event_to_sse(event) -> "SSEEvent | None":
    events = agent_events_to_sse(event)
    # Returns the lead/canonical event when one or more are produced. For
    # tool calls (start/args/end triplet) the START event is the
    # informative one for callers that just want a single event handle.
    return events[0] if events else None
