"""Projects carry slice capabilities: page_path + autonomy columns."""

from pathlib import Path

import pytest
import pytest_asyncio

import ntrp.database as database
from ntrp.context.store import SessionStore
from ntrp.services.session import SessionService


@pytest_asyncio.fixture
async def svc(tmp_path: Path):
    conn = await database.connect(tmp_path / "sessions.db")
    store = SessionStore(conn)
    await store.init_schema()
    yield SessionService(store)
    await conn.close()


@pytest.mark.asyncio
async def test_project_capability_columns_roundtrip(svc):
    project = await svc.create_project(name="Health", page_path="topics/health.md", autonomy="observe")
    assert project["page_path"] == "topics/health.md"
    assert project["autonomy"] == "observe"

    updated = await svc.update_project(project["project_id"], autonomy="act")
    assert updated["autonomy"] == "act"
    assert updated["page_path"] == "topics/health.md"


@pytest.mark.asyncio
async def test_plain_project_has_null_capabilities(svc):
    project = await svc.create_project(name="Design")
    assert project["page_path"] is None
    assert project["autonomy"] is None
