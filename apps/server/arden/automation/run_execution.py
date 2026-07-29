import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from arden.automation.event_dispatch import event_retry_at, failure_retry_at
from arden.automation.models import Automation
from arden.automation.prompts import AUTOMATION_PROMPT, AUTOMATION_SUFFIX
from arden.automation.store import AutomationStore
from arden.automation.triggers import TimeTrigger
from arden.events.sse import AutomationProgressEvent, SSEEvent
from arden.logging import get_logger
from arden.operator.runner import OperatorDeps, RunRequest, RunResult, run_agent, run_agent_streaming

AUTOMATION_BUS_KEY = "automation:events"
MANUAL_RUN_CONTEXT_KEY = "_arden_manual_run"

_logger = get_logger(__name__)

IterationDispatcher = Callable[[Automation, str | dict | None, int], Awaitable[str]]
PostDispatcher = Callable[[Automation, str | dict | None], Awaitable["CompletedAgentRun"]]
RunCompletionValidator = Callable[[Automation, str | None], Awaitable[None]]
Handler = Callable[[dict | None], Awaitable[str | None]]
EventEmitter = Callable[[SSEEvent], Awaitable[None]]


class AutomationEventBus(Protocol):
    async def emit(self, event: SSEEvent) -> None: ...


class AutomationBusRegistry(Protocol):
    def get_or_create(self, key: str) -> AutomationEventBus: ...


AgentRunner = Callable[[OperatorDeps, RunRequest], Awaitable[RunResult]]
StreamingAgentRunner = Callable[[OperatorDeps, RunRequest, AutomationEventBus, str], Awaitable[RunResult]]


def split_manual_flag(context: str | dict | None) -> tuple[bool, str | dict | None]:
    """Pop the transport-only manual-run marker from an automation context."""
    if isinstance(context, dict):
        manual = bool(context.get(MANUAL_RUN_CONTEXT_KEY))
        rest = {key: value for key, value in context.items() if key != MANUAL_RUN_CONTEXT_KEY}
        return manual, rest or None
    return False, context


@dataclass(frozen=True)
class DetachedRun:
    """A session iteration that remains durable-running until its chat event arrives."""

    handle: str


class DetachedRunBindingPending(RuntimeError):
    """A terminal chat event arrived before its automation run was bound."""


@dataclass(frozen=True)
class CompletedAgentRun:
    """A completed agent result with the durable chat run used for proof."""

    run_id: str
    result: str | None


class RunSkipped(Exception):
    """A dispatcher deliberately declined a loop execution."""


class RunDeferred(Exception):
    """A session-bound run must wait for its target session to go idle."""


@dataclass(frozen=True)
class ExecutionDependencies:
    store: AutomationStore
    build_deps: Callable[[], OperatorDeps]
    bus_registry: AutomationBusRegistry | None
    handlers: Mapping[str, Handler]
    iteration_dispatcher: IterationDispatcher | None
    post_dispatcher: PostDispatcher | None
    validate_completed_run: RunCompletionValidator | None
    event_emitter: EventEmitter
    agent_runner: AgentRunner = run_agent
    streaming_agent_runner: StreamingAgentRunner = run_agent_streaming


@dataclass(frozen=True)
class ExecutionOutcome:
    """Durable execution result; Scheduler applies live reservation/wake actions."""

    state: Literal["deferred", "detached", "settled", "unstarted"]
    result: str | None = None
    retry_at: datetime | None = None
    event_retry_at: datetime | None = None
    cancelled: bool = False
    wake_catch_up: bool = False
    start_next_event: bool = False
    release_event_claim: bool = False
    failure_message: str | None = None


@dataclass(frozen=True)
class SessionExecutionOutcome:
    """A session dispatch result and whether it disabled its automation."""

    result: str | CompletedAgentRun | DetachedRun | None
    disabled: bool = False


async def run_handler(
    automation: Automation,
    context: str | dict | None,
    handlers: Mapping[str, Handler],
) -> str | None:
    handler = handlers.get(automation.handler)
    if handler is None:
        raise RuntimeError(f"No handler registered for '{automation.handler}'")
    _, context = split_manual_flag(context)
    if isinstance(context, dict):
        handler_context = context
    elif context:
        try:
            handler_context = json.loads(context)
        except json.JSONDecodeError:
            handler_context = {"event_context": context}
    else:
        handler_context = {}
    handler_context["task_id"] = automation.task_id
    return await handler(handler_context)


async def run_agent_execution(
    automation: Automation,
    context: str | dict | None,
    *,
    build_deps: Callable[[], OperatorDeps],
    bus_registry: AutomationBusRegistry | None,
    agent_runner: AgentRunner,
    streaming_agent_runner: StreamingAgentRunner,
) -> CompletedAgentRun:
    _, context = split_manual_flag(context)
    context_text = json.dumps(context) if isinstance(context, dict) else context
    request = RunRequest(
        prompt=AUTOMATION_PROMPT.render(prompt=automation.prompt, context=context_text),
        prompt_suffix=AUTOMATION_SUFFIX,
        auto_approve=automation.auto_approve,
        source_id=automation.task_id,
        model=automation.model,
        skip_approvals=automation.auto_approve,
        automation_id=automation.task_id,
        tool_scope=tuple(automation.tool_scope) if automation.tool_scope else None,
    )
    if bus_registry is not None:
        bus = bus_registry.get_or_create(AUTOMATION_BUS_KEY)
        result = await streaming_agent_runner(build_deps(), request, bus, automation.task_id)
    else:
        result = await agent_runner(build_deps(), request)
    return CompletedAgentRun(result.run_id, result.output)


async def run_session_bound(
    automation: Automation,
    automation_run_id: int,
    context: str | dict | None,
    *,
    store: AutomationStore,
    iteration_dispatcher: IterationDispatcher | None,
    post_dispatcher: PostDispatcher | None,
) -> SessionExecutionOutcome:
    if not automation.prompt:
        raise RuntimeError(f"Automation {automation.task_id} missing prompt")
    if automation.aged_out(datetime.now(UTC)):
        await store.disable_and_clear_next_run(automation.task_id)
        return SessionExecutionOutcome(
            result=f"Loop aged out after {automation.max_age_days} days",
            disabled=True,
        )
    dispatcher: IterationDispatcher | PostDispatcher | None
    if automation.read_history:
        dispatcher = iteration_dispatcher
        mode = "iteration"
    else:
        dispatcher = post_dispatcher
        mode = "post"
    if not automation.thread_id:
        raise RuntimeError(f"{mode.title()} loop {automation.task_id} missing thread_id")
    if dispatcher is None:
        raise RuntimeError(f"{mode} dispatcher not wired")
    try:
        if automation.read_history:
            dispatched = await dispatcher(automation, context, automation_run_id)
        else:
            dispatched = await dispatcher(automation, context)
    except RunSkipped as skip:
        return SessionExecutionOutcome(result=f"Skipped: {skip}")
    if automation.read_history and not dispatched:
        raise RuntimeError(f"Iteration automation {automation.task_id} did not return a chat run id")
    result: CompletedAgentRun | DetachedRun = DetachedRun(dispatched) if automation.read_history else dispatched
    await store.increment_iteration(automation.task_id)
    if automation.max_iterations is not None and (automation.iteration_count + 1) >= automation.max_iterations:
        await store.disable_and_clear_next_run(automation.task_id)
        return SessionExecutionOutcome(result=result, disabled=True)
    return SessionExecutionOutcome(result=result)


def advance_to_future(automation: Automation, now: datetime) -> datetime | None:
    time_triggers = [trigger for trigger in automation.triggers if isinstance(trigger, TimeTrigger)]
    if not time_triggers:
        return None
    next_run = time_triggers[0].next_run(automation.next_run_at or now)
    while next_run and next_run <= now:
        next_run = time_triggers[0].next_run(next_run)
    return next_run


async def execute_and_settle(
    automation: Automation,
    context: str | dict | None,
    *,
    event_queue_id: int | None,
    event_attempt_count: int,
    run_id: int | None,
    prewritten_next_run: datetime | None,
    has_prewritten_next_run: bool,
    dependencies: ExecutionDependencies,
) -> ExecutionOutcome:
    result: str | CompletedAgentRun | DetachedRun | None = None
    success = False
    error_message = ""
    cancelled = False
    session_disabled = False
    try:
        await dependencies.event_emitter(AutomationProgressEvent(task_id=automation.task_id, status="starting..."))
        if run_id is None:
            run_id = await dependencies.store.record_run_start(automation.task_id, datetime.now(UTC))
        if automation.thread_id is not None:
            session_outcome = await run_session_bound(
                automation,
                run_id,
                context,
                store=dependencies.store,
                iteration_dispatcher=dependencies.iteration_dispatcher,
                post_dispatcher=dependencies.post_dispatcher,
            )
            result = session_outcome.result
            session_disabled = session_outcome.disabled
        elif automation.handler:
            result = await run_handler(automation, context, dependencies.handlers)
        else:
            result = await run_agent_execution(
                automation,
                context,
                build_deps=dependencies.build_deps,
                bus_registry=dependencies.bus_registry,
                agent_runner=dependencies.agent_runner,
                streaming_agent_runner=dependencies.streaming_agent_runner,
            )
        if dependencies.validate_completed_run is not None and not isinstance(result, DetachedRun):
            proof_run_id = result.run_id if isinstance(result, CompletedAgentRun) else None
            await dependencies.validate_completed_run(automation, proof_run_id)
        success = True
    except RunDeferred as deferred:
        now = datetime.now(UTC)
        discarded = await dependencies.store.defer_run(
            task_id=automation.task_id,
            run_id=run_id,
            retry_at=now if has_prewritten_next_run else None,
            expected_next_run=prewritten_next_run if has_prewritten_next_run else None,
            event_queue_id=event_queue_id,
        )
        if not discarded:
            raise RuntimeError(f"automation run {run_id} was already settled") from deferred
        return ExecutionOutcome(state="deferred")
    except asyncio.CancelledError:
        cancelled = True
        error_message = "CancelledError: automation run cancelled"
    except Exception as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        _logger.exception("Failed to execute automation %s", automation.task_id)
    if run_id is None:
        return ExecutionOutcome(
            state="unstarted",
            cancelled=cancelled,
            release_event_claim=event_queue_id is not None,
            failure_message=error_message or "automation run did not start",
        )
    if isinstance(result, DetachedRun) and success:
        if event_queue_id is not None:
            await dependencies.store.complete_event_and_bind_detached_chat_run(
                queue_id=event_queue_id,
                automation_run_id=run_id,
                task_id=automation.task_id,
                chat_run_id=result.handle,
                chat_session_id=automation.thread_id or "",
            )
        else:
            await dependencies.store.bind_detached_chat_run(
                automation_run_id=run_id,
                task_id=automation.task_id,
                chat_run_id=result.handle,
                chat_session_id=automation.thread_id or "",
            )
        return ExecutionOutcome(state="detached")
    now = datetime.now(UTC)
    result_text = result.result if isinstance(result, CompletedAgentRun) else result
    retry_at = (
        await failure_retry_at(dependencies.store, automation.task_id, now)
        if not success and event_queue_id is None
        else None
    )
    queue_retry_at = event_retry_at(event_attempt_count, now) if event_queue_id is not None and not success else None
    settled = await dependencies.store.settle_run(
        task_id=automation.task_id,
        run_id=run_id,
        status="completed" if success else "failed",
        result=result_text,
        error=error_message or None,
        ended_at=now,
        next_run=advance_to_future(automation, now)
        if success and automation.enabled and not session_disabled
        else retry_at,
        expected_next_run=prewritten_next_run if has_prewritten_next_run else None,
        preserve_external_reschedule=has_prewritten_next_run,
        preserve_result=False,
        disable_one_shot=success and any(trigger.one_shot for trigger in automation.triggers),
        event_queue_id=event_queue_id,
        event_retry_at=queue_retry_at,
    )
    if not settled:
        raise RuntimeError(f"automation run {run_id} was already settled")
    return ExecutionOutcome(
        state="settled",
        result=result_text,
        retry_at=retry_at,
        event_retry_at=queue_retry_at,
        cancelled=cancelled,
        wake_catch_up=automation.handler == "memory_maintenance",
        start_next_event=event_queue_id is not None,
        failure_message=error_message or None,
    )
