# Shared Context Menus Design

## Goal

Add predictable right-click menus to meaningful entities across every active Board mockup without making context menus the only route to an action.

## Interaction contract

- Context menus duplicate actions already available in the visible UI.
- Only meaningful entities opt in: navigation rows, work/session rows, files/tabs, settings rows, and overlay examples.
- Right-click, `ContextMenu`, and `Shift+F10` open the same menu.
- Arrow keys move focus; Enter/Space activates; Escape and outside click close.
- The menu stays inside the viewport and returns focus to its trigger.
- Destructive actions are separated and never execute silently in these mockups.

## Shared architecture

`board-motion.js` owns one delegated controller. Targets declare a stable action set with `data-context-actions`; the controller renders one transient `.dp-context-menu.dp-menu` and dispatches `dp:context-action` from the target. Page scripts may react to that event, while the shared controller handles universal actions such as opening, copying a label, and showing mockup feedback.

`board-system.css` owns menu geometry and state. Page files only opt targets in; they do not define menu radius, spacing, focus, or positioning.

## Geometry

- Pills remain limited to controls and compact row states.
- Compact context menus use `--r-menu: 10px`.
- Large panels and popovers use `--r-panel: 12px`.
- Window and sidebar shells remain `--r-shell: 16px` in the round profile.
- `--surface-radius` aliases `--r-panel`, so large Automations surfaces become soft-square without page-specific overrides.

## Initial action sets

- Navigation/entity: Open, Open in new view, Copy link.
- Automation: Open, Run now, Duplicate, Pause/Resume.
- Chat/session: Open, Rename, Copy link, Archive.
- Memory file/tab: Open, Copy path, Close tab where applicable.
- Settings: Open section, Copy deep link.
- Area/work: Open, Copy link, Set aside where applicable.

These are prototype actions. They demonstrate placement and feedback; they do not add hidden product capabilities.
