# Board redesign ledger

This is the durable execution record for the desktop redesign. Read it after every context compaction and update it only from current repository evidence.

## Goal

Finish the Board redesign as one coherent system: one token source, one component vocabulary, one state model, one motion engine, complete operational surfaces, and verified behavior under real desktop pressure.

## Non-negotiable contracts

- `PRODUCT.md` defines the product register.
- Root `DESIGN.md` will be the sole human-readable design contract.
- `board-surfaces.css` owns raw surface colors and elevation recipes only.
- `board-system.css` owns semantic tokens and shared static component styling.
- `board-motion.js` owns shared interaction geometry and motion behavior.
- Page mockups may compose shared primitives but may not redefine shared tokens, durations, easing, elevation, focus, disabled, loading, error, or overlay behavior.
- Motion communicates state, uses shared tokens, avoids layout-property animation, and honors reduced motion.
- Every interactive primitive covers default, hover, focus-visible, active, disabled, loading, and error where applicable.
- Required validation: light, dark, keyboard-only, reduced motion, narrow/short windows, 200% zoom, long content, and overlay collisions.

## Status vocabulary

- `[ ]` open
- `[-]` in progress or only indirectly covered
- `[x]` complete with evidence recorded below

## Work ledger

### L0 — Preserve and inventory the current system

- [x] Identify the primary plates: Home, Chat, Automations, Memory, Settings, language, motion, surfaces.
- [x] Identify existing mockup regression suites under `apps/desktop/tests/mockup*.test.ts`.
- [x] Confirm the redesign is a dirty, largely untracked user worktree; do not reset, overwrite, or mass-stage it.
- [x] Record the current focused-suite baseline and residual detector findings.

Evidence:

- `docs/mockups/board-{home,chat,automations,memory,settings}.html`
- `apps/desktop/tests/mockupSharedFoundation.test.ts`
- Detector residuals include overshooting easing and layout-property transitions; Geist is an intentional product choice, not a defect.
- Baseline command: `bun test apps/desktop/tests/mockupSharedFoundation.test.ts apps/desktop/tests/mockupTypography.test.ts apps/desktop/tests/mockupSettingsPolicy.test.ts apps/desktop/tests/mockupTextSwap.test.ts apps/desktop/tests/mockupHome.test.ts apps/desktop/tests/mockupAutomations.test.ts` → 51 pass, 4 stale Home-contract failures.

### L1 — Canonical human-readable design contract

- [x] Create root `DESIGN.md` as the sole normative description of materials, tokens, type, spacing, geometry, components, state, motion, overlays, accessibility, and responsive behavior.
- [x] Convert `docs/design-language.md` into a short historical pointer so it cannot drift as a second contract.
- [x] Make `board-language.html` point to `DESIGN.md` and remove contradictory “open decision” language for settled rules.
- [x] Add a test proving there is one normative design contract and that product/status/motion language agrees with it.

Evidence:

- `bun test apps/desktop/tests/mockupDesignContract.test.ts` → 2 pass, 0 fail.

### L2 — Token and elevation single source of truth

- [x] Prove raw surfaces and shadows exist only in `board-surfaces.css`.
- [x] Prove semantic color, type, spacing, radius, breakpoint, geometry, state, and motion aliases exist only in shared foundation files.
- [x] Remove remaining locally declared shared dimensions or state tokens from the production-shaped Board plates.
- [x] Define explicit overlay/z-index roles: shell, sticky, popover, peek, scrim, sheet, nested, toast, tooltip, and demo.
- [x] Add tests rejecting local shared-token redefinitions and numeric z-index values.

Evidence:

- `mockupSharedFoundation.test.ts` proves surface/token ownership.
- `mockupSystemContracts.test.ts` proves named z-index roles and rejects numeric z-index in every primary plate.

### L3 — Shared component and state vocabulary

- [x] Existing shared primitives: button, icon button, sidebar, tabs, popover, peek, toast, progressive blur, theme, content swap.
- [x] Canonicalize field/search, switch, segmented control, status message, skeleton, empty state, error state, scrim, sheet, and tooltip styling; page-specific multiline composers remain compositions.
- [x] Define and demonstrate each applicable state: default, hover, focus-visible, active, disabled, loading, error, selected, and inert.
- [x] Replace page-local primitive variants with shared classes.
- [x] Add contract tests for component ownership and full state vocabulary.

Evidence:

- Settings and Automations consume `dp-switch`; Settings fields and segments consume `dp-field` and `dp-segmented`.
- `mockupSharedFoundation.test.ts` rejects page-local field, switch, and segmented-control definitions.
- `board-system-overlays.html` demonstrates default, loading, empty, error, selected, disabled/inert overlay behavior.

### L4 — Shared motion and geometry

- [x] Existing shared engine covers text/content swaps, tabs, sidebars, popovers, peeks, progressive blur, sheets, lists, theme, and icon swaps.
- [x] Remove overshooting/bounce easing from the language and demos.
- [x] Remove layout-property transitions (`width`, `height`, `padding`, `margin`) from production-shaped mockups; use transform/opacity or immediate geometry.
- [x] Ensure page files contain no local durations, cubic-beziers, Web Animations calls, or duplicate geometry controllers.
- [x] Add tests for reduced-motion parity and forbidden local motion patterns.

Evidence:

- `mockupSystemContracts.test.ts` rejects page-local Web Animations, positive timing literals, cubic Béziers, and layout-dimension animation.
- `board-motion.js` owns overlapping content swaps and overlay focus/dismissal behavior.
- Focused Board foundation suite → 53 pass, 0 fail.

### L5 — System and overlay plate

- [x] Create `board-system-overlays.html` using shared primitives.
- [x] Cover Command Palette, Quick Capture, approval review/diff, Tool Viewer, Markdown Viewer, Mermaid Viewer, toast, tooltip, and stacked sheet behavior.
- [x] Specify focus trap, focus restoration, escape/outside-click behavior, dismissal priority, inert background, and collision handling.
- [x] Cover default, loading, empty, error, and long-content states.
- [x] Add structural and interaction regression tests.

Evidence:

- `board-system-overlays.{html,css,js}` and the shared `BOARD_MOTION.overlay` stack.
- `bun test apps/desktop/tests/mockupSystemOverlays.test.ts` → 4 pass, 0 fail.

### L6 — Area Room and Agent Hub plate

- [x] Create a production-grounded `board-area-room.html` rather than inventing a parallel product model.
- [x] Cover Area asks, outcomes, agent presence, open loops, activity, related areas, and pinned composer.
- [x] Cover the global Agent Hub: approvals, todos, agents, workflows, automations, Sources, and parent-child navigation.
- [x] Cover running, awaiting approval/input/auth, completed, failed, cancelled, interrupted, and stale states.
- [x] Add structural and state regression tests.

Evidence:

- `board-area-room.{html,css,js}` composes the current Area/agent runtime vocabulary.
- `bun test apps/desktop/tests/mockupAreaRoom.test.ts` → 4 pass, 0 fail.

### L7 — Cross-surface state matrix

- [x] Create one state matrix for Home, Chat, Automations, Memory, Settings, Area Room, Agent Hub, and overlays.
- [x] Cover loading, empty, partial data, error, offline, reconnecting, auth required, disabled, destructive confirmation, long content, and success landing elsewhere.
- [x] Chat-specific: interrupted streaming, queued messages, compaction, approval allowed/denied/expired, cancelled/stale run, and source failure.
- [x] Automations-specific: never run, running, completed, failed, paused, unsafe trigger, and unavailable integration.
- [x] Ensure state names derive from product/runtime contracts, not page-specific aliases.

Evidence:

- `BOARD_STATE_MATRIX.md` points back to authoritative `DESIGN.md` and records coverage only.
- `bun test apps/desktop/tests/mockupStateMatrix.test.ts` → 2 pass, 0 fail.

### L8 — Responsive and accessibility pressure suite

- [x] Verify 200% zoom and widths spanning compact, narrow, one-sidebar, and wide layouts.
- [x] Verify short windows and internal-scroll ownership.
- [x] Verify both sidebars, peeks, popovers, and tier-3 sheets cannot obscure the primary action or escape the viewport.
- [x] Verify light/dark contrast, including essential metadata at 4.5:1.
- [x] Verify keyboard order, focus-visible, escape, delete/tab behavior, and focus restoration.
- [x] Verify reduced motion and long/unbroken content.
- [x] Add automated geometry/contract checks and browser screenshots where reliable.

Evidence:

- `mockupPressureContracts.test.ts` checks 4.5:1 metadata contrast, global focus/reduced-motion rules, viewport constraints, long-content wrapping, inert stacking, and focus restoration.
- Playwright rendered all seven primary plates in light and dark at 1440×900, plus 720×600 at 2× density for Home, Chat, Area Room, and overlays.
- Runtime audit at 1024×640 with reduced motion: zero page errors, zero horizontal overflow, all visible floating surfaces inside the viewport, and reduced-motion Escape restored the room.
- Stacked overlay audit: command focus trapped, Tool Viewer became topmost, Escape returned to Approval Review, and focus returned to the stacked trigger.
- Visual review caught and fixed the Automations switch-selector regression and compact Agent Hub overlap.
- Screenshots: `/Users/escept1co/.codex/visualizations/2026/07/21/019f858c-4508-7c11-8843-b4f7b071b8c2/redesign/`.

### L9 — Completion audit

- [x] Run every Board/mockup test with zero failures.
- [x] Run detector and inspect every residual finding against the product contract.
- [x] Run repository formatting/diff checks for all touched files.
- [x] Render every primary plate in light/dark and representative viewport pressure cases.
- [x] Re-read this ledger and attach evidence to every completed item.
- [x] Confirm no duplicate token, component-state, motion, or design-contract authority remains.

Evidence:

- `bun test apps/desktop/tests/mockup*.test.ts` → 78 pass, 0 fail, 1,059 assertions across 12 files.
- Impeccable detector → 9 inspected findings: Geist is the explicit Board typeface and is paired with Geist Mono; the five Memory em dashes are fixture/editor prose rather than system copy.
- Targeted `git diff --check` passed; trailing-whitespace scan across every redesign contract, plate, shared foundation file, and test returned no matches.
- Light/dark and pressure renders plus runtime audits are recorded under L8; every primary plate rendered without page errors or horizontal overflow.
- Contract suites reject page-local shared-token declarations, numeric z-indexes, local timing/easing/Web Animations, layout-property motion, primitive redefinitions, and a second normative design contract.

### L10 — Shared component propagation

- [x] Make `board-system.css` the only static primitive authority and `board-motion.js` the only shared behavior authority.
- [x] Route Chat, Area Room, Memory, and Automations peeks through `peek.bind` and tab content through `tabPanels.bind`.
- [x] Move peek rows, section headers, menus, sheet regions, and trigger segments into shared primitives.
- [x] Add a seven-page consumer matrix so missing or private component variants fail contracts.
- [x] Verify stable peek geometry, tab content, focus state, narrow/short pressure, light/dark, and zero runtime errors.

Evidence:

- Shared behaviors: `BOARD_MOTION.peek.bind` and `BOARD_MOTION.tabPanels.bind`.
- Shared structure: `dp-peek-*`, `dp-menu-*`, and `dp-sheet-*` in `board-system.css`.
- Browser audit: Area Room, Chat, Memory, Automations, Settings, and System Overlays; peeks stayed 340 px at desktop and inside a 700×560 viewport.
- Full Board contract suite passes with zero failures; `git diff --check` passes.

## Execution order

`L1 → L2 → L3 → L4 → L5 → L6 → L7 → L8 → L9`

Do not skip forward to new plates while shared contracts are still ambiguous. New plates must consume the foundation rather than defining it.
