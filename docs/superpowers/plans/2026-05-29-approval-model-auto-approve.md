# Approval Model: `writable` → `auto_approve` + ASK-when-headless Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the automation `writable` flag to `auto_approve` across the stack, and fix `ASK` tool-overrides so they only block when a human (UI) is reachable — letting `notify()` and other `ASK`-tagged tools fire in headless auto-approve automations.

**Architecture:** A mechanical field rename (`writable`→`auto_approve`) through model → SQLite store (with a v8 column-rename migration) → service → HTTP schemas/routes → desktop API types + UI, plus one targeted behavior change in `ToolExecution.request_approval` that makes `ASK` conditional on `io.emit` being present.

**Tech Stack:** Python 3.13 / FastAPI / aiosqlite (backend), pytest (tests), React + TypeScript + Bun (desktop).

This is Plan 1 of 2. Plan 2 (`channels-per-automation`) builds on this and is independent.

Spec: `docs/superpowers/specs/2026-05-29-automation-channels-and-approval-model-design.md`

---

## File Structure

**Backend (modify):**
- `apps/server/arden/tools/core/context.py` — the ASK-when-headless fix (the only behavior change).
- `apps/server/arden/automation/models.py` — `Automation.writable` → `auto_approve`.
- `apps/server/arden/automation/store.py` — schema, columns, SQL, row mapping, `set_writable`→`set_auto_approve`, v8 migration.
- `apps/server/arden/automation/service.py` — `toggle_writable`→`toggle_auto_approve`, `_build_metadata_changes`, `update`, `create`, `create_loop`.
- `apps/server/arden/operator/runner.py` — `RunRequest.writable` → `auto_approve`.
- `apps/server/arden/automation/scheduler.py` — `skip_approvals=automation.writable` → `automation.auto_approve`, plus `RunRequest(writable=...)` call sites.
- `apps/server/arden/server/app.py` — `_dispatch_post`/`_dispatch_iteration` `skip_approvals=automation.writable` and `RunRequest(writable=...)`.
- `apps/server/arden/server/schemas.py` — request models carrying `writable`.
- `apps/server/arden/server/routers/automation.py` — `/writable` route + field usages.
- `apps/server/arden/tools/automation.py` — the `create_automation`/`update_automation` tool input + plumbing.

**Desktop (modify):**
- `apps/desktop/src/api.ts` — `Automation.writable`, `CreateAutomationPayload.writable`, `UpdateAutomationPayload.writable`, the toggle-writable client fn + route.
- `apps/desktop/src/components/automations/AutomationEditor.tsx` — `FormState.writable`, the toggle label/`aria-label`.
- `apps/desktop/src/lib/automationTrust.ts` — `"can write"` label → `"auto-approve"`, tone keyed off `auto_approve`.
- `apps/desktop/src/actions/automations.ts` (and `actions/sessions.ts` if it references the toggle) — toggle action.

**Tests (create/modify):**
- `apps/server/tests/` — locate the existing automation store/service tests; add migration + rename round-trip tests and the ASK-fix tests.

---

## Task 1: ASK-when-headless fix (the behavior change)

This is the highest-value change and is independent of the rename. Do it first.

**Files:**
- Modify: `apps/server/arden/tools/core/context.py:360-364`
- Test: add to the existing tool-context/approval test module (find with `grep -rln "request_approval\|skip_approvals" apps/server/tests`).

- [ ] **Step 1: Find the approval test module**

Run: `grep -rln "request_approval\|skip_approvals\|ApprovalControls" apps/server/tests`
Note the file (referred to below as `tests/<approval_test>.py`). If none exists, create `apps/server/tests/tools/test_request_approval.py`.

- [ ] **Step 2: Write failing tests for the ASK-when-headless rule**

Add tests covering the truth table. Build a `ToolExecution` whose `ctx` has: a registry returning a chosen override for the tool, `approval_controls.skip_approvals`, an `IOBridge` with/without `emit` + `pending_approvals`. Assert `request_approval(...)` returns `None` (bypass) or a `Rejection` (blocks/dead-ends) as expected.

```python
import pytest
from arden.tools.core.types import ToolOverrideDecision

@pytest.mark.asyncio
async def test_ask_override_blocks_when_ui_connected(make_execution):
    # ASK + UI present + skip_approvals → must still go to approval flow.
    # With a UI present this would await a future; assert it does NOT early-return None.
    execution = make_execution(
        override=ToolOverrideDecision.ASK,
        skip_approvals=True,
        ui_connected=True,
    )
    # request_approval should NOT bypass: it registers a pending future.
    # Resolve it as approved to keep the test fast.
    rejection = await execution.request_approval("desc")
    assert rejection is None  # approved via resolved future, not bypassed

@pytest.mark.asyncio
async def test_ask_override_bypassed_when_headless(make_execution):
    # ASK + no UI + skip_approvals → bypass (return None), do NOT dead-end.
    execution = make_execution(
        override=ToolOverrideDecision.ASK,
        skip_approvals=True,
        ui_connected=False,
    )
    assert await execution.request_approval("desc") is None

@pytest.mark.asyncio
async def test_ask_override_headless_without_skip_still_dead_ends(make_execution):
    # ASK + no UI + NOT skip_approvals → Rejection("No UI connected ...").
    execution = make_execution(
        override=ToolOverrideDecision.ASK,
        skip_approvals=False,
        ui_connected=False,
    )
    rejection = await execution.request_approval("desc")
    assert rejection is not None
    assert "No UI connected" in (rejection.feedback or "")

@pytest.mark.asyncio
async def test_no_override_skip_approvals_bypasses(make_execution):
    execution = make_execution(override=None, skip_approvals=True, ui_connected=False)
    assert await execution.request_approval("desc") is None
```

Write a `make_execution` fixture/factory in the test module that constructs a real `ToolContext`/`ToolExecution` with a stub registry (`get_override` returns the param, `get` returns a minimal tool), and an `IOBridge` whose `emit`/`pending_approvals` are set only when `ui_connected=True`. For the UI-connected approved case, pre-resolve the future: set `emit` to a coroutine that resolves `pending_approvals[tool_id]` with `{"approved": True, "result": ""}`.

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd apps/server && uv run pytest tests/<approval_test>.py -v`
Expected: the headless-bypass test FAILS (currently returns a `Rejection` because `override != ASK` guard blocks the bypass).

- [ ] **Step 4: Implement the fix**

In `apps/server/arden/tools/core/context.py`, replace the guard at lines 360-364:

```python
        override = self.ctx.registry.get_override(self.tool_name)
        ui_connected = self.ctx.io.emit is not None and self.ctx.io.pending_approvals is not None
        ask_must_block = override == ToolOverrideDecision.ASK and ui_connected
        if not ask_must_block and (self.ctx.skip_approvals or self.tool_name in self.ctx.auto_approve):
            return None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd apps/server && uv run pytest tests/<approval_test>.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/server/arden/tools/core/context.py apps/server/tests
git commit -m "fix(approvals): ASK override blocks only when a UI is connected

Lets ASK-tagged tools (e.g. notify) fire in headless auto-approve
automations while still prompting in interactive chat. DENY unaffected."
```

---

## Task 2: Store migration v8 — rename `writable` column → `auto_approve`

**Files:**
- Modify: `apps/server/arden/automation/store.py`
- Test: `apps/server/tests/` automation-store test module (find with `grep -rln "AutomationStore\|init_schema" apps/server/tests`).

- [ ] **Step 1: Write a failing migration test**

Create a test that opens an in-memory/temp DB seeded at schema v7 with a `writable` column and a row, runs `init_schema()`, and asserts the column is now `auto_approve` with the value preserved.

```python
@pytest.mark.asyncio
async def test_v8_renames_writable_to_auto_approve(tmp_path):
    import aiosqlite
    from arden.automation.store import AutomationStore, _SCHEMA
    db = tmp_path / "auto.db"
    async with aiosqlite.connect(db) as conn:
        conn.row_factory = aiosqlite.Row
        # Build a v7 schema with the OLD column name, version pinned to 7.
        await conn.executescript(_SCHEMA.replace("auto_approve INTEGER NOT NULL DEFAULT 0", "writable INTEGER NOT NULL DEFAULT 0"))
        await conn.execute("INSERT OR REPLACE INTO automation_meta (key, value) VALUES ('schema_version', '7')")
        await conn.execute(
            "INSERT INTO scheduled_tasks (task_id, description, triggers, created_at, writable) "
            "VALUES ('t1', 'd', '[]', '2026-01-01T00:00:00', 1)"
        )
        await conn.commit()
        store = AutomationStore(conn)
        await store.init_schema()
        rows = await conn.execute_fetchall("PRAGMA table_info(scheduled_tasks)")
        cols = {r["name"] for r in rows}
        assert "auto_approve" in cols and "writable" not in cols
        got = await store.get("t1")
        assert got.auto_approve is True
```

(Note: this test references `_SCHEMA` already containing `auto_approve` — which is true only after Step 3. The `.replace(...)` reconstructs the pre-rename v7 schema. Adjust the replaced substring to match the exact `_SCHEMA` line after Step 3.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/server && uv run pytest tests/<store_test>.py::test_v8_renames_writable_to_auto_approve -v`
Expected: FAIL (`auto_approve` column missing / attribute error).

- [ ] **Step 3: Update `_SCHEMA` and add the v8 migration**

In `apps/server/arden/automation/store.py`:

In `_SCHEMA` (line ~130) change:
```
    writable INTEGER NOT NULL DEFAULT 0,
```
to
```
    auto_approve INTEGER NOT NULL DEFAULT 0,
```

Bump the version constant (line 557):
```python
CURRENT_SCHEMA_VERSION = 8
```

Add a v8 block at the end of `_migrate` (after the v7 block, before the function returns):
```python
    if version < 8:
        rows = await conn.execute_fetchall("PRAGMA table_info(scheduled_tasks)")
        existing = {row["name"] for row in rows}
        if "writable" in existing and "auto_approve" not in existing:
            await conn.execute("ALTER TABLE scheduled_tasks RENAME COLUMN writable TO auto_approve")
        await _set_schema_version(conn, 8)
        await conn.commit()
        _logger.info("Migrated automation store to v8 (writable -> auto_approve)")
```

Leave `_MIGRATION_V1` and the `_LOOP_COLUMNS`/`_V5_AUTOMATION_COLUMNS` blocks referencing `writable` unchanged — they run for pre-v8 databases and v8 renames afterward. Fresh databases get `auto_approve` straight from `_SCHEMA` and the v8 block's guard skips the rename.

- [ ] **Step 4: Run to verify it passes**

Run: `cd apps/server && uv run pytest tests/<store_test>.py::test_v8_renames_writable_to_auto_approve -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/server/arden/automation/store.py apps/server/tests
git commit -m "feat(automation): v8 migration renames writable column to auto_approve"
```

---

## Task 3: Rename `writable` → `auto_approve` in store SQL + model + row mapping

**Files:**
- Modify: `apps/server/arden/automation/models.py:23`
- Modify: `apps/server/arden/automation/store.py` (columns, SQL, row mapping, save/save_with_claim/update_metadata, `set_writable`)

- [ ] **Step 1: Update the model**

`models.py:23`: `writable: bool` → `auto_approve: bool`.

- [ ] **Step 2: Update store identifiers**

In `store.py` apply these exact renames:
- `_COLUMNS` (line 232): `"writable, handler, ..."` → `"auto_approve, handler, ..."`.
- `_row_to_automation` (line 93): `writable=bool(row["writable"]),` → `auto_approve=bool(row["auto_approve"]),`.
- `save()` (line 837): `int(automation.writable),` → `int(automation.auto_approve),`.
- `save_with_claim()` (line 1123): `int(automation.writable),` → `int(automation.auto_approve),`.
- `update_metadata()` (line 932): `int(automation.writable),` → `int(automation.auto_approve),`.
- `_SQL_SET_WRITABLE` (line 307): rename to `_SQL_SET_AUTO_APPROVE` and change SQL to `UPDATE scheduled_tasks SET auto_approve = ? WHERE task_id = ?`.
- `_SQL_UPDATE_METADATA` (line 312): `writable = ?` → `auto_approve = ?`.
- `set_writable()` method (line 918): rename to `set_auto_approve(self, task_id, auto_approve)`, use `_SQL_SET_AUTO_APPROVE`, `int(auto_approve)`.

- [ ] **Step 3: Run store + model tests**

Run: `cd apps/server && uv run pytest tests/<store_test>.py -v`
Expected: PASS (round-trip + migration tests green). Fix any `AttributeError: 'Automation' has no attribute 'writable'` by updating the referencing line.

- [ ] **Step 4: Commit**

```bash
git add apps/server/arden/automation/models.py apps/server/arden/automation/store.py
git commit -m "refactor(automation): rename writable->auto_approve in model and store"
```

---

## Task 4: Rename `writable` → `auto_approve` in service + runner + scheduler + app

**Files:**
- Modify: `apps/server/arden/automation/service.py` (`toggle_writable`, `_build_metadata_changes`, `update`, `create`, `create_loop`)
- Modify: `apps/server/arden/operator/runner.py` (`RunRequest.writable`, tool-filter usage)
- Modify: `apps/server/arden/automation/scheduler.py` (`RunRequest(writable=...)`, `skip_approvals=automation.writable`)
- Modify: `apps/server/arden/server/app.py` (`RunRequest(writable=...)`, `skip_approvals=automation.writable`)

- [ ] **Step 1: Service**

In `service.py`:
- `toggle_writable` (line 77) → `toggle_auto_approve`; body uses `task.auto_approve` and `self.store.set_auto_approve(...)`.
- `_build_metadata_changes` (lines 94, 108-109): param `writable` → `auto_approve`; `changes["writable"]` → `changes["auto_approve"]`.
- `update` (line 172, 186): param `writable` → `auto_approve`; pass-through to `_build_metadata_changes(auto_approve=auto_approve, ...)`.
- `create` (param `writable: bool = False`, ~line 245; stored at ~line 304): rename param to `auto_approve`, store `auto_approve=auto_approve`.
- `create_loop` (line 397): `writable=True` → `auto_approve=True`.

- [ ] **Step 2: Runner**

In `operator/runner.py`:
- `RunRequest.writable` (line 48) → `auto_approve: bool`.
- `_prepare` tool filter (line 100): `executor.get_tools() if request.writable else executor.get_tools(read_only=True)` → `request.auto_approve`.

- [ ] **Step 3: Scheduler**

In `automation/scheduler.py`:
- `_run_agent` `RunRequest(... writable=automation.writable, ... skip_approvals=automation.writable ...)` (lines 515, 518) → `auto_approve=automation.auto_approve` and `skip_approvals=automation.auto_approve`.
- Any other `automation.writable` reference (line 518 region): rename.

- [ ] **Step 4: App dispatchers**

In `server/app.py`:
- `_dispatch_iteration` (line 114): `skip_approvals=automation.writable` → `automation.auto_approve`.
- `_dispatch_post` (lines 142, 145): `RunRequest(... writable=automation.writable ... skip_approvals=automation.writable ...)` → `auto_approve=automation.auto_approve`, `skip_approvals=automation.auto_approve`.

- [ ] **Step 5: Grep for stragglers**

Run: `cd apps/server && grep -rn "\.writable\|writable=" arden | grep -v "read_only\|read.only"`
Expected: no remaining references to the automation `writable` field (ignore unrelated `writable` words). Fix any found.

- [ ] **Step 6: Run the full backend suite**

Run: `cd apps/server && uv run pytest tests/ -x -q`
Expected: PASS. Fix references until green.

- [ ] **Step 7: Commit**

```bash
git add apps/server/arden
git commit -m "refactor(automation): rename writable->auto_approve in service/runner/scheduler/app"
```

---

## Task 5: Rename in HTTP schemas + routes + automation tool

**Files:**
- Modify: `apps/server/arden/server/schemas.py` (lines 442, 463 — `writable` fields)
- Modify: `apps/server/arden/server/routers/automation.py` (lines 37, 64, 173-179, 213)
- Modify: `apps/server/arden/tools/automation.py` (input fields lines 137, 201; usages 244, 295, 353, 391)

- [ ] **Step 1: Schemas**

In `server/schemas.py`: rename the `writable: bool = False` (line 442) and `writable: bool | None = None` (line 463) fields to `auto_approve`.

- [ ] **Step 2: Routes**

In `server/routers/automation.py`:
- Line 37: `"writable": a.writable` → `"auto_approve": a.auto_approve`.
- Line 64: `writable=request.writable` → `auto_approve=request.auto_approve`.
- Line 173-179: change the route `@router.post("/automations/{task_id}/writable")` path to `/auto-approve`, rename `toggle_writable` handler → `toggle_auto_approve`, call `svc.toggle_auto_approve(task_id)`, return `{"auto_approve": new_value}`.
- Line 213: `writable=request.writable` → `auto_approve=request.auto_approve`.

- [ ] **Step 3: Automation tool**

In `tools/automation.py`: rename the `writable` Field on the create/update input models (lines 137, 201) to `auto_approve` (update descriptions: "Allow automation to write and act without approval"), and rename usages at lines 244, 295, 353, 391. Update the description string at line 21 ("Read-only by default, set auto_approve=true ...").

- [ ] **Step 4: Run backend suite + grep**

Run: `cd apps/server && grep -rn "writable" arden | grep -vi "read_only\|read-only"` → expect none related to the field.
Run: `cd apps/server && uv run pytest tests/ -x -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/server/arden
git commit -m "refactor(api): rename writable->auto_approve in schemas, routes, automation tool"
```

---

## Task 6: Desktop rename — API types + editor + trust badge

**Files:**
- Modify: `apps/desktop/src/api.ts` (`Automation.writable` line 1067, `CreateAutomationPayload.writable` line 1093, `UpdateAutomationPayload.writable`, toggle-writable client fn + route path)
- Modify: `apps/desktop/src/components/automations/AutomationEditor.tsx` (FormState.writable lines 58/62/69/97/125/315/320-324; label + aria-label)
- Modify: `apps/desktop/src/lib/automationTrust.ts` (lines 9, 15)
- Modify: `apps/desktop/src/actions/automations.ts` / `actions/sessions.ts` (toggle action, if present)

- [ ] **Step 1: API types + client**

In `api.ts`:
- `Automation.writable: boolean` (line 1067) → `auto_approve: boolean`.
- `CreateAutomationPayload.writable?` (line 1093) → `auto_approve?`.
- `UpdateAutomationPayload.writable?` → `auto_approve?`.
- The toggle-writable client fn: rename and point at `POST /automations/{taskId}/auto-approve`, return `{ auto_approve: boolean }`.

- [ ] **Step 2: Editor**

In `AutomationEditor.tsx`: rename every `writable` (FormState field, `emptyForm`, `formFromPreset`, `formFromAutomation`, `buildPayload`, the toggle `checked`/`onChange`/`setForm`) to `auto_approve`. Change the visible label and `aria-label` (lines 322, 324) from `Writable` to `Auto-Approve`.

- [ ] **Step 3: Trust badge**

In `automationTrust.ts`:
```typescript
export function automationTrustLabel(automation: Automation): string | null {
  if (automation.handler === "knowledge_health") return "read-only";
  if (automation.handler === "knowledge_retention") return "retention";
  if (automation.handler === "knowledge_reflection") return "learns context";
  if (automation.auto_approve) return "auto-approve";
  return null;
}

export function automationTrustTone(automation: Automation): AutomationTrustTone {
  if (automation.handler?.startsWith("knowledge_")) return "neutral";
  if (automation.auto_approve) return "bad";
  return "accent";
}
```

- [ ] **Step 4: Actions + typecheck**

Update any `toggleWritable`/`writable` references in `actions/`. Then:
Run: `cd apps/desktop && grep -rn "writable" src` → expect none.
Run: `cd apps/desktop && bun run typecheck` (or the project's TS check — check `package.json` scripts).
Expected: no type errors.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src
git commit -m "refactor(desktop): rename writable->auto_approve; relabel toggle and trust badge"
```

---

## Task 7: End-to-end verification

- [ ] **Step 1: Backend suite green**

Run: `cd apps/server && uv run pytest tests/ -q`
Expected: PASS.

- [ ] **Step 2: Manual notify-in-automation check**

Start the server (`cd apps/server && uv run arden-server serve`), create an `auto_approve=true` automation whose prompt calls `notify`, with `notify→ASK` configured. Trigger it (run-now). Confirm via logs/run record that `notify` sent (no "No UI connected" rejection). In an interactive chat session, confirm `notify` still prompts for approval.

- [ ] **Step 3: Migration smoke on a real DB copy**

Copy an existing automations DB, point the server at it, start, and confirm it boots (v8 runs) and existing automations load with their `auto_approve` value preserved.

---

## Self-Review Notes

- Spec Part A coverage: rename surface (model/store/service/runner/scheduler/app/schemas/routes/tool/desktop/trust-badge) → Tasks 2-6; ASK fix → Task 1; DENY untouched (verified — handled upstream in `registry.execute`). ✓
- Migration ordering (historical migrations keep `writable`, v8 renames last; fresh DBs get `auto_approve` from `_SCHEMA`, v8 guard skips) → Task 2. ✓
- Route path `/writable` → `/auto-approve` kept consistent between server (Task 5) and desktop client (Task 6). ✓
