from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio

import arden.database as database
from arden.automation.models import Automation
from arden.automation.scheduler import Scheduler
from arden.automation.service import AutomationService
from arden.automation.store import AutomationStore
from arden.automation.triggers import TimeTrigger
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
    yield SessionService(store)
    await conn.close()


@pytest_asyncio.fixture
async def service(store: AutomationStore, session_service: SessionService):
    sched = Scheduler(store=store, build_deps=lambda: None)
    return AutomationService(store=store, scheduler=sched, session_service=session_service)


def _automation(
    task_id: str,
    *,
    parent_automation_id: str | None = None,
    enabled: bool = True,
) -> Automation:
    return Automation(
        task_id=task_id,
        name=task_id,
        description=f"{task_id} description",
        model=None,
        triggers=[TimeTrigger(at="09:00")],
        enabled=enabled,
        created_at=datetime.now(UTC),
        next_run_at=None,
        last_run_at=None,
        last_result=None,
        running_since=None,
        auto_approve=False,
        parent_automation_id=parent_automation_id,
    )


# --- Store: list_by_parent ---


@pytest.mark.asyncio
async def test_list_by_parent_returns_children_in_stable_order(store: AutomationStore):
    await store.save(_automation("parent"))
    await store.save(_automation("child-a", parent_automation_id="parent"))
    await store.save(_automation("child-b", parent_automation_id="parent"))
    await store.save(_automation("child-c", parent_automation_id="parent"))
    await store.save(_automation("unrelated"))

    children = await store.list_by_parent("parent")
    assert [c.task_id for c in children] == ["child-a", "child-b", "child-c"]


@pytest.mark.asyncio
async def test_list_by_parent_returns_orphan_child(store: AutomationStore):
    # No FK enforcement: a child pointing at a missing parent is still listed.
    await store.save(_automation("orphan", parent_automation_id="ghost-parent"))
    children = await store.list_by_parent("ghost-parent")
    assert [c.task_id for c in children] == ["orphan"]


@pytest.mark.asyncio
async def test_list_by_parent_returns_empty_when_no_children(store: AutomationStore):
    await store.save(_automation("loner"))
    assert await store.list_by_parent("loner") == []


# --- Service: list_children / cancel_children ---


@pytest.mark.asyncio
async def test_list_children_returns_expected_automations(store: AutomationStore, service: AutomationService):
    await store.save(_automation("parent"))
    await store.save(_automation("child-1", parent_automation_id="parent"))
    await store.save(_automation("child-2", parent_automation_id="parent"))

    children = await service.list_children("parent")
    assert {c.task_id for c in children} == {"child-1", "child-2"}


@pytest.mark.asyncio
async def test_list_children_empty_when_no_children(store: AutomationStore, service: AutomationService):
    await store.save(_automation("parent"))
    assert await service.list_children("parent") == []


@pytest.mark.asyncio
async def test_cancel_children_disables_all_and_returns_count(store: AutomationStore, service: AutomationService):
    await store.save(_automation("parent"))
    await store.save(_automation("c1", parent_automation_id="parent"))
    await store.save(_automation("c2", parent_automation_id="parent"))
    await store.save(_automation("c3", parent_automation_id="parent"))

    count = await service.cancel_children("parent")
    assert count == 3

    for tid in ("c1", "c2", "c3"):
        child = await store.get(tid)
        assert child is not None
        assert child.enabled is False


@pytest.mark.asyncio
async def test_cancel_children_zero_when_no_children(store: AutomationStore, service: AutomationService):
    await store.save(_automation("parent"))
    assert await service.cancel_children("parent") == 0


@pytest.mark.asyncio
async def test_cancel_children_only_touches_matching_parent(store: AutomationStore, service: AutomationService):
    await store.save(_automation("parent-a"))
    await store.save(_automation("parent-b"))
    await store.save(_automation("a1", parent_automation_id="parent-a"))
    await store.save(_automation("b1", parent_automation_id="parent-b"))

    count = await service.cancel_children("parent-a")
    assert count == 1

    a1 = await store.get("a1")
    b1 = await store.get("b1")
    assert a1.enabled is False
    assert b1.enabled is True


@pytest.mark.asyncio
async def test_disable_by_parent_only_disables_currently_enabled(store: AutomationStore, service: AutomationService):
    # Pre-disable one child; cancel_children should only report the rows it
    # actually flipped (rowcount under `AND enabled = 1`), not the total
    # number of children attached to the parent.
    await store.save(_automation("parent"))
    await store.save(_automation("c1", parent_automation_id="parent", enabled=False))
    await store.save(_automation("c2", parent_automation_id="parent"))
    await store.save(_automation("c3", parent_automation_id="parent"))

    count = await service.cancel_children("parent")
    assert count == 2

    for tid in ("c1", "c2", "c3"):
        child = await store.get(tid)
        assert child is not None
        assert child.enabled is False


# --- Service: delete cascades (disables children, preserves them) ---


@pytest.mark.asyncio
async def test_delete_parent_disables_children_and_preserves_them(store: AutomationStore, service: AutomationService):
    await store.save(_automation("parent"))
    await store.save(_automation("c1", parent_automation_id="parent"))
    await store.save(_automation("c2", parent_automation_id="parent"))

    disabled = await service.delete("parent")
    assert disabled == 2

    assert await store.get("parent") is None
    # children preserved (forensic), but disabled
    c1 = await store.get("c1")
    c2 = await store.get("c2")
    assert c1 is not None and c1.enabled is False
    assert c2 is not None and c2.enabled is False


@pytest.mark.asyncio
async def test_delete_parent_with_no_children_returns_zero(store: AutomationStore, service: AutomationService):
    await store.save(_automation("parent"))
    disabled = await service.delete("parent")
    assert disabled == 0
    assert await store.get("parent") is None


@pytest.mark.asyncio
async def test_delete_missing_raises_keyerror(service: AutomationService):
    with pytest.raises(KeyError):
        await service.delete("nope")
