import asyncio
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from inspect import isawaitable
from uuid import uuid4

from coolname import generate_slug

from arden.agent import (
    Agent,
    ReasoningBlock,
    ReasoningDelta,
    ReasoningEnded,
    ReasoningStarted,
    Result,
    Role,
    RunBudget,
    ToolCompleted,
    ToolStarted,
)
from arden.agent.types.tools import ToolSourceRef, normalize_source_refs
from arden.constants import AGENT_MAX_CONCURRENT, SUBAGENT_DEFAULT_TIMEOUT
from arden.context.models import SessionState
from arden.context.prompts import RESEARCH_AGENT_COMPACTION_CONTEXT
from arden.core.compaction_model_request_middleware import CompactionModelRequestMiddleware
from arden.core.compactor import Compactor
from arden.core.deferred_tools_middleware import DeferredToolsModelRequestMiddleware
from arden.core.isolation import IsolationLevel
from arden.core.llm_client import llm_client
from arden.core.model_context_budget import ToolResultContextBudgetMiddleware
from arden.core.naming import generate_agent_name
from arden.core.prompts import AREA_BLOCK
from arden.core.spawn_spec import ParentRef, SpawnSpec, WorkflowRef
from arden.core.tool_executor import ArdenToolExecutor
from arden.core.usage_tracker import UsageTracker
from arden.events.sse import (
    BackgroundTaskEvent,
    SSEEvent,
    TaskFinishedEvent,
    TaskProgressEvent,
    TaskStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    TokenUsageEvent,
    agent_events_to_sse,
)
from arden.llm.models import get_model
from arden.logging import get_logger
from arden.observability import observed_trace
from arden.tools.core.base import Tool
from arden.tools.core.context import ChildIOParams, IOBridge, RunContext, ToolContext

# `tools` is already this module's schema-list parameter; alias the filter
# namespace rather than shadow it.
from arden.tools.core.scope import ToolFilter
from arden.tools.core.scope import tools as tool_filters
from arden.tools.deferred import append_deferred_tools_prompt, tool_schema_names
from arden.tools.executor import ToolExecutor


@dataclass(frozen=True)
class SpawnResult:
    """What `spawn_fn` returns to its caller (research/background tools).

    `text` is the subagent's final message — the only thing that flows into
    the parent's context. `usage` and `cost` describe the subagent's
    internal LLM spend; the calling tool surfaces them via `ToolResult.data`
    so the desktop can render per-agent budget breakdowns without polluting
    the parent's context size with the subagent's internals.

    `child_run_id`, `agent_type`, and `wait` are the generic child-agent
    contract. Foreground/background execution are wait policies on that
    contract, not separate entities.

    Detached spawns return early (before the subagent has run), so their
    `usage` and `cost` are `None` — the eventual real result is delivered
    via the background-task registry on a separate channel.
    """

    text: str
    usage: dict | None = None
    cost: float | None = None
    child_run_id: str = ""
    child_session_id: str | None = None
    parent_tool_call_id: str | None = None
    agent_type: str = "sub_agent"
    wait: bool = True
    status: str = "completed"
    source_refs: tuple[ToolSourceRef, ...] = ()
    tool_call_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_refs", normalize_source_refs(self.source_refs))
        object.__setattr__(
            self, "tool_call_ids", tuple(dict.fromkeys(call_id for call_id in self.tool_call_ids if call_id))
        )

    def child_agent_data(self) -> dict:
        if not self.child_run_id:
            return {}
        child_agent = {
            "child_run_id": self.child_run_id,
            "parent_tool_call_id": self.parent_tool_call_id,
            "agent_type": self.agent_type,
            "wait": self.wait,
            "status": self.status,
        }
        if self.child_session_id:
            child_agent["child_session_id"] = self.child_session_id
        if self.tool_call_ids:
            child_agent["tool_call_ids"] = list(self.tool_call_ids)
        data = {"child_agent": child_agent}
        if self.source_refs:
            data["source_refs"] = [ref.to_dict() for ref in self.source_refs]
        return data


_logger = get_logger(__name__)

_REASONING_EVENTS = (ReasoningBlock, ReasoningStarted, ReasoningDelta, ReasoningEnded)

# A sub-agent forwards its events to the PARENT stream, where its token text is
# pure overload: every TEXT_MESSAGE_* handler on the desktop is gated on
# `!event.depth`, so nested (depth>0) message text is dropped on arrival, and
# the sub-agent's final text reaches the caller via Result — not the deltas.
# Suppressing it at the source collapses the firehose (token deltas are the
# bulk of a ~1.5k-event sub-agent run) to the coarse tool-call/result lifecycle
# the activity tree actually shows — mirroring Letta/Claude Code, which send the
# parent the sub-agent's tool calls + result, not its inner stream.
#
# Tool-call ARGS are deliberately NOT suppressed: TOOL_CALL_ARGS has no depth
# gate on the client and feeds the nested row's label (formatCallTarget), so
# dropping it would blank out every sub-agent tool row's target. (Streaming
# providers emit args as several small deltas the client accumulates — still
# orders of magnitude less volume than the token text, which is the firehose.)
_SUPPRESSED_NESTED_SSE = (
    TextMessageStartEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
)

# Salvage tunables — used when the inner agent's LLM call fails and we
# try to summarize whatever tool results were gathered before the error.
_SALVAGE_TOOL_CHAR_LIMIT = 4000
_SALVAGE_MAX_TOKENS = 2000
_SALVAGE_TAIL_RESULTS = 20

# A resumed parent that finds its awaited child dead (pending suspension +
# interrupted bg row) re-spawns it — but LLM work costs dollars, so repeated
# restart crashes must not respawn forever.
_MAX_SUBAGENT_RESPAWNS = 2


async def _suspension_callback(callback, label: str, **kwargs) -> None:
    """Best-effort durable-suspension write: the in-process await is the
    behavior, the row is the recovery record — a store hiccup must not fail
    the spawn itself."""
    if callback is None:
        return
    try:
        await callback(**kwargs)
    except asyncio.CancelledError:
        raise
    except Exception:
        _logger.exception("Subagent suspension %s failed", label)


def _compactor_with_prompt_context(
    compactor: Compactor | None,
    prompt_context: str | None,
    *,
    include_tool_messages: bool = False,
) -> Compactor | None:
    if prompt_context != "research":
        return compactor
    if compactor is None:
        return None
    with_prompt_context = getattr(compactor, "with_prompt_context", None)
    if callable(with_prompt_context):
        return with_prompt_context(
            RESEARCH_AGENT_COMPACTION_CONTEXT,
            include_tool_messages=include_tool_messages,
        )
    return compactor


def get_response_cost(response) -> float:
    try:
        return get_model(response.model).pricing.cost(response.usage)
    except ValueError:
        return 0.0


def _clamp_for_salvage(msg: dict) -> dict:
    """Defensive clamp on tool/assistant content before re-sending to the
    model for the salvage summary — the original failure may have been
    triggered by an oversized tool result, and we don't want to fail the
    salvage pass for the same reason. Handles both plain-string content
    and the list-of-blocks shape that providers use for tool results
    with images or structured payloads."""
    if msg.get("role") not in ("tool", "assistant"):
        return msg
    content = msg.get("content")
    if isinstance(content, str):
        if len(content) <= _SALVAGE_TOOL_CHAR_LIMIT:
            return msg
        head = content[: _SALVAGE_TOOL_CHAR_LIMIT - 60]
        return {**msg, "content": head + "\n\n[clamped for salvage summary]"}
    if isinstance(content, list):
        # Flatten to a string so we never re-emit huge multi-part blocks
        # to the salvage LLM. Crude but safe.
        flat = "\n".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in content)
        if len(flat) <= _SALVAGE_TOOL_CHAR_LIMIT:
            return {**msg, "content": flat}
        head = flat[: _SALVAGE_TOOL_CHAR_LIMIT - 60]
        return {**msg, "content": head + "\n\n[clamped for salvage summary]"}
    return msg


@observed_trace("agent.salvage", tags="agent")
async def _salvage_summary(model: str, child_messages: list[dict], error: str, task: str) -> str:
    """Ask the model to summarize what it found before erroring. Returns
    "" if even this attempt fails (caller falls back to deterministic)."""
    salvage_messages = [_clamp_for_salvage(m) for m in child_messages]
    salvage_messages.append(
        {
            "role": Role.USER,
            "content": (
                f"Your previous step errored: {error}\n\n"
                f"Original task: {task}\n\n"
                "Without making any more tool calls, summarize the partial findings "
                "from the tool results above. Be honest about gaps. The parent agent "
                "will use this as a partial answer, so make every fact recoverable."
            ),
        }
    )
    try:
        response = await llm_client.complete(
            model=model,
            messages=salvage_messages,
            temperature=0.2,
            max_tokens=_SALVAGE_MAX_TOKENS,
        )
        return (response.choices[0].message.content or "").strip()
    except Exception as e:
        _logger.warning("Salvage summary call failed: %s", e)
        return ""


def _deterministic_salvage(child_messages: list[dict], error: str) -> str:
    """Last-resort fallback when the LLM-based salvage also fails: emit a
    flat list of the tail tool results so the parent at least sees raw
    evidence of what was gathered."""
    tool_results: list[str] = []
    for msg in child_messages:
        if msg.get("role") != "tool":
            continue
        content = (msg.get("content") or "")[:300]
        tool_results.append(f"- {content}")
    body = "\n".join(tool_results[-_SALVAGE_TAIL_RESULTS:])
    return (
        f"[partial — sub-agent errored: {error}]\n"
        f"Last {min(len(tool_results), _SALVAGE_TAIL_RESULTS)} tool results before "
        f"the error:\n{body or '(none)'}"
    )


def _deterministic_cancel_salvage(child_messages: list[dict]) -> str:
    tool_results: list[str] = []
    for msg in child_messages:
        if msg.get("role") not in ("tool", "assistant"):
            continue
        content = (msg.get("content") or "")[:300]
        if content:
            tool_results.append(f"- {content}")
    body = "\n".join(tool_results[-_SALVAGE_TAIL_RESULTS:])
    return (
        "[partial - sub-agent cancelled]\n"
        f"Last {min(len(tool_results), _SALVAGE_TAIL_RESULTS)} findings before "
        f"cancellation:\n{body or '(none)'}"
    )


def _deterministic_empty_salvage(child_messages: list[dict]) -> str:
    tool_results: list[str] = []
    for msg in child_messages:
        if msg.get("role") not in ("tool", "assistant"):
            continue
        content = (msg.get("content") or "")[:300]
        if content:
            tool_results.append(f"- {content}")
    if not tool_results:
        return "[partial — sub-agent returned empty final answer]\nNo recoverable tool results were produced."
    body = "\n".join(tool_results[-_SALVAGE_TAIL_RESULTS:])
    return (
        "[partial — sub-agent returned empty final answer]\n"
        f"Last {min(len(tool_results), _SALVAGE_TAIL_RESULTS)} recoverable findings:\n{body}"
    )


async def _emit_visible_child_final(child_io: IOBridge | None, text: str) -> None:
    if child_io is None or child_io.emit is None or not text.strip():
        return
    message_id = f"salvage-{uuid4().hex[:10]}"
    await child_io.emit(TextMessageStartEvent(message_id=message_id, role="assistant"))
    await child_io.emit(TextMessageContentEvent(message_id=message_id, delta=text))
    await child_io.emit(TextMessageEndEvent(message_id=message_id, content=text))


def _create_session_state(calling_ctx: ToolContext, isolation: IsolationLevel) -> SessionState:
    if isolation == IsolationLevel.SHARED:
        return calling_ctx.session_state

    child_session_id = f"{calling_ctx.session_id}::{uuid4().hex[:8]}"
    return SessionState(
        session_id=child_session_id,
        started_at=datetime.now(UTC),
        auto_approve=calling_ctx.session_state.auto_approve,
    )


def _with_area_context(system_prompt: str, calling_ctx: ToolContext) -> str:
    if not calling_ctx.area:
        return system_prompt
    return f"{system_prompt}\n\n{AREA_BLOCK.render(area=calling_ctx.area)}"


def create_spawn_fn(
    executor: ToolExecutor,
    model: str,
    max_depth: int,
    current_depth: int,
    reasoning_effort: str | None = None,
    model_reasoning_efforts: dict[str, str] | None = None,
    compactor: Compactor | None = None,
    max_iterations: int | None = None,
    max_tool_calls: int | None = None,
    max_wall_time_seconds: float | None = None,
    max_cost: float | None = None,
    started_at: float | None = None,
    budget: RunBudget | None = None,
):
    @observed_trace("agent.spawn", tags="agent")
    async def spawn_child(
        calling_ctx: ToolContext,
        task: str,
        *,
        system_prompt: str,
        tools: list[dict] | None = None,
        timeout: int = SUBAGENT_DEFAULT_TIMEOUT,
        model_override: str | None = None,
        reasoning_effort_override: str | None = None,
        parent_id: str | None = None,
        isolation: IsolationLevel = IsolationLevel.FULL,
        silent: bool = False,
        background: bool = False,
        wait: bool | None = None,
        agent_type: str | None = None,
        kind: str = "sub-agent",
        extra_tools: Mapping[str, Tool] | None = None,
        compaction_prompt_context: str | None = None,
        include_tool_messages_in_compaction: bool = False,
        research_scope_id: str | None = None,
        lifecycle_id: str | None = None,
        workflow_id: str | None = None,
        phase: str | None = None,
        scope: ToolFilter | None = None,
        exclude_tools: frozenset[str] | None = None,
        task_id: str | None = None,
    ) -> str:
        should_wait = (not background) if wait is None else wait
        background = not should_wait
        # Restart recovery re-spawns under the original durable task id so the
        # background_agent_runs row (and its spawn_attempts budget) stays one
        # identity across process restarts.
        child_run_id = task_id or f"agent-{uuid4().hex[:10]}"
        resolved_agent_type = agent_type or kind.replace("-", "_").replace(" ", "_")
        task_summary = task[:120]
        # Durable-await key: one suspension row per awaited child. A research
        # call has one child and a tool_call_id that is stable across a run
        # resume; workflow fan-out shares one tool_call_id across MANY children,
        # so the per-spawn lifecycle_id keys those instead. Workflow recovery
        # does not go through these rows at all — a re-executed workflow tool
        # call replays finished spawns from the Orchestra journal and re-spawns
        # the rest with fresh lifecycle_ids; the old rows just settle inert.
        suspension_id = (lifecycle_id or parent_id) if should_wait else None
        parent_io = calling_ctx.io
        respawns = 0
        if suspension_id and parent_io.get_suspension is not None:
            try:
                suspension = await parent_io.get_suspension(
                    run_id=calling_ctx.run.run_id,
                    suspension_id=suspension_id,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                suspension = None
                _logger.exception("Subagent suspension lookup failed")
            if suspension is not None and suspension.get("kind") == "subagent_result":
                payload = suspension.get("payload") or {}
                if suspension.get("status") != "pending":
                    # A previous process already ran this child to a terminal
                    # state — hand back the recorded outcome without spawning.
                    await _suspension_callback(
                        parent_io.consume_suspension,
                        "consume",
                        run_id=calling_ctx.run.run_id,
                        suspension_id=suspension_id,
                    )
                    resolution = suspension.get("resolution") or {}
                    return SpawnResult(
                        text=str(resolution.get("result") or ""),
                        child_run_id=str(payload.get("child_run_id") or ""),
                        child_session_id=payload.get("child_session_id"),
                        parent_tool_call_id=parent_id,
                        agent_type=resolved_agent_type,
                        wait=True,
                        status=str(resolution.get("status") or "completed"),
                    )
                # Pending row on a fresh lookup = the child died with a previous
                # process (its bg row was marked interrupted at boot). Re-spawn
                # from this call's arguments — a replayed tool call carries the
                # same spawn inputs — but bounded, so crash-looping restarts
                # can't re-buy the same LLM work forever.
                respawns = int(payload.get("respawns") or 0) + 1
                if respawns > _MAX_SUBAGENT_RESPAWNS:
                    failure = (
                        f"Sub-agent did not survive {_MAX_SUBAGENT_RESPAWNS + 1} attempts across server "
                        "restarts — not respawning again. Re-issue the task explicitly if it still matters."
                    )
                    await _suspension_callback(
                        parent_io.resolve_suspension,
                        "resolve",
                        run_id=calling_ctx.run.run_id,
                        suspension_id=suspension_id,
                        status="failed",
                        resolution={"status": "failed", "result": failure},
                    )
                    await _suspension_callback(
                        parent_io.consume_suspension,
                        "consume",
                        run_id=calling_ctx.run.run_id,
                        suspension_id=suspension_id,
                    )
                    return SpawnResult(
                        text=failure,
                        child_run_id=str(payload.get("child_run_id") or ""),
                        child_session_id=payload.get("child_session_id"),
                        parent_tool_call_id=parent_id,
                        agent_type=resolved_agent_type,
                        wait=True,
                        status="failed",
                    )
        # A distinct slug still names parent activity rows immediately, so
        # concurrent sub-agents do not collapse into N generic "Agent" rows.
        agent_slug = generate_slug(2)
        child_executor_source = executor
        child_registry = executor.registry
        if extra_tools:
            shadowed = {name for name in extra_tools if executor.registry.get_source(name) == "user"}
            if shadowed:
                raise ValueError("user tools cannot shadow child-only tools: " + ", ".join(sorted(shadowed)))
            # Inject only tools not already registered. A specialized agent type
            # (research) carries its full toolset and re-passes it on every spawn,
            # but a NESTED spawn already inherited those tools — and copy_with raises
            # on duplicates. The inherited ones still surface via get_tools below.
            missing = {name: t for name, t in extra_tools.items() if name not in executor.registry}
            if missing:
                child_registry = executor.registry.copy_with(missing)
                child_executor_source = executor.with_registry(child_registry)

        # `scope` is the agent-type capability filter (tools.read for analysts,
        # None for full). A type's own extra_tools are unioned onto it by name:
        # research's ledger tools WRITE, so a read-only analyst scope would drop
        # the very tools that define the type. The hard gate for a child is
        # `parent_allowed` below, not this — a child can never exceed its parent
        # however wide its type is.
        child_scope = scope
        if child_scope is not None and extra_tools:
            for extra_name in extra_tools:
                child_scope = child_scope | tool_filters.named(extra_name)
        full_tools = child_executor_source.get_tools(scope=child_scope)
        # `tools` is either schema dicts (research/background pass these) or a
        # name allowlist a workflow author wrote, e.g. ["slack_search", ...].
        # Resolve names against the full toolset so a name-restricted agent still
        # gets WORKING tools — passing raw name strings as schemas would make
        # every downstream `t.get("function")` raise and the agent come back empty.
        if tools is None:
            filtered_tools = full_tools
        elif tools and all(isinstance(t, str) for t in tools):
            wanted = set(tools)
            filtered_tools = [t for t in full_tools if t.get("function", {}).get("name") in wanted]
        else:
            filtered_tools = tools
        parent_allowed = calling_ctx.run.allowed_tool_names
        if parent_allowed is not None:
            filtered_tools = [
                schema for schema in filtered_tools if schema.get("function", {}).get("name") in parent_allowed
            ]
        if exclude_tools:
            filtered_tools = [t for t in filtered_tools if t.get("function", {}).get("name") not in exclude_tools]
        allowed_tool_names = tool_schema_names(filtered_tools)
        child_model = model_override or model
        child_state = _create_session_state(calling_ctx, isolation)
        # FULL isolation mints a distinct child session ({parent}::{hex}); SHARED
        # reuses the parent's. Advertise child_session_id on this fact alone —
        # not on whether the row already persisted — so a provisioning hiccup
        # can't strip the id and leave the UI unable to open or clear the agent.
        has_child_session = child_state.session_id != calling_ctx.session_id
        if has_child_session:
            child_state.name = task_summary or agent_slug
            child_state.session_type = "agent"
            child_state.parent_session_id = calling_ctx.session_id
            child_state.parent_tool_call_id = parent_id
            child_state.agent_type = resolved_agent_type
            child_state.agent_status = "running"
            child_state.area_id = calling_ctx.session_state.area_id
            child_state.chat_model = child_model
        # A caller spawning FOR a role (research, a workflow agent) names the
        # effort that role is configured at. Otherwise effort follows the model.
        child_reasoning_effort = reasoning_effort_override or (
            model_reasoning_efforts.get(child_model) if model_reasoning_efforts is not None else reasoning_effort
        )

        child_run = RunContext(
            run_id=calling_ctx.run.run_id,
            current_depth=current_depth + 1,
            max_depth=max_depth,
            max_iterations=max_iterations,
            max_tool_calls=max_tool_calls,
            max_wall_time_seconds=max_wall_time_seconds,
            max_cost=max_cost,
            started_at=calling_ctx.run.started_at if calling_ctx.run.started_at is not None else started_at,
            budget=calling_ctx.run.budget or budget,
            extra_auto_approve=calling_ctx.run.extra_auto_approve,
            approval_controls=calling_ctx.run.approval_controls,
            research_model=calling_ctx.run.research_model,
            research_reasoning_effort=calling_ctx.run.research_reasoning_effort,
            workflow_reasoning_effort=calling_ctx.run.workflow_reasoning_effort,
            deferred_tools_enabled=calling_ctx.run.deferred_tools_enabled,
            deferred_tool_loader=calling_ctx.run.deferred_tool_loader,
            loaded_tools=set(calling_ctx.run.loaded_tools),
            allowed_tool_names=allowed_tool_names,
            research_scope_id=research_scope_id or calling_ctx.run.research_scope_id,
            child_io_factory=calling_ctx.run.child_io_factory,
        )

        # A FULL subagent streams to its OWN session bus (built by the chat
        # service's child_io_factory) so its session behaves exactly like a
        # normal run — the parent gets only lifecycle events. SHARED subagents
        # have no distinct session, so they keep nesting into the parent's io.
        child_io: IOBridge | None = None
        child_io_close = None
        child_io_finish = None
        # A FULL child's OWN spawns record through the CHILD session's registry
        # (provided by the io factory, wired for durable rows + nested result
        # delivery); everything else keeps sharing the caller's registry.
        child_background_tasks = calling_ctx.background_tasks
        if has_child_session and calling_ctx.run.child_io_factory is not None:
            child_session = await calling_ctx.run.child_io_factory(
                ChildIOParams(
                    session_id=child_state.session_id,
                    run_id=calling_ctx.run.run_id,
                    pending_approvals=calling_ctx.io.pending_approvals or {},
                    task_id=child_run_id,
                    parent_background_tasks=calling_ctx.background_tasks,
                )
            )
            child_io = child_session.io
            child_io_close = child_session.aclose
            child_io_finish = child_session.finish
            if child_session.background_tasks is not None:
                child_background_tasks = child_session.background_tasks
            bg_io = child_io
            if calling_ctx.run_registry is not None:
                calling_ctx.run_registry.mark_session_active(child_state.session_id, calling_ctx.run.run_id)
        elif background or silent:
            bg_io = IOBridge()
        else:
            bg_io = calling_ctx.io

        child_ctx = ToolContext(
            session_state=child_state,
            registry=child_registry,
            run=child_run,
            io=bg_io,
            services=calling_ctx.services,
            area=calling_ctx.area,
            ledger=calling_ctx.ledger,
            background_tasks=child_background_tasks,
            run_registry=calling_ctx.run_registry,
        )
        child_ctx.spawn_fn = create_spawn_fn(
            executor=child_executor_source,
            model=child_model,
            max_depth=max_depth,
            current_depth=current_depth + 1,
            reasoning_effort=child_reasoning_effort,
            model_reasoning_efforts=model_reasoning_efforts,
            compactor=compactor,
            max_iterations=max_iterations,
            max_tool_calls=max_tool_calls,
            max_wall_time_seconds=max_wall_time_seconds,
            max_cost=max_cost,
            started_at=child_run.started_at,
            budget=child_run.budget,
        )

        child_executor = ArdenToolExecutor(
            child_executor_source,
            child_ctx,
            ledger=calling_ctx.ledger,
            skip_duplicate_reads=True,
        )
        child_system_prompt = append_deferred_tools_prompt(
            _with_area_context(system_prompt, calling_ctx),
            child_registry,
            frozenset(child_ctx.services),
            filtered_tools,
            enabled=child_run.deferred_tools_enabled,
        )

        parent_emit = calling_ctx.io.emit if not silent else None
        lifecycle_task_id = lifecycle_id or parent_id or f"task-{uuid4().hex[:10]}"
        agent_label_task = (
            asyncio.create_task(generate_agent_name(child_model, task)) if parent_emit and not background else None
        )
        task_depth = current_depth + 1

        # Sub-agents reuse the parent's compactor so a long-running tool
        # sweep doesn't blow past the model's context window mid-run. The
        # salvage path stays as a backstop, but compaction prevents the
        # underlying failure in the first place.
        middlewares: tuple = (
            DeferredToolsModelRequestMiddleware(
                registry=child_registry,
                run=child_run,
                get_services=lambda: child_ctx.services,
            ),
            ToolResultContextBudgetMiddleware(),
        )
        compaction_emit = parent_emit if parent_id and not background else None
        compaction_scope = "agent" if compaction_emit else "run"
        agent_compactor = _compactor_with_prompt_context(
            compactor,
            compaction_prompt_context,
            include_tool_messages=include_tool_messages_in_compaction,
        )
        if agent_compactor is not None:
            middlewares = (
                *middlewares,
                CompactionModelRequestMiddleware(
                    compactor=agent_compactor,
                    on_compact=child_run.loaded_tools.clear,
                    get_rehydration_state=child_ctx.to_rehydration_state,
                    apply_rehydration_state=child_run.apply_rehydration_state,
                    emit=compaction_emit,
                    run_id=calling_ctx.run.run_id,
                    scope=compaction_scope,
                    parent_tool_call_id=parent_id if compaction_emit else None,
                ),
                ToolResultContextBudgetMiddleware(),
            )

        sub_tracker = UsageTracker()

        sub_agent = Agent(
            tools=filtered_tools,
            client=llm_client,
            executor=child_executor,
            model=child_model,
            max_iterations=max_iterations,
            max_tool_calls=max_tool_calls,
            max_wall_time_seconds=max_wall_time_seconds,
            max_cost=max_cost,
            max_depth=max_depth,
            current_depth=current_depth + 1,
            parent_id=parent_id,
            reasoning_effort=child_reasoning_effort,
            prompt_cache_key=child_state.session_id,
            model_request_middlewares=middlewares,
            cost_getter=(lambda: calling_ctx.parent_tracker.cost) if calling_ctx.parent_tracker is not None else None,
            started_at=child_run.started_at,
            budget=child_run.budget,
        )

        # Each spawned subagent gets its own UsageTracker so we can attribute
        # its cost (and any nested sub-subagent costs that already rolled up
        # into it) to the caller. After the subagent finishes we roll its
        # `cost` into the parent's tracker — but NOT its `usage`, because the
        # parent's `usage.prompt` is what drives the on-screen "context size"
        # gauge, and the parent's context never actually held the subagent's
        # internal back-and-forth.
        # `hasattr` so test-fake agents without hooks don't trip — they just
        # won't accumulate usage, which is the right behavior for a fake.
        if hasattr(sub_agent, "hooks"):
            sub_agent.hooks.on_response = sub_tracker.track
            if calling_ctx.parent_tracker is not None:

                async def track_parent(response) -> None:
                    await sub_tracker.track(response)
                    cost = get_response_cost(response)
                    calling_ctx.parent_tracker.cost += cost
                    if parent_emit:
                        await parent_emit(
                            TokenUsageEvent(
                                run_id=calling_ctx.run.run_id,
                                usage=response.usage.to_dict(),
                                cost=cost,
                                scope="tool",
                                task_id=lifecycle_task_id,
                                child_run_id=child_run_id,
                                workflow_id=workflow_id,
                                phase=phase,
                            )
                        )

                sub_agent.hooks.on_response = track_parent
        # Hand sub_tracker down so a nested sub-subagent rolls its cost into
        # *this* subagent's tracker — costs cascade up one level at a time.
        child_ctx.parent_tracker = sub_tracker

        child_messages = [
            {"role": Role.SYSTEM, "content": child_system_prompt},
            {"role": Role.USER, "content": task},
        ]
        # Everything is resolved by here — freeze the spawn as a durable value.
        # Live objects never cross this boundary; the spec is what makes the
        # spawn re-dispatchable and attributable after this process is gone.
        spawn_spec = SpawnSpec(
            context=tuple(dict(m) for m in child_messages),
            agent_type=resolved_agent_type,
            tools=tuple(sorted(allowed_tool_names or ())),
            model=child_model,
            reasoning_effort=child_reasoning_effort,
            timeout_seconds=timeout,
            isolation=isolation,
            parent=ParentRef(
                session_id=calling_ctx.session_id,
                run_id=calling_ctx.run.run_id,
                tool_call_id=parent_id,
            ),
            workflow=WorkflowRef(workflow_id=workflow_id, phase=phase, lifecycle_id=lifecycle_id)
            if workflow_id
            else None,
        )
        session_service = calling_ctx.services.get("session")
        child_session_persisted = False

        async def _provision_child_session() -> None:
            nonlocal child_session_persisted
            if child_session_persisted or child_state.session_id == calling_ctx.session_id:
                return
            provision_state = getattr(session_service, "provision_state", None)
            if provision_state is None:
                return
            try:
                await provision_state(child_state, [])
                child_session_persisted = True
            except Exception as exc:
                _logger.warning("Failed to provision child agent session: %s", exc)

        async def _save_child_session(status: str) -> None:
            if child_state.session_id == calling_ctx.session_id:
                return
            save_child = getattr(session_service, "save", None)
            if save_child is not None:
                child_state.agent_status = status
                try:
                    await save_child(child_state, child_messages)
                except Exception as exc:
                    _logger.warning("Failed to save child agent session: %s", exc)
            # Close the child session's run framing on a terminal status (once),
            # independent of message persistence: records the durable run status +
            # emits RunFinished/RunCancelled on the child bus so the viewed session
            # collapses from live → done instead of streaming forever.
            if child_io_finish is not None and status != "running":
                await child_io_finish(status)

        async def _save_child_step(_step: int, _response, messages: list[dict]) -> None:
            if messages is child_messages:
                await _save_child_session("running")

        await _provision_child_session()
        if hasattr(sub_agent, "hooks"):
            sub_agent.hooks.on_step_finish = _save_child_step

        def _foreground_child_events(event) -> tuple[SSEEvent, ...]:
            if isinstance(event, _REASONING_EVENTS):
                return ()
            return tuple(e for e in agent_events_to_sse(event) if not isinstance(e, _SUPPRESSED_NESTED_SSE))

        def _noop_child_events(_event) -> tuple[SSEEvent, ...]:
            # FULL subagent: none of the child's own stream goes to the parent
            # (the parent renders session-backed agents as leaves) — it streams to
            # the child bus instead, and the parent gets only lifecycle events.
            # To watch a FULL agent's tool calls live, open its session (drill-in).
            return ()

        def _rebase_for_child(sse: SSEEvent) -> SSEEvent:
            # The child agent runs at the global recursion depth (task_depth) with
            # parent_id pointing at the spawn call in the PARENT. Re-base onto the
            # child session's own frame so its top level is depth 0 with no parent
            # — the child session then renders un-nested, like any normal run.
            depth = getattr(sse, "depth", None)
            if depth is None:
                return sse
            sse_parent = getattr(sse, "parent_id", None)
            return replace(
                sse,
                depth=max(0, depth - task_depth),
                parent_id=None if sse_parent == parent_id else sse_parent,
            )

        stream_failed = False
        tool_call_count = 0
        child_tool_call_ids: list[str] = []
        child_source_refs: list[ToolSourceRef] = []

        async def _stream_to(to_events) -> str:
            nonlocal stream_failed, tool_call_count
            text = ""
            try:
                async for event in sub_agent.stream(child_messages):
                    if isinstance(event, Result):
                        text = event.text
                        continue
                    if isinstance(event, ToolStarted) and event.tool_id not in child_tool_call_ids:
                        child_tool_call_ids.append(event.tool_id)
                    if isinstance(event, ToolCompleted):
                        tool_call_count += 1
                        if event.tool_id not in child_tool_call_ids:
                            child_tool_call_ids.append(event.tool_id)
                        child_source_refs.extend(event.source_refs)
                    if child_io is not None:
                        for sse in agent_events_to_sse(event):
                            await child_io.emit(_rebase_for_child(sse))
                    if parent_emit:
                        mapped = to_events(event)
                        if isawaitable(mapped):
                            mapped = await mapped
                        for out in mapped:
                            await parent_emit(out)
                return text
            except asyncio.CancelledError:
                # User-initiated cancel — don't try to salvage, just propagate.
                raise
            except Exception as exc:
                # Fatal LLM/transport error mid-run. Whatever the sub-agent
                # already gathered in `child_messages` is real work we paid
                # for; synthesize a summary instead of returning bare error.
                _logger.warning(
                    "Sub-agent failed mid-run after %d messages, salvaging: %s",
                    len(child_messages),
                    exc,
                )
                stream_failed = True
                failure_label = type(exc).__name__
                summary = await _salvage_summary(child_model, child_messages, failure_label, task)
                if summary:
                    return f"[partial — sub-agent errored: {failure_label}]\n\n{summary}"
                return _deterministic_salvage(child_messages, failure_label)

        def _settle_with(text: str, *, status: str = "completed") -> SpawnResult:
            """Build the final SpawnResult for the caller."""
            return SpawnResult(
                text=text,
                usage=sub_tracker.usage.to_dict(),
                cost=sub_tracker.cost,
                child_run_id=child_run_id,
                child_session_id=child_state.session_id if has_child_session else None,
                parent_tool_call_id=parent_id,
                agent_type=resolved_agent_type,
                wait=should_wait,
                status=status,
                source_refs=normalize_source_refs(child_source_refs),
                tool_call_ids=tuple(child_tool_call_ids),
            )

        registry = calling_ctx.background_tasks
        task_id = child_run_id
        label = "Agent"

        # Horizontal fan-out guard: cap concurrent background agents per session
        # so a runaway loop can't spawn unbounded detached agents. Awaited
        # spawns reserve uncapped — bounded by the awaiting parent (workflow
        # fan-out legitimately exceeds the cap) — but still hold a roster slot
        # so the durable row and the cancel cascade cover them.
        reserved = registry.reserve(
            task_id,
            command=label,
            limit=None if should_wait else AGENT_MAX_CONCURRENT,
            child_session_id=child_state.session_id if has_child_session else None,
            parent_run_id=calling_ctx.run.run_id,
        )
        if not reserved:
            return SpawnResult(
                text=(
                    f"Not started — already at the limit of {AGENT_MAX_CONCURRENT} concurrent "
                    "background agents in this session. Wait for some to finish, then try again."
                ),
                agent_type=resolved_agent_type,
                wait=should_wait,
                status="failed",
            )

        # Steering channel, both wait modes: the parent (or user) can send
        # messages to this running agent via registry.queue_injection(task_id,
        # …); the agent drains them at its next step. `hasattr` guards
        # test-fake agents.
        if hasattr(sub_agent, "hooks"):

            async def _drain_steering() -> list[dict]:
                return registry.drain_injections(task_id)

            sub_agent.hooks.get_pending_messages = _drain_steering

        undelivered_steering: list[str] = []

        def _close_inbox() -> None:
            undelivered_steering.extend(m.get("content", "") for m in registry.close_inbox(task_id))

        async def _execute(to_events) -> tuple[str, str, str]:
            """The one child execution both wait modes run: stream, timeout +
            salvage ladder (LLM salvage → deterministic fallback), empty-result
            salvage, salvage text made visible on the child bus. Returns
            (status, text, detail). Cancellation propagates to the mode-specific
            caller — after the inbox is closed."""
            timed_out = False
            try:
                text = await asyncio.wait_for(_stream_to(to_events), timeout=timeout)
                status = "failed" if stream_failed else "completed"
            except asyncio.CancelledError:
                _close_inbox()
                raise
            except TimeoutError:
                timed_out = True
                text = ""
                status = "failed"
            # The loop will never step again: close the inbox NOW — before the
            # salvage LLM calls below — so late steering gets a conflict instead
            # of a false "queued". Anything already queued arrived too late for
            # the agent and is surfaced with the result.
            _close_inbox()
            if timed_out:
                _logger.warning("Sub-agent %s timed out after %ss, salvaging", task_id, timeout)
                # Belt-and-suspenders: if the salvage call itself raises, still
                # emit the deterministic fallback so the terminal path always runs.
                try:
                    summary = await _salvage_summary(child_model, child_messages, f"timed out after {timeout}s", task)
                except Exception as salvage_exc:
                    _logger.warning("Timeout salvage failed: %s", salvage_exc)
                    summary = ""
                text = (
                    f"[partial — sub-agent timed out after {timeout}s]\n\n{summary}"
                    if summary
                    else _deterministic_salvage(child_messages, f"timed out after {timeout}s")
                )
            if not text.strip():
                status = "failed"
                summary = await _salvage_summary(child_model, child_messages, "completed with empty final answer", task)
                text = (
                    f"[partial — sub-agent returned empty final answer]\n\n{summary}"
                    if summary
                    else _deterministic_empty_salvage(child_messages)
                )
            if status != "completed":
                await _emit_visible_child_final(child_io, text)
            return status, text, f"timed out after {timeout}s" if timed_out else status

        async def _teardown_child_io() -> None:
            if calling_ctx.run_registry is not None and child_io is not None:
                calling_ctx.run_registry.clear_session_active(child_state.session_id)
            if child_io_close is not None:
                await child_io_close()

        try:
            await registry.record_started(
                task_id=task_id,
                command=label,
                parent_run_id=calling_ctx.run.run_id,
                parent_tool_call_id=parent_id,
                child_session_id=child_state.session_id if has_child_session else None,
                agent_type=resolved_agent_type,
                wait=should_wait,
                spawn_spec=spawn_spec.to_json(),
            )
        except BaseException:
            registry.release(task_id)
            raise

        if should_wait:
            # The in-process await below is still what delivers the result;
            # this row is what makes the wait durable — a restarted parent run
            # replays the spawning tool call and picks the outcome (or the
            # respawn) up from here. No expires_at: the child is bounded by its
            # own timeout and the row resolves when it settles.
            if suspension_id:
                await _suspension_callback(
                    parent_io.record_suspension,
                    "record",
                    run_id=calling_ctx.run.run_id,
                    session_id=calling_ctx.session_id,
                    suspension_id=suspension_id,
                    kind="subagent_result",
                    payload={
                        "task": task_summary,
                        "child_run_id": task_id,
                        "child_session_id": child_state.session_id if has_child_session else None,
                        "agent_type": resolved_agent_type,
                        "respawns": respawns,
                    },
                )

            async def _resolve_wait_suspension(status: str, text: str) -> None:
                if suspension_id is None:
                    return
                await _suspension_callback(
                    parent_io.resolve_suspension,
                    "resolve",
                    run_id=calling_ctx.run.run_id,
                    suspension_id=suspension_id,
                    status=status,
                    resolution={"status": status, "result": text},
                )

            label_update_task: asyncio.Task[None] | None = None

            async def _emit_agent_label_when_ready() -> None:
                if parent_emit is None or agent_label_task is None:
                    return
                try:
                    agent_label = await agent_label_task
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _logger.debug("Failed to generate sub-agent label", exc_info=True)
                    return
                if agent_label and agent_label != "Agent":
                    child_state.name = agent_label
                    await _save_child_session("running")
                    await parent_emit(
                        TaskProgressEvent(
                            session_id=calling_ctx.session_id,
                            run_id=calling_ctx.run.run_id,
                            task_id=lifecycle_task_id,
                            parent_tool_call_id=parent_id,
                            child_run_id=child_run_id,
                            child_session_id=child_state.session_id if has_child_session else None,
                            agent_type=resolved_agent_type,
                            wait=should_wait,
                            name=agent_label,
                            status="running",
                            summary=task_summary,
                            depth=task_depth,
                            workflow_id=workflow_id,
                            phase=phase,
                        )
                    )

            async def _settle_agent_label_update() -> None:
                if label_update_task is None:
                    return
                if agent_label_task is not None and agent_label_task.done():
                    with suppress(asyncio.CancelledError):
                        await label_update_task
                    return
                if not label_update_task.done():
                    label_update_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await label_update_task

            def _current_agent_label() -> str:
                if agent_label_task is not None and agent_label_task.done() and not agent_label_task.cancelled():
                    with suppress(Exception):
                        return agent_label_task.result()
                return "Agent"

            def _event_agent_label() -> str:
                agent_label = _current_agent_label()
                return agent_label if agent_label != "Agent" else agent_slug

            async def _emit_task_finished(status: str, summary: str) -> None:
                if not parent_emit:
                    return
                await parent_emit(
                    TaskFinishedEvent(
                        session_id=calling_ctx.session_id,
                        run_id=calling_ctx.run.run_id,
                        task_id=lifecycle_task_id,
                        parent_tool_call_id=parent_id,
                        child_run_id=child_run_id,
                        child_session_id=child_state.session_id if has_child_session else None,
                        agent_type=resolved_agent_type,
                        wait=should_wait,
                        name=_event_agent_label(),
                        status=status,
                        summary=summary,
                        depth=task_depth,
                        workflow_id=workflow_id,
                        phase=phase,
                        tool_count=tool_call_count,
                    )
                )

            async def _settle_foreground(status: str, text: str, summary: str) -> SpawnResult:
                await _settle_agent_label_update()
                await _save_child_session(status)
                await registry.record_finished(task_id=task_id, status=status, result_text=text)
                await _resolve_wait_suspension(status, text)
                await _emit_task_finished(status, summary)
                if undelivered_steering:
                    # An awaited result returns in-process — there is no delivery
                    # channel to carry leftovers, so refusal + log is the contract.
                    _logger.warning(
                        "Awaited sub-agent %s finished with %d undelivered steering message(s)",
                        task_id,
                        len(undelivered_steering),
                    )
                return _settle_with(text, status=status)

            async def _run_foreground() -> tuple[str, str, str]:
                nonlocal label_update_task
                if parent_emit:
                    await parent_emit(
                        TaskStartedEvent(
                            session_id=calling_ctx.session_id,
                            run_id=calling_ctx.run.run_id,
                            task_id=lifecycle_task_id,
                            parent_tool_call_id=parent_id,
                            child_run_id=child_run_id,
                            child_session_id=child_state.session_id if has_child_session else None,
                            agent_type=resolved_agent_type,
                            wait=should_wait,
                            # Name with the slug initially (distinct per agent) instead of the
                            # placeholder "Agent" — the async-generated descriptive label
                            # replaces it via task_progress when ready. Avoids N identical
                            # generic rows for concurrent sub-agents.
                            name=agent_slug,
                            summary=task_summary,
                            depth=task_depth,
                            workflow_id=workflow_id,
                            phase=phase,
                        )
                    )
                if parent_emit is not None and agent_label_task is not None:
                    label_update_task = asyncio.create_task(_emit_agent_label_when_ready())
                return await _execute(_noop_child_events if child_io is not None else _foreground_child_events)

            stream_task = asyncio.create_task(_run_foreground())
            registry.register(task_id, stream_task, command=label)
            subagent_handle = None
            if calling_ctx.run_registry is not None:
                subagent_handle = calling_ctx.run_registry.register_subagent(
                    calling_ctx.run.run_id,
                    lifecycle_task_id,
                    stream_task,
                )
            try:
                status, text, detail = await stream_task
                return await _settle_foreground(status, text, detail)
            except asyncio.CancelledError:
                run_state = (
                    calling_ctx.run_registry.get_run(calling_ctx.run.run_id)
                    if calling_ctx.run_registry is not None
                    else None
                )
                if run_state and not run_state.cancelled and subagent_handle and subagent_handle.cancel_requested:
                    summary = await _salvage_summary(
                        child_model,
                        child_messages,
                        "cancelled by user",
                        task,
                    )
                    text = (
                        f"[partial - sub-agent cancelled]\n\n{summary}"
                        if summary
                        else _deterministic_cancel_salvage(child_messages)
                    )
                    await _emit_visible_child_final(child_io, text)
                    return await _settle_foreground("cancelled", text, "cancelled; partial summary returned")
                await _settle_agent_label_update()
                await _save_child_session("cancelled")
                await registry.record_finished(task_id=task_id, status="cancelled")
                await _resolve_wait_suspension("cancelled", "")
                await _emit_task_finished("cancelled", "cancelled")
                raise
            finally:
                if calling_ctx.run_registry is not None:
                    calling_ctx.run_registry.finish_subagent(calling_ctx.run.run_id, lifecycle_task_id)
                await _teardown_child_io()
                if not stream_task.done():
                    stream_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await stream_task
                if label_update_task is not None and not label_update_task.done():
                    label_update_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await label_update_task
                if agent_label_task is not None and not agent_label_task.done():
                    agent_label_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await agent_label_task

        async def _to_bg_events(event):
            if isinstance(event, ToolStarted):
                detail = event.display_name or event.name
            elif isinstance(event, ToolCompleted):
                detail = f"{event.display_name or event.name}: {event.preview}"
            else:
                return ()
            await registry.record_activity(task_id, detail)
            return (
                BackgroundTaskEvent(
                    task_id=task_id,
                    session_id=registry.session_id,
                    run_id=calling_ctx.run.run_id,
                    child_run_id=child_run_id,
                    child_session_id=child_state.session_id if has_child_session else None,
                    parent_tool_call_id=parent_id,
                    agent_type=resolved_agent_type,
                    wait=should_wait,
                    command=label,
                    status="activity",
                    detail=detail,
                ),
            )

        async def _run_detached() -> None:
            try:
                status, result, _detail = await _execute(_to_bg_events)
            except asyncio.CancelledError:
                status, result = "cancelled", "Cancelled"
                await _emit_visible_child_final(child_io, result)
            except Exception as e:
                # _stream_to salvages its own failures — only exceptions outside
                # the stream loop land here. A detached task must still deliver.
                _logger.warning("Background task %s failed: %s", task_id, e)
                _close_inbox()
                status, result = "failed", f"Error: {e}"
                await _emit_visible_child_final(child_io, result)
            try:
                await _save_child_session(status)
                await registry.deliver_result(
                    task_id=task_id,
                    result=result,
                    label=label,
                    status=status,
                    emit=parent_emit,
                    child_session_id=child_state.session_id if has_child_session else None,
                    parent_tool_call_id=parent_id,
                    agent_type=resolved_agent_type,
                    wait=should_wait,
                    undelivered_steering=undelivered_steering or None,
                )
            except Exception:
                _logger.exception("Background task %s delivery failed", task_id)
            finally:
                # Idempotent terminal frame on the child bus so a viewed background
                # session collapses live→done even if save/deliver raised above
                # (foreground does this via _save_child_session; background must too).
                if child_io_finish is not None:
                    await child_io_finish(status)
                await _teardown_child_io()

        bg_task = asyncio.create_task(_run_detached())
        registry.register(task_id, bg_task, command=label)

        if calling_ctx.io.emit:
            await calling_ctx.io.emit(
                BackgroundTaskEvent(
                    task_id=task_id,
                    session_id=registry.session_id,
                    run_id=calling_ctx.run.run_id,
                    child_run_id=child_run_id,
                    child_session_id=child_state.session_id if has_child_session else None,
                    parent_tool_call_id=parent_id,
                    agent_type=resolved_agent_type,
                    wait=should_wait,
                    command=label,
                    status="started",
                )
            )

        # Background path returns immediately — the real result is delivered
        # asynchronously via registry.deliver_result. Usage/cost belong to
        # the background task's own ledger, not this caller's tool result.
        return SpawnResult(
            text=f"Started a background agent to: {task}",
            child_run_id=child_run_id,
            child_session_id=child_state.session_id if has_child_session else None,
            parent_tool_call_id=parent_id,
            agent_type=resolved_agent_type,
            wait=False,
            status="running",
        )

    return spawn_child
