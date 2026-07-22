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
    s = SessionStore(conn)
    await s.init_schema()
    yield SessionService(s)
    await conn.close()


@pytest_asyncio.fixture
async def service(store: AutomationStore, session_service: SessionService):
    sched = Scheduler(store=store, build_deps=lambda: None)
    return AutomationService(store=store, scheduler=sched, session_service=session_service)


def _automation(
    task_id: str,
    *,
    handler: str | None = None,
    kind: str = "automation",
    thread_id: str | None = None,
) -> Automation:
    return Automation(
        task_id=task_id,
        name=task_id,
        description=f"{task_id} description",
        model=None,
        triggers=[TimeTrigger(at="09:00")],
        enabled=True,
        created_at=datetime.now(UTC),
        next_run_at=None,
        last_run_at=None,
        last_result=None,
        running_since=None,
        auto_approve=False,
        handler=handler,
        kind=kind,
        thread_id=thread_id,
    )


@pytest.mark.asyncio
async def test_backfill_gives_agent_automations_channels(service: AutomationService):
    await service.store.save(_automation("agent1"))
    await service.store.save(_automation("hdlr1", handler="knowledge_health"))
    await service.store.save(_automation("loop1", kind="loop", thread_id="sess_x"))
    await service.store.save(_automation("bound1", thread_id="sess_y"))

    count = await service.backfill_channels()
    assert count == 1

    agent1 = await service.get("agent1")
    assert agent1.thread_id is not None
    assert agent1.read_history is True

    data = await service.session_service.load(agent1.thread_id)
    assert data is not None
    assert data.state.session_type == "channel"
    assert data.state.origin_automation_id == "agent1"

    assert await service.backfill_channels() == 0


@pytest.mark.asyncio
async def test_session_bound_classification():
    automation = Automation(
        task_id="agent2",
        name="agent2",
        description="agent2 description",
        model=None,
        triggers=[TimeTrigger(at="09:00")],
        enabled=True,
        created_at=datetime.now(UTC),
        next_run_at=None,
        last_run_at=None,
        last_result=None,
        running_since=None,
        auto_approve=False,
        thread_id="sess_bound",
        read_history=True,
    )

    assert Scheduler._is_session_bound(automation) is True
    assert automation.read_history is True
