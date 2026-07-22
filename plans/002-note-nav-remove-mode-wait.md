# 002 — Remove the ~400ms dead gap when switching memory notes (`mode="wait"`)

> **Executor instructions**: Follow step by step; run every verification. On
> any STOP condition, stop and report. Update this plan's row in
> `plans/README.md` when done.
>
> **Drift check (run first)**: compare the excerpts below against the live
> code. Written against dirty working tree at commit `57ec2d10`
> (branch `codex/memory-ledger-v2`). On mismatch, STOP.

- **Status**: TODO
- **Commit**: 57ec2d10
- **Severity**: HIGH
- **Category**: Interruptibility / easing & duration (motion)
- **Estimated scope**: 1–2 files, small diff

## Problem

Selecting a page in the memory rail — the single highest-frequency action in
the memory view — swaps note content through `TabPanels`, which uses
`AnimatePresence mode="wait"`: the outgoing note must fully exit (~200ms)
before the incoming one starts entering (~200ms). Every selection costs
~400ms of dead time, and rapid selection feels laggy. The project's design
doc (`docs/design-language.md`, motion section) explicitly bans this:
crossfades must be "synchronized — never `mode="wait"`".

```tsx
// apps/desktop/src/components/ui/TabPanels.tsx:41 — current
<AnimatePresence mode="wait" custom={direction} initial={false}>
  <motion.div
    key={value}
    custom={direction}
    variants={SLIDE_PAGE_VARIANTS}
    initial="enter"
    animate="center"
    exit="exit"
    transition={{ duration: MOTION.palette, ease: EASE_EMPHASIZED }}
    className={className}
  >
    {children}
  </motion.div>
</AnimatePresence>
```

`SLIDE_PAGE_VARIANTS` (same file, lines 10-14) slides ±16px horizontally.

Consumers of `TabPanels` (check with `grep -rn "TabPanels" apps/desktop/src`):
- `features/memory/components/FileDetailPane.tsx:28` — keyed on
  `summary?.path ?? "empty"`, wraps the whole `MemoryNote`.
- `features/memory/components/RecordDetailPane.tsx` — record detail swap.
- Possibly others — enumerate before editing.

## Target

Synchronized swap: the incoming panel enters while the outgoing one exits,
overlapping. Exact target for `TabPanels.tsx`:

```tsx
<AnimatePresence mode="popLayout" custom={direction} initial={false}>
  <motion.div
    key={value}
    custom={direction}
    variants={SLIDE_PAGE_VARIANTS}
    initial="enter"
    animate="center"
    exit="exit"
    transition={{ duration: MOTION.palette, ease: EASE_EMPHASIZED }}
    className={className}
  >
    {children}
  </motion.div>
</AnimatePresence>
```

`mode="popLayout"` removes the exiting element from layout flow so both
panels can occupy the same slot during the overlap. Keep duration
`MOTION.palette` (0.2s) and `EASE_EMPHASIZED` ([0.32, 0.72, 0, 1]) — both
from `apps/desktop/src/lib/tokens/motion.ts`; do not introduce new values.

**popLayout caveat (known repo gotcha)**: popping strips the exiting
element's height reference from its parent. The memory app shell hit this
before — see the comment in `apps/desktop/src/app/App.tsx` (~line 256):
"popLayout is NOT used because popping strips the exiting column's h-full
height reference". `FileDetailPane` passes
`className="h-full min-h-0 grid-rows-[minmax(0,1fr)] overflow-hidden"` to the
motion div, whose parent is `<main data-memory-zone="workspace">` with
`relative min-h-0 overflow-hidden` (ArtifactMemoryView.tsx:1357). With
popLayout the exiting div becomes absolutely positioned — it must keep its
size. If the exiting panel visibly collapses during the swap, apply the
fallback: keep `AnimatePresence` default mode (concurrent, no `mode` prop)
and make each panel `absolute inset-0` inside a `relative` wrapper so
enter/exit overlap by construction (this is the exact pattern App.tsx uses
for Home ↔ AreaRoom).

## Repo conventions to follow

- Motion values only from `src/lib/tokens/motion.ts` (`MOTION`,
  `EASE_EMPHASIZED`). Never hand-write durations.
- Reduced motion is handled globally by `<MotionConfig reducedMotion="user">`
  in `App.tsx` — no per-component handling needed.

## Steps

1. Enumerate all `TabPanels` consumers: `grep -rn "TabPanels" apps/desktop/src` — record the list.
2. Edit `apps/desktop/src/components/ui/TabPanels.tsx:41`: `mode="wait"` → `mode="popLayout"`.
3. From `apps/desktop/`: `bun run typecheck && bun test tests/` → all pass.
4. Feel-check (see Verification). If the exiting panel collapses/jumps, revert step 2 and implement the absolute-overlay fallback described in Target, then re-run step 3.

## Boundaries

- Do NOT change `SLIDE_PAGE_VARIANTS`, durations, or eases.
- Do NOT touch `FileDetailPane`/`RecordDetailPane` markup unless the
  fallback requires the `relative`/`absolute inset-0` wrapper.
- Do NOT add dependencies.

## Verification

- **Mechanical**: `bun run typecheck` exit 0; `bun test tests/` all pass
  (memory suites simulate navigation heavily — they are the regression net).
- **Feel check**: run the desktop renderer (`.claude/launch.json` name
  `renderer`, port 5176) against a live server, open Memory, and click
  rapidly between 3–4 notes in the rail:
  - the new note starts appearing *while* the old one is still leaving — no
    blank gap between notes;
  - spamming selection never queues animations or shows a flash of empty
    workspace;
  - the directional slide still reads (new content enters from the side you
    moved toward);
  - record detail swap (open Raw records, click between records) still looks
    correct — same component.
  - In DevTools > Rendering, enable "Emulate CSS prefers-reduced-motion" and
    confirm the swap is instant (MotionConfig handles this).
- **Done when**: no dead-time gap on note switch, tests green, and the
  fallback caveat either didn't trigger or was applied cleanly.


## Execution note (2026-07-13)

Executed as `ad42c7f7`. Discovery during execution: `currentLocation()` in
`ArtifactMemoryView.tsx` used an unscoped `[data-memory-note-scroll]` query
that assumed at most one note panel in the DOM — any synchronized crossfade
(popLayout OR the fallback) briefly mounts two, capturing the stale panel's
scroller and dropping focus tokens (caught by memoryNavigation.test.tsx).
Scope was extended to path-scope that query, mirroring the restore effect's
existing pattern. popLayout's height-collapse caveat did NOT apply: the
workspace <main> is grid-track-sized and position:relative.
