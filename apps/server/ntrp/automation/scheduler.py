import asyncio
from dataclasses import dataclass
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from ntrp.automation.models import Automation
from ntrp.automation.prompts import AUTOMATION_PROMPT, AUTOMATION_SUFFIX
from ntrp.automation.store import AutomationStore
from ntrp.automation.triggers import CountTrigger, EventTrigger, IdleTrigger, MessageTrigger, TimeTrigger
from ntrp.constants import (
    MESSAGE_RECEIVED,
    SCHEDULER_DEDUP_TTL,
    SCHEDULER_EVENT_MAX_RETRIES,
    SCHEDULER_EVENT_RETRY_BASE_SECONDS,
    SCHEDULER_EVENT_RETRY_MAX_SECONDS,
    SCHEDULER_POLL_INTERVAL,
)
from ntrp.events.internal import RunCompleted
from ntrp.events.sse import AutomationFinishedEvent, AutomationProgressEvent, SSEEvent
from ntrp.constants import DETACHED_RUN_MAX_AGE
from ntrp.events.triggers import EVENT_APPROACHING, EventApproaching, MessageReceived, TriggerEvent
from ntrp.logging import get_logger
from ntrp.operator.runner import OperatorDeps, RunRequest, run_agent, run_agent_streaming
from ntrp.server.bus import BusRegistry

AUTOMATION_BUS_KEY = "automation:events"

_logger = get_logger(__name__)

# Builtin daily memory maintenance phases. When multiple phases are overdue
# after sleep/offline time, catch them up in this order on the same boot.
_CATCH_UP_PHASE_ORDER = ("memory_consolidate", "memory_publish")
_CATCH_UP_HANDLERS = set(_CATCH_UP_PHASE_ORDER)
_CATCH_UP_CADENCE = timedelta(hours=24)


IterationDispatcher = Callable[[Automation, str | dict | None], Awaitable[str | None]]
"""Fire a session-bound automation in iteration mode (read_history=True):
re-enter the target session with the loop prompt; the agent sees the full
session history. `context` is the triggering event's rendered context (None
for non-event runs)."""

PostDispatcher = Callable[[Automation, str | dict | None], Awaitable[str | None]]
"""Fire a session-bound automation in post mode (read_history=False): run
the agent fresh (no session history), then post the result back into the
target session as an assistant message. `context` is the triggering event's
rendered context (None for non-event runs)."""

# Back-compat alias — older code (and external callers) may still import
# `LoopDispatcher`. New code should reach for `IterationDispatcher`.
LoopDispatcher = IterationDispatcher

# Manual "run now" rides the context dict under this key. It is transport,
# not content: every prompt/handler consumer must split it back out via
# split_manual_flag before rendering.
MANUAL_RUN_CONTEXT_KEY = "_ntrp_manual_run"


def split_manual_flag(context: str | dict | None) -> tuple[bool, str | dict | None]:
    """Pop the manual-run marker off an automation context."""
    if isinstance(context, dict):
        manual = bool(context.get(MANUAL_RUN_CONTEXT_KEY))
        rest = {k: v for k, v in context.items() if k != MANUAL_RUN_CONTEXT_KEY}
        return manual, rest or None
    return False, context


@dataclass(frozen=True)
class DetachedRun:
    """Marker returned by fire-and-accept dispatches (iteration loops): the
    agent run continues detached, so the scheduler must NOT settle the run
    row / running flag — RunCompleted does that in handle_run_completed."""

    handle: str | None


class RunSkipped(Exception):
    """Raised by a dispatcher that declined to run (e.g. autonomous cap).
    The scheduler records the skip without consuming an iteration."""

# True ⇒ ok to fire this loop right now; False ⇒ defer to next tick. Used
# to keep loop iterations from being injected mid-turn into a user's
# active conversation — they should render as fresh turns once the
# session goes idle. Applies to both iteration and post modes: iteration
# would re-enter mid-conversation, post would race the in-flight run on
# session_service writes.
LoopFireGate = Callable[[Automation], bool]


class Scheduler:
    def __init__(
        self,
        store: AutomationStore,
        build_deps: Callable[[], OperatorDeps],
    ):
        self.store = store
        self._build_deps = build_deps
        self._bus_registry: BusRegistry | None = None
        self._task: asyncio.Task | None = None
        self._wake_task: asyncio.Task | None = None
        self._wake_deadline: datetime | None = None
        self._wake_event = asyncio.Event()
        self._running: set[asyncio.Task] = set()
        self._running_task_ids: dict[asyncio.Task, str] = {}
        self._pending_running_task_ids: set[str] = set()
        # task_id → dispatch time for in-flight detached runs; settled by
        # handle_run_completed, reaped by _release_untracked_running.
        self._detached_task_ids: dict[str, datetime] = {}
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

    # Back-compat alias for callers that still use the old name.
    set_loop_dispatcher = set_iteration_dispatcher

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
            self._detached_task_ids.clear()

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
            dispatched_at = self._detached_task_ids.get(automation.task_id)
            if dispatched_at is not None:
                if now - dispatched_at <= DETACHED_RUN_MAX_AGE:
                    continue  # detached run legitimately in flight
                # RunCompleted never arrived (the detached run died without
                # reporting) — fail the row so history stays terminal.
                _logger.warning(
                    "Detached run for %s never reported back; failing it", automation.task_id
                )
                self._detached_task_ids.pop(automation.task_id, None)
                await self.store.fail_latest_running_run(
                    automation.task_id, now, "run never reported back"
                )
                await self.store.clear_running(automation.task_id)
                await self.emit_automation_event(
                    AutomationFinishedEvent(task_id=automation.task_id, result=None),
                )
                continue
            _logger.warning("Clearing untracked running flag for automation %s", automation.task_id)
            await self.store.clear_running(automation.task_id)

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
        cleared = await self.store.clear_all_running()
        if cleared:
            _logger.info("Cleared %d stale running flags", cleared)
        orphaned = await self.store.fail_orphaned_runs(
            datetime.now(UTC), "interrupted by server restart"
        )
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
                # A daily maintenance builtin whose slot was missed (laptop asleep
                # past 03:00) stays DUE so _tick fires it now, instead of skipping
                # to tomorrow — otherwise memory maintenance can be skipped for days.
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
        """A missed daily maintenance builtin should run immediately on boot rather
        than skip to its next future slot. Scoped to builtin maintenance handlers
        on a single daily TimeTrigger whose scheduled slot is now overdue and
        still unsatisfied — so a machine asleep/off at 03:00 still gets memory
        maintenance, without surprising user-created reminders."""
        if not (automation.builtin and automation.handler in _CATCH_UP_HANDLERS):
            return False
        if len(automation.triggers) != 1 or not isinstance(automation.triggers[0], TimeTrigger):
            return False
        due_at = automation.next_run_at
        if due_at is None or due_at > now:
            return False
        last = automation.last_run_at
        return last is None or last < due_at

    @staticmethod
    def _catch_up_phase_index(handler: str | None) -> int | None:
        if handler is None:
            return None
        try:
            return _CATCH_UP_PHASE_ORDER.index(handler)
        except ValueError:
            return None

    def _should_defer_ordered_catch_up(
        self,
        automation: Automation,
        due: list[Automation],
        running_handlers: set[str],
        now: datetime,
    ) -> bool:
        phase_index = self._catch_up_phase_index(automation.handler)
        if phase_index is None or phase_index == 0:
            return False
        if not self._should_catch_up_missed(automation, now):
            return False
        earlier = set(_CATCH_UP_PHASE_ORDER[:phase_index])
        due_handlers = {
            auto.handler
            for auto in due
            if auto.task_id != automation.task_id and auto.handler and self._should_catch_up_missed(auto, now)
        }
        return any(handler in earlier for handler in due_handlers | running_handlers)

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
        running_handlers = {auto.handler for auto in await self.store.list_running() if auto.handler}
        for automation in due:
            if self._should_defer_ordered_catch_up(automation, due, running_handlers, now):
                continue
            if self._is_session_bound(automation) and not self._loop_can_fire(automation):
                # Session-bound automation is due but the target session has
                # an active run. Skip without claiming — next_run_at stays
                # past, so the task is re-evaluated on the next tick (or
                # sooner via the run-completed fast path in
                # handle_run_completed).
                continue
            await self._start_run(automation)
            if automation.handler:
                running_handlers.add(automation.handler)
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
        if self._loop_fire_gate is None:
            return True
        try:
            return self._loop_fire_gate(automation)
        except Exception:
            _logger.exception("Loop fire gate raised; defaulting to allow")
            return True

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

    async def handle_run_completed(self, event: RunCompleted) -> None:
        self.update_activity()
        now = datetime.now(UTC)

        session_bound = await self.store.list_session_bound_by_session(event.session_id)

        # Settle detached (fire-and-accept) iteration runs FIRST: their run
        # row sat in status='running' since dispatch — write the real report
        # and end time, release the running flag, and only now announce the
        # finish. The status guard in finalize_latest_run makes this
        # idempotent across duplicate/late outbox deliveries.
        for auto in session_bound:
            if not auto.read_history:
                continue
            settled = await self.store.finalize_latest_run(auto.task_id, event.result, now)
            if not settled:
                continue
            self._detached_task_ids.pop(auto.task_id, None)
            if event.result:
                await self.store.set_last_result(auto.task_id, event.result)
            await self.store.clear_running(auto.task_id)
            await self.emit_automation_event(
                AutomationFinishedEvent(task_id=auto.task_id, result=event.result),
            )

        # Fast-path for session-bound automations targeting this session
        # (loops AND channel automations created via service.create with
        # thread_id): the session just went idle, so anything deferred by
        # the fire gate (or whose next_run_at has already passed) can fire
        # now as a fresh turn without waiting for the next 60s poll tick.
        for auto in session_bound:
            if not auto.enabled or auto.next_run_at is None or auto.next_run_at > now:
                continue
            if not self._loop_can_fire(auto):
                continue
            await self._start_run(auto)

        for auto in await self.store.list_by_trigger_type("count"):
            if auto.in_cooldown(now):
                continue
            for trigger in auto.triggers:
                if not isinstance(trigger, CountTrigger):
                    continue
                sid = event.session_id
                count = await self.store.increment_count(auto.task_id, sid, now)
                if count >= trigger.every_n:
                    await self.store.clear_count(auto.task_id, sid)
                    ctx = {
                        "trigger_type": "count",
                        "session_id": event.session_id,
                        "messages": event.messages,
                    }
                    await self._start_run(auto, context=ctx)
                    break

    async def _start_run(self, automation: Automation, context: str | dict | None = None) -> None:
        if automation.in_cooldown(datetime.now(UTC)):
            return
        claimed = await self.store.try_mark_running(automation.task_id, datetime.now(UTC))
        if not claimed:
            _logger.debug("Automation %s already claimed or disabled", automation.task_id)
            return
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
            if automation.enabled:
                now = datetime.now(UTC)
                next_run = self._advance_to_future(automation, now)
                if next_run is not None:
                    await self.store.set_next_run(automation.task_id, next_run)
                    self._schedule_wake(next_run)
            execution = asyncio.create_task(self._run_and_finalize(automation, context))
            self._track(execution, automation.task_id)
            self._pending_running_task_ids.discard(automation.task_id)
        except BaseException:
            self._pending_running_task_ids.discard(automation.task_id)
            await self.store.clear_running(automation.task_id)
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
    ) -> None:
        await self.emit_automation_event(
            AutomationProgressEvent(task_id=automation.task_id, status="starting..."),
        )
        result: str | DetachedRun | None = None
        success = False
        error_message = ""
        started_at = datetime.now(UTC)
        run_id: int | None = None
        try:
            run_id = await self.store.record_run_start(automation.task_id, started_at)
        except Exception:
            _logger.exception("Failed to record automation run start %s", automation.task_id)
        try:
            if self._is_session_bound(automation):
                result = await self._run_session_bound(automation, context)
            elif automation.handler:
                result = await self._run_handler(automation, context)
            else:
                result = await self._run_agent(automation, context)
            success = True
        except Exception as e:
            error_message = f"{type(e).__name__}: {e}"
            _logger.exception("Failed to execute automation %s", automation.task_id)
        finally:
            detached = isinstance(result, DetachedRun)
            result_text = None if detached else result
            # A detached run is still going — the "finished" event (with the
            # real result) is emitted when RunCompleted settles it; this one
            # only nudges the UI to refresh schedule/running state.
            await self.emit_automation_event(
                AutomationFinishedEvent(
                    task_id=automation.task_id, result=result_text
                ),
            )
            now = datetime.now(UTC)
            # If _run_session_bound disabled this automation (aged_out / max_iterations),
            # the snapshot's enabled was mutated to False — don't write a
            # future next_run_at for a disabled loop or the UI shows a
            # countdown for something that will never fire.
            event_settlement_error: Exception | None = None
            try:
                next_run = self._advance_to_future(automation, now) if automation.enabled else None
                # Detached: advance the clock but keep last_result — the real
                # report lands via RunCompleted.
                await self.store.update_last_run(
                    automation.task_id, now, next_run, result=result_text, preserve_result=detached
                )
                if any(t.one_shot for t in automation.triggers):
                    await self.store.set_enabled(automation.task_id, False)
            except Exception:
                _logger.exception("Failed to update automation run result %s", automation.task_id)
            if run_id is not None and not (detached and success):
                try:
                    await self.store.record_run_finish(
                        run_id,
                        status="completed" if success else "failed",
                        result=result_text,
                        error=error_message or None,
                        ended_at=now,
                    )
                except Exception:
                    _logger.exception("Failed to record automation run finish %s", automation.task_id)
            if event_queue_id is not None:
                try:
                    if success:
                        await self.store.complete_event(event_queue_id)
                    else:
                        await self._handle_failed_event(
                            automation.task_id,
                            event_queue_id,
                            event_attempt_count,
                            error_message,
                        )
                except Exception as exc:
                    event_settlement_error = exc
                    _logger.exception("Failed to settle queued automation event %s", event_queue_id)
                    if success:
                        try:
                            await self.store.dead_letter_event(
                                event_queue_id,
                                f"settlement failed after successful execution: {type(exc).__name__}: {exc}",
                                datetime.now(UTC),
                            )
                        except Exception:
                            _logger.exception(
                                "Failed to dead-letter queued automation event after settlement uncertainty %s",
                                event_queue_id,
                            )
                    else:
                        try:
                            await self.store.release_event_claim(event_queue_id)
                        except Exception:
                            _logger.exception("Failed to release queued automation event %s", event_queue_id)
            if detached and success:
                # The run row stays 'running' and the flag stays set until
                # RunCompleted settles both — the automation IS running.
                self._detached_task_ids[automation.task_id] = now
            else:
                try:
                    await self.store.clear_running(automation.task_id)
                except Exception as exc:
                    _logger.exception("Failed to clear running flag for automation %s", automation.task_id)
                    raise RuntimeError(f"Failed to clear running flag for automation {automation.task_id}") from exc
            if automation.handler in _CATCH_UP_HANDLERS:
                self._wake_event.set()
            if event_queue_id is not None and event_settlement_error is None:
                try:
                    await self._start_next_queued_event_if_idle(automation.task_id)
                except Exception:
                    _logger.exception("Failed to start next queued event for automation %s", automation.task_id)
            if event_settlement_error is not None:
                raise RuntimeError(
                    f"Failed to settle queued automation event {event_queue_id}"
                ) from event_settlement_error
            _logger.info("Completed automation %s", automation.task_id)

    async def _run_handler(self, automation: Automation, context: str | dict | None = None) -> str | None:
        handler = self._handlers.get(automation.handler)
        if not handler:
            raise RuntimeError(f"No handler registered for '{automation.handler}'")
        _logger.info("Executing internal automation %s: %s", automation.task_id, automation.description[:80])
        _, context = split_manual_flag(context)
        if isinstance(context, dict):
            ctx = context
        elif context:
            try:
                ctx = json.loads(context)
            except json.JSONDecodeError:
                ctx = {"event_context": context}
        else:
            ctx = {}
        # Handlers shared by multiple automation instances (e.g. one "area_agent"
        # handler backing every area:{key} automation) need to know which
        # instance fired; singleton builtin handlers just ignore the extra key.
        ctx["task_id"] = automation.task_id
        return await handler(ctx)

    async def _run_agent(self, automation: Automation, context: str | dict | None = None) -> str | None:
        _, context = split_manual_flag(context)
        ctx_str = json.dumps(context) if isinstance(context, dict) else context
        prompt = AUTOMATION_PROMPT.render(description=automation.description, context=ctx_str)

        _logger.info("Executing automation %s: %s", automation.task_id, automation.description[:80])
        request = RunRequest(
            prompt=prompt,
            prompt_suffix=AUTOMATION_SUFFIX,
            auto_approve=automation.auto_approve,
            source_id=automation.task_id,
            model=automation.model,
            skip_approvals=automation.auto_approve,
            automation_id=automation.task_id,
            tool_scope=tuple(automation.tool_scope) if automation.tool_scope else None,
        )

        if self._bus_registry:
            bus = self._bus_registry.get_or_create(AUTOMATION_BUS_KEY)
            result = await run_agent_streaming(self._build_deps(), request, bus, automation.task_id)
        else:
            result = await run_agent(self._build_deps(), request)

        return result.output

    async def _run_session_bound(
        self, automation: Automation, context: str | dict | None = None
    ) -> str | None:
        """Fire a session-bound automation.

        Two modes, picked by `automation.read_history`:
          • True  → iteration mode: re-enter the target session via the
            iteration dispatcher; the agent sees full history.
          • False → post mode: run the agent fresh and post its result
            back into the target session as an assistant message.

        Both modes honor aged_out / max_iterations / iteration_count.
        """
        if not automation.description:
            raise RuntimeError(f"Loop {automation.task_id} missing description")
        # Aged-out check is mode-agnostic — disable before reaching for
        # a dispatcher.
        if automation.aged_out(datetime.now(UTC)):
            await self.store.set_enabled(automation.task_id, False)
            # Mutate the snapshot so _run_and_finalize's finally block sees
            # the disabled state and skips writing a future next_run_at.
            automation.enabled = False
            _logger.info("Loop %s aged out (max_age_days=%d), disabling", automation.task_id, automation.max_age_days)
            return f"Loop aged out after {automation.max_age_days} days"
        if automation.read_history:
            dispatcher = self._iteration_dispatcher
            mode = "iteration"
            # Iteration mode re-enters via `thread_id`.
            if not automation.thread_id:
                raise RuntimeError(f"Iteration loop {automation.task_id} missing thread_id")
        else:
            dispatcher = self._post_dispatcher
            mode = "post"
            # Post mode writes into `thread_id`.
            if not automation.thread_id:
                raise RuntimeError(f"Post loop {automation.task_id} missing thread_id")
        if dispatcher is None:
            raise RuntimeError(f"{mode} dispatcher not wired")
        _logger.info(
            "Firing %s loop %s (iter %d) into session %s",
            mode,
            automation.task_id,
            automation.iteration_count + 1,
            automation.thread_id,
        )
        try:
            dispatched = await dispatcher(automation, context)
        except RunSkipped as skip:
            # The run never happened — no iteration consumed, so the intake
            # first-run trigger and max_iterations accounting stay truthful.
            _logger.info("Loop %s skipped: %s", automation.task_id, skip)
            return f"Skipped: {skip}"
        # Post mode awaited the run and returns its text; iteration mode is
        # fire-and-accept — the returned value is only the run-id slug, so
        # hand back a DetachedRun marker instead of a fake result.
        result: str | DetachedRun | None = (
            DetachedRun(dispatched) if automation.read_history else dispatched
        )
        await self.store.increment_iteration(automation.task_id)
        # Disable after max_iterations is hit. iteration_count was already
        # incremented in the store; compare against the in-memory value + 1
        # since `automation` is a snapshot taken before the increment.
        if automation.max_iterations is not None and (automation.iteration_count + 1) >= automation.max_iterations:
            await self.store.set_enabled(automation.task_id, False)
            automation.enabled = False
            _logger.info("Loop %s reached max_iterations=%d, disabling", automation.task_id, automation.max_iterations)
        return result

    async def fire_event(self, event: TriggerEvent) -> None:
        now = datetime.now(UTC)
        cutoff = datetime.now(UTC) - timedelta(seconds=SCHEDULER_DEDUP_TTL)
        await self.store.evict_event_claims_older_than(cutoff)
        event_type = event.event_type
        event_key = event.event_key
        context = event.format_context()
        automations = await self._matching_event_automations(event)
        for automation in automations:
            # Check lead_minutes for event_approaching triggers
            if event_type == EVENT_APPROACHING and isinstance(event, EventApproaching):
                matching_triggers = [
                    t for t in automation.triggers if isinstance(t, EventTrigger) and t.event_type == EVENT_APPROACHING
                ]
                if matching_triggers:
                    trigger = matching_triggers[0]
                    if trigger.lead_minutes is not None and event.minutes_until > int(trigger.lead_minutes):
                        continue

            claimed = await self.store.claim_and_enqueue_event(automation.task_id, event_key, context, now)
            if not claimed:
                continue
            _logger.info("Event %s matched automation %s (%s)", event_type, automation.task_id, event_key)
            await self._start_next_queued_event_if_idle(automation.task_id)

    async def _matching_event_automations(self, event: TriggerEvent) -> list[Automation]:
        if event.event_type == MESSAGE_RECEIVED and isinstance(event, MessageReceived):
            return await self._matching_message_automations(event)
        return await self.store.list_event_triggered(event.event_type)

    async def _matching_message_automations(self, event: MessageReceived) -> list[Automation]:
        automations = await self.store.list_message_triggered(event.source, event.channel_id)
        matched: list[Automation] = []
        for automation in automations:
            triggers = [
                t
                for t in automation.triggers
                if isinstance(t, MessageTrigger) and t.source == event.source and event.channel_id in t.channel_ids
            ]
            if any(self._message_trigger_passes(t, event) for t in triggers):
                matched.append(automation)
        return matched

    @staticmethod
    def _message_trigger_passes(trigger: MessageTrigger, event: MessageReceived) -> bool:
        if trigger.from_user_id is not None and trigger.from_user_id != event.user_id:
            return False
        if trigger.contains:
            text = event.text.lower()
            if not any(keyword.lower() in text for keyword in trigger.contains):
                return False
        return True

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

        now = datetime.now(UTC)
        claimed_running = await self.store.try_mark_running(task_id, now)
        if not claimed_running:
            return
        self._pending_running_task_ids.add(task_id)

        try:
            next_event = await self.store.claim_next_event(task_id, now)
            if next_event is None:
                await self.store.clear_running(task_id)
                self._pending_running_task_ids.discard(task_id)
                return

            queue_id, context, attempt_count = next_event
            execution = asyncio.create_task(
                self._run_and_finalize(
                    automation,
                    context,
                    event_queue_id=queue_id,
                    event_attempt_count=attempt_count,
                )
            )
            self._track(execution, automation.task_id)
            self._pending_running_task_ids.discard(task_id)
        except BaseException:
            self._pending_running_task_ids.discard(task_id)
            await self.store.clear_running(task_id)
            raise

    async def _handle_failed_event(
        self,
        task_id: str,
        queue_id: int,
        attempt_count: int,
        error_message: str,
    ) -> None:
        if attempt_count + 1 >= SCHEDULER_EVENT_MAX_RETRIES:
            await self.store.dead_letter_event(queue_id, error_message, datetime.now(UTC))
            _logger.error(
                "Dead-lettering queued event %s for automation %s after %d attempts",
                queue_id,
                task_id,
                attempt_count + 1,
            )
            return

        delay = self._retry_delay_seconds(attempt_count)
        next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)
        await self.store.fail_event(queue_id, error_message, next_attempt_at)
        _logger.warning(
            "Retrying queued event %s for automation %s in %ds (attempt %d/%d)",
            queue_id,
            task_id,
            delay,
            attempt_count + 1,
            SCHEDULER_EVENT_MAX_RETRIES,
        )

    @staticmethod
    def _retry_delay_seconds(attempt_count: int) -> int:
        backoff = SCHEDULER_EVENT_RETRY_BASE_SECONDS * (2 ** max(attempt_count, 0))
        return min(SCHEDULER_EVENT_RETRY_MAX_SECONDS, backoff)

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
