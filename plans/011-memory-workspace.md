# Plan 011: Promote memory from a fixed modal to a full workspace surface

> **Executor instructions**: Follow this plan step by step. Run every
> verification command before moving on. On any STOP condition, stop and
> report. Update this plan's row in `plans/README.md` when done.
>
> **Drift check (run first)**: compare the "Current state" excerpts against
> the live code. Written against a dirty working tree at commit `57ec2d10`
> (branch `codex/memory-ledger-v2`). On mismatch, STOP.

## Status

- **Priority**: P2 (structural — highest single lever on "feels cramped")
- **Effort**: L
- **Risk**: MED (touches the app shell)
- **Depends on**: best after 001-007 land (smaller diffs to carry)
- **Category**: direction / ux
- **Planned at**: commit `57ec2d10`, 2026-07-13

## Why this matters

A three-pane knowledge vault (rail + note + links panel) currently lives
inside a fixed `PageModal` capped at 1280×820 — an editorial workspace
shoehorned into a dialog. The right panel is forced to 320px and overlays
below 900px; nothing is resizable; the vault closes whenever the user needs
the app underneath. The app already has the right hosting pattern: Home and
AreaRoom are full `<main>` surfaces swapped via store state. Memory should be
the third such surface.

## Current state

All paths under `apps/desktop/`.

- Modal host `src/features/memory/components/MemoryModal.tsx` (25 lines):
  ```tsx
  export function MemoryModal() {
    const open = useStore((s) => s.memoryOpen);
    const close = useStore((s) => s.closeMemory);
    const origin = useStore((s) => s.modalOrigin);
    return (
      <PageModal open={open} onClose={close} origin={origin}
        header={{ title: "Memory" }}
        size="w-[min(1280px,calc(100vw-32px))] h-[min(820px,calc(100vh-32px))] ...">
        <div className="flex min-h-0 flex-col border-t border-line-soft">
          <MemoryPane />
        </div>
      </PageModal>
    );
  }
  ```
- Store state: `src/stores/index.ts:266` `memoryOpen: false`; `:843-844`
  `openMemory: (origin) => set({ memoryOpen: true, modalOrigin: origin ?? null })`,
  `closeMemory: () => set({ memoryOpen: false })`. Types at
  `src/stores/types.ts:400,608`.
- The full-surface precedent, `src/app/App.tsx` (~lines 243-270): when
  `openAreaKey || showHome`, a `<main>` claims the pane geometry
  (`absolute top-0 right-0 bottom-0 left-[var(--sidebar-width,272px)]
  data-[sidebar-hidden=true]:left-0 …`) and `AnimatePresence` swaps
  `<AreaRoom>` ↔ `<Home>` with a "symmetric surface swap" (both surfaces
  absolute inset-0; the comment explains popLayout is NOT used). Area state
  lives in `src/stores/areas-domain.ts` (`openAreaKey: string | null`).
  `MemoryModal` renders lazily at `App.tsx:275` alongside `SettingsModal` /
  `AutomationsModal`.
- `ArtifactMemoryView` is host-agnostic (takes only `config`) — it moves as a
  unit. Its grid is fixed-width columns
  (`grid-cols-[280px_minmax(0,1fr)_320px]`, `ArtifactMemoryView.tsx:1318-1323`).
- Resize precedent: `SidebarResizeHandle` (`App.tsx:237`) resizes the app
  sidebar via a CSS var (`--sidebar-width`); read
  `src/app/…SidebarResizeHandle…` (grep it) for the drag pattern.
- Wherever `openMemory` is invoked (sidebar nav "Memory" item — grep
  `openMemory(` across `src/`) stays the entry point.

Design constraints (from `docs/design-language.md`, inlined): anchored chrome
(rails) sits low with tonal separation; surface swaps are synchronized
dissolve+rise (the Home↔AreaRoom comment is the canonical spec); no new
motion values — reuse `MOTION.route`, `EASE_EMPHASIZED` as App.tsx does.

## Commands you will need

| Purpose   | Command (run from `apps/desktop/`) | Expected on success |
|-----------|------------------------------------|---------------------|
| Typecheck | `bun run typecheck`                | exit 0              |
| Tests     | `bun test tests/`                  | all pass            |
| Lint      | `bun run lint`                     | exit 0              |
| Build     | `bun run build`                    | exit 0              |

## Scope

**In scope**:
- `apps/desktop/src/app/App.tsx` (surface switching)
- `apps/desktop/src/features/memory/components/MemoryModal.tsx` (delete) and a new `MemorySurface.tsx`
- `apps/desktop/src/stores/` (only if the surface-priority logic needs a tweak — see Step 2)
- `apps/desktop/src/features/memory/components/ArtifactMemoryView.tsx` (resizable columns)
- tests: update any that mount `MemoryModal`

**Out of scope**:
- Deep links / per-note URLs — there is no router in this app; "reopen where
  you left off" is delivered via persisted `selected` path instead (Step 4),
  not URLs.
- Merging records/inspector into tabs (noted in plan 004's deferral —
  optional follow-up once the panel is resizable).
- `PageModal` component itself.

## Steps

### Step 1: Create `MemorySurface`

New `src/features/memory/components/MemorySurface.tsx`: a full-screen surface
matching AreaRoom's shell contract — absolutely-positioned `inset-0` column
with the app's surface background, a slim header row (title "Memory" + a
close/back control calling `closeMemory`), and `<MemoryPane />` filling the
rest. Copy the wrapper classes from `AreaRoom`'s root (grep
`function AreaRoom` for its top-level className) so enter/exit matches the
existing surface-swap contract.

**Verify**: `bun run typecheck` exit 0 (component compiles, unmounted).

### Step 2: Swap the host in `App.tsx`

- Extend the main-surface conditional: `openAreaKey || showHome || memoryOpen`
  → inside the `<AnimatePresence initial={false}>`, add the branch
  `memoryOpen ? <MemorySurface key="memory" /> : …`. Decide priority:
  memory above area/home (most recently invoked wins is ideal but complex;
  simple fixed priority `memory > area > home` is acceptable — record the
  choice in the plan row).
- Remove `<MemoryModal />` from the lazy modal block (line ~275) and delete
  `MemoryModal.tsx`. Keep the lazy `import` pattern for `MemorySurface`
  (imitate line 39-41).
- Make `openMemory` also close conflicting overlays if the store's existing
  open-actions do so for area rooms (read `openArea`'s reducer in
  `areas-domain.ts:140` region and mirror the hygiene).

**Verify**: `bun run typecheck && bun test tests/`; update tests that mounted
`MemoryModal` (grep `MemoryModal` in `tests/`) to mount `MemorySurface` or
drive the store flag.

### Step 3: Resizable rail and inspector

In `ArtifactMemoryView.tsx`, replace the fixed grid columns with CSS-var
widths: `grid-cols-[var(--memory-rail-w,280px)_minmax(0,1fr)_var(--memory-panel-w,320px)]`
(closed state keeps `0px` for the third column). Add two drag handles
modeled on `SidebarResizeHandle` (same pointer-capture pattern), clamping
rail 220–400px and panel 280–480px, persisting to localStorage
(`memory.rail.w`, `memory.panel.w`). Keep the `max-[900px]` overlay behavior.

**Verify**: `bun test tests/` green; feel check below.

### Step 4: Restore-where-you-left-off

Persist the last `selected` path (localStorage `memory.lastPath`) and prefer
it over the first-note fallback in the default-selection effect
(`ArtifactMemoryView.tsx:443-456`) when the path still exists in
`navigableArtifacts`.

**Verify**: new test — remount the view with seeded localStorage; the
persisted note is selected instead of the first.

### Step 5: Full gate + feel check

**Verify**: `bun run typecheck && bun run lint && bun test tests/ && bun run build` → all exit 0.

## Test plan

- Update: every test mounting `MemoryModal` (grep first, count them in your
  report).
- New: surface swap (setting `memoryOpen` renders the surface, unsetting
  returns to Chat/Home); resize handle clamps and persists; last-path
  restore.
- Model store-driven mount tests on existing Home/AreaRoom tests if present
  (grep `openAreaKey` in `tests/`).

## Done criteria

- [ ] Memory renders as a full main surface; the modal is gone (`MemoryModal.tsx` deleted, no imports remain)
- [ ] Enter/exit uses the existing Home↔AreaRoom swap (no new motion values)
- [ ] Rail and panel are drag-resizable with clamped, persisted widths
- [ ] Reopening memory restores the last-viewed note
- [ ] `bun run typecheck && bun run lint && bun test tests/ && bun run build` exit 0
- [ ] `plans/README.md` updated (including the surface-priority decision)

## STOP conditions

- Excerpts don't match (drift).
- The surface-priority interaction (memory vs open area room vs Home) has no
  clean resolution in the store without restructuring domain slices — report
  with options rather than restructuring.
- `PageModal`-specific behavior the memory view silently relied on (focus
  trap, Escape-to-close, `modalOrigin` animation) leaves a UX hole you can't
  fill with ≤10 lines — report.
- More than ~6 test files need rewriting (signals the mount pattern is more
  entangled than surveyed).

## Maintenance notes

- This unlocks plan 004's deferred right-panel tabs (records + links
  side-by-side becomes viable at 480px).
- Reviewer: check Escape handling — inside the modal, Escape closed Memory;
  as a surface, Escape should NOT close it (matches Home/AreaRoom), and the
  quick switcher/find bar (plans 005/008) get Escape to themselves.
- The `openMemory(origin)` modal-origin animation parameter becomes unused —
  remove it from the store signature or repurpose; don't leave it dangling.
