# Mission Control open-canvas redesign

## Goal

Make Mission Control feel like the same quiet working environment as Memory and Settings while preserving its broader entry-point information model. The page must remain useful at a glance for an ADHD user: no page scrolling, no detached right column, and no information hidden merely to make the screen sparse.

## Layout

- Keep the existing left application sidebar and top Mission Control header.
- Keep universal capture as the first control, but render it as a quiet field on the canvas rather than a card.
- Render Suggested focus as an open editorial region. Use spacing and a single separator for hierarchy instead of a boxed card.
- Render Needs action as a compact Settings-style row list with local vertical scrolling.
- Keep Updates, In motion, Areas, and System in the existing bottom switcher and local drawer. Restyle the switcher as quiet navigation rather than a dashboard ribbon.
- The page itself never scrolls. Only the action list and an opened status drawer may scroll.

## Visual system

- Use the shared Board tokens, surface ladder, typography, icon sizing, motion, and control primitives.
- Prefer the canvas background plus tonal hover states. Reserve raised surfaces for the status drawer and transient controls.
- Use separators between rows; avoid card-within-card structure, heavy outlines, and decorative elevation.
- Keep fixed product typography and the current responsive structural breakpoints.

## Interaction and motion

- Preserve every existing action, pin override, continuous/today lens, row-action reveal, status drawer, sidebar behavior, theme behavior, and toast.
- Preserve the measured row-action reveal; labels and icons must never overlap.
- Keep motion state-driven and shared through `board-motion.js`. Do not introduce page-load choreography or local motion constants.
- Honor reduced motion through the existing shared foundation.

## Responsive behavior

- Wide: open focus region above the locally scrolling action list; status switcher remains at the bottom.
- Narrow: focus details stack, secondary row reasons hide, and row actions follow the existing compact behavior.
- Sidebar visibility must not alter the page's internal component geometry beyond the available workspace width.

## Verification

- Add structural regression checks for the open-canvas hierarchy, local scrolling, and preserved interaction contracts.
- Run all Board mockup tests, JavaScript syntax checks, and `git diff --check`.
- Verify light and dark themes, wide and narrow layouts, focus pinning, row-action reveal, status switching, and local scrolling in the rendered page before claiming visual completion.

## Out of scope

- Removing or reprioritizing current Mission Control information.
- Changing Memory, Settings, Chat, production application behavior, or backend data contracts.
- Replacing shared Board components with Mission Control-specific variants.
