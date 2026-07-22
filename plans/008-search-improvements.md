# Plan 008: Search that finds everything — include index/file pages, group results, add in-note find

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
- **Risk**: LOW
- **Depends on**: 010 recommended first (it unifies the duplicated search
  code paths this plan edits; if 010 hasn't run, apply Step 1 to BOTH sites)
- **Category**: ux
- **Planned at**: commit `57ec2d10`, 2026-07-13

## Why this matters

Rail search filters results to "notebook pages" only, silently excluding
index/README documents and everything in the "Files" bucket — pages that are
perfectly browsable by click return "No matches" when searched. And there is
no within-note find at all. Both are table-stakes for the Obsidian-style
vault the product is aiming at.

## Current state

All paths under `apps/desktop/`.

- Filtering happens twice in `src/features/memory/components/ArtifactMemoryView.tsx`
  (the duplicated search paths — see plan 010):
  - line ~384 (the `search` callback): `setSearchResults(response.artifacts.filter(isNotebookPage));`
  - line ~423 (the debounced typing effect): `setSearchResults(response.artifacts.filter(isNotebookPage));`
- The predicate, `src/features/memory/lib/notebookIndex.ts:58-60`:
  ```ts
  export function isNotebookPage(artifact: MemoryArtifactSummary): boolean {
    return isNotebookResourcePath(artifact.path) && !isIndexDocumentPath(artifact.path);
  }
  ```
  `isNotebookResourcePath` (lines 43-49) excludes reserved directories,
  `changelog/`, and reserved root files — those exclusions are intentional
  (machine-layer files) and stay.
- Results render as one flat "Results" list in
  `src/features/memory/components/NotebookRail.tsx:170-187` using `NoteRow`.
- Selection: search results are already selectable — `selectedMeta` falls
  back to `searchResults?.find(...)` (`ArtifactMemoryView.tsx:438-440`), and
  navigation to index documents works (the rail's tree links to them).
- No `Cmd+F` handler exists anywhere in the feature (global handlers: history
  at lines 545-557, edit at ~1250). The memory view is inside a `PageModal`;
  browser-native Cmd+F is unreliable in Electron — an in-app find bar is
  needed.
- The note body scroller is `MemoryNote.tsx:47`
  `<div data-memory-note-scroll className="flex-1 min-h-0 overflow-y-auto scroll-thin">`
  and markdown renders via the shared `Markdown` component into plain DOM.

## Commands you will need

| Purpose   | Command (run from `apps/desktop/`) | Expected on success |
|-----------|------------------------------------|---------------------|
| Typecheck | `bun run typecheck`                | exit 0              |
| Tests     | `bun test tests/`                  | all pass            |
| Lint      | `bun run lint`                     | exit 0              |

## Scope

**In scope**:
- `apps/desktop/src/features/memory/components/ArtifactMemoryView.tsx`
- `apps/desktop/src/features/memory/components/NotebookRail.tsx`
- `apps/desktop/src/features/memory/components/MemoryFindBar.tsx` (create)
- `apps/desktop/src/features/memory/components/MemoryNote.tsx` (mount find bar)
- tests: extend `tests/memoryNotebook.test.tsx`; create `tests/memoryFind.test.tsx`

**Out of scope**:
- Backend search (`GET /memory/artifacts?q=`) — client consumes it as-is.
- `isNotebookResourcePath` reserved-path rules — machine files stay hidden.
- Fancy find features (regex, replace, match case toggle) — literal
  case-insensitive find only.

## Steps

### Step 1: Widen the search filter

In both search sites in `ArtifactMemoryView.tsx` (lines ~384 and ~423),
replace `.filter(isNotebookPage)` with
`.filter((artifact) => isNotebookResourcePath(artifact.path))` (import
already available in the file's import block from `notebookIndex`). Index
documents and Files-bucket pages now appear in results; reserved/changelog
paths remain excluded.

**Verify**: extend `tests/memoryNotebook.test.tsx`: a search fixture
containing a `README.md`-pathed summary and a files-bucket summary renders
both as result rows. `bun test tests/memoryNotebook.test.tsx` → pass.

### Step 2: Group results by kind in the rail

In `NotebookRail.tsx`'s results branch (lines 170-187): partition
`searchResults` into pages (`isNotebookPage`) and the rest. Render two
groups using the existing section-header idiom (line 172's
`text-2xs font-semibold uppercase tracking-[0.08em] text-faint`): "Notes"
then "Indexes & files". Use `NoteRow` for notes and `FlatRow` (from
`MemoryFileTree.tsx` — already imported) for the second group so file paths
show. Omit an empty group entirely.

**Verify**: test from Step 1 extended: both group headers present when both
kinds match; only "Notes" when only notes match.

### Step 3: In-note find bar (Cmd/Ctrl+F)

Create `MemoryFindBar.tsx`:

- Props: `{ scrollerSelector: string; open: boolean; onClose: () => void }` —
  or accept a `RefObject<HTMLElement>`; match how `MemoryNote` can hand over
  its scroller (add a ref alongside `data-memory-note-scroll`).
- UI: a compact floating bar pinned top-right inside the note article
  (absolute within the `article`'s relative container): text input,
  "n of m" counter (`tabular-nums`), prev/next chevron buttons, close (X).
  Styling: match the floating toolbar chrome at `ArtifactMemoryView.tsx:1359`
  (`rounded-[9px] border border-line-soft bg-bg-main/90 p-1 shadow-sm backdrop-blur`).
- Mechanics (no dependency): walk text nodes of the scroller with
  `document.createTreeWalker(root, NodeFilter.SHOW_TEXT)`, collect
  case-insensitive match ranges, highlight via the CSS Custom Highlight API
  (`CSS.highlights.set("memory-find", new Highlight(...ranges))` + a
  `::highlight(memory-find)` rule in `styles.css`) — Electron's Chromium
  supports it. Active match gets a second Highlight ("memory-find-active")
  and `range.startContainer.parentElement.scrollIntoView({ block: "center" })`.
  Recompute on query change; clear highlights on close/unmount.
- Keys: Enter = next, Shift+Enter = prev, Escape = close (stopPropagation so
  the PageModal stays open).
- Wire-up in `MemoryNote.tsx` (or `ArtifactMemoryView` if state must clear on
  navigation — clear find state when `summary.path` changes either way).
  Cmd/Ctrl+F opens it, modeled on the history-keys guard at
  `ArtifactMemoryView.tsx:545-557`, but ONLY when not editing
  (`editing == null`) and not in the records/review modes.

**Verify**: `tests/memoryFind.test.tsx` — jsdom lacks `CSS.highlights`; stub
it (`globalThis.CSS.highlights = new Map()` plus a `Highlight` class stub)
and assert: match count computed for a fixture note; Enter advances the
active index and wraps; Escape closes. Model test setup on
`tests/memoryNotebook.test.tsx`.

### Step 4: Full gate

**Verify**: `bun run typecheck && bun run lint && bun test tests/` → all exit 0.

## Done criteria

- [ ] Searching a term that appears only in an index/README or Files-bucket page returns it
- [ ] Results are grouped Notes / Indexes & files
- [ ] Cmd+F opens an in-note find with count, next/prev, highlight, Escape-close
- [ ] Find state resets on note navigation
- [ ] `bun run typecheck && bun run lint && bun test tests/` exit 0
- [ ] `plans/README.md` updated

## STOP conditions

- Excerpts don't match (drift) — especially if plan 010 already unified the
  search paths (then Step 1 is ONE site; adjust, don't duplicate).
- `CSS.highlights` is unavailable in the app's Electron runtime (check
  `typeof CSS !== "undefined" && "highlights" in CSS` in the renderer console)
  — STOP and report; the fallback (wrapping text nodes in `<mark>`) mutates
  the Markdown DOM and needs a separate decision.
- Index documents turn out not to be navigable from search results (selection
  falls through) — report; don't patch `selectedMeta` ad hoc.

## Maintenance notes

- If search later moves server-side to full-text content search, keep the
  two-group presentation; only the fetch changes.
- The find bar deliberately doesn't search across notes — that's rail
  search's job; keep the split.
