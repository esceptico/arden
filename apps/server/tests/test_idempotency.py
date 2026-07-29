import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio

import arden.database as database
from arden.automation.models import IdempotencyClaim
from arden.automation.scheduler import Scheduler
from arden.automation.service import AutomationService
from arden.automation.store import AutomationStore
from arden.context.store import SessionStore
from arden.services.session import SessionService


@pytest_asyncio.fixture
async def store(tmp_path: Path):
    conn = await database.connect(tmp_path / "automation.db")
    s = AutomationStore(conn)
    await s.init_schema()
    yield s
    await conn.close()


@pytest_asyncio.fixture
async def session_service(tmp_path: Path):
    conn = await database.connect(tmp_path / "sessions.db")
    store = SessionStore(conn)
    await store.init_schema()
    service = SessionService(store)
    await service.provision(name="active chat", session_id="sess-1")
    yield service
    await conn.close()


@pytest_asyncio.fixture
async def service(store: AutomationStore, session_service: SessionService):
    sched = Scheduler(store=store, build_deps=lambda: None)
    return AutomationService(store=store, scheduler=sched, session_service=session_service)


# ---------------- store-level: try_claim_idempotency ----------------


@pytest.mark.asyncio
async def test_global_claim_first_succeeds_second_fails(store: AutomationStore):
    assert await store.try_claim_idempotency(scope="global", key="item-42", automation_task_id="child-a")
    assert not await store.try_claim_idempotency(scope="global", key="item-42", automation_task_id="child-b")


@pytest.mark.asyncio
async def test_global_claim_ignores_parent(store: AutomationStore):
    # Global scope: same key conflicts regardless of which parent.
    assert await store.try_claim_idempotency(scope="global", key="news-7", automation_task_id="child-a")
    assert not await store.try_claim_idempotency(scope="global", key="news-7", automation_task_id="child-b")


@pytest.mark.asyncio
async def test_idempotent_area_child_keeps_area_ownership_prefix(service: AutomationService):
    area = await service.session_service.create_area(name="Health")
    child = await service.create(
        name="daily evidence",
        description="Collects evidence for the Area.",
        prompt="Collect the next evidence.",
        trigger_type="time",
        every="1d",
        area_id=area["area_id"],
        idempotency_key="daily-evidence",
        idempotency_scope="global",
    )

    assert child is not None
    assert child.task_id.startswith(f"area:{area['area_id']}:")


@pytest.mark.asyncio
async def test_run_claim_first_succeeds_second_fails(store: AutomationStore):
    fire_at = datetime(2026, 5, 13, 10, 0, tzinfo=UTC).isoformat()
    assert await store.try_claim_idempotency(
        scope="run",
        key="item-1",
        parent_automation_id="watcher",
        parent_fire_at=fire_at,
        automation_task_id="child-a",
    )
    assert not await store.try_claim_idempotency(
        scope="run",
        key="item-1",
        parent_automation_id="watcher",
        parent_fire_at=fire_at,
        automation_task_id="child-b",
    )


@pytest.mark.asyncio
async def test_run_claim_different_fire_at_succeeds(store: AutomationStore):
    # Run scope: claim namespace resets per fire.
    fire1 = datetime(2026, 5, 13, 10, 0, tzinfo=UTC).isoformat()
    fire2 = datetime(2026, 5, 13, 11, 0, tzinfo=UTC).isoformat()
    assert await store.try_claim_idempotency(
        scope="run",
        key="item-1",
        parent_automation_id="watcher",
        parent_fire_at=fire1,
        automation_task_id="child-a",
    )
    assert await store.try_claim_idempotency(
        scope="run",
        key="item-1",
        parent_automation_id="watcher",
        parent_fire_at=fire2,
        automation_task_id="child-b",
    )


@pytest.mark.asyncio
async def test_attempt_claim_first_succeeds_second_fails(store: AutomationStore):
    fire_at = datetime(2026, 5, 13, 10, 0, tzinfo=UTC).isoformat()
    assert await store.try_claim_idempotency(
        scope="attempt",
        key="item-1",
        parent_automation_id="watcher",
        parent_fire_at=fire_at,
        attempt_n=0,
        automation_task_id="child-a",
    )
    assert not await store.try_claim_idempotency(
        scope="attempt",
        key="item-1",
        parent_automation_id="watcher",
        parent_fire_at=fire_at,
        attempt_n=0,
        automation_task_id="child-b",
    )


@pytest.mark.asyncio
async def test_attempt_claim_different_attempt_succeeds(store: AutomationStore):
    fire_at = datetime(2026, 5, 13, 10, 0, tzinfo=UTC).isoformat()
    assert await store.try_claim_idempotency(
        scope="attempt",
        key="item-1",
        parent_automation_id="watcher",
        parent_fire_at=fire_at,
        attempt_n=0,
        automation_task_id="child-a",
    )
    assert await store.try_claim_idempotency(
        scope="attempt",
        key="item-1",
        parent_automation_id="watcher",
        parent_fire_at=fire_at,
        attempt_n=1,
        automation_task_id="child-b",
    )


# ---------------- validation ----------------


@pytest.mark.asyncio
async def test_global_with_parent_raises(store: AutomationStore):
    with pytest.raises(ValueError):
        await store.try_claim_idempotency(
            scope="global",
            key="k",
            parent_automation_id="watcher",
            automation_task_id="c",
        )


@pytest.mark.asyncio
async def test_global_with_fire_at_raises(store: AutomationStore):
    with pytest.raises(ValueError):
        await store.try_claim_idempotency(
            scope="global",
            key="k",
            parent_fire_at="2026-05-13T10:00:00+00:00",
            automation_task_id="c",
        )


@pytest.mark.asyncio
async def test_global_with_attempt_n_raises(store: AutomationStore):
    with pytest.raises(ValueError):
        await store.try_claim_idempotency(
            scope="global",
            key="k",
            attempt_n=0,
            automation_task_id="c",
        )


@pytest.mark.asyncio
async def test_run_without_fire_at_raises(store: AutomationStore):
    with pytest.raises(ValueError):
        await store.try_claim_idempotency(
            scope="run",
            key="k",
            parent_automation_id="watcher",
            automation_task_id="c",
        )


@pytest.mark.asyncio
async def test_run_without_parent_raises(store: AutomationStore):
    with pytest.raises(ValueError):
        await store.try_claim_idempotency(
            scope="run",
            key="k",
            parent_fire_at="2026-05-13T10:00:00+00:00",
            automation_task_id="c",
        )


@pytest.mark.asyncio
async def test_run_with_attempt_n_raises(store: AutomationStore):
    with pytest.raises(ValueError):
        await store.try_claim_idempotency(
            scope="run",
            key="k",
            parent_automation_id="watcher",
            parent_fire_at="2026-05-13T10:00:00+00:00",
            attempt_n=0,
            automation_task_id="c",
        )


@pytest.mark.asyncio
async def test_attempt_without_attempt_n_raises(store: AutomationStore):
    with pytest.raises(ValueError):
        await store.try_claim_idempotency(
            scope="attempt",
            key="k",
            parent_automation_id="watcher",
            parent_fire_at="2026-05-13T10:00:00+00:00",
            automation_task_id="c",
        )


@pytest.mark.asyncio
async def test_unknown_scope_raises(store: AutomationStore):
    with pytest.raises(ValueError):
        await store.try_claim_idempotency(
            scope="bogus",
            key="k",
            automation_task_id="c",
        )


# ---------------- list_claims_for_parent ----------------


@pytest.mark.asyncio
async def test_list_claims_for_parent(store: AutomationStore):
    fire_at = datetime(2026, 5, 13, 10, 0, tzinfo=UTC).isoformat()
    await store.try_claim_idempotency(
        scope="run",
        key="item-1",
        parent_automation_id="watcher",
        parent_fire_at=fire_at,
        automation_task_id="child-a",
    )
    await store.try_claim_idempotency(
        scope="run",
        key="item-2",
        parent_automation_id="watcher",
        parent_fire_at=fire_at,
        automation_task_id="child-b",
    )
    # Different parent — should not appear.
    await store.try_claim_idempotency(
        scope="run",
        key="item-3",
        parent_automation_id="other-watcher",
        parent_fire_at=fire_at,
        automation_task_id="child-c",
    )

    claims = await store.list_claims_for_parent("watcher")
    assert all(isinstance(c, IdempotencyClaim) for c in claims)
    keys = {c.key for c in claims}
    assert keys == {"item-1", "item-2"}
    for c in claims:
        assert c.parent_automation_id == "watcher"
        assert c.scope == "run"
        assert isinstance(c.claimed_at, datetime)


# ---------------- service integration ----------------


@pytest.mark.asyncio
async def test_create_with_idempotency_key_returns_none_on_repeat(service: AutomationService):
    first = await service.create(
        name="watch-item-1",
        description="Pokes item 1.",
        prompt="Poke item 1.",
        trigger_type="time",
        at="09:00",
        idempotency_key="item-1",
        idempotency_scope="global",
    )
    assert first is not None
    assert first.idempotency_key == "item-1"
    assert first.idempotency_scope == "global"

    second = await service.create(
        name="watch-item-1-again",
        description="Pokes item 1 again.",
        prompt="Poke item 1 again.",
        trigger_type="time",
        at="09:00",
        idempotency_key="item-1",
        idempotency_scope="global",
    )
    assert second is None


@pytest.mark.asyncio
async def test_create_run_scope_isolated_per_fire(service: AutomationService):
    fire1 = datetime(2026, 5, 13, 10, 0, tzinfo=UTC).isoformat()
    fire2 = datetime(2026, 5, 13, 11, 0, tzinfo=UTC).isoformat()

    a = await service.create(
        name="child-a",
        description="Runs the child task.",
        prompt="Run the child task.",
        trigger_type="time",
        at="09:00",
        idempotency_key="item-1",
        idempotency_scope="run",
        parent_automation_id="watcher",
        parent_fire_at=fire1,
    )
    dup = await service.create(
        name="child-a-dup",
        description="Runs the duplicate child task.",
        prompt="Run the duplicate child task.",
        trigger_type="time",
        at="09:00",
        idempotency_key="item-1",
        idempotency_scope="run",
        parent_automation_id="watcher",
        parent_fire_at=fire1,
    )
    b = await service.create(
        name="child-a-fire2",
        description="Runs the child task for the next fire.",
        prompt="Run the child task for the next fire.",
        trigger_type="time",
        at="09:00",
        idempotency_key="item-1",
        idempotency_scope="run",
        parent_automation_id="watcher",
        parent_fire_at=fire2,
    )
    assert a is not None
    assert dup is None
    assert b is not None


@pytest.mark.asyncio
async def test_concurrent_idempotent_creates_share_one_channel(service: AutomationService):
    async def create() -> object:
        return await service.create(
            name="daily brief",
            description="Posts the daily brief.",
            prompt="Post the daily brief.",
            trigger_type="time",
            at="09:00",
            idempotency_key="daily-brief",
            idempotency_scope="global",
        )

    results = await asyncio.gather(create(), create())

    created = [automation for automation in results if automation is not None]
    assert len(created) == 1
    channels = [
        session
        for session in await service.session_service.list_sessions(limit=10)
        if session["origin_automation_id"] == created[0].task_id
    ]
    assert len(channels) == 1


@pytest.mark.asyncio
async def test_create_loop_with_idempotency(service: AutomationService):
    first = await service.create_loop(
        session_id="sess-1",
        prompt="watch CI",
        every="5m",
        idempotency_key="ci-loop",
        idempotency_scope="global",
    )
    assert first is not None
    second = await service.create_loop(
        session_id="sess-1",
        prompt="watch CI again",
        every="5m",
        idempotency_key="ci-loop",
        idempotency_scope="global",
    )
    assert second is None


# ---------------- sentinel byte validation ----------------


@pytest.mark.asyncio
async def test_claim_rejects_unit_separator_in_key(store: AutomationStore):
    with pytest.raises(ValueError, match="control bytes"):
        await store.try_claim_idempotency(
            scope="global",
            key="foo\x1fbar",
            automation_task_id="c",
        )


@pytest.mark.asyncio
async def test_claim_rejects_null_in_key(store: AutomationStore):
    with pytest.raises(ValueError, match="control bytes"):
        await store.try_claim_idempotency(
            scope="global",
            key="foo\x00bar",
            automation_task_id="c",
        )


@pytest.mark.asyncio
async def test_claim_rejects_unit_separator_in_parent_id(store: AutomationStore):
    with pytest.raises(ValueError, match="control bytes"):
        await store.try_claim_idempotency(
            scope="run",
            key="k",
            parent_automation_id="watcher\x1fhack",
            parent_fire_at="2026-05-13T10:00:00+00:00",
            automation_task_id="c",
        )


@pytest.mark.asyncio
async def test_claim_rejects_unit_separator_in_parent_fire_at(store: AutomationStore):
    with pytest.raises(ValueError, match="control bytes"):
        await store.try_claim_idempotency(
            scope="run",
            key="k",
            parent_automation_id="watcher",
            parent_fire_at="2026-05-13T10:00:00+00:00\x1fhack",
            automation_task_id="c",
        )


@pytest.mark.asyncio
async def test_claim_rejects_null_in_automation_task_id(store: AutomationStore):
    with pytest.raises(ValueError, match="control bytes"):
        await store.try_claim_idempotency(
            scope="global",
            key="k",
            automation_task_id="child\x00id",
        )


# ---------------- atomic save_with_claim ----------------


@pytest.mark.asyncio
async def test_create_rolls_back_claim_on_save_failure(service: AutomationService, store: AutomationStore, monkeypatch):
    """If the automation row write fails inside save_with_claim, the claim
    must also rollback so a retry under the same key can succeed."""
    # Simulate the INSERT INTO scheduled_tasks step failing by patching the
    # connection's execute to raise on the scheduled-task insert only.
    real_execute = store.conn.execute

    async def flaky_execute(sql, *args, **kwargs):
        if "INSERT INTO scheduled_tasks" in sql:
            raise RuntimeError("simulated disk error")
        return await real_execute(sql, *args, **kwargs)

    monkeypatch.setattr(store.conn, "execute", flaky_execute)

    with pytest.raises(RuntimeError, match="simulated disk error"):
        await service.create(
            name="first-try",
            description="Tests a failing save.",
            prompt="Test a failing save.",
            trigger_type="time",
            at="09:00",
            idempotency_key="item-99",
            idempotency_scope="global",
        )
    archived_channels = [
        session
        for session in await service.session_service.list_archived(limit=10)
        if session["origin_automation_id"] is not None
    ]
    assert len(archived_channels) == 1

    # Restore and retry — claim should have rolled back so this must succeed.
    monkeypatch.setattr(store.conn, "execute", real_execute)
    retry = await service.create(
        name="second-try",
        description="Tests a retry after a failed save.",
        prompt="Retry after the failed save.",
        trigger_type="time",
        at="09:00",
        idempotency_key="item-99",
        idempotency_scope="global",
    )
    assert retry is not None
    assert retry.idempotency_key == "item-99"
    channels = [
        session
        for session in await service.session_service.list_sessions(limit=10)
        if session["origin_automation_id"] == retry.task_id
    ]
    assert len(channels) == 1
    assert channels[0]["session_id"] == retry.thread_id


@pytest.mark.asyncio
async def test_concurrent_failed_creator_cannot_archive_winning_channel(
    service: AutomationService,
    store: AutomationStore,
    monkeypatch,
):
    first_at_save = asyncio.Event()
    second_at_save = asyncio.Event()
    channel_archived = asyncio.Event()
    original_save_with_claim = store.save_with_claim
    original_archive = service.session_service.archive
    save_calls = 0

    async def racing_save_with_claim(automation, **kwargs):
        nonlocal save_calls
        save_calls += 1
        if save_calls == 1:
            first_at_save.set()
            await second_at_save.wait()
            raise RuntimeError("simulated first-writer failure")
        second_at_save.set()
        await channel_archived.wait()
        return await original_save_with_claim(automation, **kwargs)

    async def signaling_archive(session_id: str) -> bool:
        archived = await original_archive(session_id)
        channel_archived.set()
        return archived

    monkeypatch.setattr(store, "save_with_claim", racing_save_with_claim)
    monkeypatch.setattr(service.session_service, "archive", signaling_archive)

    kwargs = {
        "name": "shared work",
        "description": "Runs shared work.",
        "prompt": "Run shared work.",
        "trigger_type": "time",
        "every": "1h",
        "idempotency_key": "shared-work",
        "idempotency_scope": "global",
    }
    first = asyncio.create_task(service.create(**kwargs))
    await first_at_save.wait()
    second = asyncio.create_task(service.create(**kwargs))
    results = await asyncio.gather(first, second, return_exceptions=True)

    winner = next(result for result in results if not isinstance(result, BaseException))
    failure = next(result for result in results if isinstance(result, BaseException))
    assert isinstance(failure, RuntimeError)
    assert winner is not None
    assert winner.thread_id is not None
    assert not await service.session_service.is_archived(winner.thread_id)


@pytest.mark.asyncio
async def test_create_loop_rolls_back_claim_on_save_failure(
    service: AutomationService, store: AutomationStore, monkeypatch
):
    await service.session_service.provision(name="retry chat", session_id="sess-x")
    real_execute = store.conn.execute

    async def flaky_execute(sql, *args, **kwargs):
        if "INSERT INTO scheduled_tasks" in sql:
            raise RuntimeError("simulated disk error")
        return await real_execute(sql, *args, **kwargs)

    monkeypatch.setattr(store.conn, "execute", flaky_execute)

    with pytest.raises(RuntimeError, match="simulated disk error"):
        await service.create_loop(
            session_id="sess-x",
            prompt="watch x",
            every="5m",
            idempotency_key="loop-x",
            idempotency_scope="global",
        )

    monkeypatch.setattr(store.conn, "execute", real_execute)
    retry = await service.create_loop(
        session_id="sess-x",
        prompt="watch x",
        every="5m",
        idempotency_key="loop-x",
        idempotency_scope="global",
    )
    assert retry is not None
