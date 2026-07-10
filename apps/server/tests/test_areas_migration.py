"""One-shot boot migration: areas.json folds into the areas table;
asks, automations, and sessions re-key from slug to area_id."""

import json
from pathlib import Path

import pytest
import pytest_asyncio

import ntrp.database as database
from ntrp.areas.asks import AskStore
from ntrp.areas.migrate import migrate_legacy_areas
from ntrp.areas.models import Ask
from ntrp.context.store import SessionStore
from ntrp.services.session import SessionService


@pytest_asyncio.fixture
async def env(tmp_path: Path):
    conn = await database.connect(tmp_path / "sessions.db")
    store = SessionStore(conn)
    await store.init_schema()
    svc = SessionService(store)
    yield tmp_path, store, svc
    await conn.close()


class _FakeAutomationStore:
    def __init__(self):
        self.rewrites: list[tuple[str, str]] = []

    async def rewrite_task_id(self, old: str, new: str) -> None:
        self.rewrites.append((old, new))


def _write_areas(tmp_path: Path) -> Path:
    f = tmp_path / "slices.json"
    f.write_text(json.dumps({"slices": [
        {"key": "health", "title": "Health", "page_path": "topics/health.md", "autonomy": "observe", "related": []},
        {"key": "dex", "title": "Dex", "page_path": "topics/dex.md", "autonomy": "act", "related": []},
    ]}))
    return f


@pytest.mark.asyncio
async def test_migration_folds_rekeys_and_renames(env):
    tmp_path, store, svc = env
    areas_file = _write_areas(tmp_path)
    # Pre-existing area whose name slugs to "dex" — must be reused, not duplicated.
    dex = await svc.create_area(name="Dex")
    # A stranded area-tagged session (the venlafaxine case) — written via raw
    # SQL because the ORM surface no longer exposes area_key.
    state = await svc.provision(name="Venlafaxine")
    await store.conn.execute("ALTER TABLE sessions ADD COLUMN slice_key TEXT")
    await store.conn.execute(
        "UPDATE sessions SET slice_key = 'health' WHERE session_id = ?", (state.session_id,)
    )
    await store.conn.commit()
    # An ask keyed by slug.
    asks = AskStore(tmp_path / "areas_state.json")
    asks.upsert(Ask(id="a1", area_key="health", text="t", kind="decide", source="agent",
                    actions=[], state="active", created_at="2026-07-09T00:00:00+00:00"))
    autos = _FakeAutomationStore()

    summary = await migrate_legacy_areas(
        areas_file=areas_file, session_service=svc, ask_store=asks,
        automation_store=autos, session_store=store,
    )

    areas = {p["name"]: p for p in await svc.list_areas()}
    assert areas["Health"]["page_path"] == "topics/health.md"
    assert areas["Health"]["autonomy"] == "observe"
    assert areas["Dex"]["area_id"] == dex["area_id"]  # reused by slug, not duplicated
    assert areas["Dex"]["autonomy"] == "act"

    health_id = areas["Health"]["area_id"]
    moved = await svc.load(state.session_id)
    assert moved.state.area_id == health_id

    assert asks.list(health_id)[0].id == "a1"  # ask re-keyed to area_id
    assert ("slice:health", f"area:{health_id}") in autos.rewrites
    assert not areas_file.exists()
    assert areas_file.with_suffix(".json.migrated").exists()
    assert summary["areas"] == 2

    # Idempotence: second call is a no-op.
    assert await migrate_legacy_areas(
        areas_file=areas_file, session_service=svc, ask_store=asks,
        automation_store=autos, session_store=store,
    ) is None
