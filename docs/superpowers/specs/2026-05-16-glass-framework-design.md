# Glass Framework Design

## Goal

Create a reusable glass material framework for arden so frosted surfaces and instruments share one visual recipe. The framework should make glass look physical: translucent tint, backdrop blur with saturation, white-alpha rim, and inset top highlight.

This replaces ad hoc glass recipes in components such as `GlassToggle` and the existing `.glass-pane*` family over time. Dense reading surfaces and settings forms should remain mostly solid unless they are floating chrome.

## Non-Goals

- Do not make every card or settings row glass.
- Do not add heavy blur to repeated list rows.
- Do not use `opacity` on glass containers.
- Do not nest live `backdrop-filter` surfaces by default.
- Do not preserve the old light-mode `.glass-pane` assumption that glass means a solid linen panel.

## Material Model

The framework has CSS tokens for the material recipe:

- `--glass-tint`
- `--glass-blur`
- `--glass-saturate`
- `--glass-border`
- `--glass-rim`
- `--glass-shadow`
- `--glass-radius`

Every live glass variant must apply the recipe in this order:

1. `background: rgba(...)` tint
2. `backdrop-filter: blur(...) saturate(...)`
3. `-webkit-backdrop-filter: blur(...) saturate(...)`
4. white-alpha `border`
5. `box-shadow` with inset top highlight plus ambient drop shadow

## Variants

`glass-clear`

Low tint and low blur. Use when content behind should remain partly readable, or when the surface is an instrument sitting over motion.

`glass-frosted`

Default floating chrome. Use for composer-like overlays, compact panels, and controls where background context should become an abstract color wash.

`glass-heavy`

Higher blur and stronger tint. Use for modal/popup attention surfaces where readability inside the surface matters more than seeing through it.

`glass-static`

No live `backdrop-filter`. Use inside an existing glass surface or inside a stacking context that would make backdrop sampling incorrect. It should visually match `glass-heavy` closely enough for popovers and menus.

## Tone

Each live variant supports:

- `data-tone="light"`
- `data-tone="dark"`
- no explicit tone, which follows app theme defaults

Both tones use white-alpha tint, white-alpha border, and white-alpha rim. Dark glass is not black glass; it is lower-alpha refractive white.

## React Layer

Add a thin helper component:

```tsx
<GlassSurface variant="frosted" tone="auto" radius="md">
  ...
</GlassSurface>
```

The component should only map props to classes/data attributes. It must not own a separate visual recipe.

Initial props:

- `variant?: "clear" | "frosted" | "heavy" | "static"`
- `tone?: "auto" | "light" | "dark"`
- `radius?: "sm" | "md" | "lg" | "pill"`
- `className?: string`
- standard `div` props

## Instrument Layer

Controls such as `GlassToggle` should consume the framework rather than duplicating it.

The first reusable instrument should be `GlassSegmentedControl` or a revised `GlassToggle`:

- track uses `glass-clear` or `glass-frosted`
- active pill uses a lightweight glass sub-surface without live backdrop blur by default
- live blur remains opt-in for expensive repeated usage
- motion uses transform and width, not left/right

Future instruments can include `GlassButton`, `GlassIconButton`, and compact floating toolbars.

## Adoption Rules

- Floating app chrome can use live glass.
- Dense settings panels, message content, code blocks, and long lists should stay solid.
- Nested menus/popovers inside glass must use `glass-static` unless tested visually.
- Avoid ancestor `transform`, `filter`, `will-change`, or `isolation` on live glass containers when backdrop sampling matters.
- If a component needs glass, it should use framework classes or `GlassSurface`, not inline material styles.

## Migration

1. Add framework tokens and classes in `apps/desktop/src/styles.css`.
2. Add `apps/desktop/src/components/GlassSurface.tsx`.
3. Rebuild the current toggle on top of the framework.
4. Replace `.glass-pane`, `.glass-pane-thick`, and `.glass-pane-static` usage with the new classes.
5. Remove duplicated material recipes from components.

The implementation should avoid touching unrelated layout and settings behavior.

## Testing

Manual visual verification is required on a colorful or gradient background, not a flat white background.

Check:

- light and dark theme
- clear, frosted, heavy, and static variants
- nested popover/static case
- no obvious lag in settings tools/MCP controls
- text remains opaque inside glass surfaces
- Safari-compatible prefixed backdrop rule exists

Automated tests are limited to component rendering and class/prop behavior where useful.
