# Context and Evidence Inspector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-turn Context inspector and a collapsed-by-default proof summary backed by durable run evidence.

**Architecture:** Resolve turns to existing run sidecars on demand; hydrate persisted tool outcomes into history so inline summaries survive reloads; derive compact summaries locally from the transcript. Add a third tab to the existing right inspector and fetch full sidecars only while that tab is visible.

**Tech Stack:** FastAPI, Pydantic, SQLite/aiosqlite, React, Zustand, TypeScript, Bun tests.

## Global Constraints

- Commit directly to `main`; stage only files and hunks owned by this feature.
- Do not expose context contents or private reasoning.
- Do not add persistence, events, dependencies, dashboards, filtering, search, or editing.
- Do not overwrite unrelated dirty desktop or memory work.
- Proof language must not claim verification from a receipt alone.

---

### Task 1: Exact turn lookup and durable outcome hydration

**Files:**
- Modify: `apps/server/arden/context/store.py`
- Modify: `apps/server/arden/server/schemas.py`
- Modify: `apps/server/arden/server/routers/session.py`
- Modify: `apps/server/tests/test_session_store.py`
- Modify: `apps/server/tests/test_session_runtime_snapshot.py`

**Interfaces:**
- Produces: `SessionStore.get_run_sidecars_for_turn(*, session_id: str, turn_id: str) -> dict | None`
- Produces: `SessionStore.list_tool_call_outcomes(*, session_id: str, tool_call_ids: list[str]) -> dict[str, dict]`
- Produces: `GET /sessions/{session_id}/turns/{turn_id}/inspector -> TurnInspectorResponse | null`
- Extends: history tool calls with optional `outcome`.

- [ ] **Step 1: Write failing store tests**

Add tests proving initiating turns resolve through `chat_runs.client_id`, ingested queued turns resolve through `chat_queued_messages.run_id`, `meta-user-{run_id}` resolves directly, and wrong-session/missing turns return `None`.

```python
sidecar = await store.get_run_sidecars_for_turn(session_id="s-1", turn_id="turn-1")
assert sidecar and sidecar["run_id"] == "run-1"
assert await store.get_run_sidecars_for_turn(session_id="s-2", turn_id="turn-1") is None
```

Add one bulk hydration test:

```python
outcomes = await store.list_tool_call_outcomes(session_id="s-1", tool_call_ids=["call-1"])
assert outcomes["call-1"]["status"] == "succeeded"
```

- [ ] **Step 2: Run the focused store tests and verify RED**

Run: `uv run pytest tests/test_session_store.py -k 'sidecars_for_turn or tool_call_outcomes' -q`

Expected: FAIL because both methods are absent.

- [ ] **Step 3: Implement exact lookup and bounded bulk hydration**

Use exact session-scoped SQL. Require queued rows to be `ingested`; strip `meta-user-` only for the direct run lookup. Deduplicate tool IDs, chunk `IN` queries below SQLite's parameter limit, and return only non-null outcomes.

```python
async def get_run_sidecars_for_turn(self, *, session_id: str, turn_id: str) -> dict | None:
    run_id = await self._resolve_run_id_for_turn(session_id=session_id, turn_id=turn_id)
    if run_id is None:
        return None
    sidecar = await self.get_run_sidecars(run_id)
    return sidecar if sidecar and sidecar["session_id"] == session_id else None
```

- [ ] **Step 4: Write failing router/history tests**

Test the nullable endpoint for exact, missing, and cross-session cases. Add a history test whose persisted assistant tool call receives its durable `ToolOutcome` in `tool_calls[0]["outcome"]`.

- [ ] **Step 5: Run the router/history tests and verify RED**

Run: `uv run pytest tests/test_session_runtime_snapshot.py -k 'turn_inspector or history_hydrates_tool_outcomes' -q`

Expected: FAIL because the endpoint/schema and history field do not exist.

- [ ] **Step 6: Add typed response models, endpoint, and history hydration**

Define explicit Pydantic models for context entries, sources, approvals, effects, receipts, checks, limitations, and the enclosing response. Collect tool-call IDs for the page, bulk-load outcomes once, and pass the mapping into `_history_tool_calls`.

```python
@router.get(
    "/sessions/{session_id}/turns/{turn_id}/inspector",
    response_model=TurnInspectorResponse | None,
)
async def get_turn_inspector(...):
    return await svc.store.get_run_sidecars_for_turn(session_id=session_id, turn_id=turn_id)
```

- [ ] **Step 7: Run focused server tests**

Run: `uv run pytest tests/test_session_store.py tests/test_session_runtime_snapshot.py -q`

Expected: PASS.

- [ ] **Step 8: Commit Task 1**

```bash
git add apps/server/arden/context/store.py apps/server/arden/server/schemas.py apps/server/arden/server/routers/session.py apps/server/tests/test_session_store.py apps/server/tests/test_session_runtime_snapshot.py
git commit -m "feat: expose durable turn evidence"
```

---

### Task 2: Desktop data contracts and proof aggregation

**Files:**
- Create: `apps/desktop/src/api/turnInspector.ts`
- Create: `apps/desktop/src/features/context/lib/turnProof.ts`
- Modify: `apps/desktop/src/api/chat.ts`
- Modify: `apps/desktop/src/stores/transcript-projection.ts`
- Modify: `apps/desktop/src/stores/types.ts`
- Modify: `apps/desktop/src/stores/index.ts`
- Test: `apps/desktop/tests/turnProof.test.ts`
- Test: `apps/desktop/tests/sourceInspector.test.ts`
- Test: `apps/desktop/tests/historyResponse.test.ts`

**Interfaces:**
- Produces: `getTurnInspector(config, sessionId, turnId): Promise<TurnInspector | null>`
- Produces: `turnProofSummary(messages, order, turnId): TurnProofSummary | null`
- Produces: `latestInspectableTurnId(messages, order): string | null`
- Produces: `openContextForTurn(turnId: string): void`

- [ ] **Step 1: Write failing pure aggregation tests**

Cover empty suppression, action/check/source counts, failed/denied/uncertain attention precedence, bounded expanded rows, and latest visible turn selection.

```typescript
expect(turnProofSummary(messages, order, "user-1")).toMatchObject({
  tone: "attention",
  actionCount: 1,
  limitationCount: 1,
});
```

- [ ] **Step 2: Run aggregation tests and verify RED**

Run: `bun test tests/turnProof.test.ts`

Expected: FAIL because the helper does not exist.

- [ ] **Step 3: Implement pure aggregation**

Build the turn using `messageSegments` and `visibleMessageIds`. Aggregate `ActivityItem.outcome` and `sourceRefs`, dedupe sources, and cap each expanded group at five rows. Return `null` unless there is a source, effect, receipt, check, limitation, or non-success status.

```typescript
export type TurnProofTone = "recorded" | "attention";

export interface TurnProofSummary {
  tone: TurnProofTone;
  actionCount: number;
  checkCount: number;
  sourceCount: number;
  limitationCount: number;
  actions: ProofAction[];
  checks: ProofCheck[];
  limitations: ProofLimitation[];
}
```

- [ ] **Step 4: Write failing API/store/history tests**

Test normalization of malformed optional fields and row caps, Context-tab selection/reset, panel opening, and `HistoryToolCall.outcome -> ActivityItem.outcome` hydration.

- [ ] **Step 5: Run the focused tests and verify RED**

Run: `bun test tests/turnProof.test.ts tests/sourceInspector.test.ts tests/historyResponse.test.ts`

Expected: FAIL on absent API/store/history behavior.

- [ ] **Step 6: Implement API normalization, store selection, and history projection**

Add `"context"` to the inspector-tab union, add `contextTurnId`, reset it on session changes, and mirror `openSourcesForTurn` with `openContextForTurn`. Normalize the endpoint's unknown JSON into capped typed arrays. Carry optional outcomes from history into activity items.

- [ ] **Step 7: Run focused desktop data tests**

Run: `bun test tests/turnProof.test.ts tests/sourceInspector.test.ts tests/historyResponse.test.ts`

Expected: PASS.

- [ ] **Step 8: Commit Task 2**

```bash
git add apps/desktop/src/api/turnInspector.ts apps/desktop/src/features/context/lib/turnProof.ts apps/desktop/src/api/chat.ts apps/desktop/src/stores/transcript-projection.ts apps/desktop/src/stores/types.ts apps/desktop/src/stores/index.ts apps/desktop/tests/turnProof.test.ts apps/desktop/tests/sourceInspector.test.ts apps/desktop/tests/historyResponse.test.ts
git commit -m "feat: project per-turn proof data"
```

---

### Task 3: Proof disclosure and Context inspector UI

**Files:**
- Create: `apps/desktop/src/features/context/components/ProofSummary.tsx`
- Create: `apps/desktop/src/features/context/components/ContextPanel.tsx`
- Modify: `apps/desktop/src/features/chat/components/AssistantMessage.tsx`
- Modify: `apps/desktop/src/features/background-agents/components/AgentRightSidebar.tsx`
- Modify: `apps/desktop/src/app/App.tsx`
- Test: `apps/desktop/tests/contextInspector.test.tsx`
- Modify: `apps/desktop/tests/sourceFooter.test.tsx`

**Interfaces:**
- Consumes: `turnProofSummary`, `getTurnInspector`, `openContextForTurn`, and `openSourcesForTurn`.
- Produces: collapsed-by-default `ProofSummary` and lazy `ContextPanel`.

- [ ] **Step 1: Write failing component tests**

Prove: no disclosure for empty turns; meaningful proof is collapsed initially; expansion renders bounded actions/checks/limitations; `Inspect context` selects the exact turn and opens the panel; Context renders metadata/evidence; missing/error states are quiet; `View sources` preserves the turn.

- [ ] **Step 2: Run component tests and verify RED**

Run: `bun test tests/contextInspector.test.tsx tests/sourceFooter.test.tsx`

Expected: FAIL because both components and the Context tab are absent.

- [ ] **Step 3: Implement `ProofSummary`**

Render beneath final Markdown and before message actions. Use a native button with `aria-expanded`; default closed. Label `Needs attention` for limitations, otherwise `Evidence recorded`. Show counts while closed; expanded content lists at most five items per group and ends with `Inspect context`.

- [ ] **Step 4: Implement lazy `ContextPanel`**

Fetch only when the Context tab is visible and a session/turn is selected. Abort stale requests on turn/session changes. Render solid, compact sections with wrapping values; use `EmptyState` for no sidecar and a restrained retry button for request errors.

- [ ] **Step 5: Add the Context tab and wire both surfaces**

Pass `<ContextPanel />` beside the existing `SourcesPanel`. Preserve the existing Activity and Sources panels and their focus behavior. Use existing `Tabs`, `Collapse`, icons, tokens, and panel scrolling; add no new CSS unless an existing utility cannot express the layout.

- [ ] **Step 6: Run focused UI tests and typecheck**

Run: `bun test tests/contextInspector.test.tsx tests/sourceFooter.test.tsx tests/sourceInspector.test.ts`

Run: `bun run typecheck`

Expected: PASS.

- [ ] **Step 7: Commit Task 3**

```bash
git add apps/desktop/src/features/context/components/ProofSummary.tsx apps/desktop/src/features/context/components/ContextPanel.tsx apps/desktop/src/features/chat/components/AssistantMessage.tsx apps/desktop/src/features/background-agents/components/AgentRightSidebar.tsx apps/desktop/src/app/App.tsx apps/desktop/tests/contextInspector.test.tsx apps/desktop/tests/sourceFooter.test.tsx
git commit -m "feat: add context evidence inspector"
```

---

### Task 4: Completion audit

**Files:**
- Verify all files changed by Tasks 1-3.

- [ ] **Step 1: Run server checks**

Run: `uv run pytest tests/test_session_store.py tests/test_session_runtime_snapshot.py -q`

Run: `uv run ruff check arden/context/store.py arden/server/schemas.py arden/server/routers/session.py tests/test_session_store.py tests/test_session_runtime_snapshot.py`

- [ ] **Step 2: Run desktop checks**

Run: `bun test tests/turnProof.test.ts tests/contextInspector.test.tsx tests/sourceFooter.test.tsx tests/sourceInspector.test.ts tests/historyResponse.test.ts`

Run: `bun run typecheck`

- [ ] **Step 3: Audit requirements against current files**

Confirm exact per-turn lookup, no context content exposure, durable reload behavior, local summary derivation, meaningful-only disclosure, lazy inspector loading, attention precedence, bounded rendering, Sources handoff, and no unrelated staged files.
