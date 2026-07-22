# Board-home ADHD mission-control redesign (2026-07-20)

Direction (settled): dark cockpit + triage deck. Research: docs/mockups/board-home-research.md
+ 5-agent fan-out this session (ADHD motion/reward, eye-candy craft, triage-deck mechanics,
family audit, existing-docs mining) — synthesis in session scratchpad research-directives.md.

## Plan

- [x] `board-motion.js`: added `motion.deck` (exit/promote/return), duration.deckExit 180 / deckPromote 460,
      curve.deckPromoteCss = spring.peek linear(), CSS tokens --motion-deck-*
- [x] `board-home.html`: chrome row · answer headline + capture · deck (center) · ambient strips (bottom floor)
- [x] `board-home.css`: deck stack on --queue-card-offset/-scale-step, tonal-receding slivers, verb kbd chips,
      overlay strip popovers (never reflow), zero-state, light pool (dark only), compact/short media queries
- [x] `board-home.js`: full state machine — 1/2/3 + Enter/H/Z, per-kind verbs, inline reply/reason, snooze presets
      w/ dual wake copy, denominator counts down ("1 of 3" → "1 of 2" → "last one"), append-only arrivals,
      handled ledger, seeded 4-family generative zero mark, capacity line, scenes morning/heavy/clear/quiet
- [x] Verified: screenshots all scenes × themes; animation inventory probe (exit 180 accelerate, promote 460 spring,
      content 220 smooth, blink 150, odometer 420); undo returns card; overflow probe 0 at 880px

## Review
Draft live at http://localhost:7137/board-home.html (append ?demo for the scene plate).
Awaiting user look before any port to apps/desktop.

## Key laws applied

One focal mover per beat · exits tween ≤180ms, promote spring ~420ms settle · zero ambient motion ·
peripheral changes = dissolve-in-place · counts down to zero ("1 of 3" → "1 of 2") · direction is semantic
(approve→right, reject→left, later→down) · no toasts/shimmer/grain · reward = state, not transient ·
absence leaves no scar · verbs ≤3 + H/Z chords · spatial stability absolute
