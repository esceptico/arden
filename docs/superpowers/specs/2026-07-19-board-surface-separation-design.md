# Board Surface Separation

## Goal

Make page, sidebar, cards, controls, and floating surfaces distinguishable in both themes without returning to heavy borders or duplicated component-level shadows.

## Design

- Keep `board-surfaces.css` as the only source of surface colors and elevation recipes.
- Increase visual separation between surfaces 1, 2, and 3. These represent the page, resting furniture, and raised cards/controls.
- Preserve the existing 4–8 elevation progression for floating panels, sheets, and nested transient surfaces.
- Strengthen only low-elevation shadows enough to separate adjacent neutral surfaces.
- Dark shadows use one restrained inner highlight and a compact dark drop. Light shadows use one ambient edge and compact vertical drops.
- Do not add borders to elevated components. Borders remain reserved for separators, inputs, status accents, and intentionally outlined content.
- Do not add per-component shadow overrides in Chat, Settings, or Memory.

## Validation

- Shared-foundation tests assert the revised surface and shadow constants.
- Existing mockup tests remain green.
- Verify page/sidebar/card/popover distinction in light and dark themes after reload.
