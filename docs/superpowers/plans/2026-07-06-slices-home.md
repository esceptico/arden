# Slices Home Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Home becomes an entrypoint (hero input + focus set + slices strip) backed by Slices — life domains projected over memory topic pages, each with a standing agent that compresses its domain into at most one focused ask.

**Architecture:** Server: a `slices` package (models, service, ask state, agent handler) + `/slices` router, reusing FilePageStore, the automation Scheduler, OperatorDeps agent spawning, and the SSE bus. Desktop: a `slices` store domain + `features/home/` (entrypoint replacing HomeHero) and `features/slices/` (room), with motion pieces ported from ~/src/interaction-lab.

**Tech Stack:** Python 3.13 / FastAPI / pytest (apps/server, run via `uv run`); React 19 / TypeScript / zustand / motion/react / Tailwind v4 / bun:test (apps/desktop).

**Spec:** docs/superpowers/specs/2026-07-06-slices-home-design.md

## Global Constraints

- No new runtime dependencies on either side (no radix, no cmdk — hero input builds on the existing hand-rolled picker patterns; NumberFlow needs = existing `RollingToken`).
- Desktop: features never import each other; cross-feature via stores/actions/api only. Domain state lives in `stores/*-domain.ts` as pure reducers.
- Motion: reuse `lib/tokens/motion.ts` poses (RISE_IN/DISSOLVE_OUT/ROW_EXIT, SPRING_*); lab ports keep tuned values verbatim (values inlined in tasks below).
- Light-first monochrome per docs/design-language.md: tone over lines, one accent, severity dots only where attention is required.
- Server style: dataclasses, imports at top, no defensive fallbacks, constants in arden/constants.py.
- Server tests: `uv run pytest tests/ -k slices` from apps/server. Desktop gate: `bun test tests/` + `bun run typecheck` + `bun run lint` from apps/desktop.
- Commits per task; do NOT push (user reviews on main).

---

### Task 1: Slice + Ask models and slice registry

**Files:**
- Create: `apps/server/arden/slices/__init__.py`
- Create: `apps/server/arden/slices/models.py`
- Create: `apps/server/arden/slices/registry.py`
- Test: `apps/server/tests/test_slices_registry.py`

**Interfaces:**
- Produces: `Slice(key, title, page_path, autonomy, related)`, `Ask(id, slice_key, text, kind, source, actions, state, created_at)`, `AskKind = Literal["review","decide","act","drift"]`, `Autonomy = Literal["observe","act"]`; `SliceRegistry(path).load() -> list[Slice]`, `.save(slices)`, `.get(key)`.

- [ ] **Step 1: Write the failing test**

```python
# apps/server/tests/test_slices_registry.py
import json
from pathlib import Path
from arden.slices.models import Slice
from arden.slices.registry import SliceRegistry


def test_registry_roundtrip(tmp_path: Path):
    path = tmp_path / "slices.json"
    reg = SliceRegistry(path)
    assert reg.load() == []
    reg.save([Slice(key="o-1a", title="O-1A", page_path="topics/o-1a.md", autonomy="observe")])
    loaded = SliceRegistry(path).load()
    assert loaded[0].key == "o-1a"
    assert loaded[0].autonomy == "observe"
    assert json.loads(path.read_text())["slices"][0]["page_path"] == "topics/o-1a.md"


def test_registry_get_unknown_lists_valid_keys(tmp_path: Path):
    reg = SliceRegistry(tmp_path / "slices.json")
    reg.save([Slice(key="o-1a", title="O-1A", page_path="topics/o-1a.md", autonomy="observe")])
    try:
        reg.get("visa")
        raise AssertionError("expected KeyError")
    except KeyError as e:
        assert "o-1a" in str(e)  # self-correcting interface: list valid keys on miss
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/server && uv run pytest tests/test_slices_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'arden.slices'`

- [ ] **Step 3: Write the implementation**

```python
# apps/server/arden/slices/models.py
from dataclasses import dataclass, field
from typing import Literal

AskKind = Literal["review", "decide", "act", "drift"]
AskState = Literal["active", "done", "dismissed", "snoozed"]
Autonomy = Literal["observe", "act"]


@dataclass
class Slice:
    key: str
    title: str
    page_path: str  # vault-relative, e.g. "topics/o-1a.md"
    autonomy: Autonomy
    related: list[str] = field(default_factory=list)


@dataclass
class Ask:
    id: str
    slice_key: str
    text: str
    kind: AskKind
    source: str  # "approval" | "run_failed" | "agent_output" | "open_loop" | "agent"
    actions: list[dict]  # [{"verb": "open_session", "ref": "<id>"}, ...]
    state: AskState
    created_at: str  # ISO
    snoozed_until: str | None = None
    provenance: str | None = None  # run/source that produced it
```

```python
# apps/server/arden/slices/registry.py
import json
from dataclasses import asdict
from pathlib import Path

from arden.slices.models import Slice


class SliceRegistry:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> list[Slice]:
        if not self._path.exists():
            return []
        data = json.loads(self._path.read_text())
        return [Slice(**s) for s in data["slices"]]

    def save(self, slices: list[Slice]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"slices": [asdict(s) for s in slices]}, indent=2))

    def get(self, key: str) -> Slice:
        slices = self.load()
        for s in slices:
            if s.key == key:
                return s
        raise KeyError(f"unknown slice '{key}'; valid: {[s.key for s in slices]}")
```

```python
# apps/server/arden/slices/__init__.py
```

Add to `apps/server/arden/constants.py` (follow existing ARDEN_DIR-style constants there):

```python
SLICES_FILE = "slices.json"  # under the ~/.arden dir
SLICES_STATE_FILE = "slices-state.json"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/server && uv run pytest tests/test_slices_registry.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add apps/server/arden/slices apps/server/arden/constants.py apps/server/tests/test_slices_registry.py
git commit -m "feat(slices): Slice/Ask models + file-backed registry"
```

---

### Task 2: Page projection — open loops from prose

**Files:**
- Create: `apps/server/arden/slices/projection.py`
- Test: `apps/server/tests/test_slices_projection.py`

**Interfaces:**
- Consumes: `arden.memory.pages.parse_page(text) -> Page` (Page.prose, Page.frontmatter).
- Produces: `parse_open_loops(prose: str) -> list[str]` (bullet texts under a `## Open loops` heading), `page_summary(page: Page) -> dict` with `{"title", "updated", "open_loops": [...]}`.

- [ ] **Step 1: Write the failing test**

```python
# apps/server/tests/test_slices_projection.py
from arden.slices.projection import parse_open_loops

PROSE = """# O-1A

## What we know
Stuff.

## Open loops
- **Assess case strength** — determine whether evidence is enough (from chat).
- **Find the right counsel** — identify an attorney (from chat).

## Related
- [[United States]]
"""


def test_parse_open_loops_extracts_bullets_until_next_heading():
    loops = parse_open_loops(PROSE)
    assert len(loops) == 2
    assert loops[0].startswith("Assess case strength")
    assert "(from chat)" not in loops[0]  # provenance suffix stripped


def test_parse_open_loops_missing_section_is_empty():
    assert parse_open_loops("# T\n\n## What we know\nx\n") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/server && uv run pytest tests/test_slices_projection.py -v`
Expected: FAIL with `ModuleNotFoundError` / `ImportError: parse_open_loops`

- [ ] **Step 3: Write the implementation**

```python
# apps/server/arden/slices/projection.py
import re

_LOOP_HEADING = re.compile(r"^##\s+open loops\s*$", re.IGNORECASE)
_HEADING = re.compile(r"^#{1,6}\s")
_BULLET = re.compile(r"^[-*]\s+(.*)$")
_PROVENANCE = re.compile(r"\s*\((?:from chat|record:[^)]*)\)\.?\s*$")
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")


def parse_open_loops(prose: str) -> list[str]:
    loops: list[str] = []
    in_section = False
    for line in prose.splitlines():
        if _LOOP_HEADING.match(line.strip()):
            in_section = True
            continue
        if in_section and _HEADING.match(line):
            break
        if in_section:
            m = _BULLET.match(line.strip())
            if m:
                text = _MD_BOLD.sub(r"\1", m.group(1))
                loops.append(_PROVENANCE.sub("", text).strip())
    return loops
```

Then add `page_summary` in the same file:

```python
from arden.memory.pages import Page


def page_summary(page: Page) -> dict:
    return {
        "title": page.frontmatter.get("title", ""),
        "updated": str(page.frontmatter.get("updated", "")),
        "open_loops": parse_open_loops(page.prose),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/server && uv run pytest tests/test_slices_projection.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add apps/server/arden/slices/projection.py apps/server/tests/test_slices_projection.py
git commit -m "feat(slices): open-loop projection from topic-page prose"
```

---

### Task 3: Ask state store + focus-set nomination

**Files:**
- Create: `apps/server/arden/slices/asks.py`
- Test: `apps/server/tests/test_slices_asks.py`

**Interfaces:**
- Consumes: `Ask`, `AskKind` from Task 1.
- Produces: `AskStore(path)` with `upsert(ask: Ask) -> None`, `list(slice_key: str | None = None, include_resolved: bool = False) -> list[Ask]`, `resolve(ask_id: str, state: AskState, snoozed_until: str | None = None) -> Ask`; `nominate_focus(asks: list[Ask], cap: int = 4) -> list[Ask]` (one per slice, kind-priority `decide > drift > review > act`, then created_at desc, capped).

- [ ] **Step 1: Write the failing test**

```python
# apps/server/tests/test_slices_asks.py
from pathlib import Path
from arden.slices.asks import AskStore, nominate_focus
from arden.slices.models import Ask


def ask(id: str, slice_key: str, kind: str, created: str = "2026-07-06T10:00:00") -> Ask:
    return Ask(id=id, slice_key=slice_key, text=id, kind=kind, source="open_loop",
               actions=[], state="active", created_at=created)


def test_store_upsert_resolve_roundtrip(tmp_path: Path):
    store = AskStore(tmp_path / "state.json")
    store.upsert(ask("a1", "o-1a", "review"))
    store.resolve("a1", "dismissed")
    assert store.list("o-1a") == []
    assert store.list("o-1a", include_resolved=True)[0].state == "dismissed"


def test_snoozed_asks_hidden_until_deadline(tmp_path: Path):
    store = AskStore(tmp_path / "state.json")
    store.upsert(ask("a1", "o-1a", "review"))
    store.resolve("a1", "snoozed", snoozed_until="2099-01-01T00:00:00")
    assert store.list("o-1a") == []
    store.resolve("a1", "snoozed", snoozed_until="2000-01-01T00:00:00")
    assert [a.id for a in store.list("o-1a")] == ["a1"]  # snooze expired → active again


def test_nominate_focus_one_per_slice_kind_priority():
    asks = [
        ask("r", "dex", "review"), ask("d", "dex", "decide"),
        ask("x", "aside", "drift"), ask("y", "health", "act"),
    ]
    focus = nominate_focus(asks, cap=2)
    assert [a.id for a in focus] == ["d", "x"]  # decide beats review; drift beats act; cap 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/server && uv run pytest tests/test_slices_asks.py -v`
Expected: FAIL with import error

- [ ] **Step 3: Write the implementation**

```python
# apps/server/arden/slices/asks.py
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from arden.slices.models import Ask, AskState

_KIND_PRIORITY = {"decide": 0, "drift": 1, "review": 2, "act": 3}


class AskStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._asks: dict[str, Ask] = {}
        if path.exists():
            data = json.loads(path.read_text())
            self._asks = {a["id"]: Ask(**a) for a in data["asks"]}

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"asks": [asdict(a) for a in self._asks.values()]}, indent=2))

    def upsert(self, ask: Ask) -> None:
        self._asks[ask.id] = ask
        self._flush()

    def resolve(self, ask_id: str, state: AskState, snoozed_until: str | None = None) -> Ask:
        if ask_id not in self._asks:
            raise KeyError(f"unknown ask '{ask_id}'; valid: {list(self._asks)}")
        ask = self._asks[ask_id]
        ask.state = state
        ask.snoozed_until = snoozed_until
        self._flush()
        return ask

    def list(self, slice_key: str | None = None, include_resolved: bool = False) -> list[Ask]:
        now = datetime.now(UTC).isoformat()
        out = []
        for a in self._asks.values():
            if slice_key and a.slice_key != slice_key:
                continue
            active = a.state == "active" or (
                a.state == "snoozed" and a.snoozed_until is not None and a.snoozed_until <= now
            )
            if include_resolved or active:
                out.append(a)
        return sorted(out, key=lambda a: a.created_at, reverse=True)


def nominate_focus(asks: list[Ask], cap: int = 4) -> list[Ask]:
    best: dict[str, Ask] = {}
    for a in asks:
        cur = best.get(a.slice_key)
        if cur is None or (_KIND_PRIORITY[a.kind], a.created_at) < (_KIND_PRIORITY[cur.kind], cur.created_at):
            best[a.slice_key] = a
    ranked = sorted(best.values(), key=lambda a: (_KIND_PRIORITY[a.kind], a.created_at))
    return ranked[:cap]
```

Note: `nominate_focus` sorts snoozed-expired asks as active because `list()` already re-admits them.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/server && uv run pytest tests/test_slices_asks.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add apps/server/arden/slices/asks.py apps/server/tests/test_slices_asks.py
git commit -m "feat(slices): ask store with snooze re-admission + focus nomination"
```

---

### Task 4: Session slice tagging

**Files:**
- Modify: `apps/server/arden/context/models.py` (SessionState — add `slice_key: str | None = None` alongside `project_id`)
- Modify: `apps/server/arden/services/session.py` (`provision(..., slice_key: str | None = None)` passes through)
- Modify: `apps/server/arden/context/store.py` (persist/load `slice_key` wherever `project_id` is persisted — follow that column/field exactly; add migration if sessions table has explicit columns)
- Modify: `apps/server/arden/server/routers/session.py` (accept `slice_key` in the create-session body; include in list/detail payloads next to `project_id`)
- Test: `apps/server/tests/test_slices_session_tag.py`

**Interfaces:**
- Consumes: `SessionService.provision(...)`, existing `project_id` persistence path.
- Produces: `SessionState.slice_key`; sessions API payloads carry `slice_key`.

- [ ] **Step 1: Write the failing test**

```python
# apps/server/tests/test_slices_session_tag.py
import pytest
from datetime import UTC, datetime
from arden.context.models import SessionState


def test_session_state_carries_slice_key():
    s = SessionState(session_id="s1", started_at=datetime.now(UTC), slice_key="o-1a")
    assert s.slice_key == "o-1a"


@pytest.mark.asyncio
async def test_provision_persists_slice_key(session_service):  # reuse existing conftest fixture pattern
    state = await session_service.provision(name="counsel", slice_key="o-1a")
    loaded = await session_service.load(state.session_id)
    assert loaded.state.slice_key == "o-1a"
```

(If no `session_service` fixture exists in conftest.py, build one inline exactly like `tests/test_session_service.py` builds its service — copy its store setup.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/server && uv run pytest tests/test_slices_session_tag.py -v`
Expected: FAIL — `unexpected keyword argument 'slice_key'`

- [ ] **Step 3: Implement**

Mirror `project_id` in all four files: dataclass field, provision parameter, store column/serialization (find every occurrence of `project_id` in context/store.py and routers/session.py and add `slice_key` beside it — including the sessions list payload). If the sessions table is SQLite with explicit columns, add `ALTER TABLE sessions ADD COLUMN slice_key TEXT` in the same migration mechanism the store already uses for `project_id`/`chat_model` (grep for the most recent `ADD COLUMN` to copy the idiom).

- [ ] **Step 4: Run tests**

Run: `cd apps/server && uv run pytest tests/test_slices_session_tag.py tests/test_session_service.py -v`
Expected: PASS (new + no regressions)

- [ ] **Step 5: Commit**

```bash
git add apps/server/arden/context apps/server/arden/services/session.py apps/server/arden/server/routers/session.py apps/server/tests/test_slices_session_tag.py
git commit -m "feat(slices): slice_key tag on sessions, mirroring project_id"
```

---

### Task 5: SliceService — mechanical ask derivation + full projection

**Files:**
- Create: `apps/server/arden/slices/service.py`
- Test: `apps/server/tests/test_slices_service.py`

**Interfaces:**
- Consumes: `SliceRegistry`, `AskStore`, `nominate_focus`, `page_summary`, FilePageStore-like `get_page(path) -> Page`, plus injected callables (keep the service store-agnostic): `pending_approvals() -> list[dict]` (rows with `session_id`, `tool_name`, `preview`, `run_id`), `session_slice(session_id) -> str | None`, `slice_automations(slice_key) -> list[Automation-like dict]` (with `name`, `last_result`, `last_run_at`, `running_since`).
- Produces:
  - `SliceService.overview() -> dict` = `{"slices": [...], "focus": [ask...]}` where each slice dict has `key,title,autonomy,live,open_loops,ask_count`.
  - `SliceService.detail(key) -> dict` = slice dict + `asks`, `open_loops`, `sessions`, `activity`.
  - `SliceService.refresh_mechanical() -> None` — derives approval/run_failed asks idempotently (stable ask ids: `f"approval:{run_id}:{tool_call_id}"`, `f"runfail:{task_id}:{last_run_at}"`).

- [ ] **Step 1: Write the failing test**

```python
# apps/server/tests/test_slices_service.py
from pathlib import Path
from arden.slices.models import Slice
from arden.slices.registry import SliceRegistry
from arden.slices.asks import AskStore
from arden.slices.service import SliceService
from arden.memory.pages import parse_page

PAGE = "---\ntitle: O-1A\nupdated: 2026-07-05\n---\n# O-1A\n\n## Open loops\n- Find counsel.\n"


def make_service(tmp_path: Path) -> SliceService:
    reg = SliceRegistry(tmp_path / "slices.json")
    reg.save([Slice(key="o-1a", title="O-1A", page_path="topics/o-1a.md", autonomy="observe")])
    return SliceService(
        registry=reg,
        asks=AskStore(tmp_path / "state.json"),
        get_page=lambda path: parse_page(PAGE),
        pending_approvals=lambda: [
            {"run_id": "r1", "tool_call_id": "t1", "session_id": "s1",
             "tool_name": "bash", "preview": "gh pr create"}
        ],
        session_slice=lambda sid: "o-1a" if sid == "s1" else None,
        slice_automations=lambda key: [],
        slice_sessions=lambda key: [{"session_id": "s1", "name": "counsel"}],
    )


def test_mechanical_approval_becomes_decide_ask(tmp_path: Path):
    svc = make_service(tmp_path)
    svc.refresh_mechanical()
    svc.refresh_mechanical()  # idempotent — no duplicates
    overview = svc.overview()
    assert len(overview["focus"]) == 1
    ask = overview["focus"][0]
    assert ask["kind"] == "decide" and ask["slice_key"] == "o-1a"
    assert {"verb": "open_session", "ref": "s1"} in ask["actions"]


def test_detail_includes_open_loops_and_sessions(tmp_path: Path):
    svc = make_service(tmp_path)
    d = svc.detail("o-1a")
    assert d["open_loops"] == ["Find counsel."]
    assert d["sessions"][0]["session_id"] == "s1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/server && uv run pytest tests/test_slices_service.py -v`
Expected: FAIL with import error

- [ ] **Step 3: Write the implementation**

```python
# apps/server/arden/slices/service.py
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime

from arden.memory.pages import Page
from arden.slices.asks import AskStore, nominate_focus
from arden.slices.models import Ask
from arden.slices.projection import page_summary
from arden.slices.registry import SliceRegistry


class SliceService:
    def __init__(
        self,
        registry: SliceRegistry,
        asks: AskStore,
        get_page: Callable[[str], Page],
        pending_approvals: Callable[[], list[dict]],
        session_slice: Callable[[str], str | None],
        slice_automations: Callable[[str], list[dict]],
        slice_sessions: Callable[[str], list[dict]],
    ) -> None:
        self._registry = registry
        self._asks = asks
        self._get_page = get_page
        self._pending_approvals = pending_approvals
        self._session_slice = session_slice
        self._slice_automations = slice_automations
        self._slice_sessions = slice_sessions

    def refresh_mechanical(self) -> None:
        now = datetime.now(UTC).isoformat()
        existing = {a.id for a in self._asks.list(include_resolved=True)}
        for row in self._pending_approvals():
            key = self._session_slice(row["session_id"])
            if key is None:
                continue
            ask_id = f"approval:{row['run_id']}:{row['tool_call_id']}"
            if ask_id in existing:
                continue
            self._asks.upsert(Ask(
                id=ask_id, slice_key=key,
                text=f"{row['tool_name']} wants: {row['preview'] or row['tool_name']}",
                kind="decide", source="approval",
                actions=[{"verb": "open_session", "ref": row["session_id"]}],
                state="active", created_at=now,
                provenance=f"run:{row['run_id']}",
            ))
        for s in self._registry.load():
            for auto in self._slice_automations(s.key):
                if not auto.get("last_result") or not str(auto["last_result"]).startswith("error"):
                    continue
                ask_id = f"runfail:{auto['name']}:{auto['last_run_at']}"
                if ask_id in existing:
                    continue
                self._asks.upsert(Ask(
                    id=ask_id, slice_key=s.key,
                    text=f"{auto['name']} failed — {auto['last_result']}",
                    kind="review", source="run_failed",
                    actions=[{"verb": "retry", "ref": auto["name"]}],
                    state="active", created_at=now,
                ))

    def overview(self) -> dict:
        slices = self._registry.load()
        all_asks = self._asks.list()
        focus = nominate_focus(all_asks)
        out = []
        for s in slices:
            summary = page_summary(self._get_page(s.page_path))
            slice_asks = [a for a in all_asks if a.slice_key == s.key]
            out.append({
                "key": s.key, "title": s.title, "autonomy": s.autonomy,
                "live": bool(slice_asks) or bool(
                    any(a.get("running_since") for a in self._slice_automations(s.key))
                ),
                "updated": summary["updated"], "ask_count": len(slice_asks),
            })
        return {"slices": out, "focus": [asdict(a) for a in focus]}

    def detail(self, key: str) -> dict:
        s = self._registry.get(key)
        summary = page_summary(self._get_page(s.page_path))
        return {
            "key": s.key, "title": s.title, "autonomy": s.autonomy,
            "page_path": s.page_path, "related": s.related,
            "open_loops": summary["open_loops"], "updated": summary["updated"],
            "asks": [asdict(a) for a in self._asks.list(key)],
            "sessions": self._slice_sessions(key),
            "automations": self._slice_automations(key),
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/server && uv run pytest tests/test_slices_service.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add apps/server/arden/slices/service.py apps/server/tests/test_slices_service.py
git commit -m "feat(slices): SliceService — overview/detail projection + mechanical asks"
```

---

### Task 6: `/slices` router + app wiring + SSE

**Files:**
- Create: `apps/server/arden/server/routers/slices.py`
- Modify: `apps/server/arden/server/app.py` (construct registry/store/service in lifespan next to the other services; `app.include_router(slices_router)`)
- Modify: `apps/server/arden/events/sse.py` (add `SLICES_CHANGED = "slices_changed"` EventType + `SlicesChangedEvent(SSEEvent)` dataclass, mirroring `MemoryChangedEvent`)
- Test: `apps/server/tests/test_slices_router.py`

**Interfaces:**
- Consumes: `SliceService` (Task 5); app.py's dependency-container idiom (grep how `memory_router` gets its service — copy exactly).
- Produces HTTP:
  - `GET /slices` → `SliceService.overview()` (calls `refresh_mechanical()` first)
  - `GET /slices/{key}` → `detail(key)` (404 with valid keys listed on unknown)
  - `POST /slices/{key}/asks/{ask_id}/resolve` body `{"state": "dismissed"|"done"|"snoozed", "snoozed_until": "..."}` → resolved ask; emits `SlicesChangedEvent` on the automation bus (`AUTOMATION_BUS_KEY`) so the desktop's existing automation SSE stream carries it.
  - `PUT /slices/{key}` body `{"autonomy": "observe"|"act"}` → updated slice.
  - `POST /slices` body `{"key","title","page_path"}` → created slice (promotion of a topic page).

- [ ] **Step 1: Write the failing test** (FastAPI TestClient, following the existing router-test idiom in tests/ — grep `TestClient` for the pattern; build the app with a `SliceService` wired to tmp_path stores and stub callables as in Task 5's test)

```python
# apps/server/tests/test_slices_router.py — core assertions
def test_get_slices_returns_overview_with_focus(client):
    res = client.get("/slices")
    assert res.status_code == 200
    body = res.json()
    assert "slices" in body and "focus" in body


def test_resolve_ask_and_unknown_slice_404(client):
    res = client.post("/slices/o-1a/asks/approval:r1:t1/resolve", json={"state": "dismissed"})
    assert res.status_code == 200 and res.json()["state"] == "dismissed"
    res = client.get("/slices/nope")
    assert res.status_code == 404 and "o-1a" in res.json()["detail"]
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/test_slices_router.py -v` → import error.

- [ ] **Step 3: Implement router**

```python
# apps/server/arden/server/routers/slices.py
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/slices", tags=["slices"])


class ResolveBody(BaseModel):
    state: str
    snoozed_until: str | None = None


class AutonomyBody(BaseModel):
    autonomy: str


class CreateBody(BaseModel):
    key: str
    title: str
    page_path: str


def _svc(request: Request):
    return request.app.state.slice_service  # set in app.py lifespan


@router.get("")
async def list_slices(request: Request):
    svc = _svc(request)
    svc.refresh_mechanical()
    return svc.overview()


@router.get("/{key}")
async def slice_detail(request: Request, key: str):
    try:
        return _svc(request).detail(key)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{key}/asks/{ask_id}/resolve")
async def resolve_ask(request: Request, key: str, ask_id: str, body: ResolveBody):
    try:
        ask = _svc(request).resolve_ask(ask_id, body.state, body.snoozed_until)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    await request.app.state.emit_slices_changed([key])
    return ask
```

Add `SliceService.resolve_ask(ask_id, state, snoozed_until) -> dict` delegating to `AskStore.resolve` and returning `asdict`. In app.py lifespan, construct: `SliceRegistry(arden_dir / SLICES_FILE)`, `AskStore(arden_dir / SLICES_STATE_FILE)`, wire the real callables — `pending_approvals` from `context_store.list_pending_tool_approvals` across active sessions, `session_slice` reading `SessionState.slice_key`, `slice_automations` filtering `AutomationStore` by name prefix `slice:{key}:`, `slice_sessions` filtering the session list by `slice_key` — and `app.state.emit_slices_changed` publishing `SlicesChangedEvent` on `AUTOMATION_BUS_KEY`. Add `PUT /slices/{key}` and `POST /slices` endpoints updating the registry.

- [ ] **Step 4: Run tests** — `uv run pytest tests/test_slices_router.py -v` → PASS; then full `uv run pytest tests/ -x -q` for regressions.

- [ ] **Step 5: Commit**

```bash
git add apps/server/arden/server apps/server/arden/events/sse.py apps/server/arden/slices apps/server/tests/test_slices_router.py
git commit -m "feat(slices): /slices router, app wiring, slices_changed SSE"
```

---

### Task 7: Slice agent handler (Layer 2)

**Files:**
- Create: `apps/server/arden/slices/agent.py`
- Modify: `apps/server/arden/server/app.py` (register handler with Scheduler; ensure one automation per registered slice exists: name `slice:{key}`, handler `slice_agent`, default TimeTrigger daily + EventTrigger on `memory_changed` paths matching the slice's page — copy the trigger construction idiom from automation/models.py usage)
- Test: `apps/server/tests/test_slices_agent.py`

**Interfaces:**
- Consumes: `run_agent(deps: OperatorDeps, request: RunRequest) -> RunResult` (arden/operator/runner.py), `RunRequest(prompt, auto_approve, source_id, automation_id, ...)`, `Slice`, `AskStore`, `page_summary`.
- Produces: `build_slice_prompt(slice: Slice, page: Page, recent: list[dict]) -> str`; `parse_agent_ask(result_text: str) -> dict | None` (extracts the trailing ```json ask block```); `run_slice_agent(deps, slice, get_page, asks, recent) -> str | None` — spawns the agent (`auto_approve = slice.autonomy == "act"`), parses nomination, upserts `Ask(kind=..., source="agent", provenance=f"run:{result.run_id}")`, silence (no block) = no ask.

- [ ] **Step 1: Write the failing test**

```python
# apps/server/tests/test_slices_agent.py
from arden.slices.agent import build_slice_prompt, parse_agent_ask
from arden.slices.models import Slice
from arden.memory.pages import parse_page

SLICE = Slice(key="o-1a", title="O-1A", page_path="topics/o-1a.md", autonomy="observe")
PAGE = parse_page("---\ntitle: O-1A\n---\n# O-1A\n\n## Open loops\n- Find counsel.\n")


def test_prompt_contains_page_loops_and_contract():
    p = build_slice_prompt(SLICE, PAGE, recent=[{"event": "memory_changed", "path": "topics/o-1a.md"}])
    assert "Find counsel." in p
    assert "at most ONE ask" in p
    assert "observe" in p  # contract stated to the agent


def test_parse_agent_ask_extracts_json_block_or_none():
    out = 'Reviewed the domain.\n```json\n{"ask": {"text": "Review counsel memo", "kind": "review"}}\n```'
    ask = parse_agent_ask(out)
    assert ask == {"text": "Review counsel memo", "kind": "review"}
    assert parse_agent_ask("All quiet, nothing needs the user.") is None
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/test_slices_agent.py -v` → import error.

- [ ] **Step 3: Implement**

```python
# apps/server/arden/slices/agent.py
import json
import re
from datetime import UTC, datetime
from uuid import uuid4

from arden.memory.pages import Page
from arden.operator.runner import OperatorDeps, RunRequest, run_agent
from arden.slices.asks import AskStore
from arden.slices.models import Ask, Slice
from arden.slices.projection import parse_open_loops

_ASK_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)

_CONTRACT = {
    "observe": "You may READ anything and update the topic page, but take no external action.",
    "act": "You may run this slice's automations and workflows; irreversible actions still require approval.",
}


def build_slice_prompt(slice: Slice, page: Page, recent: list[dict]) -> str:
    loops = "\n".join(f"- {l}" for l in parse_open_loops(page.prose)) or "- (none)"
    events = "\n".join(f"- {json.dumps(e)}" for e in recent) or "- (none)"
    return (
        f"You are the standing agent for the '{slice.title}' slice of the user's life.\n"
        f"Autonomy contract ({slice.autonomy}): {_CONTRACT[slice.autonomy]}\n\n"
        f"Topic page:\n\n{page.prose}\n\n"
        f"Open loops:\n{loops}\n\n"
        f"What changed since your last run:\n{events}\n\n"
        "Your job: absorb what changed, update the topic page if warranted (memory tools), "
        "and decide whether ANYTHING needs the user. Nominate at most ONE ask.\n"
        "If something needs them, end your reply with exactly one fenced json block:\n"
        '```json\n{"ask": {"text": "<one sentence>", "kind": "review|decide|act|drift"}}\n```\n'
        "If nothing needs them, end with no json block — silence is the correct output on a quiet day."
    )


def parse_agent_ask(result_text: str) -> dict | None:
    m = _ASK_BLOCK.search(result_text)
    if not m:
        return None
    return json.loads(m.group(1))["ask"]


async def run_slice_agent(
    deps: OperatorDeps, slice: Slice, page: Page, asks: AskStore, recent: list[dict],
) -> str | None:
    request = RunRequest(
        prompt=build_slice_prompt(slice, page, recent),
        auto_approve=slice.autonomy == "act",
        source_id=f"slice:{slice.key}",
        automation_id=f"slice:{slice.key}",
    )
    result = await run_agent(deps, request)
    nominated = parse_agent_ask(result.text)
    if nominated:
        asks.upsert(Ask(
            id=f"agent:{slice.key}:{uuid4().hex[:8]}",
            slice_key=slice.key, text=nominated["text"], kind=nominated["kind"],
            source="agent", actions=[{"verb": "open_page", "ref": slice.page_path}],
            state="active", created_at=datetime.now(UTC).isoformat(),
            provenance=f"run:{result.run_id}",
        ))
    return result.text
```

(Adjust `result.text` / `result.run_id` attribute names to the actual `RunResult` dataclass — read arden/operator/runner.py at implementation time.) Then in app.py: register a scheduler handler named `slice_agent` that resolves the slice from the automation name (`slice:{key}`), loads its page via the file store, collects `recent` from the last-run watermark (store `last_run_at` already on the Automation), and calls `run_slice_agent`. On startup, for each registered slice ensure a `slice:{key}` automation exists (daily TimeTrigger + EventTrigger on memory changes to its page path).

- [ ] **Step 4: Run tests** — `uv run pytest tests/test_slices_agent.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/server/arden/slices/agent.py apps/server/arden/server/app.py apps/server/tests/test_slices_agent.py
git commit -m "feat(slices): standing slice agent — contract prompt, one-ask nomination, scheduler handler"
```

---

### Task 8: Desktop — slices store domain + API client

**Files:**
- Create: `apps/desktop/src/stores/slices-domain.ts`
- Create: `apps/desktop/src/api/slices.ts`
- Modify: `apps/desktop/src/stores/types.ts` (add `SlicesDomainState` to State; actions)
- Modify: `apps/desktop/src/stores/index.ts` (compose domain + actions)
- Test: `apps/desktop/tests/slicesDomain.test.ts`

**Interfaces:**
- Consumes: `apiWithConfig<T>(config, path, init?)` from `src/api/core.ts`.
- Produces:

```typescript
// api/slices.ts
export interface SliceSummary { key: string; title: string; autonomy: "observe" | "act"; live: boolean; updated: string; ask_count: number }
export interface SliceAsk { id: string; slice_key: string; text: string; kind: "review"|"decide"|"act"|"drift"; source: string; actions: { verb: string; ref: string }[]; state: string; created_at: string; provenance?: string | null }
export interface SlicesOverview { slices: SliceSummary[]; focus: SliceAsk[] }
export interface SliceDetail extends SliceSummary { open_loops: string[]; asks: SliceAsk[]; sessions: { session_id: string; name: string }[]; automations: unknown[]; page_path: string; related: string[] }
export async function fetchSlicesOverview(config): Promise<SlicesOverview>
export async function fetchSliceDetail(config, key): Promise<SliceDetail>
export async function resolveAsk(config, key, askId, state, snoozedUntil?): Promise<SliceAsk>
// stores/slices-domain.ts
export interface SlicesDomainState { overview: SlicesOverview | null; detailByKey: Record<string, SliceDetail>; openSliceKey: string | null; loading: boolean }
export function createSlicesDomainState(): SlicesDomainState
export function reduceOverviewLoaded(state, overview): SlicesDomainState
export function reduceDetailLoaded(state, detail): SlicesDomainState
export function reduceAskResolved(state, key, askId): SlicesDomainState  // removes from focus + detail
export function reduceOpenSlice(state, key: string | null): SlicesDomainState
```

Store actions in index.ts: `slicesOverviewLoaded`, `sliceDetailLoaded`, `sliceAskResolved`, `openSlice(key | null)` — each a one-line `set` over the reducer, matching the automation-domain idiom.

- [ ] **Step 1: Write the failing test**

```typescript
// apps/desktop/tests/slicesDomain.test.ts
import { expect, test } from "bun:test";
import {
  createSlicesDomainState, reduceOverviewLoaded, reduceAskResolved, reduceOpenSlice,
} from "@/stores/slices-domain";

const ask = { id: "a1", slice_key: "o-1a", text: "t", kind: "review" as const, source: "agent", actions: [], state: "active", created_at: "2026-07-06" };
const overview = { slices: [{ key: "o-1a", title: "O-1A", autonomy: "observe" as const, live: true, updated: "", ask_count: 1 }], focus: [ask] };

test("overview load + ask resolve removes from focus", () => {
  let s = reduceOverviewLoaded(createSlicesDomainState(), overview);
  expect(s.overview?.focus.length).toBe(1);
  s = reduceAskResolved(s, "o-1a", "a1");
  expect(s.overview?.focus.length).toBe(0);
});

test("openSlice sets and clears the room", () => {
  let s = reduceOpenSlice(createSlicesDomainState(), "o-1a");
  expect(s.openSliceKey).toBe("o-1a");
  expect(reduceOpenSlice(s, null).openSliceKey).toBeNull();
});
```

- [ ] **Step 2: Run to verify it fails** — `cd apps/desktop && bun test tests/slicesDomain.test.ts` → module not found.

- [ ] **Step 3: Implement** the reducers/API exactly per the Interfaces block (pure functions, spread-copy like `automation-domain.ts`; API functions are thin `apiWithConfig` wrappers: `GET /slices`, `GET /slices/{key}`, `POST /slices/{key}/asks/{id}/resolve`).

- [ ] **Step 4: Run tests** — `bun test tests/slicesDomain.test.ts` → PASS; `bun run typecheck` → clean.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/stores apps/desktop/src/api/slices.ts apps/desktop/tests/slicesDomain.test.ts
git commit -m "feat(desktop): slices store domain + API client"
```

---

### Task 9: Lab ports — FieldSwap, TravelingHighlight, FocusRing border

**Files:**
- Create: `apps/desktop/src/components/ui/FieldSwap.tsx` (from lab TextSwap/InstrumentRuler FieldSwap)
- Create: `apps/desktop/src/components/ui/TravelingHighlight.tsx` (from lab component)
- Create: `apps/desktop/src/components/ui/ChargeButton.tsx` (from lab BorderCharge)
- Test: `apps/desktop/tests/fieldSwap.test.tsx`

**Interfaces:**
- Produces: `FieldSwap({ swapKey: string; dir: number; children })` — three-phase single-element swap; `TravelingHighlight({ listRef, watch: "focus" | "selected", className? })`; `ChargeButton({ onArmed, label, armedLabel, windMs?: number })`.

**Before writing anything:** grep for prior art (`BlurSwap`, `RollingToken`, any `charge`/`hold` component) — extend, don't duplicate. `BlurSwap` is a crossfade, NOT the three-phase swap; both legitimately coexist (BlurSwap for spatial content, FieldSwap for in-place text states).

Tuned values to port verbatim (from the lab sources):
- FieldSwap: `--duration-quick` (150ms fallback) `ease-in-out`; exit translateY(dir·−4px) blur(2px) opacity 0; enter from translateY(dir·4px); force reflow with `void el.offsetWidth` between phases; live children at rest, frozen snapshot only while the old state exits; skip animation under prefers-reduced-motion.
- TravelingHighlight: travel `top/height var(--duration-fast) var(--ease-smooth-out)`, opacity `var(--duration-quick)`; fresh-guard (appear in place, then travel); `focusout` deferred via `queueMicrotask`; MutationObserver on `data-selected` for list mode; scroll repositions without transition. Map `--duration-fast/quick` and `--ease-smooth-out` to the arden motion tokens in styles.css (`MOTION.fast`=100ms, `MOTION.palette`-family easings — add CSS vars if absent rather than hardcoding).
- ChargeButton: WIND_MS 1100 linear border-opacity ramp; drain 500ms `cubic-bezier(0.22,1,0.36,1)` retargeting from `getComputedStyle` opacity; arm only if still held at full; label roll 450ms transform + 160ms/260ms blur (roll/land), mask gradient `transparent → #000 14%–86% → transparent`; revert 1400ms then 300ms fade `cubic-bezier(0.2,0.8,0.2,1)`; `transitioncancel` fires on retarget — listen for `transitionend` only.

- [ ] **Step 1: Failing test** (FieldSwap is the testable one — behavioral, not visual)

```typescript
// apps/desktop/tests/fieldSwap.test.tsx
import { expect, test } from "bun:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { FieldSwap } from "@/components/ui/FieldSwap";

test("FieldSwap renders current children for a stable key", async () => {
  const el = document.createElement("div");
  const root = createRoot(el);
  await act(async () => root.render(<FieldSwap swapKey="a" dir={0}><span>alpha</span></FieldSwap>));
  expect(el.textContent).toBe("alpha");
  await act(async () => root.render(<FieldSwap swapKey="b" dir={0}><span>beta</span></FieldSwap>));
  expect(el.textContent).toBe("beta");  // dir=0 → instant swap, no phases
});
```

- [ ] **Step 2: Run to verify failure** — `bun test tests/fieldSwap.test.tsx` → module not found.
- [ ] **Step 3: Port the three components** with the values above (FieldSwap is a direct port of the InstrumentRuler version — the lab file is the reference implementation; TravelingHighlight nearly verbatim with token mapping; ChargeButton extracts BorderCharge's logic into a reusable button with `onArmed` callback).
- [ ] **Step 4: Run** — `bun test tests/fieldSwap.test.tsx` + `bun run typecheck` → PASS/clean.
- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/components/ui apps/desktop/tests/fieldSwap.test.tsx
git commit -m "feat(desktop): FieldSwap, TravelingHighlight, ChargeButton — lab ports, tuned values verbatim"
```

---

### Task 10: Home entrypoint — hero input, focus set, slices strip

**Files:**
- Create: `apps/desktop/src/features/home/components/Home.tsx`
- Create: `apps/desktop/src/features/home/components/HeroInput.tsx`
- Create: `apps/desktop/src/features/home/components/FocusRow.tsx`
- Create: `apps/desktop/src/features/home/components/SlicesStrip.tsx`
- Create: `apps/desktop/src/features/home/hooks/useSlicesData.ts`
- Create: `apps/desktop/src/features/home/lib/heroRouting.ts`
- Modify: `apps/desktop/src/features/chat/components/Messages.tsx` (line ~209: replace `<HomeHero />` with `<Home />` — chat feature may not import home; do it via a store-registered render or move the swap up into `Chat.tsx`'s no-session branch, whichever keeps imports store-mediated; if Chat.tsx composes it, add the import there and render `<Home />` when `visibleOrder.length === 0 && !running`)
- Delete: `apps/desktop/src/components/ui/HomeHero.tsx` (after the swap — cleanup rule)
- Test: `apps/desktop/tests/heroRouting.test.ts`

**Interfaces:**
- Consumes: `useStore` (slices domain from Task 8, `setCurrentSession`, `openMemory`, draft/setDraft), `sendMessage` from actions/messages.ts, `useCommandList`/`filterCommands` from features/chat/lib/commands.ts is chat-owned — so heroRouting builds its OWN suggestion model from store data (sessions, slices, automations, skills), not by importing chat's lib.
- Produces: `routeHeroInput(query, ctx) -> HeroSuggestion[]` where `ctx = { sessions, slices, automations, skills }` and `HeroSuggestion = { kind: "chat" | "slice" | "session" | "automation" | "skill"; label: string; ref: string }`; first suggestion is always `{kind:"chat", label: query}` (Enter with no selection = start a chat — the door never blocks typing).

Layout (from the approved Figma frame "Slices / Home — entrypoint"): centered 640px column; date line (11px tracking-wide faint) → HeroInput (56px, radius 14, hairline border, ⌘K chip) → `FOCUS` label + FocusRow list → `SLICES` label + SlicesStrip → nothing else. FocusRow: 52px tonal card (surface-soft, radius 10), slice key small-caps left (76px), ask text, action button right; rows enter with RISE_IN/SPRING_ROW_ENTRY, retire with ROW_EXIT; text changes via FieldSwap. SlicesStrip: chips with live-dot, quiet ones at 55% opacity, TravelingHighlight on hover. Greeting line states the count ("Two things need you." / "All clear.") — derive from focus length.

- [ ] **Step 1: Failing test** for routing (pure logic):

```typescript
// apps/desktop/tests/heroRouting.test.ts
import { expect, test } from "bun:test";
import { routeHeroInput } from "@/features/home/lib/heroRouting";

const ctx = {
  sessions: [{ session_id: "s1", name: "counsel requirements" }],
  slices: [{ key: "o-1a", title: "O-1A" }],
  automations: [{ task_id: "t1", name: "morning-digest" }],
  skills: [{ name: "research", description: "" }],
};

test("plain text routes to chat first", () => {
  const s = routeHeroInput("book flights", ctx);
  expect(s[0].kind).toBe("chat");
});

test("prefix matches surface slices, sessions, automations", () => {
  const s = routeHeroInput("o-1", ctx);
  expect(s.some((x) => x.kind === "slice" && x.ref === "o-1a")).toBe(true);
  const t = routeHeroInput("morning", ctx);
  expect(t.some((x) => x.kind === "automation" && x.ref === "t1")).toBe(true);
});
```

- [ ] **Step 2: Run to verify failure** — module not found.
- [ ] **Step 3: Implement** heroRouting (case-insensitive substring match, ordered chat → slices → sessions → automations → skills, max 6), then the components per the layout block; `useSlicesData` fetches overview on mount + refetches on `slices_changed`/`automation_finished` events (subscribe in the existing `useAutomationEvents` switch — add the `slices_changed` case there calling a store action, since that hook already owns the automation SSE stream).
- [ ] **Step 4: Run** — `bun test tests/` + `bun run typecheck` + `bun run lint` → clean. Visual check via preview harness: seed `window.__arden.setState` with a slices overview, screenshot, compare against the Figma frame.
- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/features/home apps/desktop/src/features/chat apps/desktop/tests/heroRouting.test.ts
git rm apps/desktop/src/components/ui/HomeHero.tsx
git commit -m "feat(desktop): Home entrypoint — hero input with routing, focus set, slices strip"
```

---

### Task 11: Slice room

**Files:**
- Create: `apps/desktop/src/features/slices/components/SliceRoom.tsx`
- Create: `apps/desktop/src/features/slices/components/AskCard.tsx`
- Create: `apps/desktop/src/features/slices/components/OpenLoops.tsx`
- Create: `apps/desktop/src/features/slices/components/SliceActivity.tsx`
- Modify: `apps/desktop/src/app/App.tsx` or `Chat.tsx` no-session branch: `openSliceKey ? <SliceRoom /> : <Home />` (same store-mediated slot as Task 10)
- Test: covered by store tests (Task 8) + preview walkthrough; add `apps/desktop/tests/sliceRoomSelectors.test.ts` if any non-trivial selector logic emerges (e.g. activity merging)

**Interfaces:**
- Consumes: `detailByKey[openSliceKey]`, `fetchSliceDetail`, `resolveAsk` API, `openMemory` (deep-link page via the wikiResolution mechanism), `setCurrentSession`, `sendMessage` with `{ sliceKey }` option — extend `SendMessageOptions` in actions/messages.ts to pass `slice_key` through session creation (`POST /sessions` body from Task 4).
- Layout (Figma frame "Slices / O-1A room"): back link → title + autonomy chip (chip opens a small popover — autonomy toggle uses `ChargeButton` for the observe→act grant; act→observe is a plain click, de-escalation needs no ceremony) → last-run line (NumberFlow-style cost via existing `RollingToken`) → AskCard (attention card: severity dot, text, per-action buttons mapped from `ask.actions` verbs: `open_session` → setCurrentSession, `open_page` → openMemory deep-link, `retry` → automation run-now API, dismiss ✕ → resolveAsk) → OpenLoops rows (InlineDisclosure-style expand using existing `Collapse`) → SliceActivity (quiet rows: slice sessions + automation runs, click-through) → related chips → scoped composer (existing Composer? No — chat owns Composer; render a thin local input that calls `sendMessage(text, [], { sliceKey })` then `setCurrentSession` to the new session).
- Ask resolution motion: SuccessCheck-style receipt then ROW_EXIT — implement receipt as the existing in-place button confirmation idiom (icon morph + label roll, canon per design-language.md) rather than a new SVG component; the lab SuccessCheck is the reference if a drawn check is wanted later.

- [ ] **Step 1–4:** Build components against the interfaces above; verify with `bun test tests/` + typecheck + lint; preview-harness walkthrough: open room from strip, resolve an ask (row exits, focus updates), open loops expand, scoped message creates a slice-tagged session (verify `slice_key` lands in the sessions list via preview_network).
- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/features/slices apps/desktop/src/actions/messages.ts apps/desktop/src/app
git commit -m "feat(desktop): slice room — ask card, open loops, activity, scoped composer"
```

---

### Task 12: Seed registry + end-to-end walkthrough + gate

**Files:**
- Create: `apps/server/arden/slices/seed.py` (optional CLI helper: `uv run python -m arden.slices.seed` prints candidate topic pages and writes slices.json for chosen keys — explicit promotion per spec open-question default)
- Modify: none beyond fixes the walkthrough surfaces

- [ ] **Step 1:** Seed the user's real registry: `o-1a`, `dex`, `arden`, `aside`, `health`, `united-states` (explicit list; reference topics like `letta.md` stay unpromoted).
- [ ] **Step 2:** Server up (`uv run arden-server serve` — do NOT restart the user's own running server; use a second port if theirs is up), hit `GET /slices`, verify overview JSON: slices present, mechanical asks appear when an approval is pending.
- [ ] **Step 3:** Desktop preview harness: full flow — Home renders focus set from the live server, hero input routes (type "o-1" → slice suggestion → Enter opens room), ask resolve round-trips (SSE `slices_changed` → focus updates), scoped composer starts a tagged session, slice agent manual trigger (`POST` the automation invoke endpoint for `slice:o-1a`) produces a page update + at most one ask.
- [ ] **Step 4:** Full gates: `cd apps/server && uv run pytest tests/ -q` and `cd apps/desktop && bun run typecheck && bun run lint && bun test tests/ && bun run build`. All green.
- [ ] **Step 5: Commit** any walkthrough fixes; final commit `feat(slices): seed CLI + e2e polish`.

---

## Self-Review Notes

- **Spec coverage:** placement (T10 swap at the HomeHero slot) ✓; slice entity/registry (T1) ✓; asks + focus nomination (T3, T5) ✓; rooms (T11) ✓; hero input + routing (T10) ✓; session tagging (T4) ✓; slice agent + contract + provenance + event/schedule triggers (T7) ✓; dismiss/snooze (T3/T6) ✓; SSE (T6) ✓; lab ports with verbatim values (T9, NumberFlow satisfied by existing RollingToken, SuccessCheck by the canon button-confirm idiom — deliberate reuse over port) ✓; drift kind = agent-nominated (T7 prompt) ✓.
- **Spec deviation (flagged):** spec's "open loops marked as needing the user" as a mechanical ask source is dropped — pages have no such marker; prose loops render in the room (T2/T11) and the slice agent nominates from them (T7). Focus set stays approval/failure/agent-fed.
- **Type consistency:** `Ask.actions` verbs are `open_session | open_page | retry` end-to-end (T5 server → T8 types → T11 handlers); `slice_key` name used identically in SessionState, API payloads, and SendMessageOptions; `autonomy: "observe" | "act"` everywhere.
- **Known judgment calls for the implementer:** exact `RunResult` attribute names (T7 step 3 note); whether sessions table needs an ALTER (T4 step 3 covers both); where the no-session swap lives (Chat.tsx vs Messages.tsx — T10 gives the rule: keep feature imports store-mediated).
