# Areas and Custodians Rebuild Implementation Plan

> **For agentic workers:** Execute inline in this session. Steps use checkbox
> (`- [ ]`) syntax for durable tracking. Do not use subagents unless the user
> explicitly changes the current instruction.

**Goal:** Rebuild Areas into a canonical durable domain boundary, then rebuild
Custodians as safe, self-paced delegates confined to that boundary.

**Architecture:** `AreaLifecycleService` owns cross-store Area transitions.
SQLite remains canonical for Area identity and membership; page and custodian
state are capabilities keyed by `area_id`. The desktop normalizes Area records
once and treats overview/detail as keyed projections. Custodian page mutation
uses dedicated Area-locked tools and durable write provenance.

**Tech Stack:** Python 3.13, FastAPI, aiosqlite, Pydantic, pytest, React 19,
TypeScript, Zustand, Bun.

## Global Constraints

- Preserve migrated `proj_*` IDs; mint new `area_*` IDs.
- No keyword/regex heuristics for event routing or contextual suggestions.
- Keep diffs inside Areas/Custodians and directly required shared contracts.
- Use red-green-refactor for every behavior change.
- Do not preserve behavior identified as unsafe or semantically wrong.
- Do not expose unsupported source controls in the UI.

---

## Progress ledger

Update this section after every completed task.

- 2026-07-10: Audited current branch and research. Confirmed unsafe page paths,
  destructive archive, duplicated desktop state, incomplete Area promotion,
  stale mechanical asks, unbounded observe tools, unsynchronized autonomy,
  missing immediate provisioning, lossy ask replacement, cap bypass, and
  heuristic self-echo suppression.
- 2026-07-10: Rebuild design and executable plan written before implementation.
- 2026-07-10: Task 1 complete. New Areas mint `area_*`; active names are
  casefold-unique; page ownership is unique across active/archived Areas;
  archive preserves chat membership; restore is implemented. Evidence: 73
  storage/capability tests and focused Ruff pass.
- 2026-07-10: Task 2 complete. `AreaLifecycleService` now owns create/update,
  live delegation sync, pause, archive, and restore; page attachment no longer
  silently delegates; failed runtime updates restore persisted state. Evidence:
  70 focused Areas tests and Ruff pass.
- 2026-07-10: Task 3 complete. One containment resolver protects all Area page
  reads; absolute/traversal/symlink escapes fail closed; explicit create and
  detach page operations are lifecycle-backed. Evidence: 47 focused tests and
  Ruff pass.
- 2026-07-10: Task 4 complete. Every active Area projects into Home/rooms;
  archived-area asks are filtered; mechanical approval and automation-failure
  asks reconcile against canonical state and retire when cleared. Evidence: 49
  focused projection/ask tests and Ruff pass.
- 2026-07-10: Task 5 complete. Desktop Area records are normalized under one
  domain; create/update/archive/restore/page/delegation reconcile projections;
  every room offers page creation and explicit delegation; settings can disable
  delegation and detach a page. Evidence: 62 server tests, 31 desktop tests,
  typecheck, ESLint, and Ruff pass (two pre-existing React `act()` warnings in
  `askCardOpenPage.test.tsx`).
- 2026-07-10: Task 6 complete. Dedicated Area-page read/patch/write tools are
  locked to the execution Area and preserve frontmatter; observe scope contains
  no global memory writes and includes Area-scoped transcript reads for intake.
  Evidence: 71 tool/scope tests and Ruff pass.
- 2026-07-10: Task 7 complete. Boot and live updates share one idempotent
  reconciliation path; observe and act use exact allowlists; neither mode
  bypasses tool approval policy. Evidence: 18 lifecycle/permission tests and
  Ruff pass.
- 2026-07-10: Task 8 complete. All autonomous Custodian dispatches share the
  attention cap while manual runs bypass it; state writes are atomic and
  malformed legacy state recovers; exact page digests suppress only the
  matching self-write event. Evidence: 74 runtime/scheduling tests and Ruff
  pass.
- 2026-07-10: Task 9 complete. Agent asks use stable keys, quiet or malformed
  runs preserve unresolved decisions, repeated nominations update without
  re-notifying, resolutions carry explicit metadata, and replies dispatch to
  the linked Custodian channel. Evidence: 44 server tests, 4 desktop tests,
  typecheck, ESLint, and Ruff pass (two pre-existing React `act()` warnings).
- 2026-07-10: Task 10 complete. Legacy Custodian IDs migrate in their owning
  stores; the room distinguishes paused, running, unavailable, failed, and
  scheduled states; Acting is confined to Area-owned child automations. Final
  evidence: 1,422 server tests, 575 desktop tests, repo-wide Ruff, desktop
  typecheck, ESLint, and production build pass. Existing non-failing warnings:
  React `act()` test warnings, pytest asyncio/thread cleanup warnings, and the
  Vite chunk-size advisory.
- Current: complete.
- Remaining: none.

---

### Task 1: Enforce Area storage invariants

**Files:**
- Modify: `apps/server/arden/context/store.py`
- Modify: `apps/server/arden/services/session.py`
- Test: `apps/server/tests/test_session_store.py`
- Test: `apps/server/tests/test_area_capabilities.py`

**Produces:** Safe `area_*` creation, casefold uniqueness, normalized unique
page attachment, reversible archive/restore.

- [x] Add failing tests proving new IDs use `area_`, duplicate active names are
  rejected case-insensitively, duplicate page ownership is rejected, archive
  preserves session `area_id`, and restore returns the Area.
- [x] Run each new test and confirm it fails for the intended old behavior.
- [x] Add normalized Area-name and page-path columns/index migration where
  needed; validate page paths as relative `.md` paths without `..`.
- [x] Change `create_area`, `update_area`, and archive semantics; add
  `restore_area(area_id)`.
- [x] Run focused storage tests and refactor only after green.

### Task 2: Add a transactional Area lifecycle boundary

**Files:**
- Create: `apps/server/arden/areas/lifecycle.py`
- Modify: `apps/server/arden/server/runtime/automation.py`
- Modify: `apps/server/arden/server/routers/areas.py`
- Modify: `apps/server/arden/server/app.py`
- Test: `apps/server/tests/test_areas_lifecycle.py`
- Test: `apps/server/tests/test_areas_router.py`

**Produces:** `AreaLifecycleService` methods `create`, `update`, `delegate`,
`pause`, `archive`, and `restore`, with runtime compensation.

- [x] Write failing tests for immediate agent provisioning, rename propagation,
  archive disable, restore enable, and failed-runtime compensation.
- [x] Verify failures before creating the service.
- [x] Route every Area mutation through the lifecycle service.
- [x] Remove router-owned automation side effects.
- [x] Run lifecycle/router tests to green.

### Task 3: Secure and expose the page capability

**Files:**
- Create: `apps/server/arden/areas/paths.py`
- Modify: `apps/server/arden/areas/context.py`
- Modify: `apps/server/arden/server/app.py`
- Modify: `apps/server/arden/server/routers/areas.py`
- Modify: `apps/server/arden/server/schemas.py`
- Test: `apps/server/tests/test_area_capabilities.py`
- Test: `apps/server/tests/test_areas_router.py`

**Produces:** `resolve_area_page(vault_root, page_path)` containment check and
create/attach/detach page lifecycle endpoints.

- [x] Write failing traversal, absolute-path, symlink-escape, duplicate attach,
  create-page, and delegated-detach tests.
- [x] Verify every security test fails on the old resolver.
- [x] Implement one containment resolver used by prompt context, room reads, and
  page writes.
- [x] Add page create/attach/detach lifecycle operations.
- [x] Run focused capability tests to green.

### Task 4: Make Area projections canonical and mechanical asks truthful

**Files:**
- Modify: `apps/server/arden/areas/models.py`
- Modify: `apps/server/arden/areas/service.py`
- Modify: `apps/server/arden/areas/asks.py`
- Modify: `apps/server/arden/server/app.py`
- Test: `apps/server/tests/test_areas_service.py`
- Test: `apps/server/tests/test_areas_asks.py`

**Produces:** Every active Area appears in overview; mechanical asks reconcile
against current approvals/run failures and retire when resolved.

- [x] Write failing tests for plain Area visibility, archived-ask filtering,
  approval retirement, and canonical automation failure detection.
- [x] Verify red.
- [x] Replace additive `refresh_mechanical` with keyed reconciliation.
- [x] Read failure state from canonical automation-run records.
- [x] Run projection/ask tests to green.

### Task 5: Normalize desktop Area state and lifecycle UX

**Files:**
- Modify: `apps/desktop/src/stores/areas-domain.ts`
- Modify: `apps/desktop/src/stores/index.ts`
- Modify: `apps/desktop/src/stores/types.ts`
- Modify: `apps/desktop/src/actions/areas.ts`
- Modify: `apps/desktop/src/actions/sessions.ts`
- Modify: `apps/desktop/src/api/areas.ts`
- Modify: `apps/desktop/src/api/sessions.ts`
- Modify: `apps/desktop/src/features/sessions/components/AreaSettingsModal.tsx`
- Modify: `apps/desktop/src/features/areas/components/AreaRoom.tsx`
- Test: `apps/desktop/tests/areasDomain.test.ts`
- Test: `apps/desktop/tests/areaActions.test.ts`

**Produces:** One `recordsById` Area source, reconciled overview/detail, restore,
and page capability setup in every room.

- [x] Write failing reducer/action tests for create, rename, archive, restore,
  open-room invalidation, and SSE reconciliation.
- [x] Verify red.
- [x] Remove root `areaRecords` and migrate consumers to the Area domain.
- [x] Add page create/attach/detach controls using existing UI primitives.
- [x] Run focused tests, typecheck, and lint to green.

### Task 6: Add Area-locked page and transcript tools

**Files:**
- Create: `apps/server/arden/tools/area.py`
- Modify: `apps/server/arden/integrations/core.py`
- Modify: `apps/server/arden/areas/agent.py`
- Modify: `apps/server/arden/server/runtime/core.py`
- Test: `apps/server/tests/test_area_tools.py`
- Test: `apps/server/tests/test_areas_agent.py`

**Produces:** `area_page_read`, `area_page_patch`, `area_page_write`, and an
observe allowlist containing Area-scoped transcript reads but no global writes.

- [x] Write failing tests proving traversal and cross-Area writes are impossible,
  observe excludes all global memory mutation, and intake can list/read recent
  Area chats.
- [x] Verify red.
- [x] Implement dedicated tools using the active execution Area only.
- [x] Replace wildcard observe scope with an exact allowlist.
- [x] Run tool/agent tests to green.

### Task 7: Rebuild live delegation permissions and provisioning

**Files:**
- Modify: `apps/server/arden/areas/lifecycle.py`
- Modify: `apps/server/arden/server/runtime/automation.py`
- Modify: `apps/server/arden/server/routers/areas.py`
- Test: `apps/server/tests/test_areas_lifecycle.py`
- Test: `apps/server/tests/test_areas_agent.py`

**Produces:** Immediate create/update/revoke of the exact automation contract.

- [x] Write failing tests showing observe→act and act→observe synchronously
  update description, tool scope, and approval behavior.
- [x] Verify downgrade test fails on the current branch.
- [x] Implement one idempotent `sync_custodian(area)` operation used at boot and
  by live lifecycle calls.
- [x] Define a bounded act allowlist; never use `tool_scope=None` as autonomy.
- [x] Run lifecycle/permission tests to green.

### Task 8: Rebuild scheduling, budgets, and write provenance

**Files:**
- Modify: `apps/server/arden/areas/custodian.py`
- Modify: `apps/server/arden/tools/area.py`
- Modify: `apps/server/arden/server/app.py`
- Modify: `apps/server/arden/server/runtime/automation.py`
- Test: `apps/server/tests/test_areas_custodian.py`
- Test: `apps/server/tests/test_area_tools.py`

**Produces:** Atomic custodian state, all-path autonomous cap enforcement, and
digest-based self-write suppression.

- [x] Write failing tests for heartbeat cap, event cap, manual bypass, atomic
  recovery, matching self-write suppression, and immediate external edits.
- [x] Verify red.
- [x] Move budget gating before autonomous dispatch.
- [x] Record exact post-write digests from Area page tools and consume only a
  matching watcher event.
- [x] Delete time-window self-echo logic.
- [x] Run scheduling/provenance tests to green.

### Task 9: Rebuild durable asks and reply/review flows

**Files:**
- Modify: `apps/server/arden/areas/agent.py`
- Modify: `apps/server/arden/areas/asks.py`
- Modify: `apps/server/arden/areas/models.py`
- Modify: `apps/server/arden/server/routers/areas.py`
- Modify: `apps/desktop/src/features/areas/components/AskCard.tsx`
- Modify: `apps/desktop/src/features/areas/components/AreaRoom.tsx`
- Modify: `apps/desktop/src/actions/areas.ts`
- Test: `apps/server/tests/test_areas_agent.py`
- Test: `apps/server/tests/test_areas_asks.py`
- Test: `apps/server/tests/test_areas_router.py`
- Test: `apps/desktop/tests/askCardOpenPage.test.tsx`

**Produces:** Stable ask keys, durable decisions, explicit resolution events,
Custodian-channel replies, and deduplicated notifications.

- [x] Write failing tests proving quiet/malformed runs preserve decisions,
  repeated nominations update instead of re-push, reply targets the Custodian
  channel, and approve/reject state is explicit.
- [x] Verify red.
- [x] Reconcile nominations by stable key and persist resolution metadata.
- [x] Add ask reply endpoint which writes a linked user message to the channel.
- [x] Wire desktop buttons to typed reply/resolve APIs.
- [x] Run server and desktop ask tests to green.

### Task 10: Final UX, migration, and completion audit

**Files:**
- Modify: `apps/desktop/src/features/areas/components/AreaControls.tsx`
- Modify: `apps/desktop/src/features/areas/components/AreaRoom.tsx`
- Modify: `apps/server/arden/areas/migrate.py`
- Modify: `docs/superpowers/plans/2026-07-10-areas-custodians-rebuild.md`
- Test: all relevant server/desktop tests

**Produces:** Honest liveness/error/budget UI, idempotent migration, and a closed
requirement ledger.

- [x] Add failing migration and UI tests for repaired legacy state and honest
  unavailable/paused/error states.
- [x] Verify red, implement, and return to green.
- [x] Run full server pytest and Ruff gates.
- [x] Run full desktop tests, typecheck, lint, and build.
- [x] Audit every design invariant against code/tests and record evidence in the
  progress ledger.
- [x] Review the final diff for unrelated changes and unresolved placeholders.
