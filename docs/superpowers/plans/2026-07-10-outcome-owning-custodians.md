# Outcome-Owning Custodians Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each delegated Area maintain and advance durable outcomes, then present completed work, active work, and blockers as a finite Chief-of-Staff brief.

**Architecture:** A dedicated SQLite `AreaWorkStore` owns outcomes, work items, evidence events, and idempotent report application. Custodian runs receive a structured work snapshot and return one validated `AreaCustodianReport`; the server applies its operations atomically after successful completion. Area APIs and desktop projections read the same store, while the existing Area page remains the narrative artifact.

**Tech Stack:** Python 3.13, aiosqlite, Pydantic v2, FastAPI, pytest, React 19, TypeScript, Zustand, Bun test, Tailwind CSS.

## Global Constraints

- New work IDs are deterministic from `area_id` plus a model/user stable key.
- Work from one Area must never be readable or mutable through another Area.
- Omission from a model report never retires existing work; completion and cancellation are explicit operations.
- A malformed, interrupted, duplicate, or conflicting report must not partially mutate work state.
- User edits are authoritative and use `updated_at` optimistic concurrency checks.
- Home is finite and server-capped; it is not a chronological feed or kanban surface.
- Existing Area archive preserves structured work; permanent Area deletion cascades.
- Existing Observe/Act tool boundaries remain exact; no arbitrary global automation execution.
- Every implementation task follows red → green TDD and ends in a focused commit.

## Progress Ledger

- 2026-07-10: Design approved and committed as `ec08ba25`.
- 2026-07-10: Task 1 complete. `AreaWorkStore` owns constrained SQLite
  outcomes/items/events/report tables, deterministic Area-scoped IDs, optimistic
  user updates, archive preservation, and permanent-delete cascades. Evidence:
  8 store/architecture tests and focused Ruff pass. Atomic model-report
  application remains correctly scoped to Task 2.
- Current: Task 2 pending.
- Remaining: Tasks 2–7 below.

---

## File structure

- `apps/server/ntrp/areas/work_models.py`: Pydantic report operations and typed persisted records.
- `apps/server/ntrp/areas/work_store.py`: schema, queries, transactions, idempotency, and brief ranking; sole owner of `area_work_*` tables.
- `apps/server/ntrp/areas/agent.py`: Custodian prompt and the final report schema composition.
- `apps/server/ntrp/areas/custodian.py`: progress-aware cadence and continuation scheduling.
- `apps/server/ntrp/server/runtime/automation.py`: inject snapshots and apply completed reports.
- `apps/server/ntrp/areas/service.py`: synchronous Area detail/overview projection over hydrated work snapshots.
- `apps/server/ntrp/server/routers/areas.py`: typed user work mutations.
- `apps/desktop/src/features/areas/components/AreaWork.tsx`: compact work display and inline editing.
- `apps/desktop/src/features/home/components/WorkBrief.tsx`: bounded Done/In progress sections.

---

### Task 1: Durable Area work store

**Files:**
- Create: `apps/server/ntrp/areas/work_models.py`
- Create: `apps/server/ntrp/areas/work_store.py`
- Modify: `apps/server/ntrp/server/stores.py`
- Test: `apps/server/tests/test_area_work_store.py`
- Modify: `docs/superpowers/plans/2026-07-10-outcome-owning-custodians.md`

**Interfaces:**
- Produces: `AreaWorkStore.init_schema()`, `snapshot(area_id)`, `create_outcome(...)`, `update_outcome(...)`, `create_work_item(...)`, and `update_work_item(...)`.
- Produces: `AreaWorkSnapshot`, `AreaOutcome`, `AreaWorkItem`, `AreaWorkEvent`, `OutcomeChange`, `WorkChange`, `EvidenceDraft`.

- [x] **Step 1: Write failing schema and isolation tests**

Add tests that create two Areas and prove deterministic stable keys, unique
`(area_id, stable_key)`, archive preservation, cascade on permanent deletion,
and cross-Area updates returning `None` rather than touching a foreign row.

```python
created = await work.create_outcome(
    "area_a", key="submit", title="Petition submitted",
    success_criteria="Receipt notice exists", priority=5, source="user",
)
assert created.outcome_id == "outcome:area_a:submit"
assert (await work.snapshot("area_b")).outcomes == []
assert await work.update_outcome("area_b", "submit", title="wrong") is None
```

- [x] **Step 2: Run the new store tests and verify red**

Run: `cd apps/server && uv run pytest tests/test_area_work_store.py -q`  
Expected: collection fails because `ntrp.areas.work_store` does not exist.

- [x] **Step 3: Implement models and store schema**

Create three owned tables plus an applied-report table. Use checks for every
enum and foreign keys back to `areas(area_id)`.

```python
class AreaWorkStore:
    async def snapshot(self, area_id: str) -> AreaWorkSnapshot: ...
    async def create_outcome(
        self, area_id: str, *, key: str, title: str,
        success_criteria: str, priority: int, source: str,
    ) -> AreaOutcome: ...
    async def update_outcome(
        self, area_id: str, key: str, *, expected_updated_at: str | None = None,
        **patch: object,
    ) -> AreaOutcome | None: ...
```

`Stores.connect()` constructs and initializes `AreaWorkStore(conn, read_conn)`
after `SessionStore`, exposes it as `stores.area_work`, and keeps table ownership
out of `context/store.py`.

- [x] **Step 4: Add isolation, cascade, and optimistic-conflict tests**

Prove a stale `expected_updated_at` raises `AreaWorkConflict`, a current token
succeeds, archive preserves work, and permanent deletion cascades. Atomic
multi-operation rollback is tested with `apply_report` in Task 2.

- [x] **Step 5: Run store and architecture tests to green**

Run: `cd apps/server && uv run pytest tests/test_area_work_store.py tests/test_architecture_boundaries.py -q`  
Expected: all pass.

- [x] **Step 6: Update this ledger and commit**

Commit: `feat(server): add durable area work store`

---

### Task 2: Atomic structured Custodian reports

**Files:**
- Modify: `apps/server/ntrp/areas/work_models.py`
- Modify: `apps/server/ntrp/areas/work_store.py`
- Modify: `apps/server/ntrp/areas/agent.py`
- Modify: `apps/server/ntrp/automation/output_schemas.py`
- Modify: `apps/server/ntrp/server/runtime/automation.py`
- Test: `apps/server/tests/test_areas_agent.py`
- Test: `apps/server/tests/test_area_work_store.py`
- Test: `apps/server/tests/test_areas_runtime.py`
- Modify: `docs/superpowers/plans/2026-07-10-outcome-owning-custodians.md`

**Interfaces:**
- Consumes: `AreaWorkStore.apply_report(area_id, run_ref, report)` and `snapshot(area_id)`.
- Produces: `AreaCustodianReport` registered as output schema `area_custodian`.
- Produces: runtime order `apply work report → reconcile asks → schedule → emit areas_changed`.

- [ ] **Step 1: Write failing report validation tests**

Cover create/update/complete/cancel operations, required create fields, key
format, maximum operation counts, unknown references, and duplicate keys.

```python
report = AreaCustodianReport.model_validate({
    "outcome_changes": [{
        "op": "create", "key": "submit", "title": "Petition submitted",
        "success_criteria": "Receipt notice exists", "priority": 5,
    }],
    "work_changes": [{
        "op": "create", "key": "collect-evidence", "outcome_key": "submit",
        "kind": "action", "text": "Collect final exhibits", "owner": "custodian",
    }],
    "evidence": [], "asks": [], "report": "Started evidence collection",
    "made_progress": True, "work_remaining": True,
    "next_check_hours": 24, "next_check_reason": "continue evidence review",
})
```

- [ ] **Step 2: Verify report tests fail**

Run: `cd apps/server && uv run pytest tests/test_areas_agent.py tests/test_area_work_store.py -q`  
Expected: failures for missing `AreaCustodianReport` and `apply_report` behavior.

- [ ] **Step 3: Implement explicit report operations**

Use discriminated operation models. Create operations require complete content;
update/complete/cancel require an existing stable key. `apply_report` validates
all references before opening a savepoint, inserts `run_ref` exactly once, and
appends indexed evidence events with `UNIQUE(run_ref, operation_index)`.

- [ ] **Step 4: Replace `area_ask` runtime output with `area_custodian`**

Keep `area_ask` as a read-compatible registry alias, but reconcile every live
Area automation to `output_schema="area_custodian"`. In the completed-run hook,
validate/apply work before asks. A `None` structured output leaves both stores
unchanged.

- [ ] **Step 5: Prove idempotency and failure ordering**

Tests deliver the same `run_ref` twice and assert one event; inject an unknown
work key and assert no outcome, work item, ask, or schedule mutation occurred.

- [ ] **Step 6: Run focused tests and commit**

Run: `cd apps/server && uv run pytest tests/test_areas_agent.py tests/test_area_work_store.py tests/test_areas_runtime.py -q`  
Commit: `feat(server): reconcile custodian work reports`

---

### Task 3: Progress-aware execution and responsiveness

**Files:**
- Modify: `apps/server/ntrp/areas/agent.py`
- Modify: `apps/server/ntrp/areas/custodian.py`
- Modify: `apps/server/ntrp/server/app.py`
- Modify: `apps/server/ntrp/server/runtime/automation.py`
- Test: `apps/server/tests/test_areas_custodian.py`
- Test: `apps/server/tests/test_areas_runtime.py`
- Modify: `docs/superpowers/plans/2026-07-10-outcome-owning-custodians.md`

**Interfaces:**
- Consumes: `AreaWorkStore.snapshot(area_id)`.
- Produces: `render_work_context(snapshot) -> str` and progress-aware `record_run(...) -> datetime`.
- `AreaCustodianReport.continuation_minutes`: optional integer from 5–240.

- [ ] **Step 1: Write failing context and cadence tests**

Prove each run receives current outcomes/actions/blockers, progress resets quiet
decay, executable remaining work can request a 5-minute continuation, waiting
work uses the normal heartbeat, and budget exhaustion refuses the short retry.

```python
nxt = store.record_run(
    "area_a", report.model_dump(), attention="ambient", now=NOW,
)
assert nxt == NOW + timedelta(minutes=5)
```

- [ ] **Step 2: Verify red**

Run: `cd apps/server && uv run pytest tests/test_areas_custodian.py tests/test_areas_runtime.py -q`  
Expected: continuation and structured work context assertions fail.

- [ ] **Step 3: Implement work context and multi-step prompt**

Before dispatch, append a bounded JSON `CURRENT AREA WORK` block to the wake
context. Update the standing prompt to select the highest-leverage unblocked
action, use multiple tool calls, stop only after progress/completion/blockage,
and emit explicit operations plus evidence.

- [ ] **Step 4: Implement progress-aware scheduling**

`made_progress=True` resets quiet streak. Honor `continuation_minutes` only when
`work_remaining=True`, the value is within 5–240, the Area is not paused, and
another autonomous run remains in today's attention cap. Otherwise use the
existing attention-clamped heartbeat.

- [ ] **Step 5: Run focused tests and commit**

Run: `cd apps/server && uv run pytest tests/test_areas_custodian.py tests/test_areas_runtime.py -q`  
Commit: `feat(server): let custodians continue useful work`

---

### Task 4: Canonical work projections and typed APIs

**Files:**
- Modify: `apps/server/ntrp/areas/service.py`
- Modify: `apps/server/ntrp/server/app.py`
- Modify: `apps/server/ntrp/server/routers/areas.py`
- Modify: `apps/server/ntrp/server/schemas.py`
- Test: `apps/server/tests/test_areas_service.py`
- Test: `apps/server/tests/test_areas_router.py`
- Modify: `docs/superpowers/plans/2026-07-10-outcome-owning-custodians.md`

**Interfaces:**
- Consumes: hydrated `AreaWorkSnapshot` and `AreaWorkStore.brief(...)`.
- Produces: `AreaDetail.work` and `AreasOverview.brief`.
- Produces endpoints `POST /areas/{area_id}/outcomes`, `PATCH /areas/{area_id}/outcomes/{key}`, and `PATCH /areas/{area_id}/work/{key}`.

- [ ] **Step 1: Write failing detail, brief, and mutation tests**

Assert Home data is ranked/capped as: recent material completions ≤6, one active
item per Area ≤6, and existing question/review asks ≤4. Assert archived Areas
never appear. API tests cover create/edit/complete/pause/resume/cancel and stale
`expected_updated_at` returning HTTP 409.

- [ ] **Step 2: Verify red**

Run: `cd apps/server && uv run pytest tests/test_areas_service.py tests/test_areas_router.py -q`  
Expected: missing `work`, `brief`, and endpoints.

- [ ] **Step 3: Hydrate one canonical work snapshot**

Extend the existing async `hydrate_area_snapshot()` to fetch work once per
request and feed synchronous `AreaService` closures. `overview()` returns:

```python
{
    "areas": summaries,
    "focus": needs_you,
    "brief": {"done": done, "in_progress": active, "needs_you": needs_you},
}
```

- [ ] **Step 4: Implement typed user mutations**

Pydantic bodies constrain keys, states, priority, and timestamps. Every endpoint
first verifies the active Area, mutates `AreaWorkStore`, emits `areas_changed`,
and requests an Area wake describing the authoritative user edit.

- [ ] **Step 5: Run focused tests and commit**

Run: `cd apps/server && uv run pytest tests/test_areas_service.py tests/test_areas_router.py tests/test_area_work_store.py -q`  
Commit: `feat(server): expose area outcomes and work brief`

---

### Task 5: Desktop Area work state and editor

**Files:**
- Modify: `apps/desktop/src/api/areas.ts`
- Modify: `apps/desktop/src/actions/areas.ts`
- Create: `apps/desktop/src/features/areas/components/AreaWork.tsx`
- Modify: `apps/desktop/src/features/areas/components/AreaRoom.tsx`
- Test: `apps/desktop/tests/areaWork.test.tsx`
- Test: `apps/desktop/tests/areaActions.test.ts`
- Modify: `docs/superpowers/plans/2026-07-10-outcome-owning-custodians.md`

**Interfaces:**
- Consumes: server `AreaWorkSnapshot` and typed mutation responses.
- Produces: `AreaWork` component and actions `createAreaOutcome`, `updateAreaOutcome`, `updateAreaWorkItem`.

- [ ] **Step 1: Write failing component and action tests**

Render a primary outcome, current Custodian action, user blocker, and collapsed
remaining work. Exercise add, title edit, complete, pause/resume, and cancel;
assert encoded API paths, optimistic timestamp body, refetched room, and overview.

- [ ] **Step 2: Verify red**

Run: `cd apps/desktop && bun test tests/areaWork.test.tsx tests/areaActions.test.ts`  
Expected: missing types, actions, and component.

- [ ] **Step 3: Implement API types/actions**

Add exact TypeScript unions matching server enums. Mutations update through the
API then refetch both `fetchAreaDetail(key)` and `fetchAreasOverview()`; do not
invent an optimistic parallel work store.

- [ ] **Step 4: Implement compact Area Work UI**

Render current work before `OpenLoops`. Use inline text inputs only while
editing, small status actions, and a native collapsed details section for
remaining outcomes/loops. No board, drag/drop, dependency editor, or modal.

- [ ] **Step 5: Run desktop focused gates and commit**

Run: `cd apps/desktop && bun test tests/areaWork.test.tsx tests/areaActions.test.ts && bun run typecheck && bun run lint`  
Commit: `feat(desktop): make area outcomes editable`

---

### Task 6: Finite Chief-of-Staff Home brief

**Files:**
- Modify: `apps/desktop/src/api/areas.ts`
- Create: `apps/desktop/src/features/home/components/WorkBrief.tsx`
- Modify: `apps/desktop/src/features/home/components/Home.tsx`
- Modify: `apps/desktop/src/features/home/components/FocusRow.tsx`
- Test: `apps/desktop/tests/homeWorkBrief.test.tsx`
- Modify: `docs/superpowers/plans/2026-07-10-outcome-owning-custodians.md`

**Interfaces:**
- Consumes: `AreasOverview.brief.done`, `.in_progress`, and `.needs_you`.
- Produces: bounded Home sections that route every row to its Area room.

- [ ] **Step 1: Write failing ordering and empty-state tests**

Prove order is Done for you → In progress → Needs you, server order is
preserved, no section renders when empty, rows open their Area, and the final
“That’s it for today.” appears exactly once even when all sections are empty.

- [ ] **Step 2: Verify red**

Run: `cd apps/desktop && bun test tests/homeWorkBrief.test.tsx`  
Expected: missing `WorkBrief` and `brief` contract.

- [ ] **Step 3: Implement the finite brief**

Completed rows show the evidence summary and Area name without requiring an
acknowledgement. In-progress rows show the outcome and concrete current action.
Needs-you retains the typed ask controls. Avoid timestamps and metadata unless
they change the user's next action.

- [ ] **Step 4: Run focused desktop gates and commit**

Run: `cd apps/desktop && bun test tests/homeWorkBrief.test.tsx tests/areasDomain.test.ts && bun run typecheck && bun run lint`  
Commit: `feat(desktop): add chief of staff work brief`

---

### Task 7: Migration, full verification, and completion audit

**Files:**
- Modify: `apps/server/ntrp/areas/work_store.py`
- Modify: `docs/superpowers/plans/2026-07-10-outcome-owning-custodians.md`
- Test: all server and desktop suites

**Interfaces:**
- Consumes every prior task.
- Produces an idempotent deployed schema and closed requirement ledger.

- [ ] **Step 1: Add migration and restart tests**

Initialize the schema twice over an existing Areas database, apply a report,
reopen both connections, and prove outcomes/events/brief survive with no
duplicates. Archive and restore the Area and prove work returns unchanged.

- [ ] **Step 2: Run all focused Area/Custodian tests**

Run: `cd apps/server && uv run pytest tests/test_area_work_store.py tests/test_area_tools.py tests/test_areas_agent.py tests/test_areas_asks.py tests/test_areas_custodian.py tests/test_areas_lifecycle.py tests/test_areas_migration.py tests/test_areas_router.py tests/test_areas_service.py tests/test_areas_runtime.py -q`

- [ ] **Step 3: Run full server gates**

Run: `cd apps/server && uv run pytest -q && uv run ruff check ntrp tests`  
Expected: all tests and Ruff pass.

- [ ] **Step 4: Run full desktop gates**

Run: `cd apps/desktop && bun test && bun run typecheck && bun run lint && bun run build`  
Expected: all tests, typecheck, lint, and production build pass; existing
non-failing React `act()` and Vite chunk-size warnings may remain.

- [ ] **Step 5: Audit every design requirement**

For every Verification bullet in the design, record the exact test/file
evidence in the Progress Ledger. Search changed files for `TODO`, `FIXME`,
unresolved placeholders, unrestricted tool scopes, and duplicate work stores.

- [ ] **Step 6: Commit the closed ledger**

Commit: `docs: close outcome custodian implementation ledger`
