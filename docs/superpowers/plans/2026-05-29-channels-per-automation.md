# Channels Per Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-provision a bound channel for every agent automation so its full activity (tool calls, results, assistant text) persists into a chattable session, executed in-session via the chat/iteration pipeline.

**Architecture:** At automation-creation time, `AutomationService.create` provisions a `session_type="channel"` session (via an injected `SessionService`), persists it empty, and binds the new automation to it by setting `thread_id` + `read_history=True`. This routes the automation through the scheduler's existing session-bound iteration path (`_run_session_bound` → `_iteration_dispatcher` → `_dispatch_iteration` → `submit_chat_message`), which already persists the full turn and emits live SSE. No new execution path is added; we reuse the loop machinery. A backfill migration gives existing automations a channel on upgrade. UI adds a sidebar glyph tooltip and a per-card channel link.

**Tech Stack:** Python 3.13 (uv, pytest, aiosqlite), React/TypeScript (Bun), FastAPI, SQLite.

**Prerequisite:** This plan assumes the `writable → auto_approve` rename (Plan `2026-05-29-approval-model-auto-approve.md`) is already merged. All code below uses `auto_approve`.

---

### Task 1: Inject SessionService into AutomationService

**Files:**
- Modify: `apps/server/arden/automation/service.py:50-58` (`__init__`)
- Modify: `apps/server/arden/server/runtime/automation.py:41-44` (construction)
- Test: `apps/server/tests/automation/test_service_channel_provisioning.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# apps/server/tests/automation/test_service_channel_provisioning.py
import pytest
from arden.automation.service import AutomationService
from arden.automation.scheduler import Scheduler
from arden.automation.store import AutomationStore
from arden.services.session import SessionService
from arden.context.store import SessionStore
from arden import database


@pytest.fixture
async def svc(tmp_path):
    auto_conn = await database.connect(str(tmp_path / "auto.db"))
    store = AutomationStore(auto_conn)
    await store.init()
    sess_conn = await database.connect(str(tmp_path / "sess.db"))
    sess_read = await database.connect(str(tmp_path / "sess.db"), readonly=True)
    sess_store = SessionStore(sess_conn, sess_read)
    await sess_store.init()
    session_service = SessionService(sess_store)
    scheduler = Scheduler(store=store, build_deps=lambda: None)
    return AutomationService(store=store, scheduler=scheduler, session_service=session_service)


async def test_service_accepts_session_service(svc):
    assert svc.session_service is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/server && uv run pytest tests/automation/test_service_channel_provisioning.py::test_service_accepts_session_service -v`
Expected: FAIL — `AutomationService.__init__() got an unexpected keyword argument 'session_service'`

- [ ] **Step 3: Add the parameter**

In `apps/server/arden/automation/service.py`, update `__init__`:

```python
    def __init__(
        self,
        store: AutomationStore,
        scheduler: Scheduler,
        session_service: "SessionService",
    ):
        self.store = store
        self.scheduler = scheduler
        self.session_service = session_service
```

Add the import at the top of the file (use TYPE_CHECKING to avoid a circular import if one arises; a direct import is fine if it doesn't):

```python
from arden.services.session import SessionService
```

In `apps/server/arden/server/runtime/automation.py`, update the construction:

```python
        self.automation_service = AutomationService(
            store=stores.automations,
            scheduler=self.scheduler,
            session_service=stores.sessions,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/server && uv run pytest tests/automation/test_service_channel_provisioning.py::test_service_accepts_session_service -v`
Expected: PASS

- [ ] **Step 5: Fix any other AutomationService construction sites**

Run: `cd apps/server && grep -rn "AutomationService(" arden/ tests/ | grep -v "session_service"`
For each remaining site (tests included), add `session_service=...`. Test fixtures that don't need a real session service can pass a minimal `SessionService` built on a tmp `SessionStore` (as in the fixture above).

- [ ] **Step 6: Commit**

```bash
git add apps/server/arden/automation/service.py apps/server/arden/server/runtime/automation.py apps/server/tests/automation/test_service_channel_provisioning.py
git commit -m "feat(automation): inject SessionService into AutomationService"
```

---

### Task 2: Provision a channel in `AutomationService.create`

**Files:**
- Modify: `apps/server/arden/automation/service.py` (`create`, lines ~231-328)
- Test: `apps/server/tests/automation/test_service_channel_provisioning.py` (add)

The channel is provisioned only when the caller did NOT already pass a `thread_id` (loops and other explicitly-bound automations keep their existing target). After provisioning we set `thread_id` to the channel session id and force `read_history=True` so the scheduler routes it through the iteration dispatcher.

- [ ] **Step 1: Write the failing test**

```python
async def test_create_provisions_channel(svc):
    automation = await svc.create(
        name="nudge",
        description="remind me to stretch",
        trigger_type="time",
        at="09:00",
    )
    assert automation is not None
    assert automation.thread_id is not None
    assert automation.read_history is True
    # The bound session exists, is a channel, and points back at the automation.
    session = await svc.session_service.load(automation.thread_id)
    assert session is not None
    assert session.state.session_type == "channel"
    assert session.state.origin_automation_id == automation.task_id


async def test_create_with_explicit_thread_id_skips_channel(svc):
    # Pre-create a plain session and bind to it explicitly.
    existing = svc.session_service.create(name="manual")
    await svc.session_service.save(existing, [])
    automation = await svc.create(
        name="bound",
        description="work in existing session",
        trigger_type="time",
        at="09:00",
        thread_id=existing.session_id,
    )
    assert automation.thread_id == existing.session_id
    session = await svc.session_service.load(existing.session_id)
    assert session.state.session_type == "chat"  # untouched
    assert session.state.origin_automation_id is None
```

NOTE: confirm the exact accessor for the loaded session's state. `SessionService.load` returns `SessionData`; the test above assumes `session.state`. If the attribute differs (e.g. `session.session_state`), adjust both assertions to match — check `apps/server/arden/services/session.py` `SessionData` definition before running.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/server && uv run pytest tests/automation/test_service_channel_provisioning.py::test_create_provisions_channel -v`
Expected: FAIL — `automation.thread_id is None`

- [ ] **Step 3: Implement provisioning in `create`**

In `apps/server/arden/automation/service.py`, inside `create`, after `task_id = generate_slug(2)` and before the `loop_prompt = ...` line, insert:

```python
        # Auto-provision a bound channel for agent automations that aren't
        # already bound to a session. The channel is an ordinary
        # session_type="channel" session living in the default project
        # (Inbox); the automation's full activity persists there and the
        # user can chat in it. Explicitly-bound callers (loops, internal
        # handlers passing thread_id) keep their target untouched.
        if thread_id is None:
            channel = self.session_service.create(
                name=name,
                session_type="channel",
                origin_automation_id=task_id,
            )
            await self.session_service.save(channel, [])
            thread_id = channel.session_id
            read_history = True
```

The existing `loop_prompt = description if thread_id is not None else None` line now sees the provisioned `thread_id`, so `loop_prompt` is correctly set to `description`. The `Automation(...)` constructor already passes `thread_id=thread_id` and `read_history=read_history`, so no further change is needed there.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/server && uv run pytest tests/automation/test_service_channel_provisioning.py -v`
Expected: PASS (all three tests)

- [ ] **Step 5: Verify handler automations are unaffected**

`create` is the agent-automation entry point; internal handler automations are seeded via `seed_builtins` / store directly, not via `create`. Confirm:

Run: `cd apps/server && grep -rn "\.create(" arden/automation/builtins.py`
Expected: no matches (builtins construct `Automation` directly). If any builtin uses `create`, it would now get a channel — flag it and pass an explicit `thread_id` or a dedicated skip; otherwise no action.

- [ ] **Step 6: Commit**

```bash
git add apps/server/arden/automation/service.py apps/server/tests/automation/test_service_channel_provisioning.py
git commit -m "feat(automation): provision bound channel on automation create"
```

---

### Task 3: Verify in-session execution persists tool calls

This task adds no production code if Tasks 1-2 are correct — the scheduler already routes `thread_id`-bound + `read_history=True` automations through `_run_session_bound` → iteration mode. This task proves it end-to-end and locks the behavior with a test.

**Files:**
- Test: `apps/server/tests/automation/test_channel_execution.py` (create)

- [ ] **Step 1: Write the test**

```python
# apps/server/tests/automation/test_channel_execution.py
from arden.automation.scheduler import Scheduler


def test_channel_automation_is_session_bound():
    # A created channel automation must be classified session-bound so the
    # scheduler picks the iteration dispatcher (persists full turn + SSE),
    # not the headless _run_agent path (final text only, no channel).
    from arden.automation.models import Automation
    from datetime import UTC, datetime
    a = Automation(
        task_id="t1",
        name="x",
        description="d",
        model=None,
        triggers=[],
        enabled=True,
        created_at=datetime.now(UTC),
        next_run_at=None,
        last_run_at=None,
        last_result=None,
        running_since=None,
        auto_approve=False,
        thread_id="sess_123",
        read_history=True,
        loop_prompt="d",
    )
    assert Scheduler._is_session_bound(a) is True
    assert a.read_history is True  # → iteration dispatcher in _run_session_bound
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd apps/server && uv run pytest tests/automation/test_channel_execution.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add apps/server/tests/automation/test_channel_execution.py
git commit -m "test(automation): lock channel automations to session-bound iteration path"
```

---

### Task 4: Channel backfill migration for existing automations

Existing agent automations (created before this change) have no `thread_id`. On upgrade, give each one a channel so they gain visibility too. This is a data migration that runs once at startup, after the schema is ready.

**Files:**
- Modify: `apps/server/arden/server/runtime/automation.py` (`start_scheduler`, after `seed_builtins`)
- Test: `apps/server/tests/automation/test_channel_backfill.py` (create)

Backfill targets only agent automations: `handler is None` (not internal handlers), `kind != "loop"` (loops bind to their chat session), and `thread_id is None`. Implement as a method on `AutomationService` so it's unit-testable and reuses provisioning logic.

- [ ] **Step 1: Write the failing test**

```python
# apps/server/tests/automation/test_channel_backfill.py
import pytest
from datetime import UTC, datetime
from arden.automation.models import Automation
# reuse the svc fixture pattern from test_service_channel_provisioning
from tests.automation.test_service_channel_provisioning import svc  # noqa: F401


async def _insert(store, *, task_id, handler=None, kind="automation", thread_id=None):
    await store.save(Automation(
        task_id=task_id, name=task_id, description="d", model=None,
        triggers=[], enabled=True, created_at=datetime.now(UTC),
        next_run_at=None, last_run_at=None, last_result=None,
        running_since=None, auto_approve=False, handler=handler,
        kind=kind, thread_id=thread_id, read_history=False, loop_prompt=None,
    ))


async def test_backfill_gives_agent_automations_channels(svc):
    await _insert(svc.store, task_id="agent1")           # eligible
    await _insert(svc.store, task_id="hdlr1", handler="knowledge_health")  # skip
    await _insert(svc.store, task_id="loop1", kind="loop", thread_id="sess_x")  # skip
    await _insert(svc.store, task_id="bound1", thread_id="sess_y")  # skip (already bound)

    count = await svc.backfill_channels()
    assert count == 1

    agent1 = await svc.get("agent1")
    assert agent1.thread_id is not None
    assert agent1.read_history is True
    session = await svc.session_service.load(agent1.thread_id)
    assert session.state.session_type == "channel"
    assert session.state.origin_automation_id == "agent1"

    # Idempotent: a second run provisions nothing new.
    assert await svc.backfill_channels() == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/server && uv run pytest tests/automation/test_channel_backfill.py -v`
Expected: FAIL — `AttributeError: 'AutomationService' object has no attribute 'backfill_channels'`

- [ ] **Step 3: Implement `backfill_channels`**

Add to `AutomationService` in `apps/server/arden/automation/service.py`:

```python
    async def backfill_channels(self) -> int:
        """One-time upgrade: give pre-existing agent automations a bound
        channel. Skips internal handlers, loops, and already-bound rows.
        Idempotent — only acts on rows with thread_id is None."""
        count = 0
        for task in await self.store.list_all():
            if task.handler is not None or task.kind == "loop" or task.thread_id is not None:
                continue
            channel = self.session_service.create(
                name=task.name,
                session_type="channel",
                origin_automation_id=task.task_id,
            )
            await self.session_service.save(channel, [])
            updated = replace(task, thread_id=channel.session_id, read_history=True, loop_prompt=task.description)
            await self.store.update_metadata(updated)
            count += 1
        return count
```

`replace` is already imported from `dataclasses` at the top of the file. Confirm `update_metadata` persists `thread_id`, `read_history`, and `loop_prompt` — check `apps/server/arden/automation/store.py` `_SQL_UPDATE_METADATA`. If any of those columns is not in the UPDATE set, use `self.store.save(updated)` instead (full upsert).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/server && uv run pytest tests/automation/test_channel_backfill.py -v`
Expected: PASS

- [ ] **Step 5: Wire backfill into startup**

In `apps/server/arden/server/runtime/automation.py`, in `start_scheduler`, after `await seed_builtins(self.stores.automations)` and before `self.scheduler.start()`:

```python
        await self.automation_service.backfill_channels()
```

- [ ] **Step 6: Run full automation suite**

Run: `cd apps/server && uv run pytest tests/automation/ -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add apps/server/arden/automation/service.py apps/server/arden/server/runtime/automation.py apps/server/tests/automation/test_channel_backfill.py
git commit -m "feat(automation): backfill channels for existing automations on startup"
```

---

### Task 5: Sidebar channel glyph tooltip

**Files:**
- Modify: `apps/desktop/src/components/sidebar/SessionStateIcon.tsx:33-39`

The channel glyph currently has only `aria-label="Channel"`, which does not show on hover. Add a native `title` so new users understand the radio icon.

- [ ] **Step 1: Add the tooltip**

In `apps/desktop/src/components/sidebar/SessionStateIcon.tsx`, change the channel branch:

```tsx
  if (isChannel) {
    return (
      <span
        className="grid place-items-center w-4 h-4 text-faint"
        aria-label="Channel"
        title="Channel — an automation posts its activity here; you can chat in it too"
      >
        <Radio size={ICON.SM} strokeWidth={2} />
      </span>
    );
  }
```

- [ ] **Step 2: Typecheck**

Run: `cd apps/desktop && bun run tsc --noEmit` (or the project's typecheck script — check `package.json`)
Expected: no new errors.

- [ ] **Step 3: Commit**

```bash
git add apps/desktop/src/components/sidebar/SessionStateIcon.tsx
git commit -m "feat(desktop): add hover tooltip to channel glyph"
```

---

### Task 6: Per-automation-card channel link

**Files:**
- Modify: `apps/desktop/src/components/AutomationsModal.tsx` (automation card render)

Each automation card links to its bound channel. The automation's bound channel is the session whose `origin_automation_id === automation.task_id`. The card should surface a button/link that opens that session.

- [ ] **Step 1: Locate the card render and the session-open affordance**

Run: `cd apps/desktop && grep -n "task_id\|origin_automation_id\|onOpenSession\|openSession\|sessions" src/components/AutomationsModal.tsx | head -30`
Identify (a) where a single automation card is rendered, and (b) the prop/callback used elsewhere to open a session by id (e.g. `onSelectSession`, `openSession`, a store action). Reuse the existing mechanism — do not invent a new navigation path.

- [ ] **Step 2: Resolve the bound channel and render a link**

Within the card, compute the channel session id:

```tsx
const channel = sessions.find((s) => s.origin_automation_id === automation.task_id);
```

(If `AutomationsModal` does not already receive `sessions`, thread the existing sessions list/store in via the same source `Chat.tsx` uses — `automations`/`sessions` are already app-level state. Check how `Chat.tsx:44` accesses `session.origin_automation_id` and follow the same data source.)

Render, only when `channel` exists:

```tsx
{channel && (
  <button
    type="button"
    className="..."  // match existing card action button styles
    title="Open this automation's channel"
    onClick={() => onOpenSession(channel.session_id)}
  >
    <Radio size={ICON.XS} strokeWidth={2} />
    channel
  </button>
)}
```

Use the same icon import (`Radio` from `lucide-react`, `ICON` from `../lib/icons`) and the same open-session callback identified in Step 1. Match neighboring button styling rather than introducing new classes.

- [ ] **Step 3: Typecheck**

Run: `cd apps/desktop && bun run tsc --noEmit`
Expected: no new errors.

- [ ] **Step 4: Manual UI verification**

Start the app, open the Automations modal, confirm each automation card shows a "channel" link, and clicking it opens the bound channel session. Confirm a newly-created automation immediately shows the link.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/components/AutomationsModal.tsx
git commit -m "feat(desktop): link automation cards to their bound channel"
```

---

### Task 7: End-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Backend suite**

Run: `cd apps/server && uv run pytest tests/automation/ tests/ -q`
Expected: PASS (no regressions).

- [ ] **Step 2: Desktop typecheck + build**

Run: `cd apps/desktop && bun run tsc --noEmit`
Expected: clean.

- [ ] **Step 3: Live smoke test**

1. Start server (`uv run arden-server serve`) and desktop app.
2. Create a new automation (auto-approve ON) with a near-term trigger or fire it manually.
3. Confirm: a channel session appears in Inbox with the radio glyph; hovering the glyph shows the tooltip; the automation card links to it.
4. When the automation fires, confirm the channel shows the **full turn** — assistant text plus `tool_call`/`tool_result` activity — rendered live.
5. Send a user message into the channel; confirm it persists and the next fire reads it (history in-session).
6. Create an automation with auto-approve ON that calls `notify` — confirm it fires headless (no dead-end) and the notification lands.

- [ ] **Step 4: Report results**

Summarize pass/fail per check. Do not commit anything in this task.
