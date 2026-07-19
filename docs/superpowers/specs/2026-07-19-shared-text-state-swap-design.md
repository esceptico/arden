# Shared Text State Swap

## Goal

Add one shared text-state transition for short, in-place status and action labels across the Desk/Paper mockups. It must not become a generic animation for navigation or content changes.

## Decision

Extend `docs/mockups/desk-paper-motion.js` with a shared `textSwap` primitive based on transitions.dev `text-states-swap`:

- old text exits upward by 4px with 2px blur and opacity fade;
- text changes only after the exit completes;
- new text enters from 4px below;
- each phase lasts 150ms with `ease-in-out`;
- reduced motion changes text immediately;
- repeated requests serialize or replace the pending destination without overlapping classes or timers.

The shared API is responsible only for text. Existing components remain responsible for icon swaps, width morphs, loading loops, and final state.

## Approved Uses

1. Settings refresh controls: `Refresh → Done → Refresh`. Keep the existing icon loop, icon swap, fixed right anchor, and width morph.
2. Motion reference refresh demo: use the same primitive so the reference demonstrates the shipped behavior instead of a hard text cut.
3. Memory edit signature: `editing … → edited just now` and equivalent saved-state acknowledgements.
4. Chat trace status: `Working → Worked` when a live run completes. Shimmer stops before the text exit begins.
5. Inline approval action labels only when the action has an observable pending/result phase, such as `Allow once → Approving → Approved`, before the approval row closes.
6. Visible copy labels: `Copy → Copied → Copy`. Pair with the existing icon swap.

## Explicit Exclusions

- Icon-only copy buttons: icon swap only.
- Chat/session/page titles, breadcrumbs, tabs, model names, effort values, and navigation labels.
- Numeric counters: use the shared spinning counter.
- Full content, panels, or routes: use the appropriate field, panel, or page transition.
- Static status words that do not change during the interaction.

## Shared Contract

`DESK_PAPER_MOTION.textSwap.swap(element, nextText, options)` returns whether a change was scheduled.

Options:

- `animate: false` performs an immediate accessible update.
- `label` optionally supplies the final accessible label when visible text is intentionally abbreviated.

The primitive preserves the element, changes only `textContent`, and cleans up all transient classes after completion. Consumers must update `aria-label`, `aria-busy`, and surrounding state at the same semantic transition point.

## Styling

The canonical `.t-text-swap`, `.is-exit`, and `.is-enter-start` rules live once in the shared mockup motion foundation. They use semantic variables:

- `--text-swap-dur: 150ms`
- `--text-swap-translate-y: 4px`
- `--text-swap-blur: 2px`
- `--text-swap-ease: ease-in-out`

No mockup may copy or override the transition rules locally. Layout containers may independently animate width when the label length changes.

## Verification

- Unit/static contract verifies the shared CSS, API, reduced-motion path, and cleanup behavior.
- Settings and Motion show the same `Refresh → Done → Refresh` sequence.
- Memory and Chat state changes do not overlap or leave stale transition classes.
- Rapid repeated triggers cannot interleave text.
- Icon-only copy controls remain icon-only.
- Light/dark and reduced-motion behavior remain visually stable.
