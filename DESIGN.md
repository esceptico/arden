# ARDEN desktop design system

This is the sole normative human-readable contract for the ARDEN desktop interface. The product should feel calm, exact, and alive: a quiet reading room with operational furniture that appears only when it helps the work.

## Authority map

- `PRODUCT.md`: users, product purpose, and product-level principles.
- `DESIGN.md`: design intent and rules.
- `docs/mockups/board-surfaces.css`: raw surface colors and paired elevation recipes only.
- `docs/mockups/board-system.css`: semantic tokens and shared static primitives.
- `docs/mockups/board-motion.js`: motion values, geometry measurement, and interaction controllers.
- Page mockups: composition and product-specific content only.

If two sources disagree, fix the lower source. Do not add a local override to preserve the disagreement.

## Material model

Every surface is one of two materials:

- **Room:** the edge-to-edge content plane. It is flat, quiet, readable, and scrolls only where the product model requires it.
- **Furniture:** operational chrome such as rails, controls, popovers, peeks, and sheets. It uses the shared surface/elevation ladder, never decorative glass.

Depth is semantic:

1. Page substrate.
2. Anchored sidebars and resting panels.
3. Cards, controls, and page-level popovers.
4. Reserved transition layer.
5. Blocking sheets and dialogs.
6. Reserved transition layer.
7. Popovers nested above a blocking sheet.
8. Emergency/system overlay only.

Elevated surfaces use their paired shadow. Do not add a decorative border around an already elevated surface. Borders are for separators, fields, focus, and meaningful state edges.

## Color and status

- The interface must work in monochrome. One accent is allowed for links, focus, selection, and primary action.
- Text roles are `ink`, `ink-soft`, `muted`, and `faint`. Essential text, including small metadata, must meet WCAG AA.
- Semantic colors are `success`, `warning`, and `danger`; color never carries meaning alone.
- Status is expressed with a word first. A compact glyph may reinforce it when space is constrained; structural colored rails and decorative status dots are forbidden.
- Hover and selection fills are derived from ink so they survive both themes.
- Light and dark are equal contracts, not a fallback pair.

## Typography and density

- Geist and Geist Mono are the committed product families.
- Base body text is 14px. Density comes from spacing and grouping, never from unreadably small text.
- Use the shared fixed type scale; do not introduce local pixel sizes.
- Use tabular numerals for counts, durations, schedules, and changing values.
- Keep reading prose to 65–75 characters per line.
- Interface copy uses sentence case and explains consequences, not implementation detail.

## Geometry and layout

- Shared dimensions, radii, breakpoints, and insets belong in `board-system.css`.
- Page files may set composition with shared tokens but may not redeclare those tokens.
- Standard radius roles: shell, surface, row, control, tag, and mark. Pills are for compact controls, not arbitrary cards.
- One element owns scrolling in each region. Body/root scroll must remain off for fixed-window workspaces.
- Responsive changes are structural: hide/collapse a rail, change pane ownership, or move to master-detail. Do not shrink the type scale.
- Peeks, popovers, and sheets remain inside the viewport and cannot obscure the only primary action.

## Components and states

Shared primitives are the only implementation authority for buttons, icon buttons, fields, textareas, search, switches, segmented controls, tabs, sidebars, resizers, menus, popovers, peeks, sheets, scrims, toasts, tooltips, skeletons, empty states, errors, and status messages.

Every interactive primitive defines default, hover, focus-visible, active, disabled, loading, and error where applicable. Selected and inert are additional structural states. Page code supplies content and state; it does not restyle the primitive.

- Focus is a visible, concentric accent ring.
- Disabled controls remain legible and lose pointer interaction.
- Loading uses a skeleton or in-place receipt; content regions do not use centered spinners.
- Empty states explain what appears here and the next available action.
- Errors preserve valid work, explain impact, and offer a recovery action when one exists.
- The control is the confirmation for local effects. Toasts are reserved for effects that land elsewhere.
- Destructive actions require explicit language and a recoverable path where possible.

## Overlays and focus

The shared overlay order is shell/sticky, popover, peek, scrim, sheet, nested popover, toast, tooltip. Named z-index tokens must encode these roles; arbitrary numeric z-index values are forbidden in page files.

- Opening a blocking sheet makes the background inert and moves focus into the sheet.
- Escape closes only the topmost dismissible layer.
- Closing restores focus to the initiating control when it still exists.
- Outside-click dismissal never steals a committed pointer action.
- Nested overlays close from the top down and never orphan a scrim.
- Long content scrolls inside the overlay while its title and primary actions stay reachable.

## Motion

Motion communicates state change. No bounce or elastic easing.

- Shared durations, curves, distances, blur, springs, and controllers live in `board-motion.js`.
- Page files use shared controllers and CSS variables; they contain no local durations, cubic-beziers, or direct Web Animations calls.
- Entering content may settle; exits are quicker tweens. Both remain interruptible.
- Blur means continuous defocus and never snaps on or off.
- Do not animate width, height, padding, or margin. Commit layout immediately, then animate an isolated transform/opacity/clip presentation layer.
- Keyboard-repeatable actions update without choreography.
- Reduced motion preserves the same state transition and focus result with instant or crossfade behavior.

## Product state vocabulary

Use runtime terms rather than page-specific synonyms:

- Connection: disconnected, connecting, connected, reconnecting, auth required.
- Run: pending, running, awaiting approval, awaiting input, awaiting auth, completed, failed, cancelled, interrupted, stale.
- Approval: pending, approving, approved, denying, denied, expired, cancelled.
- Content: loading, ready, empty, partial, error.

Home, Chat, Automations, Memory, Settings, Area Room, Agent Hub, and system overlays must map these states consistently.

## Accessibility and pressure tests

Before a surface is complete, verify:

- Light and dark contrast, including metadata and placeholders.
- Keyboard order, focus-visible, Escape, activation, and focus restoration.
- Reduced motion.
- 200% zoom.
- Compact, narrow, single-sidebar, and wide layouts.
- Short windows and internal-scroll ownership.
- Long translated labels, unbroken content, and large results.
- Multiple open layers and collision boundaries.

## Process

1. Search for the primitive or token before creating one.
2. Fix the shared class or controller, then verify every consumer.
3. Add a failing regression before changing behavior.
4. Keep `docs/mockups/REDESIGN_LEDGER.md` current with evidence.
5. Treat browser rendering as required evidence for visual completion; static source checks prove contracts, not pixels.

## Rejected patterns

Decorative glass, gradient text, bounce/elastic motion, layout-property animation, arbitrary z-index values, local token forks, border-plus-shadow ghost cards, structural status color, shrinking fonts for density, per-page component variants, decorative shimmer, and toasts for effects that complete at the control.
