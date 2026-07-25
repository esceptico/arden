"""Part E regression guard: the event context must reach the iteration
dispatcher for a session-bound automation.

The previously-verified bug dropped the triggering event's `context` on the
session-bound path (`_run_and_finalize` → `_run_session_bound` → dispatcher
all ignored it), so a triggering Slack message / calendar event never reached
the agent. These tests exercise the real entry point (`_run_and_finalize`) and
assert the dispatcher is invoked positionally as `(automation, context)` with
the context preserved end-to-end.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

import arden.database as database
from arden.automation.models import Automation
from arden.automation.scheduler import Scheduler
from arden.automation.store import AutomationStore
from arden.automation.triggers import TimeTrigger


@pytest_asyncio.fixture
async def store(tmp_path: Path):
    conn = await database.connect(tmp_path / "automation.db")
    s = AutomationStore(conn)
    await s.init_schema()
    yield s
    await conn.close()


def _session_bound(
    *,
    task_id: str = "auto-1",
    thread_id: str = "sess-1",
    read_history: bool = True,
) -> Automation:
    now = datetime.now(UTC)
    return Automation(
        task_id=task_id,
        name="watcher",
        description=None,
        description_source=None,
        prompt="Triage the bug.",
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
        read_history=read_history,
        thread_id=thread_id,
    )


def _capturing_dispatcher() -> tuple[list[tuple], object]:
    """Dispatcher that records its full positional+keyword call signature."""
    calls: list[tuple] = []

    async def dispatcher(*args, **kwargs) -> str | None:
        calls.append((args, kwargs))
        return "fake-run-id"

    return calls, dispatcher


@pytest.mark.asyncio
async def test_run_and_finalize_threads_context_into_iteration_dispatcher(store: AutomationStore):
    """Full session-bound path: `_run_and_finalize(automation, context)` must
    reach the iteration dispatcher with the context preserved (the Part E drop)."""
    automation = _session_bound()
    await store.save(automation)
    calls, dispatcher = _capturing_dispatcher()

    sched = Scheduler(store=store, build_deps=lambda: None)
    sched.set_iteration_dispatcher(dispatcher)

    await sched._run_and_finalize(automation, "external message context")

    assert len(calls) == 1
    args, kwargs = calls[0]
    # context must not be dropped on the way to the dispatcher.
    assert "external message context" in (*args, *kwargs.values())


@pytest.mark.asyncio
async def test_dispatcher_called_as_automation_then_context_positionally(store: AutomationStore):
    """The dispatcher contract is `(automation, context)` positionally — the
    automation first, the triggering context second."""
    automation = _session_bound()
    await store.save(automation)
    calls, dispatcher = _capturing_dispatcher()

    sched = Scheduler(store=store, build_deps=lambda: None)
    sched.set_iteration_dispatcher(dispatcher)

    await sched._run_and_finalize(automation, "ctx block")

    args, kwargs = calls[0]
    assert kwargs == {}
    assert len(args) == 2
    assert args[0] is automation
    assert args[1] == "ctx block"


@pytest.mark.asyncio
async def test_dict_context_is_forwarded_unchanged(store: AutomationStore):
    """The dispatcher type accepts `str | dict | None`; the scheduler must not
    coerce a dict context — rendering happens downstream in the dispatcher."""
    automation = _session_bound()
    await store.save(automation)
    calls, dispatcher = _capturing_dispatcher()

    sched = Scheduler(store=store, build_deps=lambda: None)
    sched.set_iteration_dispatcher(dispatcher)

    ctx = {"trigger_type": "message", "text": "the deploy is broken"}
    await sched._run_and_finalize(automation, ctx)

    args, _kwargs = calls[0]
    assert args[1] is ctx


@pytest.mark.asyncio
async def test_only_iteration_dispatcher_receives_context_for_iteration_run(store: AutomationStore):
    """A read_history=True (iteration-mode) run must hand the context to the
    iteration dispatcher and never touch the post dispatcher."""
    automation = _session_bound(read_history=True)
    await store.save(automation)
    iteration_calls, iteration_dispatcher = _capturing_dispatcher()
    post_calls, post_dispatcher = _capturing_dispatcher()

    sched = Scheduler(store=store, build_deps=lambda: None)
    sched.set_iteration_dispatcher(iteration_dispatcher)
    sched.set_post_dispatcher(post_dispatcher)

    await sched._run_and_finalize(automation, "ctx for iteration")

    assert post_calls == []
    assert len(iteration_calls) == 1
    assert iteration_calls[0][0][1] == "ctx for iteration"


@pytest.mark.asyncio
async def test_context_threaded_through_event_queue_run(store: AutomationStore):
    """The event-driven entry point (`_start_next_queued_event_if_idle`) must
    also deliver the enqueued event context to the iteration dispatcher — this
    is the path a Slack/calendar TriggerEvent actually takes via fire_event."""
    automation = _session_bound()
    await store.save(automation)
    await store.enqueue_event(automation.task_id, "slack:C1:1700.0001", "queued event context", datetime.now(UTC))
    calls, dispatcher = _capturing_dispatcher()

    sched = Scheduler(store=store, build_deps=lambda: None)
    sched.set_iteration_dispatcher(dispatcher)

    await sched._start_next_queued_event_if_idle(automation.task_id)
    for t in list(sched._running):
        await t

    assert len(calls) == 1
    args, _kwargs = calls[0]
    assert args[0].task_id == automation.task_id
    assert args[1] == "queued event context"
