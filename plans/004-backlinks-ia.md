# Plan 004: Make links the front door — rename, reorder, and auto-open the inspector

> **Executor instructions**: Follow this plan step by step. Run every
> verification command before moving on. On any STOP condition, stop and
> report. Update this plan's row in `plans/README.md` when done.
>
> **Drift check (run first)**: compare the "Current state" excerpts against
> the live code. Written against a dirty working tree at commit `57ec2d10`
> (branch `codex/memory-ledger-v2`). On mismatch, STOP.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: LOW
- **Depends on**: none (001 recommended first — it touches the same file)
- **Category**: ux
- **Planned at**: commit `57ec2d10`, 2026-07-13

## Why this matters

The product's stated reference is "Obsidian, but for memory". In Obsidian the
backlinks panel is the core of link-based thinking and is persistently
visible. In this app the equivalent panel: (a) is closed by default, (b) is
toggled by a button whose aria-label says "Open memory trust inspector" — the
word "links" never appears in the affordance, (c) titles itself
"Context / Connections and provenance", and (d) puts Links first but under a
generic header, followed by four provenance sections that dominate the
scroll. Users will not discover the feature that most defines the vault.

## Current state

All paths under `apps/desktop/`.

- `src/features/memory/components/ArtifactMemoryView.tsx`
  - line 209: `const [inspectorOpen, setInspectorOpen] = useState(false);`
  - lines 1392-1398 (toolbar toggle):
    ```tsx
    <button
      type="button"
      aria-label={inspectorOpen ? "Close memory trust inspector" : "Open memory trust inspector"}
      aria-pressed={inspectorOpen}
      onClick={() => setInspectorOpen((open) => !open)}
      className="grid size-7 place-items-center rounded-[6px] text-muted hover:bg-surface-soft hover:text-ink aria-pressed:bg-surface-soft aria-pressed:text-ink"
    >
      <PanelRight className="size-4" />
    </button>
    ```
  - line 969: `const rightPanelOpen = recordsOpen || inspectorOpen;` — the
    records diagnostic and the inspector share one `<aside>` slot; opening
    one force-closes the other (lines 1346-1353).
  - line 1536: inspector renders only `{!recordsOpen && inspectorOpen && visibleDetail && (<MemoryInspector .../>)}` — blank while content loads.
- `src/features/memory/components/MemoryInspector.tsx`
  - lines 199-202 (header):
    ```tsx
    <div className="border-b border-line-soft px-4 py-3">
      <h2 className="text-sm font-medium text-ink">Context</h2>
      <p className="text-2xs text-muted">Connections and provenance</p>
    </div>
    ```
  - Section order (lines 204-302): Links → Scope → Evidence → Lifecycle →
    Page events → Pending review. The Links section (204-219) renders
    "Backlinks · N" and "Outgoing · N" as tiny `text-2xs text-faint` labels
    with `LinkRow` lists.
- Preference persistence exemplar: the store persists UI prefs (see
  `src/stores/index.ts` — grep `prefs.` for the persisted-prefs pattern, e.g.
  `prefs.quickCaptureShortcut` read in `src/app/App.tsx:196`). Simpler
  session-only persistence via `useState` initializer + `localStorage` is
  also used in the codebase — grep `localStorage.getItem` in `src/` and match
  whichever pattern the store already uses for view toggles.

Tests: `tests/memoryInspector.test.tsx` covers inspector content; several
navigation tests in `tests/memoryNotebook*.test.tsx` toggle the inspector via
the "trust inspector" aria-label — those assertions WILL need updating.

## Commands you will need

| Purpose   | Command (run from `apps/desktop/`) | Expected on success |
|-----------|------------------------------------|---------------------|
| Typecheck | `bun run typecheck`                | exit 0              |
| Tests     | `bun test tests/`                  | all pass            |
| Lint      | `bun run lint`                     | exit 0              |

## Scope

**In scope**:
- `apps/desktop/src/features/memory/components/ArtifactMemoryView.tsx`
- `apps/desktop/src/features/memory/components/MemoryInspector.tsx`
- `apps/desktop/tests/memoryInspector.test.tsx`, `tests/memoryNotebook*.test.tsx` (assertion updates + new tests)

**Out of scope**:
- Merging records + inspector into tabs (deliberately deferred — see
  Maintenance notes).
- The inspector's data loading (`getPageLinks`/`getPageHistory` effects) —
  they already gate on `inspectorOpen` and must keep doing so.
- Backend link APIs.

## Steps

### Step 1: Rename the affordance and header

- `ArtifactMemoryView.tsx:1392-1398`: aria-label → `"Open links and provenance"` / `"Close links and provenance"`; add `title="Links & provenance"`.
- `MemoryInspector.tsx:199-202`: `<h2>` → `Links`, subtitle `<p>` → `Provenance and history`.

**Verify**: `grep -rn "trust inspector" apps/desktop/src apps/desktop/tests` → matches only in tests (fix them in Step 4).

### Step 2: Give backlinks a real header and lead the panel with them

In `MemoryInspector.tsx`'s Links section (204-219):
- Split into two labelled sub-blocks with the existing `Section` header style
  but real counts: `Backlinks (N)` and `Outgoing (N)` — keep the
  `text-2xs font-semibold uppercase tracking-[0.08em] text-faint` idiom from
  `Section` (line 114).
- When `links.backlinks.length === 0`, render the existing empty-copy pattern
  (`<p className="text-xs text-faint">No backlinks yet.</p>` — matches
  "No lifecycle relationships." at line 246).
- Keep section order otherwise: Links → Scope → Evidence → Lifecycle → Page
  events → Pending review.

**Verify**: `bun test tests/memoryInspector.test.tsx` (update copy assertions as needed).

### Step 3: Remember the open state and default it open on wide layouts

In `ArtifactMemoryView.tsx`:
- Initialize `inspectorOpen` from persisted state with default `true`:
  `useState(() => localStorage.getItem("memory.inspectorOpen") !== "false")`
  (or the store-prefs pattern if that's what view toggles use — check first,
  match the codebase).
- Persist on toggle (write in the `onClick`, not an effect).
- Keep the existing narrow-viewport behavior: below 900px the aside already
  overlays (`max-[900px]:absolute…`, line 1488) — defaulting open there is
  acceptable since it's an overlay the user can close; do NOT add a
  viewport-width branch to the state itself.

**Verify**: `bun test tests/` — several tests assume the inspector starts
closed; update them to either seed `localStorage` or assert the new default.

### Step 4: Fix loading blankness

`ArtifactMemoryView.tsx:1536` hides the whole inspector until `visibleDetail`
exists. Change the aside content so that when `inspectorOpen && !recordsOpen`
but `visibleDetail` is null, it renders a minimal placeholder (reuse
`DetailPlaceholder` from `@/components/ui/EmptyState` as `MemoryNote.tsx:35`
does: `<DetailPlaceholder>Loading…</DetailPlaceholder>`), instead of an empty
320px column.

**Verify**: `bun run typecheck && bun test tests/` → all pass. Update
remaining aria-label assertions.

## Test plan

- Update: all tests referencing "trust inspector" labels.
- New: (1) inspector defaults open and shows the Links header; (2) toggling
  it off persists across a remount (seed/inspect `localStorage`); (3) with
  zero backlinks, "No backlinks yet." renders. Model after existing tests in
  `tests/memoryInspector.test.tsx`.

## Done criteria

- [ ] `grep -rn "trust inspector" apps/desktop/` → no matches
- [ ] Inspector opens by default on first run; state persists
- [ ] Backlinks have a labelled header with count and an empty state
- [ ] Open inspector during content load shows a placeholder, not a blank column
- [ ] `bun run typecheck && bun run lint && bun test tests/` exit 0
- [ ] `plans/README.md` updated

## STOP conditions

- Excerpts don't match (drift).
- Defaulting the inspector open breaks >3 unrelated test files — report; the
  default may need to land behind the persistence key instead.
- The links effect (`ArtifactMemoryView.tsx:779` region) starts firing for
  every page load in a way that visibly slows navigation — report before
  changing effect logic.

## Maintenance notes

- Deferred (deliberately): unifying the records diagnostic and inspector into
  one tabbed right panel. Plan 011 (workspace promotion) is the natural home
  for that — a resizable right panel with sections. Don't half-build tabs
  here.
- Reviewer: check that `rightPanelOpen` (line 969) semantics didn't change —
  records and inspector must still be mutually exclusive after this plan.
