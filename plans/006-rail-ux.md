# Plan 006: Bring the rail up to vault bar — collapsible folders, reveal-active, arrow-key navigation

> **Executor instructions**: Follow this plan step by step. Run every
> verification command before moving on. On any STOP condition, stop and
> report. Update this plan's row in `plans/README.md` when done.
>
> **Drift check (run first)**: compare the "Current state" excerpts against
> the live code. Written against a dirty working tree at commit `57ec2d10`
> (branch `codex/memory-ledger-v2`). On mismatch, STOP.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED (keyboard handling must not hijack typing surfaces)
- **Depends on**: none (007 touches NotebookRail too — land this first)
- **Category**: ux
- **Planned at**: commit `57ec2d10`, 2026-07-13

## Why this matters

Obsidian's file explorer has collapsible folders, auto-reveals the active
file, and moves with the keyboard. This rail has none of that: directory
sections are always-expanded `<section>`s, navigating via wikilink/history
does not scroll the rail to where you are, and moving between notes requires
Tab-through-everything or the mouse. On a deep vault the rail is one long
non-foldable list where you lose your place.

## Current state

All paths under `apps/desktop/`.

- `src/features/memory/components/NotebookRail.tsx`
  - Directory entries (lines 84-103) — always expanded:
    ```tsx
    return (
      <section
        data-memory-entry={entry.path}
        data-memory-directory={entry.path}
        aria-labelledby={id}
        className={clsx(depth > 0 && "ml-2 border-l border-line-soft/60 pl-2")}
      >
        <div className="px-2 pb-1.5 pt-1">
          <h2 id={id} className="text-xs font-semibold text-ink-soft">{entry.title}</h2>
          ...
        </div>
        {entry.children.length > 0 && (
          <div className="flex flex-col gap-1">
            {entry.children.map((child) => (<RailEntry ... depth={depth + 1} ... />))}
          </div>
        )}
      </section>
    );
    ```
  - The "Files" bucket already IS a collapsible `<details data-memory-files>`
    with a rotating ChevronRight (lines 216-229) — that's the disclosure
    idiom to reuse:
    ```tsx
    <details data-memory-files className="group/files rounded-[10px] bg-surface-soft/35 px-2 py-1.5">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 rounded px-1 py-1 text-xs font-medium text-muted hover:text-ink-soft">
        <ChevronRight className="h-3 w-3 transition-transform duration-check group-open/files:rotate-90" />
        Files
        <span className="ml-auto tabular-nums text-faint">{model.files.length}</span>
      </summary>
      ...
    </details>
    ```
  - Note rows (lines 42-56): `<button data-memory-entry={artifact.path} className="app-row ..." data-active={selected}>`.
  - Scroll container: line 162, `<div className="flex-1 min-h-0 overflow-y-auto scroll-thin px-3 py-3">`.
- `src/features/memory/components/ArtifactMemoryView.tsx`
  - `primaryOrder` (memo starting ~line 458) already computes the flat
    depth-first list of note paths in rail order — read its construction
    before writing the roving-focus order; reuse it, don't rebuild it.
  - Selection callback passed to the rail: `onSelect={selectFile}` (line 1343).
  - Existing input-guard idiom for global keys (lines 548-551):
    `target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)`.
  - Scroll/focus restore per location exists (effect around lines 645-677,
    uses `data-memory-note-scroll`) — rail reveal is separate and new.

Tests: `tests/memoryNotebook.test.tsx` renders the rail and asserts
directory/note structure via `data-memory-entry` / `data-memory-directory` —
keep those attributes.

## Commands you will need

| Purpose   | Command (run from `apps/desktop/`) | Expected on success |
|-----------|------------------------------------|---------------------|
| Typecheck | `bun run typecheck`                | exit 0              |
| Tests     | `bun test tests/`                  | all pass            |
| Lint      | `bun run lint`                     | exit 0              |

## Scope

**In scope**:
- `apps/desktop/src/features/memory/components/NotebookRail.tsx`
- `apps/desktop/src/features/memory/components/ArtifactMemoryView.tsx` (only: pass selected-path reveal + keyboard order props if needed)
- `apps/desktop/tests/memoryNotebook.test.tsx` (extend)

**Out of scope**:
- `MemoryFileTree.tsx` (`TreeSearch`, `FlatRow`) — unchanged.
- Search-results rendering branch — collapse/arrows apply to the tree
  branch only; search results stay a flat list.
- The border-l styling of nested directories (plan 009 removes it — don't
  fight over the same lines; leave styling as-is here).

## Steps

### Step 1: Collapsible directories with persisted state

Convert the directory `<section>` in `RailEntry` to the same
`<details>/<summary>` disclosure idiom as the Files bucket:

- `<details open={!collapsed} onToggle={...}>` — controlled via a
  `collapsedDirs: Set<string>` state owned by `NotebookRail` (component
  state, persisted to `localStorage` key `memory.rail.collapsed` as a JSON
  array; initialize lazily).
- `<summary>` holds the ChevronRight (rotate via the exact Files-bucket
  classes, `transition-transform duration-check group-open/…:rotate-90`) +
  the existing `<h2>` title + description block.
- Keep `data-memory-entry` and `data-memory-directory` attributes on the
  `<details>` element and `aria-labelledby` semantics.
- Default: everything expanded (empty collapsed set).

**Verify**: `bun test tests/memoryNotebook.test.tsx` still passes (structure
assertions); add a test: clicking a directory summary hides its child rows;
remount with seeded localStorage keeps it collapsed.

### Step 2: Reveal the active note

In `NotebookRail`, add an effect keyed on `selectedPath`:

```tsx
useEffect(() => {
  if (!selectedPath) return;
  const node = scrollerRef.current?.querySelector<HTMLElement>(
    `[data-memory-entry="${CSS.escape(selectedPath)}"]`);
  node?.scrollIntoView({ block: "nearest" });
}, [selectedPath]);
```

(`scrollerRef` = new ref on the line-162 scroll div.) Additionally, if the
selected path sits inside a collapsed directory, remove that directory (and
its ancestors) from `collapsedDirs` first — ancestors are derivable by
splitting the path on `/`. Use `block: "nearest"` so click-driven selection
(already visible) doesn't jump the scroll.

**Verify**: new test — select a note inside a collapsed directory (call the
select handler directly or navigate via a wikilink fixture); the directory
expands. jsdom lacks real scrolling: assert `scrollIntoView` was called
(stub it on `HTMLElement.prototype`).

### Step 3: Arrow-key navigation over the rail (roving tabindex)

- The flat keyboard order = visible note rows in DOM order. Simplest robust
  source: query `[data-memory-entry]` note buttons inside the scroller and
  filter to visible ones (a row inside a closed `<details>` is not rendered
  by the browser but IS in the DOM — check `element.closest("details:not([open])")`
  to exclude).
- Attach a `keydown` handler on the scroll container (not window):
  - ArrowDown/ArrowUp: move focus to next/previous note row
    (`.focus()`), `preventDefault` to stop page scroll.
  - Enter/Space on a focused row already works (they're `<button>`s).
  - Home/End: first/last row.
- Roving tabindex: the selected (or last-focused) row gets `tabIndex={0}`,
  all other rows `tabIndex={-1}` — so Tab enters the list once and leaves it
  in one press. Apply via a prop on `NoteRow`/`FlatRow` rows in the rail.
- Do NOT intercept keys when focus is in the `TreeSearch` input (it's outside
  the scroll container, so the container-scoped listener already avoids it —
  verify).

**Verify**: new tests — with three notes rendered: focus first row,
ArrowDown focuses second; Tab from outside lands on the selected row only;
ArrowDown skips rows inside a collapsed directory.

### Step 4: Full gate

**Verify**: `bun run typecheck && bun run lint && bun test tests/` → all exit 0.

## Test plan

Extend `tests/memoryNotebook.test.tsx` (same mocked-API setup): the five new
tests described in Steps 1–3. Model DOM queries on existing tests in that
file (they already use `data-memory-entry`).

## Done criteria

- [ ] Directories collapse/expand with persisted state; default all-expanded
- [ ] Selecting/navigating reveals the active row (auto-expanding ancestors)
- [ ] ArrowUp/Down/Home/End move through visible note rows; single Tab stop
- [ ] Typing in the search box is never intercepted
- [ ] `bun run typecheck && bun run lint && bun test tests/` exit 0
- [ ] `plans/README.md` updated

## STOP conditions

- Excerpts don't match (drift).
- Converting `<section>`→`<details>` breaks more than the structural
  assertions in `memoryNotebook.test.tsx` (e.g. a11y-tree tests elsewhere) —
  report before rewriting semantics.
- The roving-tabindex interacts badly with the `navigationDisabled` disabled
  state (disabled buttons aren't focusable) — if all rows can be disabled,
  keep `tabIndex` handling but skip `.focus()` on disabled rows; if that
  degenerates, report.

## Maintenance notes

- Plan 007 adds motion to rail content swaps; plan 009 restyles the nested
  border. Both touch `NotebookRail.tsx` — execute 006 → 007 → 009 in that
  order to minimize rebase pain.
- Reviewer: check `CSS.escape` is used on the path in the query selector —
  paths contain `/` and dots.
