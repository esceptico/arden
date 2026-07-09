# Slices/Projects Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One container concept — the `projects` table absorbs slice capabilities (`page_path`, `autonomy`); every `slice_key` value becomes a `project_id`; the slug bridge, `slices.json`, and `sessions.slice_key` die.

**Architecture:** The `Slice` dataclass becomes a read-only projection over project rows that carry capabilities. Identity is `project_id` everywhere: ask records, automation task_ids (`slice:{project_id}`), room routes, and the desktop slices domain. A one-shot idempotent boot migration folds `slices.json` into the projects table and re-keys asks/automations/sessions, then renames the file to `.migrated`.

**Tech Stack:** Python 3.13 / FastAPI / aiosqlite (server, `apps/server`, run via `uv run`), React/TypeScript/zustand/Vite (desktop, `apps/desktop`, run via `bun`).

## Global Constraints

- Field **names** `key` (Slice, API payloads) and `slice_key` (Ask records) are KEPT; their **values** become the container's `project_id`. Only `sessions.slice_key` (column) is deleted outright — sessions already have `project_id`.
- Automation task_id convention stays `slice:{identifier}`; identifier becomes the project_id. `slice_automation_match(task_id, key)` in `ntrp/slices/projection.py` is unchanged code (callers pass project_ids).
- The boot migration must be idempotent: second boot is a no-op (guarded by `slices.json` existence; it is renamed to `slices.json.migrated` on success).
- Slice capabilities on a project row: `page_path TEXT` (nullable), `autonomy TEXT` (nullable; `'observe'` or `'act'`; non-null iff the container has the standing-agent capability; `page_path` must be non-null whenever `autonomy` is).
- Overview (`GET /slices`) lists only capability-bearing containers (page or agent or open asks) — plain containers (Design, mats, Life) never appear on Home.
- The room's sessions list shows primary chats only: `session_type == "chat"` and no `parent_session_id`.
- No `slice_key` reads or writes may survive on the sessions table (grep gate in Task 7).
- Server commands run from `apps/server`: `uv run pytest tests/ -q`, `uv run ruff check ntrp/`. Desktop commands run from `apps/desktop`: `bun run typecheck`, `bun run lint`, `bun test tests/`.
- Commit after every task (local main, never push).

---

### Task 1: Projects table grows capability columns

**Files:**
- Modify: `apps/server/ntrp/context/store.py` (SCHEMA ~line 40, `_column_migrations` list ~line 641, `create_project` ~line 688, `update_project` ~line 776 region)
- Modify: `apps/server/ntrp/services/session.py` (`create_project`/`update_project` passthroughs ~line 432)
- Test: `apps/server/tests/test_project_capabilities.py` (create)

**Interfaces:**
- Consumes: existing `SessionStore.create_project(name=..., default_cwd=..., instructions=..., knowledge_scope=...)` and `update_project(project_id, **patch)`.
- Produces: project dict rows now include `page_path: str | None` and `autonomy: str | None`; `create_project`/`update_project` (store AND service) accept both as optional kwargs. Later tasks rely on exactly these names.

- [ ] **Step 1: Write the failing test**

```python
# apps/server/tests/test_project_capabilities.py
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_project_capabilities.py -v` (from `apps/server`)
Expected: FAIL — `create_project() got an unexpected keyword argument 'page_path'`.

- [ ] **Step 3: Implement**

In `apps/server/ntrp/context/store.py`:

1. SCHEMA `projects` table — add two columns after `knowledge_scope TEXT`:

```sql
    page_path TEXT,
    autonomy TEXT,
```

2. Find the additive column-migration list around line 641 (the one containing `"project_id TEXT REFERENCES projects(project_id) ON DELETE SET NULL"` and `"slice_key TEXT"` entries — read the surrounding function to see whether it targets `sessions` or is keyed per-table) and register `page_path TEXT` and `autonomy TEXT` for the **projects** table following the file's existing migration mechanism. If the mechanism is sessions-only, extend it the same way it is written for sessions (same helper, `projects` table name) — do not invent a new migration framework.

3. `create_project(...)`: add keyword params `page_path: str | None = None, autonomy: str | None = None`, include both columns in the INSERT column list and values tuple, and in the returned dict.

4. `update_project(...)`: it builds `assignments` from a whitelist of updatable fields — add `page_path` and `autonomy` to that whitelist.

In `apps/server/ntrp/services/session.py`, extend the passthroughs:

```python
    async def create_project(
        self,
        *,
        name: str,
        default_cwd: str | None = None,
        instructions: str | None = None,
        knowledge_scope: str | None = None,
        page_path: str | None = None,
        autonomy: str | None = None,
    ) -> dict:
        return await self.store.create_project(
            name=name,
            default_cwd=default_cwd,
            instructions=instructions,
            knowledge_scope=knowledge_scope,
            page_path=page_path,
            autonomy=autonomy,
        )
```

and pass `page_path`/`autonomy` through `update_project` the same way the other patch fields flow.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_project_capabilities.py tests/ -q -k "project or session"`
Expected: PASS (new tests + no regressions in project/session suites).

- [ ] **Step 5: Commit**

```bash
git add apps/server/ntrp/context/store.py apps/server/ntrp/services/session.py apps/server/tests/test_project_capabilities.py
git commit -m "feat(unify): projects table carries slice capabilities (page_path, autonomy)"
```

---

### Task 2: Slice projection over projects; SliceService loses the registry

**Files:**
- Modify: `apps/server/ntrp/slices/models.py`
- Modify: `apps/server/ntrp/slices/service.py`
- Delete: `apps/server/ntrp/slices/registry.py`, `apps/server/ntrp/slices/seed.py`, `apps/server/tests/test_slices_registry.py`, `apps/server/tests/test_slices_seed.py`
- Test: `apps/server/tests/test_slices_service.py` (update fixtures), `apps/server/tests/test_slices_projection.py` (add loader test)

**Interfaces:**
- Consumes: project dict rows with `project_id`, `name`, `page_path`, `autonomy` (Task 1).
- Produces:
  - `Slice` dataclass: `key: str` (the project_id), `title: str`, `page_path: str | None`, `autonomy: Autonomy | None`. The `related: list[str]` field is DELETED (related now comes from the page's `## Related` section only).
  - `slices_from_projects(projects: list[dict]) -> list[Slice]` in `ntrp/slices/models.py`: rows where `page_path` or `autonomy` is set, mapped `key=project_id, title=name`.
  - `SliceService.__init__(self, *, slices: Callable[[], list[Slice]], asks, get_page, pending_approvals, session_slice, slice_automations, slice_sessions)` — `registry` param deleted; `update_autonomy` and `create_slice` methods deleted (they become project updates in the router, Task 5).

- [ ] **Step 1: Write the failing loader test** (append to `tests/test_slices_projection.py`)

```python
def test_slices_from_projects_projection():
    from ntrp.slices.models import slices_from_projects

    rows = [
        {"project_id": "p1", "name": "Health", "page_path": "topics/health.md", "autonomy": "observe"},
        {"project_id": "p2", "name": "Design", "page_path": None, "autonomy": None},
        {"project_id": "p3", "name": "Reading", "page_path": "topics/reading.md", "autonomy": None},
    ]
    slices = slices_from_projects(rows)
    assert [(s.key, s.title, s.autonomy) for s in slices] == [
        ("p1", "Health", "observe"),
        ("p3", "Reading", None),  # page-only container IS a slice; plain Design is not
    ]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_slices_projection.py -v`
Expected: FAIL — `ImportError: cannot import name 'slices_from_projects'`.

- [ ] **Step 3: Implement models + service**

`ntrp/slices/models.py` — replace the `Slice` dataclass and add the loader:

```python
@dataclass
class Slice:
    """Projection of a capability-bearing project row: key IS the project_id."""

    key: str
    title: str
    page_path: str | None
    autonomy: Autonomy | None


def slices_from_projects(projects: list[dict]) -> list[Slice]:
    """The containers that are slices: any project carrying a capability
    (page or standing agent). Plain containers never surface as slices."""
    return [
        Slice(
            key=p["project_id"],
            title=p["name"],
            page_path=p.get("page_path"),
            autonomy=p.get("autonomy"),
        )
        for p in projects
        if p.get("page_path") or p.get("autonomy")
    ]
```

`ntrp/slices/service.py`:
- Delete `from ntrp.slices.registry import SliceRegistry`.
- Constructor: replace `registry: SliceRegistry` with `slices: Callable[[], list[Slice]]`, stored as `self._slices`; import `Slice` from models.
- Every `self._registry.load()` → `self._slices()`.
- `detail(key)`: replace `self._registry.get(key)` with:

```python
        by_key = {s.key: s for s in self._slices()}
        if key not in by_key:
            raise KeyError(f"unknown slice '{key}'; valid: {list(by_key)}")
        s = by_key[key]
```

- `detail` related merge: `s.related` is gone — `related = [k for k in dict.fromkeys(summary["related"]) if k in known and k != key]`. NOTE: page `## Related` wikilinks are page SLUGS while `known` now holds project_ids — related resolution must map slug → slice whose `page_path` stem equals the slug:

```python
        slug_to_key = {Path(sl.page_path).stem: sl.key for sl in self._slices() if sl.page_path}
        related = [
            slug_to_key[slug]
            for slug in dict.fromkeys(summary["related"])
            if slug in slug_to_key and slug_to_key[slug] != key
        ]
```

(add `from pathlib import Path` at top).
- `detail`/`overview`: guard `page_summary` for pageless slices — `summary = page_summary(self._get_page(s.page_path)) if s.page_path else {"title": s.title, "updated": "", "open_loops": [], "related": []}`.
- Delete `update_autonomy` and `create_slice` methods.

Delete `ntrp/slices/registry.py`, `ntrp/slices/seed.py`, `tests/test_slices_registry.py`, `tests/test_slices_seed.py` (the seed CLI is a bootstrap-era utility superseded by the suggester; check `pyproject.toml` for a console-script entry pointing at `ntrp.slices.seed` and remove it if present).

Update `tests/test_slices_service.py` fixtures: wherever a `SliceRegistry` was constructed and passed, pass `slices=lambda: [Slice(key=..., title=..., page_path=..., autonomy=...)]` with project_id-shaped keys (e.g. `"proj_health"`). Update `tests/test_slices_suggester.py` (registry import) minimally — the suggester still compares against existing slices; it now takes the keys set directly (see Task 5; if Task 5 hasn't run yet, adjust the import to models and construct `Slice` directly).

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/ -q -k "slice"`
Expected: PASS (registry/seed tests deleted; service/projection tests green). `uv run ruff check ntrp/` clean.

- [ ] **Step 5: Commit**

```bash
git add -A apps/server/ntrp/slices apps/server/tests
git commit -m "feat(unify): Slice becomes a projection over capability-bearing projects; registry and seed CLI deleted"
```

---

### Task 3: Boot migration — fold slices.json, re-key asks/automations/sessions

**Files:**
- Create: `apps/server/ntrp/slices/migrate.py`
- Modify: `apps/server/ntrp/server/app.py` (replace the "Slices↔projects unification backfill" block in `lifespan`, ~line 110)
- Modify: `apps/server/ntrp/automation/store.py` (add `rewrite_task_id`)
- Modify: `apps/server/ntrp/context/store.py` (add `rewrite_origin_automation_ids` + `clear_session_slice_keys`... see Step 3 for exact names)
- Test: `apps/server/tests/test_slices_migration.py` (create)

**Interfaces:**
- Consumes: `slices_from_projects`, `SessionService.create_project/list_projects/move_session_to_project/list_sessions`, `AskStore`, automation store.
- Produces: `async def migrate_slices_to_projects(*, slices_file: Path, session_service, ask_store, automation_store, session_store) -> dict | None` — returns a summary dict (or None when `slices_file` doesn't exist), renames the file to `<name>.migrated` on success. Produces `AutomationStore.rewrite_task_id(old: str, new: str) -> None` and `SessionStore.rewrite_origin_automation_id(old: str, new: str) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# apps/server/tests/test_slices_migration.py
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
    # A stranded slice-tagged session (the venlafaxine case).
    state = await svc.provision(name="Venlafaxine", slice_key="health")
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
    assert (f"slice:health", f"slice:{health_id}") in autos.rewrites
    assert not slices_file.exists()
    assert slices_file.with_suffix(".json.migrated").exists()
    assert summary["slices"] == 2

    # Idempotence: second call is a no-op.
    assert await migrate_slices_to_projects(
        slices_file=slices_file, session_service=svc, ask_store=asks,
        automation_store=autos, session_store=store,
    ) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_slices_migration.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ntrp.slices.migrate'`.

NOTE: `svc.provision(..., slice_key=...)` still exists at this point in the sequence — Task 4 deletes the sessions-table slice_key surface. For THIS test, after Task 4 lands, provision loses the kwarg; the migration reads raw rows instead. Write the migration to read `slice_key` via direct SQL (`SELECT session_id, slice_key FROM sessions WHERE slice_key IS NOT NULL`) on `session_store` so it keeps working when the ORM surface drops the field, and update this test's stranded-session setup to raw SQL then too (Task 4 Step 4 covers it).

- [ ] **Step 3: Implement**

`ntrp/slices/migrate.py`:

```python
"""One-shot fold of the slices.json era into the projects table.

Runs once at boot: creates/updates projects with slice capabilities, re-keys
ask records and slice automations from page slugs to project_ids, links
stranded slice-tagged sessions, then renames slices.json → .migrated so the
scan never runs again. Idempotent by construction — no file, no work."""

import json
from pathlib import Path

from ntrp.logging import get_logger

_logger = get_logger(__name__)


def _slug(name: str) -> str:
    return name.strip().lower().replace(" ", "-")


async def migrate_slices_to_projects(
    *,
    slices_file: Path,
    session_service,
    ask_store,
    automation_store,
    session_store,
) -> dict | None:
    if not slices_file.exists():
        return None
    entries = json.loads(slices_file.read_text()).get("slices", [])

    # 1. Fold registry entries into projects (reuse slug-matched rows — the
    #    slug rule's last stand — else create).
    projects = await session_service.list_projects()
    by_slug = {_slug(p["name"]): p for p in projects}
    key_to_project: dict[str, str] = {}
    for entry in entries:
        existing = by_slug.get(entry["key"])
        if existing:
            await session_service.update_project(
                existing["project_id"], page_path=entry["page_path"], autonomy=entry["autonomy"]
            )
            key_to_project[entry["key"]] = existing["project_id"]
        else:
            created = await session_service.create_project(
                name=entry["title"], page_path=entry["page_path"], autonomy=entry["autonomy"]
            )
            key_to_project[entry["key"]] = created["project_id"]

    # 2. Sessions: link slice-tagged rows to their project; slice_key column
    #    is dead after this (read via raw SQL — the ORM surface no longer
    #    exposes it).
    rows = await session_store.list_slice_tagged_sessions()
    for row in rows:
        project_id = key_to_project.get(row["slice_key"])
        if project_id and not row["project_id"]:
            await session_service.move_session_to_project(row["session_id"], project_id)

    # 3. Asks: slug → project_id in place.
    for ask in ask_store.list(include_resolved=True):
        if ask.slice_key in key_to_project:
            ask.slice_key = key_to_project[ask.slice_key]
    ask_store._flush()

    # 4. Automations: slice:{slug} → slice:{project_id} across task tables.
    for key, project_id in key_to_project.items():
        await automation_store.rewrite_task_id(f"slice:{key}", f"slice:{project_id}")
        await session_store.rewrite_origin_automation_id(f"slice:{key}", f"slice:{project_id}")

    slices_file.rename(slices_file.with_suffix(".json.migrated"))
    summary = {"slices": len(entries), "sessions": len(rows)}
    _logger.info("Slices→projects migration complete: %s", summary)
    return summary
```

`ntrp/context/store.py` — add:

```python
    async def list_slice_tagged_sessions(self) -> list[dict]:
        rows = await self._fetch_all(
            "SELECT session_id, slice_key, project_id FROM sessions WHERE slice_key IS NOT NULL"
        )
        return [dict(r) for r in rows]

    async def rewrite_origin_automation_id(self, old: str, new: str) -> bool:
        return await self._update(
            "UPDATE sessions SET origin_automation_id = ? WHERE origin_automation_id = ?", (new, old)
        )
```

(match the file's actual fetch/update helper names — read the neighboring methods and use the same helpers.)

`ntrp/automation/store.py` — add on the automation store class:

```python
    async def rewrite_task_id(self, old: str, new: str) -> None:
        """Migration helper: rename a task across every task_id-keyed table."""
        for table in ("scheduled_tasks", "automation_runs", "automation_event_dedupe", "automation_event_queue"):
            await self._conn.execute(f"UPDATE {table} SET task_id = ? WHERE task_id = ?", (new, old))
        await self._conn.commit()
```

(match the class's actual connection attribute/commit pattern.)

`ntrp/server/app.py` — delete the entire "Slices↔projects unification backfill" block (lines ~110-122, added earlier today) and the `ensure_project_for_slice` import; in its place:

```python
        from ntrp.constants import SLICES_FILE
        from ntrp.slices.migrate import migrate_slices_to_projects

        await migrate_slices_to_projects(
            slices_file=runtime.config.ntrp_dir / SLICES_FILE,
            session_service=runtime.session_service,
            ask_store=runtime.automation.slice_asks,
            automation_store=runtime.stores.automations,
            session_store=runtime.session_service.store,
        )
```

(imports go to the top of app.py per style; shown inline here only for placement clarity.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_slices_migration.py tests/ -q -k "slice or automation"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/server/ntrp/slices/migrate.py apps/server/ntrp/server/app.py apps/server/ntrp/automation/store.py apps/server/ntrp/context/store.py apps/server/tests/test_slices_migration.py
git commit -m "feat(unify): one-shot boot migration folds slices.json into projects and re-keys asks/automations/sessions"
```

---

### Task 4: Server re-key — runtime, agent pipeline, chat context, session surface

**Files:**
- Modify: `apps/server/ntrp/server/runtime/automation.py` (registry uses → projects loader; seeder; `_on_slice_run_completed`)
- Modify: `apps/server/ntrp/slices/context.py` (project-record based)
- Modify: `apps/server/ntrp/services/chat.py` (~line 628 injection)
- Modify: `apps/server/ntrp/core/spawner.py` (~line 391 — delete the `slice_key` copy line; children inherit `project_id` already)
- Modify: `apps/server/ntrp/services/session.py` (drop `slice_key` from `session_row`, `create`, `provision`; delete `move_session_to_slice`)
- Modify: `apps/server/ntrp/context/store.py` (delete `SQL_UPDATE_SESSION_SLICE`, `update_session_slice`, `slice_key` from insert/select column lists and row dicts — keep the physical column and the migration's raw-SQL readers)
- Modify: `apps/server/ntrp/context/models.py` (drop `SessionState.slice_key`)
- Modify: `apps/server/ntrp/server/routers/session.py` (delete `/sessions/{id}/slice`, `ensure_project_for_slice`, `_project_for_slice`, `_slug`; triage candidates)
- Modify: `apps/server/ntrp/server/schemas.py` (delete `MoveSessionSliceRequest`)
- Modify: `apps/server/ntrp/slices/triage.py` (single-kind targets)
- Modify: `apps/server/ntrp/server/app.py` (snapshot closures key off project_id)
- Modify: `apps/server/ntrp/automation/service.py` (`_provision_channel`/`create` slice_key params → project_id)
- Test: update `tests/test_slices_session_tag.py` (mostly deletions), `tests/test_slices_triage.py`, `tests/test_slices_agent.py`, `tests/test_slices_router.py`

**Interfaces:**
- Consumes: `slices_from_projects` (Task 2); capability columns (Task 1).
- Produces:
  - `AutomationRuntime.load_slices() -> list[Slice]` (async; `slices_from_projects(await self.stores.sessions.list_projects())` — verify the projects accessor available on the runtime's stores and use it; if only `SessionService` has it, thread the callable in from `runtime/core.py` the way `get_records` etc. are).
  - `load_slice_context(vault_dir: Path, project: dict) -> dict | None` — title = `project["name"]`, page from `project["page_path"]`; None when no page or file missing.
  - Triage: `TriageTarget` loses `kind` (fields: `key`, `title`); candidates built from ALL projects (`{"key": p["project_id"], "title": p["name"]}`); `_triage_candidates` drops the slice registry + slug dedup entirely.
  - `session_row` no longer contains `slice_key`.

- [ ] **Step 1: Re-key the runtime + seeder**

In `runtime/automation.py`:
- Delete `self.slice_registry = SliceRegistry(...)` and the import; add a loader method:

```python
    async def load_slices(self) -> list[Slice]:
        return slices_from_projects(await self._list_projects())
```

where `_list_projects` is the projects accessor threaded in at construction (add a `list_projects: Callable[[], Awaitable[list[dict]]]` ctor param wired from `runtime/core.py` as `lambda: self.session_service.list_projects()` — mirror how `get_records` is passed).
- `_seed_slice_automations`: iterate `await self.load_slices()`; only seed slices with the agent capability (`slice_.autonomy is not None`); `task_id = f"slice:{slice_.key}"` (key is now a project_id); `_provision_channel(channel_name, task_id, project_id=slice_.key)` — the channel session lands IN the project instead of carrying a slice tag.
- `_on_slice_run_completed`: `key = auto.task_id.removeprefix("slice:")` is now a project_id; replace `self.slice_registry.get(key)` with a lookup over `await self.load_slices()` (skip when absent), keep `record_slice_run(self.slice_asks, key, slice_.page_path, ...)`.
- `slice_agent_instructions(slice_)` (agent.py): `_CONTRACT[slice.autonomy]` — autonomy is non-null for seeded agents; add `assert slice.autonomy` or index with the observed value.

In `automation/service.py`: rename the `slice_key` params on `_provision_channel`/`create` to nothing — delete them; channel provisioning uses the existing `project_id` param (already present at line 167).

- [ ] **Step 2: Chat context + spawner + session surface**

`slices/context.py` — full replacement:

```python
from pathlib import Path

from ntrp.memory.pages import parse_page


def load_slice_context(vault_dir: Path, project: dict | None) -> dict | None:
    """Prompt context for a chat filed into a slice: the container's title +
    topic page prose. None for plain containers or a missing page — the chat
    degrades to an ordinary project chat rather than failing the run."""
    if not project or not project.get("page_path"):
        return None
    page_file = vault_dir / project["page_path"]
    if not page_file.exists():
        return None
    page = parse_page(page_file.read_text())
    return {"title": project["name"], "page": page.prose.strip()}
```

`services/chat.py` ~line 628 — the project record is already loaded two lines above; replace the slice_key block:

```python
    slice_context = None
    if project_record:
        slice_context = load_slice_context(get_config().memory_artifacts_dir, project_record)
```

(drop the `SLICES_FILE` import if now unused.)

`core/spawner.py:391` — delete `child_state.slice_key = calling_ctx.session_state.slice_key`.

`services/session.py` — remove `slice_key` from `session_row`, and the `slice_key` params/fields from `create`/`provision` (and the `SessionState(...)` constructions); delete `move_session_to_slice`. `context/models.py` — delete the `slice_key` field from `SessionState`. `context/store.py` — delete `SQL_UPDATE_SESSION_SLICE`, `update_session_slice`, and every `slice_key` in INSERT/SELECT column lists and row-dict constructions (lines ~340-420, ~1507, ~2650, ~2685, ~2765, ~2806 region — grep to catch all). The physical column stays; only `list_slice_tagged_sessions` (Task 3) touches it via raw SQL.

- [ ] **Step 3: Router + triage single-kind**

`server/routers/session.py`:
- Delete `move_session_to_slice` endpoint, `ensure_project_for_slice`, `_project_for_slice`, `_slug`, the `MoveSessionSliceRequest` import; delete `MoveSessionSliceRequest` from `schemas.py`.
- `create_session`: delete the `slice_key` request field handling (`CreateSessionRequest.slice_key` field also dies in schemas.py) — the slice-room composer files by `project_id` now.
- `_triage_candidates` becomes:

```python
async def _triage_candidates(svc: SessionService) -> list[dict]:
    return [{"key": p["project_id"], "title": p.get("name", "")} for p in await svc.list_projects()]
```

(drop the `runtime` param at the call site.)

`slices/triage.py` — `TriageTarget` drops `kind`; `_validated`'s move branch re-stamps `key`/`title` only; the system prompt keeps its wording (homes are homes).

- [ ] **Step 4: Update tests**

- `tests/test_slices_session_tag.py`: delete `test_session_state_carries_slice_key`, `test_provision_persists_slice_key`, `test_project_for_slice_bridges_by_slug`, `test_ensure_project_for_slice_creates_backing_project` (all describe deleted surface). Migration keying is covered by Task 3's test — update its stranded-session setup to raw SQL: `await store._conn.execute("UPDATE sessions SET slice_key = 'health' WHERE session_id = ?", (state.session_id,)); await store._conn.commit()` after a plain `svc.provision(name="Venlafaxine")` (match the store's real connection attribute).
- `tests/test_slices_triage.py`: candidates lose `kind`; `test_move_restamps_from_catalog_not_model_echo` asserts `d.target.title == "O-1A Visa"` and no `kind` attr.
- `tests/test_slices_agent.py` / `test_slices_router.py`: keys become project_id-shaped strings; constructor changes from Task 2 apply.

Run: `uv run pytest tests/ -q` (full suite) and `uv run ruff check ntrp/`
Expected: PASS / clean. Also: `grep -rn "slice_key" apps/server/ntrp --include="*.py" | grep -v migrate.py | grep -v "slices/models.py" | grep -v "slices/asks.py" | grep -v "slices/agent.py" | grep -v "slices/service.py"` returns NOTHING (the four allowed files keep the `slice_key` FIELD name on Ask records only).

- [ ] **Step 5: Commit**

```bash
git add -A apps/server
git commit -m "feat(unify): server keyed by project_id end-to-end; sessions.slice_key surface deleted"
```

---

### Task 5: Slices router + suggester promote = project capabilities

**Files:**
- Modify: `apps/server/ntrp/server/routers/slices.py`
- Modify: `apps/server/ntrp/server/app.py` (SliceService wiring; `_slice_sessions`/`_slice_session_slice` by project_id)
- Modify: `apps/server/ntrp/slices/suggester.py` (exclusion set by page slug)
- Test: `apps/server/tests/test_slices_router.py`, `tests/test_slices_suggester.py`

**Interfaces:**
- Consumes: `slices_from_projects`, service ctor from Task 2, capability columns from Task 1.
- Produces:
  - `GET /slices` unchanged shape (`key` values are project_ids); `suggested` excluded by page slug: `exclude = {Path(s.page_path).stem for s in slices if s.page_path}` — suggestions are page-keyed and must not resurface once a page is attached anywhere.
  - `GET /slices/{project_id}`, `POST /slices/{project_id}/asks/{ask_id}/resolve`, `PUT /slices/{project_id}` (autonomy — now `svc` → `session_service.update_project(project_id, autonomy=body.autonomy)`; 404 when project unknown; response = updated project row).
  - `POST /slices` body becomes `{"project_id": str | None, "name": str | None, "page_path": str}` — attach capabilities to an existing container (`project_id` given) or create a new one (`name` given); sets `autonomy="observe"`; 422 when neither/both given; returns the project row. The desktop's promote call sends `{name, page_path}`.

- [ ] **Step 1: Rewire app.py**

```python
    async def _load_slices() -> list[Slice]:
        return slices_from_projects(await runtime.session_service.list_projects())
```

The snapshot pattern keeps SliceService sync: hydrate stores the slices list in `slice_snapshot["slices"]` during `hydrate_slice_snapshot()`, and the service's `slices=lambda: slice_snapshot["slices"]`. `_slice_session_slice(session_id)` returns the session's `project_id` when that project is in the slices list (else None). `_slice_sessions(key)` filters `row["project_id"] == key and row.get("session_type") == "chat" and not row.get("parent_session_id")`. `_slice_automations(key)` unchanged (task_id convention now carries project_ids).

- [ ] **Step 2: Router edits**

Replace `CreateBody` and the create/detail/autonomy handlers in `server/routers/slices.py`:

```python
class AttachBody(BaseModel):
    project_id: str | None = None
    name: str | None = None
    page_path: str


@router.post("")
async def attach_slice(request: Request, body: AttachBody):
    """Grow capabilities on a container: attach a page (+observe agent) to an
    existing project, or mint a new one. One of project_id | name, not both."""
    if bool(body.project_id) == bool(body.name):
        raise HTTPException(status_code=422, detail="exactly one of project_id or name")
    svc = request.app.state.runtime.session_service
    if body.project_id:
        project = await svc.update_project(body.project_id, page_path=body.page_path, autonomy="observe")
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
    else:
        project = await svc.create_project(name=body.name, page_path=body.page_path, autonomy="observe")
    await request.app.state.emit_slices_changed([project["project_id"]])
    return project


@router.put("/{project_id}")
async def update_slice_autonomy(request: Request, project_id: str, body: AutonomyBody):
    svc = request.app.state.runtime.session_service
    existing = await svc.get_project(project_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Project not found")
    if not existing.get("page_path"):
        raise HTTPException(status_code=409, detail="Attach a page before granting an agent")
    project = await svc.update_project(project_id, autonomy=body.autonomy)
    await request.app.state.emit_slices_changed([project_id])
    return project
```

**Universal rooms** (spec requirement): `GET /slices/{project_id}` must work for EVERY container, not only capability-bearing ones. In `SliceService.detail`, when `key` is not in the slices projection, fall back to a bare room from the project row — thread a `get_project: Callable[[str], dict | None]` snapshot callable into the service (same snapshot pattern) and return:

```python
        project = self._get_project(key)
        if project is None:
            raise KeyError(f"unknown container '{key}'")
        return {
            "key": key, "title": project["name"], "autonomy": None,
            "page_path": None, "related": [], "open_loops": [], "updated": "",
            "asks": [asdict(a) for a in self._asks.list(key)],
            "sessions": self._slice_sessions(key),
            "automations": self._slice_automations(key),
        }
```

(`slice_snapshot["projects"]` is hydrated alongside sessions in `hydrate_slice_snapshot`.)

- [ ] **Step 3: Suggester exclusion** — `list_slices` passes `exclude_keys={Path(s["page_path"]).stem for s in overview["slices"] if s.get("page_path")}`; the overview rows must therefore include `page_path` (add it to `SliceService.overview` output rows).

- [ ] **Step 4: Tests + gates**

Update `test_slices_router.py` for the new create/attach body and project_id keys; suggester test unchanged semantics. Run `uv run pytest tests/ -q` + ruff.
Expected: PASS/clean.

- [ ] **Step 5: Commit**

```bash
git add -A apps/server
git commit -m "feat(unify): slices router keyed by project_id; promote = attach capabilities to a container"
```

---

### Task 6: Desktop re-key + universal room affordance + copy sweep

**Files:**
- Modify: `apps/desktop/src/api/types.ts` (delete `SessionListItem.slice_key`)
- Modify: `apps/desktop/src/api/slices.ts` (comments; `createSlice` body `{name, title→drop, page_path}` → `{name: title, page_path}`; `SliceSuggestion` unchanged)
- Modify: `apps/desktop/src/api/sessions.ts` (delete `moveSessionToSliceApi`, `TriageTarget.kind`)
- Modify: `apps/desktop/src/actions/triage.ts` (move = `moveSessionToProject(sessionId, target.key)` always; delete the slice branch + `refreshProjects` staleness check is KEPT for the create branch only — the move target always exists client-side now)
- Modify: `apps/desktop/src/actions/sessions.ts` (delete `createSessionWithSlice`)
- Modify: `apps/desktop/src/features/slices/components/SliceRoom.tsx` (composer files via `createSession(detail.key)` — detail.key IS the project_id)
- Modify: `apps/desktop/src/features/chat/components/Chat.tsx` (breadcrumb: `slice_key` → `project_id` ∈ overview slices)
- Modify: `apps/desktop/src/features/sessions/components/SessionList.tsx` (slug matching → direct `project_id` membership)
- Modify: `apps/desktop/src/actions/slices.ts` (`promoteSuggestedSlice` body)
- Copy sweep: `apps/desktop/src/features/sessions/components/ProjectSettingsModal.tsx`, `SessionContextMenu.tsx`, command-palette entries — user-visible "project" → "slice"
- Test: `apps/desktop/tests/` — run full suite; update any fixture using `slice_key` on sessions

**Interfaces:**
- Consumes: server API from Tasks 4-5 (breaking change lands together — desktop and server ship in the same branch).
- Produces: compiles + full desktop suite green with zero `slice_key` references on session objects (`grep -rn "slice_key" apps/desktop/src` shows only `SliceAsk.slice_key`, which now holds project_ids).

- [ ] **Step 1: Mechanical re-key** (per file list; exact current code was verified July 9):
- `Chat.tsx` ChatHeader: rooms are universal, so the breadcrumb chip renders for ANY filed chat — replace the `slice_key` selector with

```tsx
  const sliceId = useStore(
    (s) => s.sessions.find((x) => x.session_id === s.currentSessionId)?.project_id ?? null,
  );
  const sliceTitle = useStore((s) => {
    const pid = s.sessions.find((x) => x.session_id === s.currentSessionId)?.project_id;
    if (!pid) return null;
    return (
      s.slices.overview?.slices.find((sl) => sl.key === pid)?.title ??
      s.projects.find((p) => p.project_id === pid)?.name ??
      null
    );
  });
```

  and render the chip when `sliceId && sliceTitle` (`openSlice(sliceId)`).
- `SessionList.tsx`: delete `sliceKeyForGroup`/slug logic and the `sliceKeySet` memo entirely; the ↗ action shows for EVERY `group.project` (rooms are universal per Task 5) and calls `openSlice(group.project.project_id)`, label `` `Open the ${group.label} room` ``.
- `triage.ts` acceptTriage move branch:

```typescript
    if (decision.decision === "move" && decision.target) {
      await moveSessionToProject(sessionId, decision.target.key);
    } else if (decision.decision === "create" && decision.new_title) {
```

  (delete the slice/project fork and `moveSessionToSliceApi` import).
- `SliceRoom.tsx` composer send: replace `createSessionWithSlice(sliceKey)` with `createSession(detail.key)` (import from `@/actions/sessions`; it already switches to the new session).

- [ ] **Step 2: Copy sweep** — grep user-visible strings: `rg -n '"[^"]*[Pp]roject[^"]*"' apps/desktop/src --type tsx -g '!*.test.*'` and rename visible labels ("Project settings" → "Slice settings", "New project" → "New slice", "Move to project…" → "Move to slice…"). Identifiers, types, and API fields stay.

- [ ] **Step 3: Gates**

Run from `apps/desktop`: `bun run typecheck && bun run lint && bun test tests/`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add -A apps/desktop
git commit -m "feat(unify): desktop keyed by project_id; universal room affordance; project→slice copy"
```

---

### Task 7: End-to-end verification on real data

**Files:** none (verification only) — plus any fix commits it forces.

- [ ] **Step 1: Migration dry-run against a COPY of live data**

```bash
cp ~/.ntrp/sessions.db /tmp/claude-e2e/sessions.db && cp ~/.ntrp/slices.json /tmp/claude-e2e/slices.json
```

Write a scratch script that opens the copies, runs `migrate_slices_to_projects`, and prints the summary + resulting projects. Expected: 6 slices folded; Dex/ntrp reuse existing projects; Health/O-1A/Aside/United States projects created (Health may already exist from the earlier backfill); venlafaxine session linked; `.migrated` rename. Never touch the live files; do NOT restart the user's server.

- [ ] **Step 2: Preview-harness sweep** (session's own vite server, `renderer-alt`): stage overview/detail with project_id keys; verify Home strip, room open (sidebar ↗ on slice groups only), triage chip accept → `moveSessionToProject`, breadcrumb from project_id. Light + dark screenshots.

- [ ] **Step 3: Full gates, both apps**

`uv run pytest tests/ -q` + `uv run ruff check ntrp/` (server); `bun run typecheck && bun run lint && bun test tests/` (desktop).

- [ ] **Step 4: Grep gates**

```bash
grep -rn "slice_key" apps/server/ntrp --include="*.py" | grep -vE "migrate\.py|models\.py|asks\.py|agent\.py|service\.py"   # → empty
grep -rn "_slug\|_project_for_slice\|ensure_project_for_slice" apps/server/ntrp   # → only migrate.py's _slug
grep -rn "slice_key" apps/desktop/src | grep -v "SliceAsk"   # → empty
```

- [ ] **Step 5: Report** — summary to the user: what moved, what the next server restart will do (migration), that the old slices.json is preserved as `.migrated`.
