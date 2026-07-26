import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

import arden.database as database
from arden.agent import Usage
from arden.automation.models import Automation
from arden.automation.scheduler import Scheduler
from arden.automation.service import AutomationService
from arden.automation.store import AutomationStore
from arden.automation.triggers import MessageTrigger, TimeTrigger
from arden.events.internal import RunCompleted, RunFailed
from arden.events.sse import AutomationFinishedEvent, AutomationProgressEvent, SSEEvent
from arden.events.triggers import MessageReceived


@pytest_asyncio.fixture
async def store(tmp_path: Path):
    conn = await database.connect(tmp_path / "automation.db")
    s = AutomationStore(conn)
    await s.init_schema()
    yield s
    await conn.close()


@pytest_asyncio.fixture
async def session_service(tmp_path: Path):
    from arden.context.store import SessionStore
    from arden.services.session import SessionService

    conn = await database.connect(tmp_path / "sessions.db")
    s = SessionStore(conn)
    await s.init_schema()
    yield SessionService(s)
    await conn.close()


def _loop(
    task_id: str = "loop-1",
    *,
    session_id: str = "sess-1",
    prompt: str = "check CI",
    every: str = "5m",
    max_iterations: int | None = None,
    iteration_count: int = 0,
    next_run_at: datetime | None = None,
    running_since: datetime | None = None,
    read_history: bool = True,
    thread_id: str | None = None,
) -> Automation:
    now = datetime.now(UTC)
    return Automation(
        task_id=task_id,
        name=f"Loop: {prompt[:40]}",
        description=None,
        description_source=None,
        prompt=prompt,
        model=None,
        triggers=[TimeTrigger(every=every)],
        enabled=True,
        created_at=now,
        next_run_at=next_run_at or (now - timedelta(seconds=1)),
        last_run_at=None,
        last_result=None,
        running_since=running_since,
        auto_approve=True,
        kind="loop",
        max_iterations=max_iterations,
        iteration_count=iteration_count,
        read_history=read_history,
        thread_id=thread_id if thread_id is not None else session_id,
    )


def _make_scheduler(store: AutomationStore) -> tuple[Scheduler, list[Automation]]:
    dispatched: list[Automation] = []

    async def dispatcher(auto: Automation, context: str | dict | None = None) -> str | None:
        dispatched.append(auto)
        return "fake-run-id"

    sched = Scheduler(store=store, build_deps=lambda: None)
    sched.set_iteration_dispatcher(dispatcher)
    return sched, dispatched


def _make_post_scheduler(
    store: AutomationStore,
) -> tuple[Scheduler, list[Automation], list[str]]:
    """Scheduler wired with only a post dispatcher (returns the agent's text result)."""
    dispatched: list[Automation] = []
    results: list[str] = []

    async def post_dispatcher(auto: Automation, context: str | dict | None = None) -> str | None:
        dispatched.append(auto)
        result = f"agent result for {auto.task_id}"
        results.append(result)
        return result

    sched = Scheduler(store=store, build_deps=lambda: None)
    sched.set_post_dispatcher(post_dispatcher)
    return sched, dispatched, results


@pytest.mark.asyncio
async def test_scheduler_dispatches_due_loop(store: AutomationStore):
    await store.save(_loop())
    sched, dispatched = _make_scheduler(store)

    await sched._tick()
    # Loops fire asynchronously through _start_run → _run_and_finalize.
    # Drain the scheduler's tracked tasks.
    for t in list(sched._running):
        await t

    assert len(dispatched) == 1
    assert dispatched[0].task_id == "loop-1"
    assert dispatched[0].prompt == "check CI"

    reloaded = await store.get("loop-1")
    assert reloaded.iteration_count == 1
    assert reloaded.enabled is True
    assert reloaded.next_run_at is not None and reloaded.next_run_at > datetime.now(UTC)


@pytest.mark.asyncio
async def test_start_run_clears_running_when_reschedule_fails(store: AutomationStore):
    auto = _loop()
    await store.save(auto)
    await store.conn.execute(
        """
        CREATE TRIGGER fail_next_run_update
        BEFORE UPDATE OF next_run_at ON scheduled_tasks
        WHEN OLD.running_since IS NOT NULL
        BEGIN
            SELECT RAISE(ABORT, 'next run update failed');
        END
        """
    )
    await store.conn.commit()
    sched, _dispatched = _make_scheduler(store)

    with pytest.raises(Exception, match="next run update failed"):
        await sched._start_run(auto)

    reloaded = await store.get(auto.task_id)
    assert reloaded is not None
    assert reloaded.running_since is None


@pytest.mark.asyncio
async def test_start_next_queued_event_clears_running_when_claim_fails(store: AutomationStore):
    auto = _loop()
    await store.save(auto)
    await store.enqueue_event(auto.task_id, "event-1", "{}", datetime.now(UTC))
    await store.conn.execute(
        """
        CREATE TRIGGER fail_event_claim
        BEFORE UPDATE OF claimed_at ON automation_event_queue
        BEGIN
            SELECT RAISE(ABORT, 'event claim failed');
        END
        """
    )
    await store.conn.commit()
    sched, _dispatched = _make_scheduler(store)

    with pytest.raises(Exception, match="event claim failed"):
        await sched._start_next_queued_event_if_idle(auto.task_id)

    reloaded = await store.get(auto.task_id)
    assert reloaded is not None
    assert reloaded.running_since is None


@pytest.mark.asyncio
async def test_run_finalize_keeps_detached_running_when_result_write_fails(store: AutomationStore):
    """Detached (iteration) dispatches keep the running flag on purpose —
    the run is genuinely in flight; RunCompleted (or the reaper) releases
    it. A failed last-run bookkeeping write must not change that."""
    auto = _loop(running_since=datetime.now(UTC))
    await store.save(auto)
    await store.conn.execute(
        """
        CREATE TRIGGER fail_last_run_update
        BEFORE UPDATE OF last_run_at ON scheduled_tasks
        BEGIN
            SELECT RAISE(ABORT, 'last run update failed');
        END
        """
    )
    await store.conn.commit()
    sched, dispatched = _make_scheduler(store)

    await sched._run_and_finalize(auto)

    assert len(dispatched) == 1
    reloaded = await store.get(auto.task_id)
    assert reloaded is not None
    assert reloaded.running_since is not None
    assert auto.task_id in sched._detached_task_ids


async def test_run_finalize_clears_running_for_post_mode_when_result_write_fails(store: AutomationStore):
    """Post-mode loops await their run — the old invariant holds: a failed
    bookkeeping write must not leave a zombie running flag."""
    auto = _loop(running_since=datetime.now(UTC), read_history=False)
    await store.save(auto)
    await store.conn.execute(
        """
        CREATE TRIGGER fail_last_run_update
        BEFORE UPDATE OF last_run_at ON scheduled_tasks
        BEGIN
            SELECT RAISE(ABORT, 'last run update failed');
        END
        """
    )
    await store.conn.commit()
    sched, dispatched, _results = _make_post_scheduler(store)

    await sched._run_and_finalize(auto)

    assert len(dispatched) == 1
    reloaded = await store.get(auto.task_id)
    assert reloaded is not None
    assert reloaded.running_since is None


@pytest.mark.asyncio
async def test_run_finalize_does_not_silently_complete_when_clear_running_fails(store: AutomationStore):
    auto = _loop(read_history=False)
    await store.save(auto)
    await store.conn.execute(
        """
        CREATE TRIGGER fail_clear_running
        BEFORE UPDATE OF running_since ON scheduled_tasks
        WHEN NEW.running_since IS NULL
        BEGIN
            SELECT RAISE(ABORT, 'clear running failed');
        END
        """
    )
    await store.conn.commit()
    started = asyncio.Event()
    release = asyncio.Event()

    async def dispatcher(_auto: Automation, context: str | dict | None = None) -> str | None:
        started.set()
        await release.wait()
        return "agent result"

    sched = Scheduler(store=store, build_deps=lambda: None)
    sched.set_post_dispatcher(dispatcher)

    await sched._start_run(auto)
    await asyncio.wait_for(started.wait(), timeout=1)
    task = next(iter(sched._running))
    release.set()
    with pytest.raises(RuntimeError, match="Failed to clear running flag"):
        await task

    reloaded = await store.get(auto.task_id)
    assert reloaded is not None
    assert reloaded.running_since is not None

    await store.conn.execute("DROP TRIGGER fail_clear_running")
    await store.conn.commit()
    await sched._tick()

    recovered = await store.get(auto.task_id)
    assert recovered is not None
    assert recovered.running_since is None


@pytest.mark.asyncio
async def test_pending_running_claim_is_not_cleared_before_task_is_tracked(store: AutomationStore, monkeypatch):
    auto = _loop()
    await store.save(auto)
    sched, dispatched = _make_scheduler(store)
    entered = asyncio.Event()
    release = asyncio.Event()
    original_set_next_run = store.set_next_run

    async def blocked_set_next_run(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original_set_next_run(*args, **kwargs)

    monkeypatch.setattr(store, "set_next_run", blocked_set_next_run)

    start_task = asyncio.create_task(sched._start_run(auto))
    await asyncio.wait_for(entered.wait(), timeout=1)
    await sched._release_untracked_running()

    claimed = await store.get(auto.task_id)
    assert claimed is not None
    assert claimed.running_since is not None

    release.set()
    await start_task
    for task in list(sched._running):
        await task

    assert len(dispatched) == 1


@pytest.mark.asyncio
async def test_successful_queued_event_settlement_failure_does_not_release_for_retry(store: AutomationStore):
    auto = _loop()
    await store.save(auto)
    await store.enqueue_event(auto.task_id, "event-1", "{}", datetime.now(UTC))
    await store.conn.execute(
        """
        CREATE TRIGGER fail_event_delete
        BEFORE DELETE ON automation_event_queue
        BEGIN
            SELECT RAISE(ABORT, 'event delete failed');
        END
        """
    )
    await store.conn.commit()
    sched, dispatched = _make_scheduler(store)

    await sched._start_next_queued_event_if_idle(auto.task_id)
    task = next(iter(sched._running))
    with pytest.raises(RuntimeError, match="Failed to settle queued automation event"):
        await task

    assert len(dispatched) == 1
    rows = await store.conn.execute_fetchall("SELECT claimed_at FROM automation_event_queue")
    assert len(rows) == 1
    assert rows[0]["claimed_at"] is not None


@pytest.mark.asyncio
async def test_reconcile_recovers_stale_running_rescheduled_fire(store: AutomationStore):
    now = datetime.now(UTC)
    auto = _loop(
        running_since=now - timedelta(minutes=1),
        next_run_at=now + timedelta(minutes=5),
    )
    await store.save(auto)
    sched, dispatched = _make_scheduler(store)

    await sched._reconcile()

    recovered = await store.get(auto.task_id)
    assert recovered is not None
    assert recovered.running_since is None
    assert recovered.next_run_at is not None
    assert recovered.next_run_at <= datetime.now(UTC)

    await sched._tick()
    for task in list(sched._running):
        await task

    assert len(dispatched) == 1


@pytest.mark.asyncio
async def test_scheduler_wakes_at_rescheduled_loop_time(store: AutomationStore):
    await store.save(_loop(every="1m"))
    sched, _ = _make_scheduler(store)

    await sched._tick()

    reloaded = await store.get("loop-1")
    assert reloaded.next_run_at is not None
    assert sched._wake_deadline == reloaded.next_run_at
    assert sched._wake_task is not None

    sched._wake_task.cancel()
    try:
        await sched._wake_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_max_iterations_disables_loop(store: AutomationStore):
    await store.save(_loop(max_iterations=2, iteration_count=1))
    sched, dispatched = _make_scheduler(store)

    await sched._tick()
    for t in list(sched._running):
        await t

    assert len(dispatched) == 1
    reloaded = await store.get("loop-1")
    assert reloaded.iteration_count == 2
    assert reloaded.enabled is False


@pytest.mark.asyncio
async def test_loop_without_dispatcher_raises(store: AutomationStore):
    await store.save(_loop())
    sched = Scheduler(store=store, build_deps=lambda: None)  # no dispatcher

    await sched._tick()
    for t in list(sched._running):
        await t

    # Loop run failed (no dispatcher); iteration_count untouched, next_run advanced.
    reloaded = await store.get("loop-1")
    assert reloaded.iteration_count == 0


@pytest.mark.asyncio
async def test_create_loop_via_service_stores_correctly(store: AutomationStore, session_service):
    sched, _ = _make_scheduler(store)
    svc = AutomationService(store=store, scheduler=sched, session_service=session_service)

    loop = await svc.create_loop(
        session_id="sess-1",
        prompt="watch CI",
        every="5m",
        max_iterations=10,
        stop_when="when green",
    )

    assert loop.kind == "loop"
    assert loop.thread_id == "sess-1"
    assert loop.prompt == "watch CI"
    assert loop.max_iterations == 10
    assert loop.stop_when == "when green"
    assert loop.triggers[0].params()["every"] == "5m"

    loops = await svc.list_loops_by_session("sess-1")
    assert [a.task_id for a in loops] == [loop.task_id]


@pytest.mark.asyncio
async def test_create_loop_rejects_empty_prompt(store: AutomationStore, session_service):
    sched, _ = _make_scheduler(store)
    svc = AutomationService(store=store, scheduler=sched, session_service=session_service)

    with pytest.raises(ValueError, match="prompt"):
        await svc.create_loop(session_id="s", prompt="   ", every="5m")


@pytest.mark.asyncio
async def test_create_loop_sets_next_run_at_to_now(store: AutomationStore, session_service):
    # First fire happens "as soon as session is idle" — next_run_at = now
    # so the scheduler picks it up immediately (or the run-completed
    # fast-path fires it the moment the /loop turn ends).
    sched, _ = _make_scheduler(store)
    svc = AutomationService(store=store, scheduler=sched, session_service=session_service)
    before = datetime.now(UTC)

    loop = await svc.create_loop(session_id="sess-1", prompt="watch CI", every="5m")

    assert loop.next_run_at is not None
    # within a second of creation
    assert abs((loop.next_run_at - before).total_seconds()) < 1


@pytest.mark.asyncio
async def test_loop_fire_gate_defers_while_session_busy(store: AutomationStore):
    # Loop is due; the fire gate says "no, session has an active run" →
    # scheduler skips it. iteration_count stays at 0; next_run_at stays
    # in the past so the next tick re-evaluates.
    await store.save(_loop())
    sched, dispatched = _make_scheduler(store)
    sched.set_loop_fire_gate(lambda _auto: False)

    await sched._tick()
    for t in list(sched._running):
        await t

    assert dispatched == []
    reloaded = await store.get("loop-1")
    assert reloaded.iteration_count == 0
    assert reloaded.next_run_at is not None
    assert reloaded.next_run_at <= datetime.now(UTC)


@pytest.mark.asyncio
async def test_loop_fire_gate_allows_when_session_idle(store: AutomationStore):
    await store.save(_loop())
    sched, dispatched = _make_scheduler(store)
    sched.set_loop_fire_gate(lambda _auto: True)

    await sched._tick()
    for t in list(sched._running):
        await t

    assert len(dispatched) == 1
    reloaded = await store.get("loop-1")
    assert reloaded.iteration_count == 1


@pytest.mark.asyncio
async def test_handle_run_completed_fires_due_loops_for_session(store: AutomationStore):
    from arden.events.internal import RunCompleted

    await store.save(_loop(session_id="sess-1"))
    sched, dispatched = _make_scheduler(store)

    await sched.handle_run_completed(
        RunCompleted(run_id="run-x", session_id="sess-1", messages=(), usage=Usage(), result=None)
    )
    for t in list(sched._running):
        await t

    assert len(dispatched) == 1
    assert dispatched[0].thread_id == "sess-1"


@pytest.mark.asyncio
async def test_handle_run_completed_respects_fire_gate(store: AutomationStore):
    from arden.events.internal import RunCompleted

    await store.save(_loop(session_id="sess-1"))
    sched, dispatched = _make_scheduler(store)
    sched.set_loop_fire_gate(lambda _auto: False)

    await sched.handle_run_completed(
        RunCompleted(run_id="run-x", session_id="sess-1", messages=(), usage=Usage(), result=None)
    )
    for t in list(sched._running):
        await t

    assert dispatched == []
    reloaded = await store.get("loop-1")
    assert reloaded.iteration_count == 0
    assert reloaded.next_run_at is not None
    assert reloaded.next_run_at <= datetime.now(UTC)


@pytest.mark.asyncio
async def test_handle_run_completed_ignores_other_sessions(store: AutomationStore):
    from arden.events.internal import RunCompleted

    await store.save(_loop(session_id="sess-1"))
    sched, dispatched = _make_scheduler(store)

    await sched.handle_run_completed(
        RunCompleted(run_id="run-x", session_id="sess-other", messages=(), usage=Usage(), result=None)
    )
    for t in list(sched._running):
        await t

    assert dispatched == []


@pytest.mark.asyncio
async def test_handle_run_completed_skips_loop_not_yet_due(store: AutomationStore):
    from arden.events.internal import RunCompleted

    future_loop = _loop(next_run_at=datetime.now(UTC) + timedelta(minutes=5))
    await store.save(future_loop)
    sched, dispatched = _make_scheduler(store)

    await sched.handle_run_completed(
        RunCompleted(run_id="run-x", session_id="sess-1", messages=(), usage=Usage(), result=None)
    )
    for t in list(sched._running):
        await t

    assert dispatched == []


@pytest.mark.asyncio
async def test_max_iterations_loop_clears_next_run_at(store: AutomationStore):
    # Fire the final iteration of a 2-iter loop. After it, the loop should
    # be disabled AND next_run_at should be None (no future countdown for
    # a loop that will never fire again).
    await store.save(_loop(max_iterations=2, iteration_count=1))
    sched, dispatched = _make_scheduler(store)

    await sched._tick()
    for t in list(sched._running):
        await t

    assert len(dispatched) == 1
    reloaded = await store.get("loop-1")
    assert reloaded.enabled is False
    assert reloaded.next_run_at is None


@pytest.mark.asyncio
async def test_aged_out_loop_clears_next_run_at(store: AutomationStore):
    old = datetime.now(UTC) - timedelta(days=8)
    loop = Automation(
        task_id="loop-old",
        name="x",
        description=None,
        description_source=None,
        prompt="Check the current status.",
        model=None,
        triggers=[TimeTrigger(every="5m")],
        enabled=True,
        created_at=old,
        next_run_at=datetime.now(UTC) - timedelta(seconds=1),
        last_run_at=None,
        last_result=None,
        running_since=None,
        auto_approve=True,
        kind="loop",
        thread_id="sess",
        max_age_days=7,
    )
    await store.save(loop)
    sched, _ = _make_scheduler(store)

    await sched._tick()
    for t in list(sched._running):
        await t

    reloaded = await store.get("loop-old")
    assert reloaded.enabled is False
    assert reloaded.next_run_at is None


@pytest.mark.asyncio
async def test_aged_out_loop_disables_without_firing(store: AutomationStore):
    # Loop created 8 days ago, max_age_days=7 → aged out.
    old = datetime.now(UTC) - timedelta(days=8)
    loop = Automation(
        task_id="loop-old",
        name="x",
        description=None,
        description_source=None,
        prompt="Check the current status.",
        model=None,
        triggers=[TimeTrigger(every="5m")],
        enabled=True,
        created_at=old,
        next_run_at=datetime.now(UTC) - timedelta(seconds=1),
        last_run_at=None,
        last_result=None,
        running_since=None,
        auto_approve=True,
        kind="loop",
        thread_id="sess",
        max_age_days=7,
    )
    await store.save(loop)
    sched, dispatched = _make_scheduler(store)

    await sched._tick()
    for t in list(sched._running):
        await t

    assert dispatched == []  # never dispatched
    reloaded = await store.get("loop-old")
    assert reloaded.enabled is False
    assert reloaded.iteration_count == 0


# ---------------------------------------------------------------------------
# Post mode (channel monitor): read_history=False, agent runs fresh, result
# is appended into the target session as an assistant message.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_mode_dispatches_when_due(store: AutomationStore):
    await store.save(_loop(session_id="sess-post", read_history=False, thread_id="sess-post"))
    sched, dispatched, _results = _make_post_scheduler(store)

    await sched._tick()
    for t in list(sched._running):
        await t

    assert len(dispatched) == 1
    assert dispatched[0].task_id == "loop-1"
    assert dispatched[0].read_history is False

    reloaded = await store.get("loop-1")
    assert reloaded.iteration_count == 1
    assert reloaded.enabled is True


@pytest.mark.asyncio
async def test_post_mode_routes_to_post_not_iteration_dispatcher(store: AutomationStore):
    """A post-mode loop must NOT call the iteration dispatcher."""
    await store.save(_loop(read_history=False))
    iteration_calls: list[Automation] = []
    post_calls: list[Automation] = []

    async def iteration(auto: Automation, context: str | dict | None = None) -> str | None:
        iteration_calls.append(auto)
        return "iter-id"

    async def post(auto: Automation, context: str | dict | None = None) -> str | None:
        post_calls.append(auto)
        return "posted text"

    sched = Scheduler(store=store, build_deps=lambda: None)
    sched.set_iteration_dispatcher(iteration)
    sched.set_post_dispatcher(post)

    await sched._tick()
    for t in list(sched._running):
        await t

    assert iteration_calls == []
    assert len(post_calls) == 1


@pytest.mark.asyncio
async def test_iteration_mode_routes_to_iteration_not_post_dispatcher(store: AutomationStore):
    """An iteration-mode loop (read_history=True) must NOT call the post dispatcher."""
    await store.save(_loop(read_history=True))
    iteration_calls: list[Automation] = []
    post_calls: list[Automation] = []

    async def iteration(auto: Automation, context: str | dict | None = None) -> str | None:
        iteration_calls.append(auto)
        return "iter-id"

    async def post(auto: Automation, context: str | dict | None = None) -> str | None:
        post_calls.append(auto)
        return "posted text"

    sched = Scheduler(store=store, build_deps=lambda: None)
    sched.set_iteration_dispatcher(iteration)
    sched.set_post_dispatcher(post)

    await sched._tick()
    for t in list(sched._running):
        await t

    assert post_calls == []
    assert len(iteration_calls) == 1


@pytest.mark.asyncio
async def test_session_bound_threads_event_context_to_iteration_dispatcher(store: AutomationStore):
    """Regression guard for the Part E event-context drop: a session-bound
    event automation must hand its triggering context to the dispatcher (covers
    Slack message + calendar event_approaching runs alike)."""
    await store.save(_loop(read_history=True))
    seen: list[str | dict | None] = []

    async def iteration(auto: Automation, context: str | dict | None = None) -> str | None:
        seen.append(context)
        return "iter-id"

    sched = Scheduler(store=store, build_deps=lambda: None)
    sched.set_iteration_dispatcher(iteration)

    automation = await store.get("loop-1")
    await sched._run_session_bound(automation, "event context block")

    assert seen == ["event context block"]


@pytest.mark.asyncio
async def test_session_bound_passes_none_context_for_non_event_runs(store: AutomationStore):
    """Non-event runs default context to None, so the dispatcher submits the
    bare description exactly as before Part E."""
    await store.save(_loop(read_history=True))
    seen: list[str | dict | None] = []

    async def iteration(auto: Automation, context: str | dict | None = None) -> str | None:
        seen.append(context)
        return "iter-id"

    sched = Scheduler(store=store, build_deps=lambda: None)
    sched.set_iteration_dispatcher(iteration)

    automation = await store.get("loop-1")
    await sched._run_session_bound(automation)

    assert seen == [None]


def _message_event(
    *,
    source: str = "slack",
    channel_id: str = "C1",
    user_id: str = "U1",
    text: str = "the deploy is broken",
) -> MessageReceived:
    return MessageReceived(
        source=source,
        channel_id=channel_id,
        channel_name="bugs",
        user_id=user_id,
        user_name="alice",
        text=text,
        ts="1700000000.000100",
        thread_ts=None,
        permalink=None,
    )


def _make_trigger(
    *,
    channel_id: str = "C1",
    from_user_id: str | None = None,
    contains: list[str] | None = None,
) -> MessageTrigger:
    return MessageTrigger(
        source="slack",
        channels=[{"id": channel_id, "name": "bugs"}],
        from_user_id=from_user_id,
        contains=contains or [],
    )


def test_message_gate_passes_when_no_user_or_text_constraint():
    assert Scheduler._message_trigger_passes(_make_trigger(), _message_event()) is True


def test_message_gate_filters_on_from_user():
    trigger = _make_trigger(from_user_id="U1")
    assert Scheduler._message_trigger_passes(trigger, _message_event(user_id="U1")) is True
    assert Scheduler._message_trigger_passes(trigger, _message_event(user_id="U2")) is False


def test_message_gate_contains_is_any_of_and_case_insensitive():
    trigger = _make_trigger(contains=["Broken", "down"])
    assert Scheduler._message_trigger_passes(trigger, _message_event(text="the deploy is BROKEN")) is True
    assert Scheduler._message_trigger_passes(trigger, _message_event(text="everything is fine")) is False


def test_message_gate_empty_contains_passes_all_text():
    trigger = _make_trigger(contains=[])
    assert Scheduler._message_trigger_passes(trigger, _message_event(text="anything at all")) is True


async def _save_message_automation(
    store: AutomationStore,
    task_id: str,
    trigger: MessageTrigger,
    *,
    enabled: bool = True,
) -> None:
    now = datetime.now(UTC)
    await store.save(
        Automation(
            task_id=task_id,
            name="watcher",
            description="Triages matching messages.",
            description_source="manual",
            prompt="Triage matching messages.",
            model=None,
            triggers=[trigger],
            enabled=enabled,
            created_at=now,
            next_run_at=None,
            last_run_at=None,
            last_result=None,
            running_since=None,
            auto_approve=True,
            kind="automation",
            thread_id=f"sess-{task_id}",
        )
    )


@pytest.mark.asyncio
async def test_matching_message_automations_applies_channel_and_gate(store: AutomationStore):
    await _save_message_automation(store, "match", _make_trigger(channel_id="C1", from_user_id="U1"))
    await _save_message_automation(store, "wrong-channel", _make_trigger(channel_id="C2"))
    await _save_message_automation(store, "wrong-user", _make_trigger(channel_id="C1", from_user_id="U9"))
    await _save_message_automation(store, "disabled", _make_trigger(channel_id="C1"), enabled=False)

    sched = Scheduler(store=store, build_deps=lambda: None)
    matched = await sched._matching_message_automations(_message_event(channel_id="C1", user_id="U1"))

    assert {a.task_id for a in matched} == {"match"}


@pytest.mark.asyncio
async def test_post_mode_without_dispatcher_does_not_increment(store: AutomationStore):
    await store.save(_loop(read_history=False))
    sched = Scheduler(store=store, build_deps=lambda: None)  # no dispatcher of either kind

    await sched._tick()
    for t in list(sched._running):
        await t

    reloaded = await store.get("loop-1")
    assert reloaded.iteration_count == 0


@pytest.mark.asyncio
async def test_post_mode_max_iterations_disables(store: AutomationStore):
    await store.save(
        _loop(read_history=False, max_iterations=2, iteration_count=1),
    )
    sched, dispatched, _results = _make_post_scheduler(store)

    await sched._tick()
    for t in list(sched._running):
        await t

    assert len(dispatched) == 1
    reloaded = await store.get("loop-1")
    assert reloaded.iteration_count == 2
    assert reloaded.enabled is False


@pytest.mark.asyncio
async def test_post_mode_fire_gate_defers_while_session_busy(store: AutomationStore):
    await store.save(_loop(read_history=False))
    sched, dispatched, _results = _make_post_scheduler(store)
    sched.set_loop_fire_gate(lambda _auto: False)

    await sched._tick()
    for t in list(sched._running):
        await t

    assert dispatched == []
    reloaded = await store.get("loop-1")
    assert reloaded.iteration_count == 0


@pytest.mark.asyncio
async def test_post_mode_handle_run_completed_fires_due_loop(store: AutomationStore):
    from arden.events.internal import RunCompleted

    await store.save(_loop(read_history=False, session_id="sess-post"))
    sched, dispatched, _results = _make_post_scheduler(store)

    await sched.handle_run_completed(
        RunCompleted(run_id="run-x", session_id="sess-post", messages=(), usage=Usage(), result=None),
    )
    for t in list(sched._running):
        await t

    assert len(dispatched) == 1


@pytest.mark.asyncio
async def test_post_mode_aged_out_disables_without_firing(store: AutomationStore):
    old = datetime.now(UTC) - timedelta(days=8)
    loop = Automation(
        task_id="loop-old-post",
        name="x",
        description=None,
        description_source=None,
        prompt="Check the current status.",
        model=None,
        triggers=[TimeTrigger(every="5m")],
        enabled=True,
        created_at=old,
        next_run_at=datetime.now(UTC) - timedelta(seconds=1),
        last_run_at=None,
        last_result=None,
        running_since=None,
        auto_approve=True,
        kind="loop",
        max_age_days=7,
        read_history=False,
        thread_id="sess",
    )
    await store.save(loop)
    sched, dispatched, _results = _make_post_scheduler(store)

    await sched._tick()
    for t in list(sched._running):
        await t

    assert dispatched == []
    reloaded = await store.get("loop-old-post")
    assert reloaded.enabled is False


# ---------------------------------------------------------------------------
# Integration: full post mode flow with a real SessionService — the agent's
# result must land in the target session as an assistant message.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_mode_persists_assistant_message_to_target_session(store: AutomationStore, tmp_path: Path):
    """End-to-end: the post dispatcher's return value must be saved as a
    role='assistant' message in the target session's history."""
    from arden.context.models import SessionData
    from arden.context.store import SessionStore
    from arden.services.session import SessionService

    # Tmp session DB.
    session_conn = await database.connect(tmp_path / "sessions.db")
    session_store = SessionStore(session_conn)
    await session_store.init_schema()
    session_service = SessionService(session_store)

    # Seed an empty session for the post-mode automation to write into.
    state = session_service.create()
    state.session_id = "sess-post-target"
    await session_service.save(state, [])

    await store.save(
        _loop(
            session_id="sess-post-target",
            thread_id="sess-post-target",
            read_history=False,
        ),
    )

    # Post dispatcher: emulate what app.py wires — run agent, append assistant
    # message into target session, return text.
    async def post_dispatcher(auto: Automation, context: str | dict | None = None) -> str | None:
        result_text = f"hello from {auto.task_id}"
        loaded = await session_service.load(auto.thread_id)
        assert loaded is not None
        loaded.messages.append({"role": "assistant", "content": result_text})
        await session_service.save_progress(loaded.state, loaded.messages)
        return result_text

    sched = Scheduler(store=store, build_deps=lambda: None)
    sched.set_post_dispatcher(post_dispatcher)

    await sched._tick()
    for t in list(sched._running):
        await t

    final: SessionData | None = await session_service.load("sess-post-target")
    assert final is not None
    assistant_msgs = [m for m in final.messages if m.get("role") == "assistant"]
    assert len(assistant_msgs) == 1
    assert assistant_msgs[0]["content"] == "hello from loop-1"

    await session_conn.close()


# ---------------------------------------------------------------------------
# Code-review fixes: per-session write lock + fire-gate / dispatcher target
# resolution via `thread_id`.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_post_dispatches_serialize_under_session_lock():
    """Two concurrent post-mode dispatches against the same target session
    must serialize: their critical sections (load → agent → save) must not
    overlap. Replicates the lock pattern wired in app.py's `_dispatch_post`."""
    import asyncio

    from arden.server.app import _get_or_create_session_lock

    locks: dict[str, asyncio.Lock] = {}
    timeline: list[tuple[str, str]] = []  # (event, dispatch_id)

    async def post_dispatch(dispatch_id: str, target_id: str) -> None:
        async with _get_or_create_session_lock(locks, target_id):
            timeline.append(("enter", dispatch_id))
            # Simulate agent run + load + save_progress under the lock.
            await asyncio.sleep(0.05)
            timeline.append(("exit", dispatch_id))

    # Two concurrent dispatches against the same session.
    await asyncio.gather(
        post_dispatch("A", "sess-shared"),
        post_dispatch("B", "sess-shared"),
    )

    # Must be enter/exit/enter/exit (serialized), never enter/enter/exit/exit.
    assert len(timeline) == 4
    assert timeline[0][0] == "enter"
    assert timeline[1] == ("exit", timeline[0][1])
    assert timeline[2][0] == "enter"
    assert timeline[2][1] != timeline[0][1]
    assert timeline[3] == ("exit", timeline[2][1])


@pytest.mark.asyncio
async def test_session_lock_is_per_session_not_global():
    """Two dispatches against DIFFERENT sessions must NOT serialize — the
    lock map is per-session, so they should run in parallel."""
    import asyncio

    from arden.server.app import _get_or_create_session_lock

    locks: dict[str, asyncio.Lock] = {}
    overlap = {"yes": False}
    active = {"count": 0}

    async def post_dispatch(target_id: str) -> None:
        async with _get_or_create_session_lock(locks, target_id):
            active["count"] += 1
            if active["count"] > 1:
                overlap["yes"] = True
            await asyncio.sleep(0.05)
            active["count"] -= 1

    await asyncio.gather(
        post_dispatch("sess-A"),
        post_dispatch("sess-B"),
    )

    assert overlap["yes"] is True


def test_loop_target_id_returns_none_when_unset():
    from arden.server.app import _loop_target_id

    auto = Automation(
        task_id="t",
        name="x",
        description=None,
        description_source=None,
        prompt="Check the current status.",
        model=None,
        triggers=[TimeTrigger(every="5m")],
        enabled=True,
        created_at=datetime.now(UTC),
        next_run_at=None,
        last_run_at=None,
        last_result=None,
        running_since=None,
        auto_approve=True,
        kind="loop",
        thread_id=None,
    )
    assert _loop_target_id(auto) is None


# ---------------------------------------------------------------------------
# Channel-aware automations: a row created via `svc.create(thread_id=...,
# read_history=False)` has kind="automation" (not "loop") but is still
# session-bound. The scheduler must route it through the post dispatcher,
# not the standalone agent path.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_with_thread_id_routes_through_post_dispatcher(store: AutomationStore, session_service):
    """A `kind="automation"` row with thread_id + read_history=False MUST flow
    through the post dispatcher — not _run_agent. This is the bug surfaced by
    the Task 9 e2e test: `service.create(thread_id=X, read_history=False)`
    silently routed to _run_agent because the scheduler discriminator was
    `kind == "loop"`."""
    sched, dispatched, _results = _make_post_scheduler(store)
    svc = AutomationService(store=store, scheduler=sched, session_service=session_service)

    auto = await svc.create(
        name="channel-watcher",
        description="Posts updates into the channel.",
        prompt="Post the current update into the channel.",
        trigger_type="time",
        every="5m",
        thread_id="sess-channel",
        read_history=False,
    )
    assert auto is not None
    assert auto.kind == "automation"  # NOT "loop"
    assert auto.thread_id == "sess-channel"
    assert auto.prompt == "Post the current update into the channel."

    # Force next_run_at into the past so _tick picks it up immediately.
    await store.set_next_run(auto.task_id, datetime.now(UTC) - timedelta(seconds=1))

    await sched._tick()
    for t in list(sched._running):
        await t

    assert len(dispatched) == 1, "channel automation must route to post dispatcher"
    assert dispatched[0].task_id == auto.task_id
    assert dispatched[0].thread_id == "sess-channel"


@pytest.mark.asyncio
async def test_handle_run_completed_fires_kind_automation_with_thread_id(
    store: AutomationStore,
):
    """The run-completed fast-path must catch session-bound automations
    regardless of `kind` — they're identified by thread_id."""
    from arden.events.internal import RunCompleted

    now = datetime.now(UTC)
    auto = Automation(
        task_id="channel-auto",
        name="x",
        description="Posts a status update.",
        description_source="manual",
        prompt="Post the current status update.",
        model=None,
        triggers=[TimeTrigger(every="5m")],
        enabled=True,
        created_at=now,
        next_run_at=now - timedelta(seconds=1),
        last_run_at=None,
        last_result=None,
        running_since=None,
        auto_approve=True,
        kind="automation",
        thread_id="sess-channel",
        read_history=False,
    )
    await store.save(auto)

    sched, dispatched, _results = _make_post_scheduler(store)

    await sched.handle_run_completed(
        RunCompleted(run_id="run-x", session_id="sess-channel", messages=(), usage=Usage(), result=None),
    )
    for t in list(sched._running):
        await t

    assert len(dispatched) == 1
    assert dispatched[0].task_id == "channel-auto"


# ---------------------------------------------------------------------------
# Fix coverage: iteration loops created via `svc.create(thread_id=X,
# read_history=True)` must fire through the iteration dispatcher. Pre-fix,
# the iteration validation rejected them and the dispatcher target
# resolution returned an empty string.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_iteration_loop_with_only_thread_id_fires(store: AutomationStore, session_service):
    """`svc.create(thread_id=X, read_history=True)` produces a row with
    thread_id set. The scheduler must accept it as a valid iteration-mode
    loop and route it through the iteration dispatcher."""
    sched, dispatched = _make_scheduler(store)
    svc = AutomationService(store=store, scheduler=sched, session_service=session_service)

    auto = await svc.create(
        name="iter-thread-only",
        description="Continues the session work.",
        prompt="Continue the session work.",
        trigger_type="time",
        every="5m",
        thread_id="sess-new",
        read_history=True,
    )
    assert auto is not None
    assert auto.thread_id == "sess-new"
    assert auto.read_history is True

    await store.set_next_run(auto.task_id, datetime.now(UTC) - timedelta(seconds=1))

    await sched._tick()
    for t in list(sched._running):
        await t

    assert len(dispatched) == 1
    assert dispatched[0].task_id == auto.task_id
    assert dispatched[0].thread_id == "sess-new"


@pytest.mark.asyncio
async def test_create_loop_sets_thread_id_and_read_history(store: AutomationStore, session_service):
    """`svc.create_loop` must align new rows with the canonical model:
    thread_id is the sole session binding. read_history must be True so the
    row routes through the iteration dispatcher (loops re-enter the session
    with full history)."""
    sched, _ = _make_scheduler(store)
    svc = AutomationService(store=store, scheduler=sched, session_service=session_service)

    loop = await svc.create_loop(
        session_id="sess-canon",
        prompt="watch CI",
        every="5m",
    )

    assert loop is not None
    assert loop.thread_id == "sess-canon"
    assert loop.read_history is True


@pytest.mark.asyncio
async def test_list_session_bound_excludes_disabled(store: AutomationStore):
    """`list_session_bound_by_session` is consumed by the run-completed
    fast path. Disabled rows shouldn't be hydrated — they'll never fire."""
    enabled_row = _loop(task_id="loop-enabled", session_id="sess-shared")
    disabled_row = _loop(task_id="loop-disabled", session_id="sess-shared")
    await store.save(enabled_row)
    await store.save(disabled_row)
    await store.set_enabled("loop-disabled", False)

    rows = await store.list_session_bound_by_session("sess-shared")
    task_ids = {r.task_id for r in rows}
    assert task_ids == {"loop-enabled"}


@pytest.mark.asyncio
async def test_detached_run_settles_on_run_completed(store: AutomationStore):
    """The proper lifecycle: an iteration dispatch leaves the run row (and
    the running flag) in flight; RunCompleted settles both with the agent's
    real report."""
    auto = _loop()
    await store.save(auto)
    # Mirror _start_run: the claim sets the running flag before finalize.
    assert await store.try_mark_running(auto.task_id, datetime.now(UTC))
    sched, dispatched = _make_scheduler(store)

    await sched._run_and_finalize(auto)

    assert len(dispatched) == 1
    runs = await store.list_runs(auto.task_id)
    assert runs[0]["status"] == "running"
    assert runs[0]["result"] is None
    inflight = await store.get(auto.task_id)
    assert inflight.running_since is not None
    assert inflight.last_result is None

    await sched.handle_run_completed(
        RunCompleted(
            run_id="run-1",
            session_id=auto.thread_id,
            messages=(),
            usage=Usage(),
            result="two emails need replies",
        )
    )

    runs = await store.list_runs(auto.task_id)
    assert runs[0]["status"] == "completed"
    assert runs[0]["result"] == "two emails need replies"
    assert runs[0]["ended_at"] is not None
    settled = await store.get(auto.task_id)
    assert settled.running_since is None
    assert settled.last_result == "two emails need replies"
    assert auto.task_id not in sched._detached_task_ids


@pytest.mark.asyncio
async def test_detached_run_fails_immediately_on_chat_error(store: AutomationStore):
    auto = _loop()
    await store.save(auto)
    assert await store.try_mark_running(auto.task_id, datetime.now(UTC))
    sched, _ = _make_scheduler(store)
    await sched._run_and_finalize(auto)

    await sched.handle_run_failed(
        RunFailed(run_id="run-1", session_id=auto.thread_id, error="provider rejected history")
    )

    runs = await store.list_runs(auto.task_id)
    assert runs[0]["status"] == "failed"
    assert runs[0]["error"] == "provider rejected history"
    assert (await store.get(auto.task_id)).running_since is None
    assert auto.task_id not in sched._detached_task_ids


@pytest.mark.asyncio
async def test_detached_run_reaped_when_completion_never_arrives(store: AutomationStore):
    from arden.constants import DETACHED_RUN_MAX_AGE

    auto = _loop()
    await store.save(auto)
    assert await store.try_mark_running(auto.task_id, datetime.now(UTC))
    sched, _dispatched = _make_scheduler(store)
    await sched._run_and_finalize(auto)

    # Backdate the dispatch past the max age — RunCompleted was lost.
    sched._detached_task_ids[auto.task_id] = datetime.now(UTC) - DETACHED_RUN_MAX_AGE - timedelta(minutes=1)
    await sched._release_untracked_running()

    runs = await store.list_runs(auto.task_id)
    assert runs[0]["status"] == "failed"
    assert runs[0]["error"] == "run never reported back"
    reaped = await store.get(auto.task_id)
    assert reaped.running_since is None
    assert auto.task_id not in sched._detached_task_ids


# ---------------------------------------------------------------------------
# automation_finished must mean "this run reached a terminal state". A
# fire-and-accept iteration dispatch has only *started* one.
# ---------------------------------------------------------------------------


def _capture_automation_events(sched: Scheduler) -> list[SSEEvent]:
    events: list[SSEEvent] = []

    async def emit(event: SSEEvent) -> None:
        events.append(event)

    sched.emit_automation_event = emit  # type: ignore[method-assign]
    return events


@pytest.mark.asyncio
async def test_iteration_dispatch_announces_start_not_finish(store: AutomationStore):
    await store.save(_loop())
    sched, dispatched = _make_scheduler(store)
    events = _capture_automation_events(sched)

    await sched._tick()
    for t in list(sched._running):
        await t

    assert len(dispatched) == 1
    assert [e.status for e in events if isinstance(e, AutomationProgressEvent)] == ["starting..."]
    # The agent is still working — a finish here would report a completion at
    # the moment the automation started.
    assert [e for e in events if isinstance(e, AutomationFinishedEvent)] == []

    events.clear()
    await sched.handle_run_completed(
        RunCompleted(run_id="run-x", session_id="sess-1", messages=(), usage=Usage(), result="wrote 3 notes")
    )

    finished = [e for e in events if isinstance(e, AutomationFinishedEvent)]
    assert len(finished) == 1
    assert finished[0].task_id == "loop-1"
    assert finished[0].result == "wrote 3 notes"


@pytest.mark.asyncio
async def test_iteration_dispatch_failure_announces_a_finish(store: AutomationStore):
    """A dispatch that never detached IS terminal — it must still announce."""
    await store.save(_loop())

    async def dispatcher(auto: Automation, context: str | dict | None = None) -> str | None:
        raise RuntimeError("session gone")

    sched = Scheduler(store=store, build_deps=lambda: None)
    sched.set_iteration_dispatcher(dispatcher)
    events = _capture_automation_events(sched)

    await sched._tick()
    for t in list(sched._running):
        await t

    assert [e for e in events if isinstance(e, AutomationFinishedEvent)] != []


@pytest.mark.asyncio
async def test_post_mode_announces_one_finish_carrying_its_result(store: AutomationStore):
    await store.save(_loop(session_id="sess-post", read_history=False, thread_id="sess-post"))
    sched, _dispatched, _results = _make_post_scheduler(store)
    events = _capture_automation_events(sched)

    await sched._tick()
    for t in list(sched._running):
        await t

    finished = [e for e in events if isinstance(e, AutomationFinishedEvent)]
    assert len(finished) == 1
    assert finished[0].result == "agent result for loop-1"
