# Compact model picker

## Goal

Replace the nested summary picker with a compact, direct model menu that matches the shared mockup geometry.

## Interaction

- The composer trigger shows the selected model and effort.
- Opening it shows the available models immediately.
- Each 32px row shows model name, configured effort, and the selected checkmark.
- Selecting a model updates the trigger without closing the picker.
- Activating a row's effort value opens a small adjacent effort submenu.
- Keyboard navigation and Escape follow the existing menu behavior.

## Visual contract

- 264px main menu; shared popover surface, radius, border, shadow, and typography.
- Compact control-sized type; no search, Auto mode, provider badges, large headings, or redundant summary page.
- Selected and hover states use the shared inset row treatment.
- Keyboard-opened surfaces appear immediately; pointer opening may use the existing subtle popover motion.

## Verification

- Browser-check light and dark themes.
- Verify model selection, effort selection, outside click, and Escape.
- Keep the existing mockup contract tests passing.
