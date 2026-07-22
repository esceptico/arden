# Plan 005: Quick switcher — Cmd/Ctrl+O fuzzy note jump inside the memory view

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
- **Depends on**: none
- **Category**: ux / feature
- **Planned at**: commit `57ec2d10`, 2026-07-13

## Why this matters

The reference experience is Obsidian, where Cmd+O (quick switcher) is *the*
primary navigation. The memory view currently has no keyboard jump at all —
the only global handlers are history (Cmd+[ / Cmd+]) and edit (Cmd+E/S).
Every jump means visually scanning the rail or typing in search then
clicking. A fuzzy-match overlay over the already-loaded note list closes the
single biggest "feels bad vs Obsidian" gap.

## Current state

All paths under `apps/desktop/`.

- `src/features/memory/components/ArtifactMemoryView.tsx`:
  - Keyboard handler pattern to imitate (lines 545-557):
    ```tsx
    useEffect(() => {
      const onKeyDown = (event: KeyboardEvent) => {
        if ((!event.metaKey && !event.ctrlKey) || (event.key !== "[" && event.key !== "]")) return;
        const targets = [event.target, document.activeElement];
        if (targets.some((target) => target instanceof HTMLElement && (
          target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)
        ))) return;
        event.preventDefault();
        moveHistory(event.key === "[" ? "back" : "forward");
      };
      window.addEventListener("keydown", onKeyDown);
      return () => window.removeEventListener("keydown", onKeyDown);
    }, [moveHistory]);
    ```
  - The candidate list already exists (line ~437):
    `const navigableArtifacts = useMemo(() => artifacts.filter((artifact) => isNotebookResourcePath(artifact.path)), [artifacts]);`
    Each item is a `MemoryArtifactSummary` with `path`, `title`, `summary`,
    `snippet` (type in `src/features/memory/lib/notebookTypes.ts`).
  - Navigation entry point: `navigateTo(path, anchor)` — the same callback
    passed to `MemoryInspector`'s `onNavigate` (line 1546) and used by
    wikilinks. Grep `const navigateTo` in the file for the exact signature.
  - `navigationDisabled` semantics: rail selection is disabled while
    `reviewPending` is true (`navigationDisabled={reviewPending}`, line 1340).
    The switcher must respect the same guard.
- App-level overlay exemplar: `src/features/command-palette/components/CommandPalette.tsx`
  — a portal + scrim + `useFocusTrap(panelRef, open)` + global Cmd+K handler.
  Its open/close is intentionally instant ("keyboard-frequency surface" — see
  the comment at its return). Imitate its structure: portal into
  `document.querySelector("#app")`, `modal-scrim` class, `pt-[14vh]`
  positioning, `useFocusTrap` from `@/lib/hooks`.
- The memory view is hosted inside a `PageModal` (`MemoryModal.tsx`) which
  may have its own focus trap and Escape handling — check `PageModal`'s
  Escape behavior (`src/components/ui/PageModal.tsx`) so the switcher's
  Escape closes the switcher, not the whole memory modal
  (`event.stopPropagation()` in the switcher's keydown, capture phase if
  needed).

Design constraints from `docs/design-language.md` (quoted, executor hasn't
read it): "Motion clarifies change, never decorates"; keyboard-frequency
surfaces open instantly (the command palette comment codifies this); dense
rows at readable sizes ("Compactness is spacing, not font size"); selection
reads through tonal fills (`app-row` + `data-active` idiom, see
`NotebookRail.tsx:42-56`).

## Commands you will need

| Purpose   | Command (run from `apps/desktop/`) | Expected on success |
|-----------|------------------------------------|---------------------|
| Typecheck | `bun run typecheck`                | exit 0              |
| Tests     | `bun test tests/`                  | all pass            |
| Lint      | `bun run lint`                     | exit 0              |

## Scope

**In scope**:
- `apps/desktop/src/features/memory/components/MemoryQuickSwitcher.tsx` (create)
- `apps/desktop/src/features/memory/components/ArtifactMemoryView.tsx` (mount + keybinding)
- `apps/desktop/tests/memoryQuickSwitcher.test.tsx` (create)

**Out of scope**:
- The global `CommandPalette` (Cmd+K) — do not extend it; the switcher is
  memory-scoped and only active while the memory view is mounted.
- Server-side search (`listMemoryArtifactSummaries({q})`) — the switcher
  matches client-side over already-loaded summaries only.
- New dependencies — write the fuzzy matcher by hand (subsequence match, see
  Step 2); do NOT add fuse.js or similar.

## Steps

### Step 1: Create `MemoryQuickSwitcher.tsx`

Props:

```tsx
export function MemoryQuickSwitcher({
  open,
  artifacts,          // MemoryArtifactSummary[] (navigableArtifacts)
  recentPaths,        // string[] — most-recent-first, for the empty query
  onClose,
  onSelect,           // (path: string) => void
}: { ... })
```

Structure copied from `CommandPalette.tsx`: `createPortal` into `#app`,
scrim div (`modal-scrim absolute inset-0 z-[var(--z-modal)] grid
place-items-start justify-center pt-[14vh] p-8`, `onClick={onClose}`), inner
panel with `useFocusTrap`, an `<input>` autofocused on open, and a results
list. Panel width ~560px, rows use the `app-row` idiom with
`data-active={index === highlighted}`. Show each result as title (text-sm)
with the path as a `text-2xs text-muted` second line (same two-line shape as
`FlatRow` in `MemoryFileTree.tsx:39-61`). No open/close animation (instant,
per the palette precedent).

Keyboard inside the panel: ArrowUp/ArrowDown move the highlight (clamped),
Enter selects the highlighted result, Escape calls `onClose` and
`stopPropagation` so the hosting `PageModal` stays open.

### Step 2: Fuzzy matching + ranking (pure function, exported for tests)

```tsx
export function rankSwitcherMatches(
  artifacts: MemoryArtifactSummary[],
  query: string,
  recentPaths: string[],
): MemoryArtifactSummary[]
```

- Empty query → recent paths first (in `recentPaths` order), then the rest
  alphabetically by title; cap at 12 rows.
- Non-empty query → case-insensitive subsequence match against
  `title + " " + path`. Score: exact-substring in title beats
  prefix-of-word beats scattered subsequence; ties broken by shorter title.
  Keep it simple — ~30 lines, no dependency.

### Step 3: Wire into `ArtifactMemoryView`

- State: `const [switcherOpen, setSwitcherOpen] = useState(false);`
- Add a keydown effect modeled EXACTLY on the history handler excerpt above:
  Cmd/Ctrl+O (and Cmd/Ctrl+P as alias) → `setSwitcherOpen(true)` — but do
  NOT apply the input/textarea guard to this binding when the switcher is
  closed only if focus is in the rail search; simplest correct rule: open the
  switcher unless the editor is active (`editing != null`) or a review is
  pending (`reviewPending`). Prevent default in all handled cases (Cmd+O is
  "open file" in browsers).
- `recentPaths`: derive from the existing `NavigationHistory` ref — grep
  `navigationHistory` in the file; its entries expose `path`. If the class
  lacks a way to enumerate entries, add a read-only `locations()` accessor to
  `src/features/memory/lib/navigationHistory.ts` (bounded list already, limit
  100) — do not change its mutation API.
- `onSelect`: `(path) => { setSwitcherOpen(false); navigateTo(path, null); }`.
- Mount `<MemoryQuickSwitcher …/>` next to `WikiLinkPreview` in the main
  workspace region.

**Verify** after each step: `bun run typecheck` → exit 0.

## Test plan

Create `tests/memoryQuickSwitcher.test.tsx`, modeled structurally on
`tests/memoryNotebook.test.tsx` (rendering `ArtifactMemoryView` with mocked
API modules — copy its setup):

1. `rankSwitcherMatches` unit cases: empty query recency order; substring
   beats subsequence; cap at 12.
2. Cmd+O opens the switcher; typing filters; Enter navigates (the selected
   note's title becomes the workspace `<h1>`).
3. Escape closes the switcher and the memory view is still mounted.
4. Cmd+O while editing does NOT open the switcher.

**Verify**: `bun test tests/memoryQuickSwitcher.test.tsx` → all pass; then full `bun test tests/`.

## Done criteria

- [ ] Cmd/Ctrl+O and Cmd/Ctrl+P open a fuzzy switcher over memory notes
- [ ] Enter navigates via the existing `navigateTo` (history recorded — Cmd+[ returns to the previous note)
- [ ] Escape closes only the switcher
- [ ] All 4 test groups pass; `bun run typecheck && bun run lint && bun test tests/` exit 0
- [ ] Only in-scope files modified; `plans/README.md` updated

## STOP conditions

- Excerpts don't match (drift).
- `PageModal` swallows Escape/keydown before the switcher can (capture-phase
  conflict you can't resolve with `stopPropagation`) — report the conflict.
- `NavigationHistory` can't expose entries without changing mutation
  semantics — ship with empty-query = alphabetical only and note it.

## Maintenance notes

- If memory later becomes a top-level workspace (plan 011), the Cmd+O scope
  should widen from "while memory modal is open" to "while memory surface is
  active" — the binding lives in `ArtifactMemoryView`, so it moves for free.
- Future: the global CommandPalette could gain a "memory notes" source using
  the same `rankSwitcherMatches` — keep the ranking function pure/exported.
