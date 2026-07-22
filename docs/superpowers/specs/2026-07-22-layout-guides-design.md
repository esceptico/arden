# Shared Mockup Layout Guides

## Goal

Add a review-only alignment overlay to every primary Board mockup so spacing and shared edges can be checked visually without editing each page.

## Interaction

- Off by default.
- Toggle with `G`, a shared review-menu action, or `?guides=1`.
- Ignore the shortcut while typing in a field.
- Preserve the query parameter when the user reloads or shares the URL.

## Visuals

- Hovering a component draws guides through its left, center, right, top, middle, and bottom edges.
- The selected component and its parent are outlined; a badge reports dimensions and parent padding.
- Clicking replaces the pinned selection; Shift-click adds or removes components; Escape clears all selections.
- No static grid or arbitrary viewport guides are shown.
- The overlay never captures pointer events and is excluded from product UI semantics.

## Boundaries

The implementation lives in `board-system.css` and `board-motion.js`. Page files only update the shared asset version. This is a mockup review utility, not production UI.
