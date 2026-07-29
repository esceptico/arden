"""Missed-run catch-up: daily maintenance builtins that miss their slot while
the machine is asleep must run on boot, not skip to tomorrow."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
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
        "description": "Runs daily memory maintenance.",
        "description_source": "manual",
        "prompt": "Run daily memory maintenance.",
        "model": None,
        "triggers": [TimeTrigger(at="03:00", days="daily")],
        "enabled": True,
        "created_at": NOW,
        "next_run_at": NOW - timedelta(hours=6),
        "last_run_at": None,
        "last_result": None,
        "running_since": None,
        "auto_approve": True,
        "handler": "memory_maintenance",
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
    assert Scheduler._should_catch_up_missed(_auto(handler="other"), NOW) is False


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
async def test_overdue_memory_maintenance_catches_up(store: AutomationStore):
    started: list[str] = []
    release_maintenance = asyncio.Event()

    async def memory_maintenance(_ctx):
        started.append("memory_maintenance")
        await release_maintenance.wait()
        return "memory_maintenance"

    await store.save(
        _auto(
            task_id="maintenance",
            handler="memory_maintenance",
            last_run_at=NOW - timedelta(hours=30),
            next_run_at=NOW - timedelta(hours=6),
        )
    )

    sched = Scheduler(store=store, build_deps=lambda: None)
    sched.register_handler("memory_maintenance", memory_maintenance)

    loop_task = asyncio.create_task(sched._loop())
    try:
        await _wait_until(lambda: started == ["memory_maintenance"])

        release_maintenance.set()

        for task in list(sched._running):
            await task
    finally:
        loop_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await loop_task


@pytest.mark.asyncio
async def test_fact_synthesis_stays_due_through_startup_reconciliation(store: AutomationStore):
    """The six-hour trigger is a reliability backstop, not permission to skip."""
    from arden.automation.builtins import seed_builtins
    from arden.constants import BUILTIN_MEMORY_SYNTHESIZE_ID

    await seed_builtins(store, memory_model="memory-model")
    synthesis = await store.get(BUILTIN_MEMORY_SYNTHESIZE_ID)
    assert synthesis is not None
    last_run = datetime.now(UTC) - timedelta(hours=12)
    await store.save(
        replace(
            synthesis,
            last_run_at=last_run,
            next_run_at=last_run + timedelta(days=1),
        )
    )
    await seed_builtins(store, memory_model="memory-model")

    migrated = await store.get(BUILTIN_MEMORY_SYNTHESIZE_ID)
    assert migrated is not None
    assert migrated.next_run_at == last_run + timedelta(hours=6)

    started: list[str] = []

    async def memory_synthesize(_ctx):
        started.append("memory_synthesize")
        return "published"

    sched = Scheduler(store=store, build_deps=lambda: None)
    sched.register_handler("memory_synthesize", memory_synthesize)
    await sched._reconcile()

    reconciled = await store.get(BUILTIN_MEMORY_SYNTHESIZE_ID)
    assert reconciled is not None
    assert reconciled.next_run_at == last_run + timedelta(hours=6)

    await sched._tick()
    for task in list(sched._running):
        await task
    assert started == ["memory_synthesize"]


@pytest.mark.asyncio
async def test_overdue_memory_retention_stays_due_through_startup_reconciliation(store: AutomationStore):
    from arden.automation.builtins import seed_builtins
    from arden.constants import BUILTIN_MEMORY_RETENTION_ID

    await seed_builtins(store, memory_model="memory-model")
    retention = await store.get(BUILTIN_MEMORY_RETENTION_ID)
    assert retention is not None
    last_run = datetime.now(UTC) - timedelta(days=2)
    await store.save(
        replace(
            retention,
            last_run_at=last_run,
            next_run_at=last_run + timedelta(days=1),
        )
    )

    sched = Scheduler(store=store, build_deps=lambda: None)
    await sched._reconcile()

    reconciled = await store.get(BUILTIN_MEMORY_RETENTION_ID)
    assert reconciled is not None
    assert reconciled.next_run_at == last_run + timedelta(days=1)


@pytest.mark.asyncio
async def test_overdue_wiki_maintenance_catches_up_once(store: AutomationStore):
    from arden.automation.builtins import seed_builtins
    from arden.constants import BUILTIN_WIKI_MAINTENANCE_ID

    await seed_builtins(store, memory_model="memory-model")
    maintenance = await store.get(BUILTIN_WIKI_MAINTENANCE_ID)
    assert maintenance is not None
    last_run = datetime.now(UTC) - timedelta(hours=12)
    await store.save(
        replace(
            maintenance,
            last_run_at=last_run,
            next_run_at=last_run + timedelta(hours=6),
        )
    )

    started: list[str] = []

    async def wiki_maintenance(_ctx):
        started.append("wiki_maintenance")
        return "reconciled"

    sched = Scheduler(store=store, build_deps=lambda: None)
    sched.register_handler("wiki_maintenance", wiki_maintenance)
    await sched._reconcile()

    reconciled = await store.get(BUILTIN_WIKI_MAINTENANCE_ID)
    assert reconciled is not None
    assert reconciled.next_run_at == last_run + timedelta(hours=6)

    await sched._tick()
    for task in list(sched._running):
        await task
    assert started == ["wiki_maintenance"]


@pytest.mark.asyncio
async def test_overdue_managed_history_collection_catches_up_once(store: AutomationStore):
    from arden.automation.builtins import seed_builtins
    from arden.constants import BUILTIN_MEMORY_STORAGE_MAINTENANCE_ID

    await seed_builtins(store, memory_model="memory-model", include_managed_history_collection=True)
    collection = await store.get(BUILTIN_MEMORY_STORAGE_MAINTENANCE_ID)
    assert collection is not None
    last_run = datetime.now(UTC) - timedelta(days=14)
    await store.save(
        replace(
            collection,
            last_run_at=last_run,
            next_run_at=last_run + timedelta(days=7),
        )
    )
    await seed_builtins(store, memory_model="memory-model", include_managed_history_collection=True)

    started: list[str] = []

    async def managed_history_collection(_ctx):
        started.append("managed_history_collection")
        return "collected"

    sched = Scheduler(store=store, build_deps=lambda: None)
    sched.register_handler("managed_history_collection", managed_history_collection)
    await sched._reconcile()

    reconciled = await store.get(BUILTIN_MEMORY_STORAGE_MAINTENANCE_ID)
    assert reconciled is not None
    assert reconciled.next_run_at == last_run + timedelta(days=7)

    await sched._tick()
    for task in list(sched._running):
        await task
    assert started == ["managed_history_collection"]

    completed = await store.get(BUILTIN_MEMORY_STORAGE_MAINTENANCE_ID)
    assert completed is not None
    assert completed.last_run_at is not None
    assert completed.last_result == "collected"
    assert completed.next_run_at is not None
    assert completed.next_run_at > datetime.now(UTC)
    runs = await store.list_runs(BUILTIN_MEMORY_STORAGE_MAINTENANCE_ID)
    assert [run["status"] for run in runs] == ["completed"]

    await sched._tick()
    assert started == ["managed_history_collection"]


@pytest.mark.asyncio
async def test_ordinary_overdue_interval_advances_without_catch_up(store: AutomationStore):
    due = datetime.now(UTC) - timedelta(days=8)
    await store.save(
        _auto(
            task_id="ordinary",
            builtin=False,
            handler=None,
            triggers=[TimeTrigger(every="7d")],
            created_at=due - timedelta(days=7),
            next_run_at=due,
        )
    )

    sched = Scheduler(store=store, build_deps=lambda: None)
    await sched._reconcile()

    ordinary = await store.get("ordinary")
    assert ordinary is not None
    assert ordinary.next_run_at is not None
    assert ordinary.next_run_at > datetime.now(UTC)
    assert await store.list_runs("ordinary") == []


@pytest.mark.asyncio
async def test_failed_managed_history_collection_records_retry_without_advancing_success(
    store: AutomationStore,
):
    from arden.automation.builtins import seed_builtins
    from arden.constants import BUILTIN_MEMORY_STORAGE_MAINTENANCE_ID

    await seed_builtins(store, memory_model=None, include_managed_history_collection=True)
    collection = await store.get(BUILTIN_MEMORY_STORAGE_MAINTENANCE_ID)
    assert collection is not None
    last_success = datetime.now(UTC) - timedelta(days=14)
    await store.save(
        replace(
            collection,
            last_run_at=last_success,
            next_run_at=last_success + timedelta(days=7),
        )
    )

    async def managed_history_collection(_ctx):
        raise ExceptionGroup("managed-history collection failed: facts", [OSError("unavailable")])

    sched = Scheduler(store=store, build_deps=lambda: None)
    sched.register_handler("managed_history_collection", managed_history_collection)
    await sched._tick()
    for task in list(sched._running):
        await task

    failed = await store.get(BUILTIN_MEMORY_STORAGE_MAINTENANCE_ID)
    assert failed is not None
    assert failed.last_run_at == last_success
    assert failed.next_run_at is not None
    retry_delay = failed.next_run_at - datetime.now(UTC)
    assert timedelta(seconds=20) < retry_delay <= timedelta(seconds=30)
    runs = await store.list_runs(BUILTIN_MEMORY_STORAGE_MAINTENANCE_ID)
    assert [run["status"] for run in runs] == ["failed"]
    assert "ExceptionGroup: managed-history collection failed: facts" in runs[0]["error"]
