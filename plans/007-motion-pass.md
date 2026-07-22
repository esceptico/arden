# 007 — Motion pass: animate the memory view's panel, mode, and content changes

> **Executor instructions**: Follow step by step; run every verification. On
> any STOP condition, stop and report. Update this plan's row in
> `plans/README.md` when done.
>
> **Drift check (run first)**: compare excerpts against live code. Written
> against dirty working tree at commit `57ec2d10` (branch
> `codex/memory-ledger-v2`). On mismatch, STOP.

- **Status**: TODO
- **Commit**: 57ec2d10
- **Severity**: MEDIUM (bundle)
- **Category**: physicality & origin / cohesion / missed opportunities
- **Estimated scope**: 6 files
- **Depends on**: 002 (TabPanels), 006 (rail markup) — land those first

## Problem

Beyond the `mode="wait"` fix (plan 002), the memory view's state changes
teleport where the rest of the app has a crafted motion vocabulary
(RISE_IN/DISSOLVE_OUT poses, uniform ≤4px blur, spring-in/tween-out — all in
`apps/desktop/src/lib/tokens/motion.ts`). Specifically:

1. **Inspector/records panel snaps** — `ArtifactMemoryView.tsx:1318-1323`
   toggles the grid track between `…_0px]` and `…_320px]` with no
   transition; the 320px panel teleports in.
2. **Workspace mode swaps teleport** — `ArtifactMemoryView.tsx:1401-1470`
   renders `RecordDetailPane` / `MemoryEditReview` / `MemoryEditor` /
   `FileDetailPane` from a bare ternary. Entering/exiting edit (Cmd+E) is
   high-frequency and has zero continuity.
3. **Skeleton→content pops** — `MemoryNote.tsx:56-84` swaps
   `<Skeleton>` ↔ `<Markdown>` with a bare conditional. The design doc
   explicitly says: "Skeletons pulse subtly and reveal via crossfade+blur."
4. **Rail content swap teleports** — `NotebookRail.tsx:165-231` replaces
   tree ↔ search-results ↔ skeleton with a bare ternary chain.
5. **Polish**: Forget-confirm alertdialog mounts instantly
   (`MemoryInspector.tsx:260-283`); CopyPath label flips via raw ternary
   (`CopyPath.tsx:16`); inspector rows appear with no motion while
   `RecordListPane` rows RISE_IN (cohesion mismatch).

## Target vocabulary (exact values — all already exported from `src/lib/tokens/motion.ts`)

- `RISE_IN = { opacity: 0, y: 6, filter: "blur(3px)" }` / `RISE_SETTLED = { opacity: 1, y: 0, filter: "blur(0px)" }`
- `DISSOLVE_OUT = { opacity: 0, scale: 0.97, filter: "blur(3px)" }`; exits via `withExit(DISSOLVE_OUT)` (= `EXIT_FAST`: 0.1s, `EASE_OUT` [0.2, 0.8, 0.2, 1])
- `EASE_EMPHASIZED = [0.32, 0.72, 0, 1]`, `MOTION.panel = 0.2`, `MOTION.palette = 0.2`, `MOTION.row = 0.15`
- Reduced motion: global `<MotionConfig reducedMotion="user">` in `App.tsx` covers all motion/react usage — no per-site work.
- House rules (from `docs/design-language.md`, inlined): crossfades are
  synchronized, never `mode="wait"`; blur means defocus, ≤4px on
  content-size elements; exits are tweens one tier quicker than entrances;
  never animate `grid-template-columns` or other layout properties.

## Steps

### 1. Inspector/records panel slide (`ArtifactMemoryView.tsx`)

Keep the grid-column snap (do NOT transition `grid-template-columns` — layout
property, per-frame reflow). Instead animate the aside's *content*: wrap the
records section and `MemoryInspector` mounting in

```tsx
<AnimatePresence initial={false}>
  {rightPanelOpen && (
    <motion.div
      key={recordsOpen ? "records" : "inspector"}
      className="h-full min-h-0"
      initial={{ opacity: 0, x: 24, filter: "blur(3px)" }}
      animate={{ opacity: 1, x: 0, filter: "blur(0px)" }}
      exit={{ opacity: 0, x: 24, filter: "blur(3px)", transition: { duration: MOTION.fast, ease: EASE_OUT } }}
      transition={{ duration: MOTION.panel, ease: EASE_EMPHASIZED }}
    >
      ...existing aside content...
    </motion.div>
  )}
</AnimatePresence>
```

The `x: 24` drift-from-right encodes the panel's spatial origin (its toggle
button sits at the right edge of the toolbar). Precedent: the chat right
sidebar hide (see `DURATION_RIGHT_PANEL_HIDE` comment in `motion.ts:108-112`)
uses the same fade+drift+blur grammar. The keyed motion.div also gives a free
crossfade when switching records ↔ inspector.

### 2. Workspace mode crossfade (`ArtifactMemoryView.tsx:1401-1470`)

Wrap the four-branch ternary in a synchronized AnimatePresence keyed by mode:

```tsx
const workspaceMode = recordsOpen ? "records" : editReview ? "review" : editing ? "editor" : "note";
...
<AnimatePresence initial={false}>
  <motion.div
    key={workspaceMode}
    className="absolute inset-0 min-h-0"
    initial={RISE_IN}
    animate={RISE_SETTLED}
    exit={withExit(DISSOLVE_OUT)}
    transition={{ duration: MOTION.panel, ease: EASE_EMPHASIZED }}
  >
    {…the existing branch content…}
  </motion.div>
</AnimatePresence>
```

The parent `<main data-memory-zone="workspace">` is already
`relative min-h-0 overflow-hidden` (line 1357) — `absolute inset-0` panels
overlap by construction (the exact Home↔AreaRoom pattern in `App.tsx`, whose
comment explains why popLayout is NOT used there). Important: the `key` must
be the MODE, not the note path — note→note swaps stay `TabPanels`' job
(plan 002); double-animating would stack.

### 3. Skeleton→content blur reveal (`MemoryNote.tsx:56-84`)

Wrap the three content branches (error / skeleton / markdown) in a
synchronized crossfade keyed by branch:

```tsx
const bodyState = contentError && !content ? "error" : contentLoading && !content ? "loading" : "ready";
<AnimatePresence initial={false} mode="popLayout">
  <motion.div key={bodyState}
    initial={{ opacity: 0, filter: "blur(3px)" }}
    animate={{ opacity: 1, filter: "blur(0px)" }}
    exit={{ opacity: 0, filter: "blur(3px)", transition: { duration: MOTION.fast, ease: EASE_OUT } }}
    transition={{ duration: MOTION.palette, ease: EASE_EMPHASIZED }}>
    {…branch content…}
  </motion.div>
</AnimatePresence>
```

No `y` drift here — content shouldn't move, just sharpen in (fog-of-war: the
not-yet-resolved arrives by sharpening out of blur). If popLayout collapses
the article column (height-reference gotcha), fall back to keying WITHOUT
AnimatePresence: a single motion.div keyed by `bodyState` re-mounts and
plays `initial→animate` only (enter-only, no exit) — acceptable per "exits
dissolve" being polish, entries being the load-bearing cue.

### 4. Rail swap (`NotebookRail.tsx:165-231`)

Same enter-only pattern as step 3's fallback (the rail swaps on every
keystroke — full AnimatePresence would churn):

```tsx
const railState = searchActive ? (searchLoading && searchResults == null ? "search-loading" : searchError ? "search-error" : searchResults?.length ? "results" : "no-matches") : loading && empty ? "loading" : error ? "error" : "tree";
<motion.div key={railState} initial={{ opacity: 0, filter: "blur(3px)" }} animate={{ opacity: 1, filter: "blur(0px)" }} transition={{ duration: MOTION.row, ease: EASE_EMPHASIZED }}>
```

Guard: keep `initial={false}`-equivalent behavior on first mount by passing
`initial={mounted ? {…} : false}` or accepting the one-time settle — pick
whichever the feel check prefers; do not animate per keystroke *within* the
"results" state (the key only changes when the branch changes).

### 5. Polish trio

- `MemoryInspector.tsx:260-283` (Forget alertdialog): convert the mounting
  `<div role="alertdialog">` to `motion.div` with `initial={RISE_IN}`
  `animate={RISE_SETTLED}` `transition={{ duration: MOTION.panel, ease: EASE_EMPHASIZED }}` —
  a destructive confirm should ease in, slightly deliberate.
- `CopyPath.tsx:16`: wrap the label in the app's existing text-swap
  primitive — grep `BlurSwap` (memory: `src/components/ui/…`); use it with
  `MOTION.check` duration so "Copy path" ⇄ "Copied" dissolves. If `BlurSwap`
  requires a keyed child, key on `state`.
- Inspector link/history rows: do NOT add row entrances (the panel-level
  motion from step 1 covers arrival; per-row springs in a dense provenance
  list would be decoration). Instead note cohesion is resolved by the panel
  reveal — no code change. (Recorded so the finding isn't re-reported.)

## Boundaries

- Do NOT touch `TabPanels.tsx` (plan 002 owns it).
- Do NOT transition `grid-template-columns`, `width`, or `height` anywhere.
- Do NOT add dependencies or new motion constants — only tokens from
  `src/lib/tokens/motion.ts`.
- Markup changes only where a step says (wrapping in motion.div).
- If a step's target doesn't match the live code (drift, or plans 002/006
  changed the region), STOP and report.

## Verification

- **Mechanical**: from `apps/desktop/`: `bun run typecheck && bun run lint && bun test tests/` → all pass. The memory suites simulate open/close/edit flows and will catch broken mounting.
- **Feel check** (renderer via `.claude/launch.json` `renderer`, port 5176, against a live server):
  - Toggle the inspector rapidly: panel drifts in from the right with a soft
    sharpen; mid-animation toggles retarget smoothly (never restart from 0).
  - Cmd+E into edit and Esc/close out: note and editor crossfade, no blank
    frame, no vertical layout jump.
  - Select an uncached note: skeleton pulses then content sharpens in — no
    pop.
  - Type in rail search: tree→results swap is one soft crossfade; further
    keystrokes do NOT re-animate the list.
  - DevTools > Animations at 10% speed: confirm blur never exceeds 3px and
    exits are visibly quicker than entrances.
  - Toggle prefers-reduced-motion (Rendering panel): all of the above become
    instant swaps.
- **Done when**: all feel checks pass and tests are green.
