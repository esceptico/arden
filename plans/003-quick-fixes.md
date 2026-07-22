# Plan 003: Quick fixes — honest Reload, indexBlocked dead end, bounded draft store

> **Executor instructions**: Follow this plan step by step. Run every
> verification command before moving on. On any STOP condition, stop and
> report. Update this plan's row in `plans/README.md` when done.
>
> **Drift check (run first)**: `git status --short apps/desktop/src/features/memory/` and compare the "Current state" excerpts against the live code. Written against a dirty working tree at commit `57ec2d10` (branch `codex/memory-ledger-v2`). On mismatch, STOP.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug / dx
- **Planned at**: commit `57ec2d10`, 2026-07-13

## Why this matters

Three small, independent trust/robustness fixes in the memory view:

1. The rail's "Reload" button (spinner icon) calls a backend endpoint that is
   an explicit no-op — users expecting an index repair get nothing.
2. When the root `index.md` fails to load, the rail body renders `null` —
   an empty vault with no recovery path.
3. The editor draft store is a module-global `Map` that never evicts —
   the only unbounded cache in the feature; a slow leak in a long-lived
   desktop session.

## Current state

All paths under `apps/desktop/` unless noted.

**(a) Reload no-op.** `src/features/memory/components/NotebookRail.tsx:246-254`:

```tsx
<button
  type="button"
  onClick={onRebuild}
  disabled={rebuilding}
  aria-label="Reload memory notes"
  className="grid size-7 place-items-center rounded-[7px] text-faint hover:bg-surface-soft hover:text-ink disabled:opacity-50"
>
  <RefreshCw className={clsx("h-3.5 w-3.5", rebuilding && "animate-spin")} />
</button>
```

The empty state (lines 204-210) also offers `Reload`/`Reloading…` via
`onRebuild`. `onRebuild` → `rebuild()` in `ArtifactMemoryView.tsx:946-967`,
which calls `rebuildMemoryArtifactSummaries` → POST `/memory/artifacts/rebuild`.
The backend (`apps/server/arden/server/routers/memory.py:210-222`) documents it:
"this is a no-op that just returns the current pages." So the button IS a
refresh (re-list + re-fetch index docs via `acceptSummaries`), never a rebuild.

**(b) indexBlocked dead end.** `src/features/memory/components/NotebookRail.tsx:201-202`:

```tsx
) : indexBlocked ? (
  null
) : empty ? (
```

`indexBlocked` is computed in `ArtifactMemoryView.tsx:436`:
`indexErrors.has("index.md") && !indexDocuments.has("index.md")`. When true,
only the small `indexAlert` (`ListError`, lines 151-157) shows above an empty
body; the flat file list (`model.files`) is unreachable even though those
notes loaded fine.

**(c) Unbounded draft store.** `src/features/memory/lib/draftStore.ts` (27
lines, module-global):

```ts
const drafts = new Map<string, string>();

export function draftKey(path: string, baseRevision: string): string {
  return `${path}\u0000${baseRevision}`;
}
export function getDraft(path: string, baseRevision: string): string | null { ... }
export function setDraft(path: string, baseRevision: string, content: string): void {
  drafts.set(draftKey(path, baseRevision), content);
}
```

Entries are removed only on save-match (`clearDraftIfMatches`) or when a
draft equals base. Abandoned drafts for old revisions live forever. The
exemplar for bounded LRU is `src/features/memory/lib/artifactCache.ts:99-126`
(`RevisionCache`): Map + delete-then-set touch on `get`, evict-oldest loop on
`set`. Keep-across-unmount is intentional (drafts persist across navigation)
— bound the size, don't clear on unmount.

## Commands you will need

| Purpose   | Command (run from `apps/desktop/`) | Expected on success |
|-----------|------------------------------------|---------------------|
| Typecheck | `bun run typecheck`                | exit 0              |
| Lint      | `bun run lint`                     | exit 0              |
| Tests     | `bun test tests/`                  | all pass            |

## Scope

**In scope**:
- `apps/desktop/src/features/memory/components/NotebookRail.tsx`
- `apps/desktop/src/features/memory/lib/draftStore.ts`
- `apps/desktop/tests/memoryEditing.test.tsx` (add draft-eviction test)
- `apps/desktop/tests/memoryNotebook.test.tsx` (adjust label assertions if any)

**Out of scope**:
- `apps/server/**` — do NOT build a real rebuild endpoint; memory is
  file-canonical by design (documented in the router comment).
- `ArtifactMemoryView.tsx` — `rebuild()` stays; it's a legitimate refresh.

## Steps

### Step 1: Honest refresh labels

In `NotebookRail.tsx`, change `aria-label="Reload memory notes"` →
`aria-label="Refresh memory notes"` and `title` if present; in the empty
state (lines 204-210) change the button text `Reload`/`Reloading…` →
`Refresh`/`Refreshing…` and the hint sentence stays. No behavior change.

**Verify**: `grep -n "Reload" apps/desktop/src/features/memory/components/NotebookRail.tsx` → no matches. `bun test tests/` → if a test asserts the old label, update the assertion to the new label (check `tests/memoryNotebook*.test.tsx`).

### Step 2: indexBlocked fallback body

In `NotebookRail.tsx` replace the `indexBlocked ? null :` branch with a body
that (a) keeps the existing top `indexAlert`, and (b) still lists the flat
files so notes remain reachable. Concretely: change the branch to render the
same `model.files` block used at lines 216-229 (the `<details data-memory-files>`
element), or — simpler and acceptable — change the condition so
`indexBlocked` falls through to the normal tree branch (the tree will be
empty of directory entries but `model.files` renders). Preserve the retry
path: `indexAlert` already carries `onRetry={onRetryIndex}`.

**Verify**: `bun test tests/` passes. Add one test in
`tests/memoryNotebook.test.tsx` (model after existing rail-render tests
there): with an index error for `index.md` and artifacts present, the rail
still renders the Files disclosure and the error alert with a retry button.

### Step 3: Bound the draft store

Rework `draftStore.ts` to cap at 50 entries with LRU semantics, following
`RevisionCache` exactly: on `getDraft` hit, delete+set to refresh recency;
on `setDraft`, insert then `while (drafts.size > LIMIT)` delete the oldest
key (`drafts.keys().next().value`). Keep the exported function signatures
identical — callers must not change.

**Verify**: add a test in `tests/memoryEditing.test.tsx` (there is an
existing `clearDrafts()` usage at ~line 242 showing the import pattern):
setting 51 drafts evicts the first-set one; getting a draft refreshes its
recency so it survives a subsequent eviction. `bun test tests/` → all pass.

### Step 4: Full gate

**Verify**: from `apps/desktop/`: `bun run typecheck && bun run lint && bun test tests/` → all exit 0.

## Done criteria

- [ ] No "Reload" strings remain in `NotebookRail.tsx`
- [ ] indexBlocked state renders files + retry (new test passes)
- [ ] `draftStore` caps at 50 with LRU (new test passes)
- [ ] `bun run typecheck && bun run lint && bun test tests/` exit 0
- [ ] Only in-scope files modified; `plans/README.md` updated

## STOP conditions

- Excerpts don't match (drift).
- A test outside the memory suites starts failing.
- Step 2's fallback requires changes in `ArtifactMemoryView.tsx` beyond
  passing existing props — report instead of expanding scope.

## Maintenance notes

- If a real index-rebuild endpoint ever lands server-side, revisit Step 1's
  labels and wire the button to it.
- The 50-draft cap mirrors `ArtifactCache`'s limit; if users report lost
  drafts, raise the cap rather than removing the bound.


## Execution note (2026-07-13)

Steps 1 (Refresh labels) and 3 (LRU-bounded draft store) executed. Step 2
(indexBlocked fallback) REJECTED during execution: the null render on root
index failure is deliberate, guarded design — test "root index failure blocks
silent file demotion and retry recovers" (commit 529b8794, "harden memory
index navigation") asserts the rail must render NOTHING rather than demote
categorized notes to the flat Files list. The audit missed this settled
tradeoff; do not re-propose.
