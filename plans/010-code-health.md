# Plan 010: Code health — dedupe conflict construction & search, test the records pane

> **Executor instructions**: Follow this plan step by step. Run every
> verification command before moving on. On any STOP condition, stop and
> report. Update this plan's row in `plans/README.md` when done.
>
> **Drift check (run first)**: compare the "Current state" excerpts against
> the live code. Written against a dirty working tree at commit `57ec2d10`
> (branch `codex/memory-ledger-v2`). This file is the branch's churn magnet —
> expect drift; on ANY excerpt mismatch, STOP.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED (touches the edit/review flow — behavior pinned by ~34 tests)
- **Depends on**: none, but land BEFORE 008 (008 edits the search sites this
  plan unifies)
- **Category**: tech-debt / tests
- **Planned at**: commit `57ec2d10`, 2026-07-13

## Why this matters

`ArtifactMemoryView.tsx` (1559 lines) contains three copy-paste hazards that
must be edited in lockstep today, plus one untested mutation flow:

1. The conflict-`ReviewState` construction (generation bump +
   `editableContent ?? content` fallback) is duplicated at three sites — the
   exact kind of detail that drifts and produces a subtle conflict bug.
2. Notebook search is implemented twice (a callback and a debounced effect)
   with subtly different control flow.
3. The records pane's pin toggle — the feature's only optimistic mutation —
   has zero test coverage, and its rollback can clobber a concurrent refresh.

This is deliberately NOT the big reducer refactor (that's deferred — see
`plans/README.md`); these are the safe, high-leverage extractions.

## Current state

All paths under `apps/desktop/`. File: `src/features/memory/components/ArtifactMemoryView.tsx`.

**(a) Conflict construction × 3.** Site 1 — drift effect (lines 619-643):

```tsx
const snapshot = snapshotEditing(editing, editRequestGeneration.current);
setEditReview((current) => current?.kind === "conflict" && current.conflict.currentRevision === activeDetail.revision
  ? current
  : {
      kind: "conflict",
      generation: ++reviewGeneration.current,
      snapshot,
      conflict: { currentRevision: activeDetail.revision, currentContent },
    });
```

Site 2 — `returnFromEditReview` (lines 1094-1108) and Site 3 —
`finishExternalReview` (lines 1152-1166) share this identical block,
including the identical guard predicate:

```tsx
const draft = editingRef.current;
if (
  draft
  && activeDetail?.path === draft.path
  && activeDetail.revision !== draft.baseRevision
  && draft.draftContent !== draft.baseContent
) {
  setEditReview({
    kind: "conflict",
    generation: ++reviewGeneration.current,
    snapshot: snapshotEditing(draft, editRequestGeneration.current),
    conflict: {
      currentRevision: activeDetail.revision,
      currentContent: activeDetail.editableContent ?? activeDetail.content,
    },
  });
} else {
  setEditReview(null);
}
```

**(b) Search × 2.** The `search` callback (lines 373-393) and the debounced
typing effect (lines 395-433) both call
`listMemoryArtifactSummaries(config, { q }, { signal })`, both
`.filter(isNotebookPage)`, both guard with `isCurrentSummaryRequest` +
`queryRef.current`. The callback exists to be re-invoked from
`drainMemoryChanges` (~line 705), `rebuild` (line 959), and a vault-version
effect (~line 769); the effect handles keystrokes with a
180ms `SEARCH_DEBOUNCE_MS` timer and previous-query reset logic the callback
lacks.

**(c) Pin toggle, untested + rollback clobber.** Lines 934-944:

```tsx
const togglePinned = (record: MemoryItem) => {
  const next = !record.pinned;
  setPinningId(record.id);
  setRecords((current) => current.map((item) => item.id === record.id ? { ...item, pinned: next } : item));
  setRecordPinned(config, record.id, next)
    .catch((reason) => {
      setRecords((current) => current.map((item) => item.id === record.id ? { ...item, pinned: record.pinned } : item));
      setRecordsError(reason instanceof Error ? reason.message : String(reason));
    })
    .finally(() => setPinningId((current) => current === record.id ? null : current));
};
```

The records list can be refetched mid-flight (`recordsRefreshKey` deps at
lines 906-931); a late failure rollback writes the *captured* `record.pinned`
onto whatever list is then current — possibly a freshly-loaded record. No
test selects a kind filter, selects a record, or pins one
(`RecordListPane.tsx` and `RecordDetailPane.tsx` are effectively uncovered;
grep `pinned` in `tests/` — fixture data only).

## Commands you will need

| Purpose   | Command (run from `apps/desktop/`) | Expected on success |
|-----------|------------------------------------|---------------------|
| Typecheck | `bun run typecheck`                | exit 0              |
| Tests     | `bun test tests/`                  | all pass            |
| Lint      | `bun run lint`                     | exit 0              |

## Scope

**In scope**:
- `apps/desktop/src/features/memory/components/ArtifactMemoryView.tsx`
- `apps/desktop/tests/memoryRecords.test.tsx` (create)

**Out of scope**:
- The reducer/state-machine refactor of the edit flow (deferred by decision).
- The `useAsyncResource` consolidation of all 8 fetch sites (same decision —
  too broad for this pass; only the search pair is unified).
- `RecordListPane.tsx` / `RecordDetailPane.tsx` component internals.
- Search result filtering semantics (plan 008 changes the filter — here the
  behavior must stay byte-identical).

## Steps

### Step 1: Extract `conflictFromDrift`

Add near `snapshotEditing` (module scope, ~line 86):

```tsx
function conflictFromDrift(
  draft: EditingSession,
  detail: MemoryArtifactDetail,
  requestGeneration: number,
  generation: number,
): ReviewState {
  return {
    kind: "conflict",
    generation,
    snapshot: snapshotEditing(draft, requestGeneration),
    conflict: {
      currentRevision: detail.revision,
      currentContent: detail.editableContent ?? detail.content,
    },
  };
}
```

and a predicate `draftDrifted(draft, detail): boolean` capturing the
four-clause guard. Replace all three sites (619-643, 1094-1108, 1152-1166)
with calls. The generation bump (`++reviewGeneration.current`) MUST remain at
the call sites (it's a ref mutation — keep it out of the pure helper);
site 1's "keep current conflict if same revision" setEditReview-updater
semantics must be preserved exactly.

**Verify**: `bun test tests/memoryEditing.test.tsx` → all ~34 tests pass
unmodified. If any fails, STOP (the helper changed semantics).

### Step 2: Unify the search paths

Make the debounce effect delegate to the `search` callback:

```tsx
const timer = window.setTimeout(() => { void search(value); }, SEARCH_DEBOUNCE_MS);
```

and delete the inline fetch inside the effect. Precondition check before
editing: `search` must reproduce the effect's stale-guards — it already
guards `isCurrentSummaryRequest(request) && queryRef.current === queryValue`
(line 384). Two behavioral details to preserve from the effect: (i) it calls
`beginSummaryRequest()` + `setSearchLoading(true)` immediately on keystroke
(before the timer) so a rapid previous request is cancelled — keep that
pre-timer block; `search` will `beginSummaryRequest()` again when it runs,
which is harmless (same epoch discipline) — confirm by reading
`beginSummaryRequest` (~line 287); (ii) the empty-query reset branch (lines
400-411) stays untouched.

**Verify**: `bun test tests/` → the notebook/search suites pass unmodified.
`grep -c "listMemoryArtifactSummaries(config, { q" apps/desktop/src/features/memory/components/ArtifactMemoryView.tsx` → 1.

### Step 3: Guard the pin rollback

In `togglePinned`, capture a request marker and ignore the rollback if the
records list has been refetched since:

```tsx
const requestId = recordsRequestId.current;
...
.catch((reason) => {
  if (recordsRequestId.current === requestId) {
    setRecords((current) => current.map(...rollback...));
  }
  setRecordsError(...);
})
```

`recordsRequestId` already increments on every records refetch (line 908) —
reuse it; do not add a new ref.

### Step 4: Records pane test coverage

Create `tests/memoryRecords.test.tsx`, copying the mock/setup harness from
`tests/memoryNotebook.test.tsx` (same mocked `@/api/memoryItems` /
`@/api/memoryArtifacts` pattern). Cover:

1. Opening Raw records issues `listMemoryItems` with
   `{ limit: 100, offset: 0, status: "active" }`; choosing a kind in the
   Select re-queries with `kind`.
2. Typing in the records search re-queries with `q`.
3. Selecting a record row shows it in `RecordDetailPane`.
4. Pin success: optimistic flip renders immediately; `setRecordPinned`
   called with `(config, id, true)`.
5. Pin failure: mock rejection → pin state rolls back and an error renders.
6. Pin failure AFTER a refetch (bump the mock to resolve a new list first):
   rollback is skipped — the fresh record's pinned state is untouched.

**Verify**: `bun test tests/memoryRecords.test.tsx` → 6 tests pass; then full
`bun test tests/`.

## Done criteria

- [ ] One conflict-construction helper; three call sites; `memoryEditing` suite untouched and green
- [ ] One search fetch path (grep count = 1)
- [ ] Pin rollback guarded by `recordsRequestId`
- [ ] `tests/memoryRecords.test.tsx` exists with the 6 cases, all green
- [ ] `bun run typecheck && bun run lint && bun test tests/` exit 0
- [ ] `plans/README.md` updated

## STOP conditions

- Any excerpt mismatch (this file churns).
- Any `memoryEditing.test.tsx` failure after Step 1 or 2 — do not "fix" the
  test; the extraction changed behavior.
- `beginSummaryRequest` turns out not to be idempotent-per-keystroke in the
  Step 2 delegation (double-epoch causes a visible regression in the search
  tests) — report with the failing test name.

## Maintenance notes

- Deferred by decision, recorded here so nobody re-audits: the
  `useReducer` edit-state machine (ARCH-01) and the `useAsyncResource`
  consolidation of the remaining fetch sites (ARCH-02). Revisit after this
  branch's churn settles; characterization coverage from this plan makes
  that safer.
- Reviewer: scrutinize Step 2's pre-timer block — the loading-state sequence
  on fast typing is the likeliest subtle regression.
