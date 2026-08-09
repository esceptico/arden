import asyncio
from collections.abc import Mapping
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
from arden.context.models import BackgroundStartDisposition, SessionState
from arden.context.prompts import RESEARCH_AGENT_COMPACTION_CONTEXT
from arden.core.compaction_model_request_middleware import CompactionModelRequestMiddleware
from arden.core.compactor import Compactor
from arden.core.deferred_tools_middleware import DeferredToolsModelRequestMiddleware
from arden.core.isolation import IsolationLevel
from arden.core.llm_client import llm_client
from arden.core.prompts import AREA_BLOCK, TEAM_CHILD_BLOCK
from arden.core.public_refs import is_public_ref, public_ref
from arden.core.spawn_lifecycle import (
    ChildSessionLifecycle,
    activate_child,
    append_failure,
    cancel_and_join,
    collect_failure,
    decode_child_suspension,
    finish_despite_cancellation,
    raise_failures,
    run_operations,
)
from arden.core.spawn_spec import ParentRef, SpawnSpec, WorkflowRef
from arden.core.tool_executor import ArdenToolExecutor
from arden.core.usage_tracker import UsageTracker
from arden.events.sse import (
    BackgroundTaskEvent,
    SSEEvent,
    TaskFinishedEvent,
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
from arden.tools.core.context import IOBridge, RunContext, ToolContext

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
    agent_ref: str | None = None
    child_session_ref: str | None = None
    parent_tool_call_id: str | None = None
    agent_type: str = "sub_agent"
    wait: bool = True
    status: str = "completed"
    source_refs: tuple[ToolSourceRef, ...] = ()
    tool_call_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.child_run_id and (self.agent_ref is None or not is_public_ref(self.agent_ref)):
            raise ValueError("a spawned child must have a valid agent_ref")
        if (self.child_session_id is None) != (self.child_session_ref is None):
            raise ValueError("child session id and ref must either both be present or both be absent")
        if self.child_session_ref is not None and self.child_session_ref != self.agent_ref:
            raise ValueError("a child session ref must equal its agent_ref")
        object.__setattr__(self, "source_refs", normalize_source_refs(self.source_refs))
        object.__setattr__(
            self, "tool_call_ids", tuple(dict.fromkeys(call_id for call_id in self.tool_call_ids if call_id))
        )

    def child_agent_data(self) -> dict:
        if not self.child_run_id:
            return {}
        child_agent = {
            "agent_ref": self.agent_ref,
            "agent_type": self.agent_type,
            "wait": self.wait,
            "status": self.status,
        }
        if self.child_session_ref:
            child_agent["session_ref"] = self.child_session_ref
        data = {"child_agent": child_agent}
        if self.source_refs:
            data["source_refs"] = [ref.to_dict() for ref in self.source_refs]
        return data


class _AgentStreamFailure(Exception):
    """An exception raised by the child agent itself, not its delivery path."""

    def __init__(self, error: Exception) -> None:
        super().__init__(str(error))
        self.error = error


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

# A resumed parent that finds its awaited child dead (pending suspension +
# interrupted bg row) re-spawns it — but LLM work costs dollars, so repeated
# restart crashes must not respawn forever.
_MAX_SUBAGENT_RESPAWNS = 2


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
    return compactor.with_prompt_context(
        RESEARCH_AGENT_COMPACTION_CONTEXT,
        include_tool_messages=include_tool_messages,
    )


def get_response_cost(response) -> float:
    return get_model(response.model).pricing.cost(response.usage)


async def _emit_visible_child_final(child_io: IOBridge | None, text: str) -> None:
    if child_io is None or child_io.emit is None or not text.strip():
        return
    message_id = f"failure-{uuid4().hex[:10]}"
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
        agent_ref: str | None = None,
        _resume_child_session_id: str | None = None,
    ) -> SpawnResult:
        should_wait = (not background) if wait is None else wait
        background = not should_wait
        # Restart recovery re-spawns under the original durable task id so the
        # background_agent_runs row (and its spawn_attempts budget) stays one
        # identity across process restarts.
        child_run_id = task_id or f"agent-{uuid4().hex[:10]}"
        resolved_agent_type = agent_type or kind.replace("-", "_").replace(" ", "_")
        task_summary = task[:120]
        if agent_ref is None:
            agent_ref = public_ref(task_summary or "agent", child_run_id, empty_slug="agent")
        elif not is_public_ref(agent_ref):
            raise ValueError("agent_ref is invalid")
        # Durable-await key: one suspension row per awaited child. A research
        # call has one child and a tool_call_id that is stable across a run
        # resume; workflow fan-out shares one tool_call_id across MANY children,
        # so the per-spawn lifecycle_id keys those instead. Workflow recovery
        # does not go through these rows at all — a re-executed workflow tool
        # call replays finished spawns from the Orchestra journal and re-spawns
        # the rest with fresh lifecycle_ids; the old rows just settle inert.
        suspension_id = (lifecycle_id or parent_id) if should_wait else None
        parent_io = calling_ctx.io
        registry = calling_ctx.background_tasks
        suspension_callbacks = (
            parent_io.get_suspension,
            parent_io.consume_suspension,
            parent_io.record_suspension,
            parent_io.resolve_suspension,
        )
        recovered_child_session_id = _resume_child_session_id
        if suspension_id:
            has_any_suspension_callback = any(callback is not None for callback in suspension_callbacks)
            has_all_suspension_callbacks = all(callback is not None for callback in suspension_callbacks)
            has_durable_agent_store = registry.record_event is not None
            if has_any_suspension_callback != has_all_suspension_callbacks:
                raise RuntimeError("subagent suspension callbacks must be wired together")
            if has_all_suspension_callbacks != has_durable_agent_store:
                raise RuntimeError("subagent suspension and agent-run persistence must be wired together")
        respawns = 0
        if suspension_id and parent_io.get_suspension is not None:
            raw_suspension = await parent_io.get_suspension(
                run_id=calling_ctx.run.run_id,
                suspension_id=suspension_id,
            )
            if raw_suspension is not None:
                suspension = decode_child_suspension(raw_suspension)
                child_run_id = suspension.child_run_id
                agent_ref = suspension.agent_ref
                if suspension.status != "pending":
                    # A previous process already ran this child to a terminal
                    # state — hand back the recorded outcome without spawning.
                    await parent_io.consume_suspension(
                        run_id=calling_ctx.run.run_id,
                        suspension_id=suspension_id,
                    )
                    result_status, result = suspension.terminal_result()
                    return SpawnResult(
                        text=result,
                        child_run_id=suspension.child_run_id,
                        child_session_id=suspension.child_session_id,
                        agent_ref=agent_ref,
                        child_session_ref=agent_ref if suspension.child_session_id is not None else None,
                        parent_tool_call_id=parent_id,
                        agent_type=resolved_agent_type,
                        wait=True,
                        status=result_status,
                    )
                # Pending row on a fresh lookup = the child died with a previous
                # process (its bg row was marked interrupted at boot). Re-spawn
                # from this call's arguments — a replayed tool call carries the
                # same spawn inputs — but bounded, so crash-looping restarts
                # can't re-buy the same LLM work forever.
                respawns = suspension.respawns + 1
                if respawns > _MAX_SUBAGENT_RESPAWNS:
                    failure = (
                        f"Sub-agent did not survive {_MAX_SUBAGENT_RESPAWNS + 1} attempts across server "
                        "restarts — not respawning again. Re-issue the task explicitly if it still matters."
                    )
                    await parent_io.resolve_suspension(
                        run_id=calling_ctx.run.run_id,
                        suspension_id=suspension_id,
                        status="failed",
                        resolution={"status": "failed", "result": failure},
                    )
                    await parent_io.consume_suspension(
                        run_id=calling_ctx.run.run_id,
                        suspension_id=suspension_id,
                    )
                    return SpawnResult(
                        text=failure,
                        child_run_id=suspension.child_run_id,
                        child_session_id=suspension.child_session_id,
                        agent_ref=agent_ref,
                        child_session_ref=agent_ref if suspension.child_session_id is not None else None,
                        parent_tool_call_id=parent_id,
                        agent_type=resolved_agent_type,
                        wait=True,
                        status="failed",
                    )
                recovered_child_session_id = suspension.child_session_id
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
        if recovered_child_session_id is not None:
            child_state.session_id = recovered_child_session_id
        # FULL isolation mints a distinct child session ({parent}::{hex}); SHARED
        # reuses the parent's. Advertise child_session_id on this fact alone —
        # not on whether the row already persisted — so a provisioning hiccup
        # can't strip the id and leave the UI unable to open or clear the agent.
        has_child_session = child_state.session_id != calling_ctx.session_id
        if has_child_session:
            child_state.public_ref = agent_ref
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
            _resource_observations=(calling_ctx.run._resource_observations if not has_child_session else {}),
            allowed_tool_names=allowed_tool_names,
            research_scope_id=research_scope_id or calling_ctx.run.research_scope_id,
            child_io_factory=calling_ctx.run.child_io_factory,
        )

        # A FULL subagent streams to its OWN session bus (built by the chat
        # service's child_io_factory) so its session behaves exactly like a
        # normal run — the parent gets only lifecycle events. SHARED subagents
        # have no distinct session, so they keep nesting into the parent's io.
        bg_io = IOBridge() if background or silent else calling_ctx.io

        child_ctx = ToolContext(
            session_state=child_state,
            registry=child_registry,
            run=child_run,
            io=bg_io,
            services=calling_ctx.services,
            area=calling_ctx.area,
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

        child_executor = ArdenToolExecutor(
            child_executor_source,
            child_ctx,
            ledger=calling_ctx.ledger,
            skip_duplicate_reads=True,
        )
        # Every child gets the one team-identity block here — not per surface —
        # so the envelope names and the delivery contract have a single source.
        child_system_prompt = append_deferred_tools_prompt(
            f"{_with_area_context(system_prompt, calling_ctx)}\n\n{TEAM_CHILD_BLOCK}",
            child_registry,
            frozenset(child_ctx.services),
            filtered_tools,
            enabled=child_run.deferred_tools_enabled,
        )

        parent_emit = calling_ctx.io.emit if not silent else None
        lifecycle_task_id = lifecycle_id or parent_id or f"task-{uuid4().hex[:10]}"
        task_depth = current_depth + 1

        # Sub-agents reuse the parent's compactor so a long-running tool
        # sweep does not exceed the model's context window mid-run.
        middlewares: tuple = (
            DeferredToolsModelRequestMiddleware(
                registry=child_registry,
                run=child_run,
                get_services=lambda: child_ctx.services,
            ),
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
                    on_compact=child_run.downgrade_resource_observations,
                    get_rehydration_state=child_ctx.to_rehydration_state,
                    apply_rehydration_state=child_run.apply_rehydration_state,
                    emit=compaction_emit,
                    run_id=calling_ctx.run.run_id,
                    scope=compaction_scope,
                    parent_tool_call_id=parent_id if compaction_emit else None,
                ),
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
        task_id = child_run_id
        # One human title identifies the child session, durable roster row, and
        # live lifecycle events. Consumers must not invent a second title.
        label = task_summary or agent_slug
        child_lifecycle = ChildSessionLifecycle(
            parent=calling_ctx,
            child=child_ctx,
            state=child_state,
            messages=child_messages,
            task_id=task_id,
            has_child_session=has_child_session,
        )

        async def _abort_unstarted_child(
            status: str,
            *,
            durable_started: bool,
        ) -> None:
            registry.release(task_id)

            async def finalize() -> None:
                async def record_terminal(terminal_status: str, _persistence_error: Exception | None) -> None:
                    if durable_started:
                        await registry.record_finished(
                            task_id=task_id,
                            agent_ref=agent_ref,
                            status=terminal_status,
                        )

                await run_operations(
                    "unstarted child cleanup failed",
                    lambda: child_lifecycle.settle(status, record_terminal, persist=child_lifecycle.provisioned),
                )

            await finish_despite_cancellation(finalize())

        async def _raise_start_failure(
            primary: Exception,
            status: str,
            *,
            durable_started: bool,
        ) -> None:
            errors: list[Exception] = []
            append_failure(errors, primary)
            await collect_failure(
                errors,
                _abort_unstarted_child(status, durable_started=durable_started),
            )
            raise_failures("child start failed", errors)

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

        tool_call_count = 0
        child_tool_call_ids: list[str] = []
        child_source_refs: list[ToolSourceRef] = []

        async def _stream_to(to_events) -> str:
            nonlocal tool_call_count
            text = ""
            events = sub_agent.stream(child_messages).__aiter__()
            while True:
                try:
                    event = await anext(events)
                except StopAsyncIteration:
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    raise _AgentStreamFailure(error) from error
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
                if child_lifecycle.io is not None:
                    for sse in agent_events_to_sse(event):
                        await child_lifecycle.io.emit(_rebase_for_child(sse))
                if parent_emit:
                    mapped = to_events(event)
                    if isawaitable(mapped):
                        mapped = await mapped
                    for out in mapped:
                        await parent_emit(out)
            return text

        def _settle_with(text: str, *, status: str = "completed") -> SpawnResult:
            """Build the final SpawnResult for the caller."""
            return SpawnResult(
                text=text,
                usage=sub_tracker.usage.to_dict(),
                cost=sub_tracker.cost,
                child_run_id=child_run_id,
                child_session_id=child_state.session_id if has_child_session else None,
                agent_ref=agent_ref,
                child_session_ref=child_state.public_ref if has_child_session else None,
                parent_tool_call_id=parent_id,
                agent_type=resolved_agent_type,
                wait=should_wait,
                status=status,
                source_refs=normalize_source_refs(child_source_refs),
                tool_call_ids=tuple(child_tool_call_ids),
            )

        # Horizontal fan-out guard: cap concurrent background agents per session
        # so a runaway loop can't spawn unbounded detached agents. Awaited
        # spawns reserve uncapped — bounded by the awaiting parent (workflow
        # fan-out legitimately exceeds the cap) — but still hold a roster slot
        # so the durable row and the cancel cascade cover them.
        durable_started = False
        try:
            reserved = registry.reserve(
                task_id,
                command=label,
                limit=None if should_wait else AGENT_MAX_CONCURRENT,
                agent_ref=agent_ref,
                child_session_id=child_state.session_id if has_child_session else None,
                parent_run_id=calling_ctx.run.run_id,
                summary=task_summary,
                agent_type=resolved_agent_type,
            )
        except Exception as exc:
            await _raise_start_failure(
                exc,
                "failed",
                durable_started=False,
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

        try:
            await child_lifecycle.provision()
            await child_lifecycle.acquire_io()
            sub_agent.hooks.on_step_finish = child_lifecycle.save_step
            start_disposition = await registry.record_started(
                task_id=task_id,
                command=label,
                parent_run_id=calling_ctx.run.run_id,
                parent_tool_call_id=parent_id,
                suspension_id=suspension_id,
                child_session_id=child_state.session_id if has_child_session else None,
                agent_ref=agent_ref,
                agent_type=resolved_agent_type,
                wait=should_wait,
                spawn_spec=spawn_spec.to_json(),
            )
            if start_disposition is BackgroundStartDisposition.CANCELLED:
                raise asyncio.CancelledError
            durable_started = True
        except asyncio.CancelledError:
            registry.release(task_id)
            await _abort_unstarted_child("cancelled", durable_started=durable_started)
            raise
        except Exception as exc:
            await _raise_start_failure(
                exc,
                "failed",
                durable_started=durable_started,
            )

        # Steering channel, both wait modes: the parent (or user) can send
        # messages to this running agent via registry.queue_injection(task_id,
        # …); the agent drains them at its next step.
        # Only a child with its OWN registry gets a roster: when it shares
        # the parent's registry (SHARED isolation / no io factory), that
        # roster lists the child itself and its siblings — the parent's
        # team, not this agent's.
        child_owns_registry = child_lifecycle.background_tasks is not calling_ctx.background_tasks

        async def _drain_steering() -> list[dict]:
            batch = registry.drain_injections(task_id)
            if child_owns_registry:
                # The child's own roster — its spawns register through the
                # CHILD session's registry — rendered only when the live
                # set changed, so it never repeats every step.
                roster = child_lifecycle.background_tasks.roster_note_if_changed()
                if roster is not None:
                    batch.append(roster)
            return batch

        sub_agent.hooks.get_pending_messages = _drain_steering

        undelivered_steering: list[str] = []

        def _close_inbox() -> None:
            undelivered_steering.extend(m.get("content", "") for m in registry.close_inbox(task_id))

        async def _execute(to_events) -> tuple[str, str, str]:
            """Run one child and return its explicit terminal outcome."""
            try:
                text = await asyncio.wait_for(_stream_to(to_events), timeout=timeout)
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                text = f"Sub-agent timed out after {timeout}s."
                _logger.warning("Sub-agent %s timed out after %ss", task_id, timeout)
                status, detail = "failed", f"timed out after {timeout}s"
            except _AgentStreamFailure as failure:
                text = f"Sub-agent failed: {type(failure.error).__name__}."
                _logger.exception("Sub-agent %s failed", task_id)
                status, detail = "failed", f"execution failed: {type(failure.error).__name__}"
            else:
                if text.strip():
                    status, detail = "completed", "completed"
                else:
                    text = "Sub-agent returned no final answer."
                    status, detail = "failed", "empty final answer"
            finally:
                _close_inbox()
            return status, text, detail

        if should_wait:
            if suspension_id and parent_io.record_suspension is not None:
                try:
                    await parent_io.record_suspension(
                        run_id=calling_ctx.run.run_id,
                        session_id=calling_ctx.session_id,
                        suspension_id=suspension_id,
                        kind="subagent_result",
                        payload={
                            "task": task_summary,
                            "child_run_id": task_id,
                            "child_session_id": child_state.session_id if has_child_session else None,
                            "agent_ref": agent_ref,
                            "agent_type": resolved_agent_type,
                            "respawns": respawns,
                        },
                    )
                except asyncio.CancelledError:
                    registry.release(task_id)
                    await _abort_unstarted_child("cancelled", durable_started=True)
                    raise
                except Exception as exc:
                    await _raise_start_failure(exc, "failed", durable_started=True)

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
                        name=label,
                        status=status,
                        summary=summary,
                        depth=task_depth,
                        workflow_id=workflow_id,
                        phase=phase,
                        tool_count=tool_call_count,
                    )
                )

            async def _settle_foreground(status: str, text: str, summary: str) -> SpawnResult:
                async def finalize() -> str:
                    async def record_terminal(
                        terminal_status: str,
                        persistence_error: Exception | None,
                    ) -> None:
                        result_text = (
                            f"Child session persistence failed: {type(persistence_error).__name__}: {persistence_error}"
                            if persistence_error is not None
                            else text
                        )
                        terminal_operations = []
                        if terminal_status != "completed":
                            terminal_operations.append(
                                lambda: _emit_visible_child_final(child_lifecycle.io, result_text)
                            )
                        terminal_operations.extend(
                            (
                                lambda: registry.record_finished(
                                    task_id=task_id,
                                    agent_ref=agent_ref,
                                    status=terminal_status,
                                    result_text=result_text,
                                ),
                                lambda: _emit_task_finished(terminal_status, summary),
                            )
                        )
                        await run_operations(
                            "child terminal record failed",
                            *terminal_operations,
                        )

                    return await child_lifecycle.settle(status, record_terminal)

                terminal_status = await finish_despite_cancellation(finalize())
                if undelivered_steering:
                    _logger.warning(
                        "Awaited sub-agent %s finished with %d undelivered steering message(s)",
                        task_id,
                        len(undelivered_steering),
                    )
                return _settle_with(text, status=terminal_status)

            async def _emit_task_started() -> None:
                if not parent_emit:
                    return
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
                        name=label,
                        summary=task_summary,
                        depth=task_depth,
                        workflow_id=workflow_id,
                        phase=phase,
                    )
                )

            async def _run_foreground() -> tuple[str, str, str]:
                return await _execute(
                    _noop_child_events if child_lifecycle.io is not None else _foreground_child_events
                )

            start_gate = asyncio.Event()

            async def _run_registered_foreground() -> tuple[str, str, str]:
                await start_gate.wait()
                return await _run_foreground()

            stream_task = asyncio.create_task(_run_registered_foreground())
            subagent_handle = None
            subagent_registration_attempted = False

            async def _cleanup_foreground_tasks() -> None:
                errors: list[Exception] = []
                if subagent_registration_attempted and calling_ctx.run_registry is not None:
                    try:
                        calling_ctx.run_registry.finish_subagent(calling_ctx.run.run_id, lifecycle_task_id)
                    except Exception as exc:
                        append_failure(errors, exc)
                await collect_failure(errors, cancel_and_join(stream_task))
                raise_failures("child task cleanup failed", errors)

            async def _start_foreground() -> None:
                nonlocal subagent_handle, subagent_registration_attempted
                registry.register(task_id, stream_task, command=label)
                if calling_ctx.run_registry is not None:
                    subagent_registration_attempted = True
                    subagent_handle = calling_ctx.run_registry.register_subagent(
                        calling_ctx.run.run_id,
                        lifecycle_task_id,
                        stream_task,
                    )
                await _emit_task_started()
                start_gate.set()

            await activate_child(
                _start_foreground(),
                _cleanup_foreground_tasks,
                lambda status: _abort_unstarted_child(status, durable_started=True),
            )

            async def _await_foreground_result() -> SpawnResult:
                try:
                    status, text, detail = await stream_task
                except asyncio.CancelledError as cancellation:
                    run_state = (
                        calling_ctx.run_registry.get_run(calling_ctx.run.run_id)
                        if calling_ctx.run_registry is not None
                        else None
                    )
                    if run_state and not run_state.cancelled and subagent_handle and subagent_handle.cancel_requested:
                        text = "Sub-agent cancelled by user."
                        return await _settle_foreground(
                            "cancelled",
                            text,
                            "cancelled by user",
                        )
                    try:
                        await _settle_foreground("cancelled", "Sub-agent cancelled.", "cancelled")
                    except asyncio.CancelledError:
                        raise
                    except Exception as settlement_exc:
                        raise cancellation from settlement_exc
                    raise
                except Exception as execution_exc:
                    failure_text = f"Sub-agent lifecycle failed: {type(execution_exc).__name__}."
                    try:
                        await _settle_foreground(
                            "failed",
                            failure_text,
                            f"execution failed: {type(execution_exc).__name__}",
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as settlement_exc:
                        raise ExceptionGroup(
                            "child execution and settlement failed",
                            [execution_exc, settlement_exc],
                        )
                    raise
                return await _settle_foreground(status, text, detail)

            try:
                foreground_result = await _await_foreground_result()
            except asyncio.CancelledError as cancellation:
                try:
                    await _cleanup_foreground_tasks()
                except asyncio.CancelledError:
                    raise
                except Exception as cleanup_error:
                    raise cancellation from cleanup_error
                raise
            except Exception as execution_error:
                try:
                    await _cleanup_foreground_tasks()
                except asyncio.CancelledError:
                    raise
                except Exception as cleanup_error:
                    raise ExceptionGroup(
                        "child execution cleanup failed",
                        [execution_error, cleanup_error],
                    )
                raise
            await _cleanup_foreground_tasks()
            return foreground_result

        async def _to_bg_events(event):
            if isinstance(event, ToolStarted):
                detail = event.display_name or event.name
            elif isinstance(event, ToolCompleted):
                detail = f"{event.display_name or event.name}: {event.preview}"
            else:
                return ()
            await registry.record_activity(task_id, detail, agent_ref=agent_ref)
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
            execution_error: Exception | None = None
            try:
                status, result, _detail = await _execute(_to_bg_events)
            except asyncio.CancelledError:
                status, result = "cancelled", "Sub-agent cancelled."
            except Exception as error:
                _logger.exception("Background task %s lifecycle failed", task_id)
                status, result = "failed", f"Sub-agent lifecycle failed: {type(error).__name__}."
                execution_error = error

            async def record_terminal(terminal_status: str, persistence_error: Exception | None) -> None:
                terminal_result = (
                    f"Child session persistence failed: {type(persistence_error).__name__}: {persistence_error}"
                    if persistence_error is not None
                    else result
                )
                terminal_operations = []
                if terminal_status != "completed":
                    terminal_operations.append(lambda: _emit_visible_child_final(child_lifecycle.io, terminal_result))
                terminal_operations.append(
                    lambda: registry.deliver_result(
                        task_id=task_id,
                        result=terminal_result,
                        label=label,
                        status=terminal_status,
                        emit=parent_emit,
                        child_session_id=child_state.session_id if has_child_session else None,
                        agent_ref=agent_ref,
                        parent_tool_call_id=parent_id,
                        agent_type=resolved_agent_type,
                        wait=should_wait,
                        undelivered_steering=undelivered_steering or None,
                    )
                )
                await run_operations(
                    "background child terminal record failed",
                    *terminal_operations,
                )

            try:
                await finish_despite_cancellation(child_lifecycle.settle(status, record_terminal))
            except asyncio.CancelledError:
                raise
            except Exception as settlement_error:
                if execution_error is not None:
                    raise ExceptionGroup(
                        "background child execution and settlement failed",
                        [execution_error, settlement_error],
                    )
                raise
            if execution_error is not None:
                raise execution_error

        start_gate = asyncio.Event()

        async def _run_registered_detached() -> None:
            await start_gate.wait()
            await _run_detached()

        bg_task = asyncio.create_task(_run_registered_detached())

        async def _start_detached() -> None:
            registry.register(task_id, bg_task, command=label, observe_failure=True)
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
            start_gate.set()

        await activate_child(
            _start_detached(),
            lambda: cancel_and_join(bg_task),
            lambda status: _abort_unstarted_child(status, durable_started=True),
        )

        # Background path returns immediately — the real result is delivered
        # asynchronously via registry.deliver_result. Usage/cost belong to
        # the background task's own ledger, not this caller's tool result.
        return SpawnResult(
            text=f"Started a background agent to: {task}",
            child_run_id=child_run_id,
            child_session_id=child_state.session_id if has_child_session else None,
            agent_ref=agent_ref,
            child_session_ref=child_state.public_ref if has_child_session else None,
            parent_tool_call_id=parent_id,
            agent_type=resolved_agent_type,
            wait=False,
            status="running",
        )

    return spawn_child
