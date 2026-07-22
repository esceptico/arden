# Board style authority

Active screens are `board-home`, `board-chat`, `board-automations`, `board-memory`, `board-settings`, `board-area-room`, and `board-system-overlays`.

- `board-surfaces.css`: raw surface colors and elevation recipes.
- `board-system.css`: semantic tokens and reusable component primitives.
- `board-motion.js`: shared interaction and motion behavior.
- Page files: layout and content composition only. They may consume shared roles; they must not define primitive radii, interaction-state colors, or spacing scales.

Allowed page-local exceptions:

- Appearance color swatches: literal colors are the content being previewed.
- Gradient mask stops: literals describe alpha geometry, not product color.
- Memory review plate: prototype-only annotation chrome, not a product primitive.
- Data visualization geometry and values.

Every exception must stay isolated to its named demo or visualization selector. New exceptions require this file and the shared-foundation test to be updated together.
