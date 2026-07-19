# Board surfaces and tool motion

## Scope

Rename the desktop mockup system from Desk/Paper to **Board**, align its surfaces with the local Fluid Functionalism reference, and smooth the existing live tool-call ticker without introducing a new visual effect.

## Naming

- Mockups use the `board-*` prefix.
- Shared foundations are `board-system.css`, `board-surfaces.css`, `board-motion.js`, and `board-icons.js`.
- The rename changes mockup artifacts and their internal references only. It does not rename shipped product concepts.

## Surface foundation

`board-surfaces.css` is the only source of surface colors and elevation shadows. It mirrors Fluid Functionalism's eight paired levels:

- Light surfaces: `#FAFAFA`, `#FCFCFC`, then white through level 8.
- Dark surfaces: `#171717` through `#484848` in additive steps.
- Light shadows use the canonical stacked hairline/drop ladder.
- Dark shadows use the canonical inset highlight, inset ring, outer hairline, and stacked drops.

`board-system.css` imports that file and exposes semantic aliases; local mockups may not redefine ladder values.

Semantic mapping:

- Page substrate: level 1.
- Sidebar and resting panels: level 2.
- Cards, controls, and page-level popovers: level 3.
- Dialogs and sheets: level 5 (`substrate + 4`).
- Popovers nested in dialogs: level 7 (`substrate + 2`).

An elevated surface uses its paired shadow recipe instead of an additional explicit outline. Explicit borders remain only for separators, inputs, state accents, and intentionally bordered content.

## Live tool-call motion

Keep the proven rolling tail and maximum of three visible tool rows.

When a tool arrives:

1. The previous live row settles and its suffix swaps from `now` to elapsed time.
2. The new row enters from `translateY(4px)`, light blur, and zero opacity.
3. Retained rows move with FLIP position transforms.
4. The oldest row exits upward by `4px` with a light blur/dissolve.

The list container changes height immediately. There is no height spring, glow, pulse, scale, thread drawing, or novelty animation. Reduced-motion mode performs an immediate state update.

## Verification

- Static tests assert the canonical eight surface and shadow levels live only in the shared surface file.
- Tests assert semantic aliases and prevent elevated components from combining a border with a ring-bearing shadow.
- Tool-motion tests assert the three-row limit, shared motion tokens, suffix handoff, FLIP movement, and reduced-motion behavior.
- Verify Chat, Memory, and Settings in light/dark and narrow/wide layouts.
