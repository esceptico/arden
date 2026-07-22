# 005 — Add one restrained entrance system to every Board mockup

- **Status**: DONE
- **Commit**: cdaf578d
- **Severity**: MEDIUM
- **Category**: Missed opportunities; cohesion; accessibility
- **Estimated scope**: 10 files, about 180 lines

## Problem

Every primary Board mockup appears at its final state on navigation. The shared
runtime already owns content blur, skeleton reveal, reduced motion, durations,
and easing, but it has no page-entry contract. Adding local keyframes to seven
pages would create drift.

`/Users/escept1co/src/interaction-lab/src/studies/FocusProgress.tsx` proves the
useful visual idea: unresolved content sharpens as it becomes ready. Its 3200ms
rAF progress loop is deliberately for long-running work and must not be copied
into navigation. The beUI Text Reveal reference uses per-word springs, 700–900ms
opacity/filter transitions, 50–90ms stagger, and up to 12px blur; that is too
theatrical for a frequently visited desktop workspace.

## Target

- Add `BOARD_MOTION.pageEntrance.bind(root)` and auto-bind it on
  `DOMContentLoaded` when `[data-page-enter]` exists.
- Animate only marked `[data-page-enter-item]` blocks with WAAPI:
  `opacity: 0 → 1`, `translateY(6px) → 0`, `blur(2px) → 0`, 240ms,
  `cubic-bezier(0.23, 1, 0.32, 1)`, and 36ms stagger capped at four steps.
- Items marked `data-page-enter-item="chrome"` use opacity only for 180ms with
  no stagger or spatial movement.
- Reduced motion keeps a 160ms opacity reveal and removes translation and blur.
- Add a shared generated skeleton bridge only to elements marked
  `[data-page-skeleton]`: 180ms hold, 220ms opacity/2px-blur crossfade, no
  layout-property animation, and no shimmer under reduced motion.
- Use skeletons only on Chat, Automations, Memory, and Settings. Home, Area
  Room, and System Overlays enter without skeletons.
- Page content is interactive immediately; animation never blocks pointers.

## Repo conventions to follow

- Motion tokens and runtime live only in `docs/mockups/board-motion.js`.
- Shared structural styling lives only in `docs/mockups/board-system.css`.
- Use the existing strong ease-out and existing 2px blur vocabulary.
- Use WAAPI, transform, opacity, and filter only; do not animate layout.
- Preserve every page’s existing interaction and overlay motion.

## Steps

1. Add failing contracts covering all seven pages, exact shared durations,
   capped stagger, reduced-motion behavior, skeleton scope, and no layout motion.
2. Add page-entry duration/distance tokens and `pageEntrance.bind` to
   `docs/mockups/board-motion.js`; expose it on `BOARD_MOTION` and auto-bind.
3. Add shared skeleton-overlay styling to `docs/mockups/board-system.css`.
4. Mark the major chrome/content regions in Home, Chat, Automations, Memory,
   Settings, Area Room, and System Overlays. Mark only the four dense content
   planes with `data-page-skeleton`.
5. Bump shared motion/system asset cache keys on all seven pages.
6. Run all mockup tests, then navigate through all seven pages in the in-app
   browser and verify the entrance, skeleton scope, and reduced-motion branch.

## Boundaries

- Do not split text into words or characters.
- Do not copy Focus Progress’s 3200ms rAF loop.
- Do not add Motion/React dependencies to static mockups.
- Do not animate tabs, list selection, keyboard actions, or page exit.
- Do not place skeletons on Home, Area Room, or System Overlays.

## Verification

- **Mechanical**: `bun test apps/desktop/tests/mockup*.test.ts`; expect zero
  failures. `git diff --check`; expect no output.
- **Feel check**: navigate Home → Chat → Automations → Memory → Settings → Area
  Room → Overlays. Chrome should quietly fade while content resolves downward
  in four or fewer perceptible beats. Skeletons should appear only on the four
  dense views and crossfade without moving geometry.
- **Reduced motion**: emulate `prefers-reduced-motion: reduce`; confirm a short
  opacity reveal remains, with zero translation, blur, or shimmer.
- **Done when**: every primary page visibly enters through the same shared
  contract, skeleton scope matches the target, and no page adds local entrance
  keyframes or timing values.

## Result

- `bun test apps/desktop/tests/mockup*.test.ts`: 103 passed, 0 failed.
- In-app browser: all seven pages reached `running → done`; skeletons appeared
  only on Chat, Automations, Memory, and Settings and were removed after reveal.
- Browser console: no warnings or errors across the seven-page pass.
