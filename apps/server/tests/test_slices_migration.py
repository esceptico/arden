"""One-shot boot migration: slices.json folds into the projects table;
asks, automations, and sessions re-key from slug to project_id."""

import json
from pathlib import Path

import pytest
import pytest_asyncio

import ntrp.database as database
from ntrp.context.store import SessionStore
from ntrp.services.session import SessionService
from ntrp.slices.asks import AskStore
from ntrp.slices.migrate import migrate_slices_to_projects
from ntrp.slices.models import Ask


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


def _write_slices(tmp_path: Path) -> Path:
    f = tmp_path / "slices.json"
    f.write_text(json.dumps({"slices": [
        {"key": "health", "title": "Health", "page_path": "topics/health.md", "autonomy": "observe", "related": []},
        {"key": "dex", "title": "Dex", "page_path": "topics/dex.md", "autonomy": "act", "related": []},
    ]}))
    return f


@pytest.mark.asyncio
async def test_migration_folds_rekeys_and_renames(env):
    tmp_path, store, svc = env
    slices_file = _write_slices(tmp_path)
    # Pre-existing project whose name slugs to "dex" — must be reused, not duplicated.
    dex = await svc.create_project(name="Dex")
    # A stranded slice-tagged session (the venlafaxine case) — written via raw
    # SQL because the ORM surface no longer exposes slice_key.
    state = await svc.provision(name="Venlafaxine")
    await store.conn.execute(
        "UPDATE sessions SET slice_key = 'health' WHERE session_id = ?", (state.session_id,)
    )
    await store.conn.commit()
    # An ask keyed by slug.
    asks = AskStore(tmp_path / "slices_state.json")
    asks.upsert(Ask(id="a1", slice_key="health", text="t", kind="decide", source="agent",
                    actions=[], state="active", created_at="2026-07-09T00:00:00+00:00"))
    autos = _FakeAutomationStore()

    summary = await migrate_slices_to_projects(
        slices_file=slices_file, session_service=svc, ask_store=asks,
        automation_store=autos, session_store=store,
    )

    projects = {p["name"]: p for p in await svc.list_projects()}
    assert projects["Health"]["page_path"] == "topics/health.md"
    assert projects["Health"]["autonomy"] == "observe"
    assert projects["Dex"]["project_id"] == dex["project_id"]  # reused by slug, not duplicated
    assert projects["Dex"]["autonomy"] == "act"

    health_id = projects["Health"]["project_id"]
    moved = await svc.load(state.session_id)
    assert moved.state.project_id == health_id

    assert asks.list(health_id)[0].id == "a1"  # ask re-keyed to project_id
    assert ("slice:health", f"slice:{health_id}") in autos.rewrites
    assert not slices_file.exists()
    assert slices_file.with_suffix(".json.migrated").exists()
    assert summary["slices"] == 2

    # Idempotence: second call is a no-op.
    assert await migrate_slices_to_projects(
        slices_file=slices_file, session_service=svc, ask_store=asks,
        automation_store=autos, session_store=store,
    ) is None
