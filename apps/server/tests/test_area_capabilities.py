"""Areas carry area capabilities: page_path + autonomy columns."""

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
async def test_area_capability_columns_roundtrip(svc):
    area = await svc.create_area(name="Health", page_path="topics/health.md", autonomy="observe")
    assert area["page_path"] == "topics/health.md"
    assert area["autonomy"] == "observe"

    updated = await svc.update_area(area["area_id"], autonomy="act")
    assert updated["autonomy"] == "act"
    assert updated["page_path"] == "topics/health.md"


@pytest.mark.asyncio
async def test_plain_area_has_null_capabilities(svc):
    area = await svc.create_area(name="Design")
    assert area["page_path"] is None
    assert area["autonomy"] is None
