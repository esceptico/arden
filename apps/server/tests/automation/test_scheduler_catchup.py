"""Missed-run catch-up: daily maintenance builtins that miss their slot while
the machine is asleep must run on boot, not skip to tomorrow."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest
import pytest_asyncio

import arden.database as database
from arden.automation.models import Automation
from arden.automation.scheduler import Scheduler
from arden.automation.store import AutomationStore
from arden.automation.triggers import TimeTrigger

NOW = datetime(2026, 6, 18, 9, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def store(tmp_path: Path):
    conn = await database.connect(tmp_path / "automation.db")
    s = AutomationStore(conn)
    await s.init_schema()
    yield s
    await conn.close()


def _auto(**kw) -> Automation:
    base = {
        "task_id": "t",
        "name": "n",
        "description": "d",
        "model": None,
        "triggers": [TimeTrigger(at="03:00", days="daily")],
        "enabled": True,
        "created_at": NOW,
        "next_run_at": NOW - timedelta(hours=6),
        "last_run_at": None,
        "last_result": None,
        "running_since": None,
        "auto_approve": True,
        "handler": "memory_consolidate",
        "builtin": True,
        "cooldown_minutes": None,
    }
    base.update(kw)
    return Automation(**base)


def test_catch_up_when_never_run():
    assert Scheduler._should_catch_up_missed(_auto(last_run_at=None), NOW) is True


def test_catch_up_when_stale_beyond_cadence():
    assert Scheduler._should_catch_up_missed(_auto(last_run_at=NOW - timedelta(hours=30)), NOW) is True


def test_no_catch_up_when_recently_run():
    assert Scheduler._should_catch_up_missed(_auto(last_run_at=NOW - timedelta(hours=2)), NOW) is False


def test_catch_up_when_previous_day_catch_up_was_late_but_todays_slot_was_missed():
    assert (
        Scheduler._should_catch_up_missed(
            _auto(
                triggers=[TimeTrigger(at="03:30", days="daily")],
                next_run_at=datetime(2026, 6, 19, 3, 30, tzinfo=UTC),
                last_run_at=datetime(2026, 6, 18, 16, 0, tzinfo=UTC),
            ),
            datetime(2026, 6, 19, 9, 0, tzinfo=UTC),
        )
        is True
    )


def test_no_catch_up_for_user_automation():
    assert Scheduler._should_catch_up_missed(_auto(builtin=False), NOW) is False


def test_no_catch_up_for_other_builtin_handler():
    assert Scheduler._should_catch_up_missed(_auto(handler="automation_suggester_daily"), NOW) is False


def test_no_catch_up_with_extra_triggers():
    two = [TimeTrigger(at="03:00", days="daily"), TimeTrigger(at="15:00", days="daily")]
    assert Scheduler._should_catch_up_missed(_auto(triggers=two), NOW) is False


async def _wait_until(predicate, *, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for condition")


@pytest.mark.asyncio
async def test_overdue_memory_builtin_catches_up(store: AutomationStore):
    started: list[str] = []
    release_consolidate = asyncio.Event()

    async def memory_consolidate(_ctx):
        started.append("memory_consolidate")
        await release_consolidate.wait()
        return "memory_consolidate"

    await store.save(
        _auto(
            task_id="consolidate",
            handler="memory_consolidate",
            last_run_at=NOW - timedelta(hours=30),
            next_run_at=NOW - timedelta(hours=6),
        )
    )

    sched = Scheduler(store=store, build_deps=lambda: None)
    sched.register_handler("memory_consolidate", memory_consolidate)

    loop_task = asyncio.create_task(sched._loop())
    try:
        await _wait_until(lambda: started == ["memory_consolidate"])

        release_consolidate.set()

        for task in list(sched._running):
            await task
    finally:
        loop_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await loop_task
