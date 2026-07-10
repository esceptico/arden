from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

import ntrp.database as database
from ntrp.agent_surface.schedules import compile_schedules_to_automations, discover_schedules
from ntrp.automation.store import AutomationStore
from ntrp.server.routers.dev_runtime import router as dev_runtime_router
from ntrp.skills.service import get_skills_dirs


@pytest_asyncio.fixture
async def automation_store(tmp_path: Path):
    conn = await database.connect(tmp_path / "automation.db")
    store = AutomationStore(conn)
    await store.init_schema()
    yield store
    await conn.close()


def test_area_agent_skills_dir_is_loaded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    dirs = get_skills_dirs()

    assert (tmp_path / "agent" / "skills", "agent") in dirs


def test_discovers_markdown_and_yaml_schedules(tmp_path):
    md = tmp_path / "agent" / "schedules" / "weekly_digest.md"
    md.parent.mkdir(parents=True)
    md.write_text(
        '---\ncron: "0 9 * * 1"\ntimezone: "America/Los_Angeles"\nchannel: "chat"\n---\nWeekly digest.'
    )
    yaml_path = tmp_path / "agent" / "schedules" / "memory" / "rebuild.yaml"
    yaml_path.parent.mkdir(parents=True)
    yaml_path.write_text('cron: "0 10 * * *"\nprompt: "Rebuild memory."\nchannel: "chat"\n')

    schedules = discover_schedules(tmp_path)

    assert [s.id for s in schedules] == ["memory/rebuild", "weekly_digest"]
    assert schedules[0].prompt == "Rebuild memory."
    assert schedules[1].prompt == "Weekly digest."
    assert schedules[1].timezone == "America/Los_Angeles"


@pytest.mark.asyncio
async def test_compile_schedules_to_automation_rows(tmp_path, automation_store):
    schedule = tmp_path / "agent" / "schedules" / "daily_digest.yaml"
    schedule.parent.mkdir(parents=True)
    schedule.write_text('cron: "0 9 * * *"\nprompt: "Prepare digest."\nchannel: "chat"\n')

    compiled = await compile_schedules_to_automations(tmp_path, automation_store, now=datetime(2026, 6, 17, tzinfo=UTC))

    task = await automation_store.get("fs:daily_digest")
    assert compiled == ["fs:daily_digest"]
    assert task is not None
    assert task.name == "daily_digest"
    assert task.description == "Prepare digest."
    assert task.handler is None
    assert task.triggers[0].params() == {"at": "09:00", "days": "daily"}


def test_dev_schedule_dispatch_uses_automation_service():
    calls = []

    class AutomationService:
        async def get(self, task_id):
            calls.append(("get", task_id))
            return object()

        async def run_now(self, task_id):
            calls.append(("run_now", task_id))

    class Runtime:
        automation_service = AutomationService()

    app = FastAPI()
    app.state.runtime = Runtime()
    app.include_router(dev_runtime_router)

    response = TestClient(app).post("/runtime/dev/schedules/daily_digest/dispatch")

    assert response.status_code == 200
    assert response.json() == {"schedule_id": "daily_digest", "task_id": "fs:daily_digest", "status": "queued"}
    assert calls == [("get", "fs:daily_digest"), ("run_now", "fs:daily_digest")]
