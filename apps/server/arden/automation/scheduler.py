import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from arden.automation.event_dispatch import (
    claim_next_queued_event,
    enqueue_matching_events,
    event_retry_at,
    failure_retry_at,
    matching_event_automations,
    matching_message_automations,
    message_trigger_passes,
    retry_delay_seconds,
)
from arden.automation.models import Automation
from arden.automation.run_execution import (
    AUTOMATION_BUS_KEY,
    MANUAL_RUN_CONTEXT_KEY,
    CompletedAgentRun,
    DetachedRun,
    DetachedRunBindingPending,
    ExecutionDependencies,
    RunCompletionValidator,
    RunDeferred,
    RunSkipped,
    execute_and_settle,
    run_agent_execution,
    run_handler,
    run_session_bound,
    split_manual_flag,
)
from arden.automation.store import AutomationStore
from arden.automation.triggers import CountTrigger, IdleTrigger, MessageTrigger, TimeTrigger
from arden.constants import (
    BUILTIN_MEMORY_RETENTION_ID,
    BUILTIN_MEMORY_STORAGE_MAINTENANCE_ID,
    BUILTIN_MEMORY_SYNTHESIZE_ID,
    BUILTIN_WIKI_MAINTENANCE_ID,
    DETACHED_RUN_MAX_AGE,
    MEMORY_RETENTION_AT,
    SCHEDULER_EVENT_RETRY_BASE_SECONDS,
    SCHEDULER_POLL_INTERVAL,
)
from arden.events.internal import RunCompleted, RunFailed
from arden.events.sse import AutomationFinishedEvent, SSEEvent
from arden.events.triggers import MessageReceived, TriggerEvent
from arden.logging import get_logger
from arden.operator.runner import OperatorDeps, run_agent, run_agent_streaming
from arden.server.bus import BusRegistry

_logger = get_logger(__name__)

__all__ = (
    "AUTOMATION_BUS_KEY",
    "CompletedAgentRun",
    "DetachedRun",
    "DetachedRunBindingPending",
    "RunDeferred",
    "RunSkipped",
    "Scheduler",
    "split_manual_flag",
)

# Builtin daily memory maintenance phases. When one is overdue after
# sleep/offline time, catch it up on boot.
_CATCH_UP_HANDLERS = {"memory_maintenance"}
_CATCH_UP_CADENCE = timedelta(hours=24)
_MEMORY_RETENTION_BACKSTOP = TimeTrigger(at=MEMORY_RETENTION_AT, days="daily")
_FACT_SYNTHESIS_BACKSTOP = TimeTrigger(every="6h")
_WIKI_MAINTENANCE_BACKSTOP = TimeTrigger(every="6h")
_MANAGED_HISTORY_COLLECTION_BACKSTOP = TimeTrigger(every="7d")


# True ⇒ ok to fire this loop right now; False ⇒ defer to next tick. Used
# to keep loop iterations from being injected mid-turn into a user's
# active conversation — they should render as fresh turns once the
# session goes idle. Applies to both iteration and post modes: iteration
# would re-enter mid-conversation, post would race the in-flight run on
# session_service writes.
LoopFireGate = Callable[[Automation], bool]
IterationDispatcher = Callable[[Automation, str | dict | None, int], Awaitable[str]]
PostDispatcher = Callable[[Automation, str | dict | None], Awaitable[CompletedAgentRun]]


class Scheduler:
    def __init__(
        self,
        store: AutomationStore,
        build_deps: Callable[[], OperatorDeps],
        validate_completed_run: RunCompletionValidator | None = None,
    ):
        self.store = store
        self._build_deps = build_deps
        self._validate_completed_run = validate_completed_run
        self._bus_registry: BusRegistry | None = None
        self._task: asyncio.Task | None = None
        self._wake_task: asyncio.Task | None = None
        self._wake_deadline: datetime | None = None
        self._wake_event = asyncio.Event()
        self._running: set[asyncio.Task] = set()
        self._running_task_ids: dict[asyncio.Task, str] = {}
        self._pending_running_task_ids: set[str] = set()
        # A target session accepts one scheduler-owned run at a time. This
        # closes the window before the chat registry observes a new run.
        self._reserved_session_ids: set[str] = set()
        self._handlers: dict[str, Callable[[dict | None], Awaitable[str | None]]] = {}
        self._iteration_dispatcher: IterationDispatcher | None = None
        self._post_dispatcher: PostDispatcher | None = None
        self._loop_fire_gate: LoopFireGate | None = None
        self._last_activity_at: datetime = datetime.now(UTC)
        self._started_at: datetime | None = None
        self._last_tick_at: datetime | None = None
        self._last_tick_error: str | None = None
        self._idle_fired: set[str] = set()  # task_ids that already fired this idle period

    def set_bus_registry(self, registry: BusRegistry) -> None:
        self._bus_registry = registry

    def set_iteration_dispatcher(self, dispatcher: IterationDispatcher) -> None:
        self._iteration_dispatcher = dispatcher

    def set_post_dispatcher(self, dispatcher: PostDispatcher) -> None:
        self._post_dispatcher = dispatcher

    def set_loop_fire_gate(self, gate: LoopFireGate) -> None:
        self._loop_fire_gate = gate

    def register_handler(self, name: str, handler: Callable[[dict | None], Awaitable[str | None]]) -> None:
        self._handlers[name] = handler

    def update_activity(self) -> None:
        self._last_activity_at = datetime.now(UTC)
        self._idle_fired.clear()  # new activity resets idle state

    def start(self) -> None:
        if self._task is not None:
            return
        self._started_at = datetime.now(UTC)
        self._task = asyncio.create_task(self._startup_and_loop())
        _logger.info("Scheduler started (polling every %ds)", SCHEDULER_POLL_INTERVAL)

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        if self._wake_task:
            self._wake_task.cancel()
            try:
                await self._wake_task
            except asyncio.CancelledError:
                pass
            self._wake_task = None
            self._wake_deadline = None

        if self._running:
            for task in self._running:
                task.cancel()
            await asyncio.gather(*self._running, return_exceptions=True)
            self._running.clear()
            self._running_task_ids.clear()
            self._pending_running_task_ids.clear()
            self._reserved_session_ids.clear()

        _logger.info("Scheduler stopped")

    def _track(self, task: asyncio.Task, task_id: str | None = None) -> None:
        self._running.add(task)
        if task_id is not None:
            self._running_task_ids[task] = task_id
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        self._running.discard(task)
        self._running_task_ids.pop(task, None)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception as exc:
            self._last_tick_at = datetime.now(UTC)
            self._last_tick_error = f"{type(exc).__name__}: {exc}"
            _logger.exception("Scheduler task failed")

    async def _release_untracked_running(self) -> None:
        tracked_ids = set(self._running_task_ids.values()) | self._pending_running_task_ids
        now = datetime.now(UTC)
        for automation in await self.store.list_running():
            if automation.task_id in tracked_ids:
                continue
            detached = await self.store.get_running_detached_chat_run(automation.task_id)
            if detached is not None:
                _chat_run_id, _chat_session_id, dispatched_at = detached
                if now - dispatched_at <= DETACHED_RUN_MAX_AGE:
                    continue  # detached run legitimately in flight
                # RunCompleted never arrived (the detached run died without
                # reporting) — fail the row so history stays terminal.
                _logger.warning("Detached run for %s never reported back; failing it", automation.task_id)
                await self._settle_failure(automation, "run never reported back", now)
                continue
            _logger.warning("Failing untracked automation run %s", automation.task_id)
            claimed_event = await self.store.get_claimed_event(automation.task_id)
            if claimed_event is None:
                settled = await self._settle_failure(automation, "automation task stopped before settlement", now)
            else:
                event_queue_id, attempt_count = claimed_event
                settled = await self._settle_failure(
                    automation,
                    "automation event stopped before settlement",
                    now,
                    event_queue_id=event_queue_id,
                    event_attempt_count=attempt_count,
                )
            if not settled:
                await self.store.clear_running(automation.task_id)
                self._release_session(automation)

    async def _startup_and_loop(self) -> None:
        while True:
            try:
                await self._reconcile()
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_tick_at = datetime.now(UTC)
                self._last_tick_error = f"{type(exc).__name__}: {exc}"
                _logger.exception("Scheduler reconciliation failed")
                await self._wait_for_wake_or_poll()
        await self._loop()

    async def _reconcile(self) -> None:
        stale_running = await self.store.list_running()
        stale_running_ids = {automation.task_id for automation in stale_running}
        cleared = await self.store.clear_orphaned_running()
        if cleared:
            _logger.info("Cleared %d stale running flags", cleared)
        orphaned = await self.store.fail_orphaned_runs(datetime.now(UTC), "interrupted by server restart")
        if orphaned:
            _logger.info("Failed %d orphaned run rows from the previous process", orphaned)
        released = await self.store.release_all_claimed_events()
        if released:
            _logger.info("Released %d stale claimed event rows", released)

        now = datetime.now(UTC)
        for automation in stale_running:
            if not automation.enabled:
                continue
            if automation.next_run_at and automation.next_run_at > now:
                await self.store.set_next_run(automation.task_id, automation.running_since or now)
                _logger.warning(
                    "Recovered interrupted automation %s; restored due time after stale running flag",
                    automation.task_id,
                )

        for automation in await self.store.list_due(now):
            if automation.task_id in stale_running_ids:
                _logger.warning(
                    "Recovered interrupted automation %s; leaving due for immediate retry", automation.task_id
                )
                continue
            if self._should_catch_up_missed(automation, now):
                # A maintenance builtin whose slot was missed stays DUE so _tick
                # fires it now instead of silently skipping its cadence.
                _logger.info(
                    "Catch-up: leaving missed maintenance automation %s due for immediate run", automation.task_id
                )
                continue
            missed_at = automation.next_run_at
            next_run = self._advance_to_future(automation, now)
            if not next_run:
                continue
            await self.store.set_next_run(automation.task_id, next_run)
            _logger.warning(
                "Missed run of automation %s (was due %s), advanced to %s",
                automation.task_id,
                missed_at.isoformat() if missed_at else "unknown",
                next_run.isoformat(),
            )

        await self._drain_event_backlog()

    @staticmethod
    def _advance_to_future(automation: Automation, now: datetime) -> datetime | None:
        time_triggers = [t for t in automation.triggers if isinstance(t, TimeTrigger)]
        if not time_triggers:
            return None
        trigger = time_triggers[0]
        ref = automation.next_run_at or now
        next_run = trigger.next_run(ref)
        while next_run and next_run <= now:
            next_run = trigger.next_run(next_run)
        return next_run

    @staticmethod
    def _should_catch_up_missed(automation: Automation, now: datetime) -> bool:
        """Whether an overdue built-in must run now rather than skip ahead.

        Builtin maintenance keeps an explicit catch-up policy. An offline
        interval must not turn an already-due required operation into a
        future slot during startup reconciliation.
        """
        is_fact_synthesis_backstop = (
            automation.builtin
            and automation.task_id == BUILTIN_MEMORY_SYNTHESIZE_ID
            and automation.cooldown_minutes is None
            and automation.triggers == [_FACT_SYNTHESIS_BACKSTOP]
        )
        is_memory_retention_backstop = (
            automation.builtin
            and automation.task_id == BUILTIN_MEMORY_RETENTION_ID
            and automation.cooldown_minutes is None
            and automation.triggers == [_MEMORY_RETENTION_BACKSTOP]
        )
        is_wiki_maintenance_backstop = (
            automation.builtin
            and automation.task_id == BUILTIN_WIKI_MAINTENANCE_ID
            and automation.handler == "wiki_maintenance"
            and automation.cooldown_minutes is None
            and automation.triggers == [_WIKI_MAINTENANCE_BACKSTOP]
        )
        is_managed_history_collection_backstop = (
            automation.builtin
            and automation.task_id == BUILTIN_MEMORY_STORAGE_MAINTENANCE_ID
            and automation.handler == "managed_history_collection"
            and automation.cooldown_minutes is None
            and automation.triggers == [_MANAGED_HISTORY_COLLECTION_BACKSTOP]
        )
        if not (
            is_fact_synthesis_backstop
            or is_memory_retention_backstop
            or is_wiki_maintenance_backstop
            or is_managed_history_collection_backstop
            or (automation.builtin and automation.handler in _CATCH_UP_HANDLERS)
        ):
            return False
        if len(automation.triggers) != 1 or not isinstance(automation.triggers[0], TimeTrigger):
            return False
        due_at = automation.next_run_at
        if due_at is None or due_at > now:
            return False
        last = automation.last_run_at
        return last is None or last < due_at

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
                self._last_tick_at = datetime.now(UTC)
                self._last_tick_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_tick_at = datetime.now(UTC)
                self._last_tick_error = f"{type(exc).__name__}: {exc}"
                _logger.exception("Scheduler tick failed")
            await self._wait_for_wake_or_poll()

    async def _wait_for_wake_or_poll(self) -> None:
        try:
            await asyncio.wait_for(self._wake_event.wait(), timeout=SCHEDULER_POLL_INTERVAL)
        except TimeoutError:
            pass
        finally:
            self._wake_event.clear()

    def _schedule_wake(self, deadline: datetime) -> None:
        if self._wake_deadline is not None and self._wake_deadline <= deadline:
            return
        if self._wake_task is not None:
            self._wake_task.cancel()
        self._wake_deadline = deadline
        self._wake_task = asyncio.create_task(self._wake_at(deadline))

    @staticmethod
    def _time_trigger_backstop(automation: Automation, now: datetime) -> datetime | None:
        """Return the next natural time-trigger slot, independent of a debounce."""
        trigger = next((t for t in automation.triggers if isinstance(t, TimeTrigger)), None)
        if trigger is None:
            return None
        # Interval schedules are anchored to their last completed run.  Wall
        # clock schedules derive directly from now and need no stored anchor.
        if trigger.every:
            return trigger.next_run(automation.last_run_at or automation.created_at)
        return trigger.next_run(now)

    async def request_delayed_run(self, task_id: str, delay: timedelta) -> bool:
        """Reset one enabled task's delayed run, retaining its time-trigger cap.

        An active run already satisfies the reliability cadence, so only idle
        tasks are capped at their independently derived next time-trigger slot.
        """
        if delay < timedelta(0):
            raise ValueError("delay must not be negative")
        automation = await self.store.get(task_id)
        if automation is None or not automation.enabled:
            return False

        now = datetime.now(UTC)
        requested = now + delay
        if automation.running_since is None:
            if automation.next_run_at is not None and automation.next_run_at <= now:
                requested = automation.next_run_at
            else:
                backstop = self._time_trigger_backstop(automation, now)
                if backstop is not None:
                    requested = min(requested, backstop)

        return await self.reschedule_run(task_id, requested)

    async def reschedule_run(
        self,
        task_id: str,
        deadline: datetime,
        *,
        only_if_earlier: bool = False,
    ) -> bool:
        updated = (
            await self.store.set_earlier_next_run_if_enabled(task_id, deadline)
            if only_if_earlier
            else await self.store.set_next_run_if_enabled(task_id, deadline)
        )
        if not updated:
            return False
        self._schedule_wake(deadline)
        self._wake_event.set()
        return True

    async def _wake_at(self, deadline: datetime) -> None:
        try:
            delay = max(0.0, (deadline - datetime.now(UTC)).total_seconds())
            await asyncio.sleep(delay)
            if self._wake_deadline == deadline:
                self._wake_event.set()
        finally:
            if asyncio.current_task() is self._wake_task:
                self._wake_task = None
                self._wake_deadline = None

    async def _tick(self) -> None:
        await self._release_untracked_running()
        now = datetime.now(UTC)
        due = await self.store.list_due(now)
        for automation in due:
            if self._is_session_bound(automation) and not self._loop_can_fire(automation):
                # Session-bound automation is due but the target session has
                # an active run. Skip without claiming — next_run_at stays
                # past, so the task is re-evaluated on the next tick (or
                # sooner via the run-completed fast path in
                # handle_run_completed).
                continue
            await self._start_run(automation)
        await self._drain_event_backlog()
        await self._evaluate_idle_triggers(now)

    @staticmethod
    def _is_session_bound(automation: Automation) -> bool:
        """A session-bound automation targets a specific session, so its
        firing must coordinate with that session's run lifecycle. Identified
        by thread_id — kind-agnostic because channel automations
        (kind='automation') created via `service.create(thread_id=...)` are
        also session-bound."""
        return automation.thread_id is not None

    def _loop_can_fire(self, automation: Automation) -> bool:
        if automation.thread_id in self._reserved_session_ids:
            return False
        if self._loop_fire_gate is None:
            return True
        return self._loop_fire_gate(automation)

    def _reserve_session(self, automation: Automation) -> None:
        if automation.thread_id is not None:
            self._reserved_session_ids.add(automation.thread_id)

    def _release_session(self, automation: Automation) -> None:
        if automation.thread_id is not None:
            self._reserved_session_ids.discard(automation.thread_id)

    async def _evaluate_idle_triggers(self, now: datetime) -> None:
        idle_seconds = (now - self._last_activity_at).total_seconds()
        idle_minutes = idle_seconds / 60
        if idle_minutes < 1:
            return

        for auto in await self.store.list_by_trigger_type("idle"):
            if auto.task_id in self._idle_fired:
                continue
            for trigger in auto.triggers:
                if not isinstance(trigger, IdleTrigger):
                    continue
                if idle_minutes < trigger.idle_minutes:
                    continue
                self._idle_fired.add(auto.task_id)
                ctx = {"trigger_type": "idle", "idle_minutes": int(idle_minutes)}
                await self._start_run(auto, context=ctx)
                break

    async def handle_run_completed(
        self,
        event: RunCompleted,
        *,
        preserve_next_run: bool = False,
    ) -> None:
        await self._reject_unbound_detached_event(
            event.automation_task_id,
            event.session_id,
        )
        if event.automation_task_id is None:
            self.update_activity()
        now = datetime.now(UTC)

        session_bound = await self.store.list_session_bound_by_session(event.session_id, include_disabled=True)
        settled_task_ids: set[str] = set()

        # Settle detached (fire-and-accept) iteration runs FIRST: their run
        # row sat in status='running' since dispatch — write the real report
        # and end time, release the running flag, and only now announce the
        # finish. Exact task/run/session matching plus terminal status guards
        # make duplicate and unrelated outbox deliveries no-ops.
        for auto in session_bound:
            detached = await self.store.get_running_detached_chat_run(auto.task_id)
            if (
                not auto.read_history
                or detached is None
                or event.automation_task_id != auto.task_id
                or event.run_id != detached[0]
                or event.session_id != detached[1]
            ):
                continue
            if self._validate_completed_run is not None:
                try:
                    await self._validate_completed_run(auto, event.run_id)
                except Exception as exc:
                    if await self._settle_failure(auto, f"{type(exc).__name__}: {exc}", now):
                        settled_task_ids.add(auto.task_id)
                    continue
            settled = await self.store.settle_run(
                task_id=auto.task_id,
                run_id=None,
                status="completed",
                result=event.result,
                error=None,
                ended_at=now,
                next_run=(
                    auto.next_run_at
                    if preserve_next_run
                    else self._advance_to_future(auto, now)
                    if auto.enabled
                    else None
                ),
                expected_next_run=auto.next_run_at,
                preserve_external_reschedule=True,
                preserve_result=False,
                disable_one_shot=any(trigger.one_shot for trigger in auto.triggers),
            )
            if not settled:
                continue
            self._release_session(auto)
            settled_task_ids.add(auto.task_id)
            await self.emit_automation_event(
                AutomationFinishedEvent(task_id=auto.task_id, result=event.result),
            )

        # Fast-path for session-bound automations targeting this session
        # (loops AND channel automations created via service.create with
        # thread_id): the session just went idle, so anything deferred by
        # the fire gate (or whose next_run_at has already passed) can fire
        # now as a fresh turn without waiting for the next 60s poll tick.
        for auto in session_bound:
            if (
                auto.task_id in settled_task_ids
                or not auto.enabled
                or auto.next_run_at is None
                or auto.next_run_at > now
            ):
                continue
            if not self._loop_can_fire(auto):
                continue
            await self._start_run(auto)

        if event.automation_task_id is not None:
            return
        for auto in await self.store.list_by_trigger_type("count"):
            if auto.in_cooldown(now):
                continue
            for trigger in auto.triggers:
                if not isinstance(trigger, CountTrigger):
                    continue
                count = await self.store.increment_count(auto.task_id, event.session_id, now)
                if count < trigger.every_n:
                    continue
                await self.store.clear_count(auto.task_id, event.session_id)
                await self._start_run(
                    auto,
                    context={
                        "trigger_type": "count",
                        "session_id": event.session_id,
                        "messages": event.messages,
                    },
                )
                break

    async def handle_run_failed(self, event: RunFailed) -> None:
        """Settle detached session-bound automations as soon as chat fails."""
        await self._reject_unbound_detached_event(
            event.automation_task_id,
            event.session_id,
        )
        now = datetime.now(UTC)
        for auto in await self.store.list_session_bound_by_session(event.session_id, include_disabled=True):
            detached = await self.store.get_running_detached_chat_run(auto.task_id)
            if (
                not auto.read_history
                or detached is None
                or event.automation_task_id != auto.task_id
                or event.run_id != detached[0]
                or event.session_id != detached[1]
            ):
                continue
            await self._settle_failure(auto, event.error, now)

    async def _reject_unbound_detached_event(
        self,
        automation_task_id: str | None,
        session_id: str,
    ) -> None:
        if automation_task_id is None:
            return
        automation = await self.store.get(automation_task_id)
        if (
            automation is not None
            and automation.read_history
            and automation.thread_id == session_id
            and await self.store.has_unbound_running_run(automation_task_id)
        ):
            raise DetachedRunBindingPending(f"automation {automation_task_id} has not bound its chat run yet")

    async def _settle_failure(
        self,
        automation: Automation,
        error: str,
        now: datetime,
        *,
        event_queue_id: int | None = None,
        event_attempt_count: int = 0,
    ) -> bool:
        retry_at = await self._failure_retry_at(automation.task_id, now) if event_queue_id is None else None
        event_retry_at = self._event_retry_at(event_attempt_count, now) if event_queue_id is not None else None
        settled = await self.store.settle_run(
            task_id=automation.task_id,
            run_id=None,
            status="failed",
            result=None,
            error=error,
            ended_at=now,
            next_run=retry_at,
            expected_next_run=automation.next_run_at,
            preserve_external_reschedule=True,
            preserve_result=False,
            disable_one_shot=False,
            event_queue_id=event_queue_id,
            event_retry_at=event_retry_at,
        )
        if not settled:
            return False
        self._release_session(automation)
        if retry_at is not None:
            self._schedule_wake(retry_at)
        if event_retry_at is not None:
            self._schedule_wake(event_retry_at)
        await self.emit_automation_event(
            AutomationFinishedEvent(task_id=automation.task_id, result=None),
        )
        return True

    async def _start_run(self, automation: Automation, context: str | dict | None = None) -> None:
        if automation.in_cooldown(datetime.now(UTC)):
            return
        if self._is_session_bound(automation) and not self._loop_can_fire(automation):
            return
        claimed = await self.store.try_mark_running(automation.task_id, datetime.now(UTC))
        if not claimed:
            _logger.debug("Automation %s already claimed or disabled", automation.task_id)
            return
        self._reserve_session(automation)
        self._pending_running_task_ids.add(automation.task_id)
        # Reschedule next_run_at *now*, before the task body executes, so
        # the UI countdown ticks from full interval during task execution
        # instead of pinning at 0s until the task finishes. Matches Claude
        # Code's cronScheduler pattern (reschedule on fire, not on finish).
        #
        # Crucially we do NOT mutate `automation.next_run_at` on the local
        # snapshot — the finally block in `_run_and_finalize` re-runs
        # `_advance_to_future` anchored to the *original* next_run_at and
        # writes the same value (or correctly catches up if the task
        # outran the interval). Mutating the snapshot here would cause the
        # finally block to advance *again*, shifting the schedule forward
        # by one extra interval every fire.
        try:
            next_run: datetime | None = None
            if automation.enabled:
                now = datetime.now(UTC)
                next_run = self._advance_to_future(automation, now)
                prewrote = await self.store.compare_and_set_next_run(
                    automation.task_id,
                    automation.next_run_at,
                    next_run,
                )
                if prewrote and next_run is not None:
                    self._schedule_wake(next_run)
            run_id = await self.store.record_run_start(automation.task_id, datetime.now(UTC))
            execution = asyncio.create_task(
                self._run_and_finalize(
                    automation,
                    context,
                    run_id=run_id,
                    prewritten_next_run=next_run,
                    has_prewritten_next_run=True,
                )
            )
            self._track(execution, automation.task_id)
            self._pending_running_task_ids.discard(automation.task_id)
        except BaseException:
            self._pending_running_task_ids.discard(automation.task_id)
            await self.store.clear_running(automation.task_id)
            self._release_session(automation)
            raise

    async def emit_automation_event(self, event: SSEEvent) -> None:
        if self._bus_registry:
            try:
                bus = self._bus_registry.get_or_create(AUTOMATION_BUS_KEY)
                await bus.emit(event)
            except Exception:
                _logger.debug("Failed to emit automation event", exc_info=True)

    async def _run_and_finalize(
        self,
        automation: Automation,
        context: str | dict | None = None,
        event_queue_id: int | None = None,
        event_attempt_count: int = 0,
        run_id: int | None = None,
        prewritten_next_run: datetime | None = None,
        has_prewritten_next_run: bool = False,
    ) -> None:
        try:
            outcome = await execute_and_settle(
                automation,
                context,
                event_queue_id=event_queue_id,
                event_attempt_count=event_attempt_count,
                run_id=run_id,
                prewritten_next_run=prewritten_next_run,
                has_prewritten_next_run=has_prewritten_next_run,
                dependencies=ExecutionDependencies(
                    store=self.store,
                    build_deps=self._build_deps,
                    bus_registry=self._bus_registry,
                    handlers=self._handlers,
                    iteration_dispatcher=self._iteration_dispatcher,
                    post_dispatcher=self._post_dispatcher,
                    validate_completed_run=self._validate_completed_run,
                    event_emitter=self.emit_automation_event,
                    agent_runner=run_agent,
                    streaming_agent_runner=run_agent_streaming,
                ),
            )
        except BaseException:
            self._release_session(automation)
            raise
        if outcome.state == "detached":
            return
        if outcome.release_event_claim and event_queue_id is not None:
            await self.store.release_event_claim(event_queue_id)
        self._release_session(automation)
        if outcome.state == "unstarted":
            await self.store.clear_running(automation.task_id)
            if outcome.cancelled:
                raise asyncio.CancelledError
            raise RuntimeError(outcome.failure_message or "automation run did not start")
        if outcome.state == "deferred":
            self._schedule_wake(datetime.now(UTC) + timedelta(seconds=SCHEDULER_EVENT_RETRY_BASE_SECONDS))
            await self.emit_automation_event(AutomationFinishedEvent(task_id=automation.task_id, result=None))
            return
        if outcome.retry_at is not None:
            self._schedule_wake(outcome.retry_at)
        if outcome.event_retry_at is not None:
            self._schedule_wake(outcome.event_retry_at)
        await self.emit_automation_event(AutomationFinishedEvent(task_id=automation.task_id, result=outcome.result))
        if outcome.cancelled:
            raise asyncio.CancelledError
        if outcome.wake_catch_up:
            self._wake_event.set()
        if outcome.start_next_event:
            await self._start_next_queued_event_if_idle(automation.task_id)
        if outcome.failure_message is None:
            _logger.info("Completed automation %s", automation.task_id)

    async def _run_handler(
        self,
        automation: Automation,
        context: str | dict | None = None,
    ) -> str | None:
        return await run_handler(automation, context, self._handlers)

    async def _run_agent(self, automation: Automation, context: str | dict | None = None) -> CompletedAgentRun:
        return await run_agent_execution(
            automation,
            context,
            build_deps=self._build_deps,
            bus_registry=self._bus_registry,
            agent_runner=run_agent,
            streaming_agent_runner=run_agent_streaming,
        )

    async def _run_session_bound(
        self,
        automation: Automation,
        automation_run_id: int,
        context: str | dict | None = None,
    ) -> str | CompletedAgentRun | DetachedRun | None:
        outcome = await run_session_bound(
            automation,
            automation_run_id,
            context,
            store=self.store,
            iteration_dispatcher=self._iteration_dispatcher,
            post_dispatcher=self._post_dispatcher,
        )
        return outcome.result

    async def fire_event(self, event: TriggerEvent) -> None:
        now = datetime.now(UTC)
        for task_id in await enqueue_matching_events(self.store, event, now):
            _logger.info("Event %s matched automation %s (%s)", event.event_type, task_id, event.event_key)
            await self._start_next_queued_event_if_idle(task_id)

    async def _matching_event_automations(self, event: TriggerEvent) -> list[Automation]:
        return await matching_event_automations(self.store, event)

    async def _matching_message_automations(self, event: MessageReceived) -> list[Automation]:
        return await matching_message_automations(self.store, event)

    @staticmethod
    def _message_trigger_passes(trigger: MessageTrigger, event: MessageReceived) -> bool:
        return message_trigger_passes(trigger, event)

    def schedule_run(self, task_id: str) -> None:
        execution = asyncio.create_task(self._manual_run(task_id))
        self._track(execution)

    async def _manual_run(self, task_id: str) -> None:
        if not (automation := await self.store.get(task_id)):
            _logger.warning("Automation %s not found for manual run", task_id)
            return
        if automation.running_since:
            _logger.warning("Automation %s already running, skipping manual run", task_id)
            return
        await self._start_run(automation, context={MANUAL_RUN_CONTEXT_KEY: True})

    async def _drain_event_backlog(self) -> None:
        for task_id in await self.store.list_tasks_with_pending_events():
            await self._start_next_queued_event_if_idle(task_id)

    async def _start_next_queued_event_if_idle(self, task_id: str) -> None:
        automation = await self.store.get(task_id)
        if not automation or not automation.enabled:
            return
        if self._is_session_bound(automation) and not self._loop_can_fire(automation):
            return

        now = datetime.now(UTC)
        claimed_running = await self.store.try_mark_running(task_id, now)
        if not claimed_running:
            return
        self._reserve_session(automation)
        self._pending_running_task_ids.add(task_id)

        try:
            next_event = await claim_next_queued_event(self.store, task_id, now)
            if next_event is None:
                await self.store.clear_running(task_id)
                self._pending_running_task_ids.discard(task_id)
                self._release_session(automation)
                return

            execution = asyncio.create_task(
                self._run_and_finalize(
                    automation,
                    next_event.context,
                    event_queue_id=next_event.queue_id,
                    event_attempt_count=next_event.attempt_count,
                )
            )
            self._track(execution, automation.task_id)
            self._pending_running_task_ids.discard(task_id)
        except BaseException:
            self._pending_running_task_ids.discard(task_id)
            await self.store.clear_running(task_id)
            self._release_session(automation)
            raise

    async def _failure_retry_at(self, task_id: str, now: datetime) -> datetime:
        return await failure_retry_at(self.store, task_id, now)

    @staticmethod
    def _event_retry_at(attempt_count: int, now: datetime) -> datetime | None:
        return event_retry_at(attempt_count, now)

    @staticmethod
    def _retry_delay_seconds(attempt_count: int) -> int:
        return retry_delay_seconds(attempt_count)

    async def get_status(self) -> dict:
        now = datetime.now(UTC)
        return {
            "status": "running" if self.is_running else "stopped",
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "last_tick_at": self._last_tick_at.isoformat() if self._last_tick_at else None,
            "last_tick_error": self._last_tick_error,
            "last_activity_at": self._last_activity_at.isoformat(),
            "running_tasks": len(self._running),
            "registered_handlers": sorted(self._handlers),
            "store": await self.store.get_status(now),
        }
