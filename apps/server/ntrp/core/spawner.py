import asyncio
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from inspect import isawaitable
from uuid import uuid4

from coolname import generate_slug

from ntrp.agent import (
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
from ntrp.constants import AGENT_MAX_CONCURRENT, SUBAGENT_DEFAULT_TIMEOUT
from ntrp.context.models import SessionState
from ntrp.context.prompts import RESEARCH_AGENT_COMPACTION_CONTEXT
from ntrp.core.compaction_model_request_middleware import CompactionModelRequestMiddleware
from ntrp.core.compactor import Compactor
from ntrp.core.deferred_tools_middleware import DeferredToolsModelRequestMiddleware
from ntrp.core.isolation import IsolationLevel
from ntrp.core.llm_client import llm_client
from ntrp.core.model_context_budget import ToolResultContextBudgetMiddleware
from ntrp.core.naming import generate_agent_name
from ntrp.core.prompts import PROJECT_BLOCK
from ntrp.core.tool_executor import NtrpToolExecutor
from ntrp.core.usage_tracker import UsageTracker
from ntrp.events.sse import (
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
from ntrp.llm.models import get_model
from ntrp.logging import get_logger
from ntrp.observability import observed_trace
from ntrp.tools.core.base import Tool
from ntrp.tools.core.context import ChildIOParams, IOBridge, RunContext, ToolContext
from ntrp.tools.core.types import ToolAction
from ntrp.tools.deferred import append_deferred_tools_prompt, tool_schema_names
from ntrp.tools.executor import ToolExecutor


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
        return {
            "child_agent": child_agent,
        }


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


def _with_project_context(system_prompt: str, calling_ctx: ToolContext) -> str:
    if not calling_ctx.project:
        return system_prompt
    return f"{system_prompt}\n\n{PROJECT_BLOCK.render(project=calling_ctx.project)}"


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
        actions: frozenset[ToolAction] | None = None,
        exclude_tools: frozenset[str] | None = None,
    ) -> str:
        should_wait = (not background) if wait is None else wait
        background = not should_wait
        child_run_id = f"agent-{uuid4().hex[:10]}"
        resolved_agent_type = agent_type or kind.replace("-", "_").replace(" ", "_")
        task_summary = task[:120]
        # A distinct slug still names parent activity rows immediately, so
        # concurrent sub-agents do not collapse into N generic "Agent" rows.
        agent_slug = generate_slug(2)
        child_executor_source = executor
        child_registry = executor.registry
        if extra_tools:
            # Inject only tools not already registered. A specialized agent type
            # (research) carries its full toolset and re-passes it on every spawn,
            # but a NESTED spawn already inherited those tools — and copy_with raises
            # on duplicates. The inherited ones still surface via get_tools below.
            missing = {name: t for name, t in extra_tools.items() if name not in executor.registry}
            if missing:
                child_registry = executor.registry.copy_with(missing)
                child_executor_source = executor.with_registry(child_registry)

        # `actions` is the agent-type capability filter ({READ} for analysts, None
        # for full): it narrows the base toolset to those ToolActions, the same way
        # research read-only-filtered its tools. extra_tools (merged into the child
        # registry above) and exclude_tools then add/remove on top.
        full_tools = child_executor_source.get_tools(actions=actions)
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
            child_state.project_id = calling_ctx.session_state.project_id
            child_state.chat_model = child_model
        child_reasoning_effort = (
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
            deferred_tools_enabled=calling_ctx.run.deferred_tools_enabled,
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
        if has_child_session and calling_ctx.run.child_io_factory is not None:
            child_session = await calling_ctx.run.child_io_factory(
                ChildIOParams(
                    session_id=child_state.session_id,
                    run_id=calling_ctx.run.run_id,
                    pending_approvals=calling_ctx.io.pending_approvals or {},
                )
            )
            child_io = child_session.io
            child_io_close = child_session.aclose
            child_io_finish = child_session.finish
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
            project=calling_ctx.project,
            ledger=calling_ctx.ledger,
            background_tasks=calling_ctx.background_tasks,
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

        child_executor = NtrpToolExecutor(
            child_executor_source,
            child_ctx,
            ledger=calling_ctx.ledger,
            skip_duplicate_reads=True,
        )
        child_system_prompt = append_deferred_tools_prompt(
            _with_project_context(system_prompt, calling_ctx),
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

        async def _stream_to(to_events) -> str:
            nonlocal stream_failed, tool_call_count
            text = ""
            try:
                async for event in sub_agent.stream(child_messages):
                    if isinstance(event, Result):
                        text = event.text
                        continue
                    if isinstance(event, ToolCompleted):
                        tool_call_count += 1
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
                summary = await _salvage_summary(child_model, child_messages, str(exc), task)
                if summary:
                    return f"[partial — sub-agent errored: {exc}]\n\n{summary}"
                return _deterministic_salvage(child_messages, str(exc))

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
            )

        if not background:
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

            async def _run_foreground() -> tuple[str, str]:
                nonlocal label_update_task, stream_failed
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
                text = await _stream_to(_noop_child_events if child_io is not None else _foreground_child_events)
                if not text.strip():
                    stream_failed = True
                    summary = await _salvage_summary(
                        child_model,
                        child_messages,
                        "completed with empty final answer",
                        task,
                    )
                    text = (
                        f"[partial — sub-agent returned empty final answer]\n\n{summary}"
                        if summary
                        else _deterministic_empty_salvage(child_messages)
                    )
                if stream_failed:
                    await _emit_visible_child_final(child_io, text)
                return _current_agent_label(), text

            stream_task = asyncio.create_task(_run_foreground())
            subagent_handle = None
            if calling_ctx.run_registry is not None:
                subagent_handle = calling_ctx.run_registry.register_subagent(
                    calling_ctx.run.run_id,
                    lifecycle_task_id,
                    stream_task,
                )
            try:
                _agent_label, text = await asyncio.wait_for(stream_task, timeout=timeout)
                await _settle_agent_label_update()
                await _save_child_session("failed" if stream_failed else "completed")
                if parent_emit:
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
                            status="failed" if stream_failed else "completed",
                            summary="failed" if stream_failed else "completed",
                            depth=task_depth,
                            workflow_id=workflow_id,
                            phase=phase,
                            tool_count=tool_call_count,
                        )
                    )
                return _settle_with(text, status="failed" if stream_failed else "completed")
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
                    await _settle_agent_label_update()
                    await _save_child_session("cancelled")
                    if parent_emit:
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
                                status="cancelled",
                                summary="cancelled; partial summary returned",
                                depth=task_depth,
                                workflow_id=workflow_id,
                                phase=phase,
                                tool_count=tool_call_count,
                            )
                        )
                    return _settle_with(text, status="cancelled")
                await _settle_agent_label_update()
                await _save_child_session("cancelled")
                if parent_emit:
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
                            status="cancelled",
                            summary="cancelled",
                            depth=task_depth,
                            workflow_id=workflow_id,
                            phase=phase,
                            tool_count=tool_call_count,
                        )
                    )
                raise
            except TimeoutError:
                # Same idea on timeout — try to salvage what we collected.
                await _settle_agent_label_update()
                await _save_child_session("failed")
                if parent_emit:
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
                            status="failed",
                            summary=f"timed out after {timeout}s",
                            depth=task_depth,
                            workflow_id=workflow_id,
                            phase=phase,
                            tool_count=tool_call_count,
                        )
                    )
                _logger.warning("Sub-agent timed out after %ss, salvaging", timeout)
                summary = await _salvage_summary(child_model, child_messages, f"timed out after {timeout}s", task)
                if summary:
                    text = f"[partial — sub-agent timed out after {timeout}s]\n\n{summary}"
                    await _emit_visible_child_final(child_io, text)
                    return _settle_with(
                        text,
                        status="failed",
                    )
                text = _deterministic_salvage(child_messages, f"timed out after {timeout}s")
                await _emit_visible_child_final(child_io, text)
                return _settle_with(
                    text,
                    status="failed",
                )
            finally:
                if calling_ctx.run_registry is not None:
                    calling_ctx.run_registry.finish_subagent(calling_ctx.run.run_id, lifecycle_task_id)
                    if child_io is not None:
                        calling_ctx.run_registry.clear_session_active(child_state.session_id)
                if child_io_close is not None:
                    await child_io_close()
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

        registry = calling_ctx.background_tasks
        task_id = child_run_id
        label = "Agent"

        # Horizontal fan-out guard: cap concurrent background agents per session
        # so a runaway loop can't spawn unbounded detached agents.
        reserved = registry.reserve(
            task_id,
            command=label,
            limit=AGENT_MAX_CONCURRENT,
            child_session_id=child_state.session_id if has_child_session else None,
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

        # Steering channel: the parent (or user) can send messages to this
        # running agent via registry.queue_injection(task_id, …); the agent
        # drains them at its next step. `hasattr` guards test-fake agents.
        if hasattr(sub_agent, "hooks"):

            async def _drain_steering() -> list[dict]:
                return registry.drain_injections(task_id)

            sub_agent.hooks.get_pending_messages = _drain_steering

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

        async def _run_background():
            try:
                result = await asyncio.wait_for(_stream_to(_to_bg_events), timeout=timeout)
                status = "completed"
            except asyncio.CancelledError:
                result = "Cancelled"
                status = "cancelled"
            except TimeoutError:
                _logger.warning("Background task %s timed out, salvaging", task_id)
                # Belt-and-suspenders: if the salvage call itself raises
                # (e.g. cancelled mid-await), still emit the deterministic
                # fallback so deliver_result always runs.
                try:
                    summary = await _salvage_summary(child_model, child_messages, f"timed out after {timeout}s", task)
                except Exception as salvage_exc:
                    _logger.warning("Background salvage failed: %s", salvage_exc)
                    summary = ""
                result = (
                    f"[partial — background agent timed out after {timeout}s]\n\n{summary}"
                    if summary
                    else _deterministic_salvage(child_messages, f"timed out after {timeout}s")
                )
                status = "failed"
            except Exception as e:
                # _stream_to handles its own salvage internally, so we only
                # land here for exceptions outside the stream loop itself.
                result = f"Error: {e}"
                status = "failed"
                _logger.warning("Background task %s failed: %s", task_id, e)
            if not result.strip():
                result = _deterministic_empty_salvage(child_messages)
                status = "failed"
            if stream_failed or status != "completed":
                await _emit_visible_child_final(child_io, result)
            # A steering message can land after the loop drained but before the
            # task is marked done; it can't be honored now, so surface the drop
            # instead of silently discarding it on cleanup.
            if leftover := registry.drain_injections(task_id):
                _logger.warning(
                    "Dropped %d steering message(s) for finished background agent %s", len(leftover), task_id
                )
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
                )
            except Exception:
                _logger.exception("Background task %s delivery failed", task_id)
            finally:
                # Idempotent terminal frame on the child bus so a viewed background
                # session collapses live→done even if save/deliver raised above
                # (foreground does this via _save_child_session; background must too).
                if child_io_finish is not None and status != "running":
                    await child_io_finish(status)
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
            )
            bg_task = asyncio.create_task(_run_background())
            registry.register(task_id, bg_task, command=label)
        except BaseException:
            registry.release(task_id)
            raise

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
            text=(
                f"Started a background agent to: {task}\n"
                "It runs independently — I'll surface the results automatically when it finishes."
            ),
            child_run_id=child_run_id,
            child_session_id=child_state.session_id if has_child_session else None,
            parent_tool_call_id=parent_id,
            agent_type=resolved_agent_type,
            wait=False,
            status="running",
        )

    return spawn_child
