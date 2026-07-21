# Board component unification

## Goal

Make the seven primary Board mockups consume one implementation for the same UI and interaction circumstances. A change to a peek, tab switch, menu, sheet, or other shared primitive must propagate to every matching consumer without page-local CSS or behavior changes.

Primary scope: Home, Chat, Automations, Memory, Settings, Area Room, and System Overlays. Experimental and reference plates remain outside the maintained component contract.

## Authority and boundaries

- `DESIGN.md` remains the normative design contract.
- `board-surfaces.css` owns raw materials and elevation recipes.
- `board-system.css` owns shared primitive geometry, appearance, and states.
- `board-motion.js` owns shared behavior, measurement, focus, and motion.
- Page files own product content, composition, and data mapping only.

Pages may select a documented component variant. They may not restyle a primitive, reproduce its state machine, call Web Animations directly, or fork duration, easing, focus, aria, or inert behavior.

## Component model

### Peek

`dp-peek` is one shell with shared width, surface, viewport containment, opening/closing motion, aria/inert state, Escape handling, and focus restoration.

Shared structure:

- `dp-peek-header`
- `dp-peek-title`
- `dp-peek-tabs`
- `dp-peek-actions`
- `dp-peek-body`
- `dp-peek-section`
- `dp-peek-section-head`
- `dp-peek-list`
- `dp-peek-row`

The shared behavior controller binds triggers, close actions, optional tabs, and panels. Pages configure content and optional docking; they do not implement open/close or tab-panel transitions.

### Tabs and tab panels

Tabs have two semantic variants:

- `line`: navigation between peer content views, including Activity/Sources and Memory peek directions.
- `segmented`: compact mode or policy selection inside a form or toolbar.

One tab controller owns selection, roving tabindex, arrow/Home/End keys, indicator measurement, interruption, and reduced motion. One tab-panel controller composes it with directional content swapping and supports either existing panels or a page render callback.

Changing indicator styling or panel-switch motion in the shared implementation changes all matching consumers.

### Menus and popovers

`dp-popover` owns surface placement and visibility. Shared menu composition owns label, item, selected/check state, metadata, disabled state, keyboard focus, and long-label truncation. Pages provide item data and commit callbacks only.

### Sheets

`dp-sheet` owns viewport geometry, surface, header/body/footer structure, internal scrolling, focus trapping, inert background, Escape order, and focus restoration. Settings setup and System Overlay sheets use the same structure and controller; product-specific bodies remain page compositions.

### Existing primitives

Buttons, icon buttons, fields, search, switches, segmented controls, sidebars, resizers, scrims, toasts, tooltips, skeletons, empty states, errors, status messages, and disclosures remain shared. The migration audit must remove local equivalents and local state styling.

Sidebar contents may differ by product role, but shell geometry, resizing, hidden state, navigation-row states, and shell controls remain shared.

## Consumer migration

- Chat and Area Room: use the same peek, line tabs, tab panels, sections, lists, and rows.
- Memory: use the shared peek shell and line-tab controller; dynamic link-direction rendering goes through the shared tab-panel render callback.
- Automations: use the shared peek lifecycle. Trigger-type selection uses the shared tab-panel controller with the appropriate documented variant. Run Result uses the shared peek header/body structure.
- Settings: migrate setup to the shared sheet structure and controller; menus and segmented/policy controls consume shared variants.
- Home and System Overlays: retain page composition while consuming shared buttons, menus, popovers, sheets, states, and overlay behavior.

## Accessibility and behavior

- Tab selection and panel state remain synchronized for pointer and keyboard input.
- Roving tabindex and arrow/Home/End navigation are identical across tab sets.
- Opening a peek or sheet moves focus to a useful control; closing restores the initiating control.
- Escape closes only the topmost dismissible surface.
- Reduced motion preserves the same committed state and focus result.
- Long labels truncate or wrap inside the component contract; page code does not patch overflow.

## Verification

Contract tests must:

- enumerate every primary consumer of peeks, tabs, menus, sheets, and existing primitives;
- require shared classes and controllers for each matching circumstance;
- reject page-local primitive selectors, direct animations, timing values, focus/inert logic, and duplicate state rules;
- prove shared variants cover all legitimate differences;
- keep light/dark, keyboard, reduced-motion, narrow/short, 200% zoom, long-content, and overlay-collision pressure checks.

Browser verification must exercise representative consumers rather than only the shared showcase: Chat and Area Room tabs, Memory peek switching, Automations trigger/result peeks, Settings setup sheet/menu, and stacked System Overlays.

## Non-goals

- Do not migrate experimental/reference plates.
- Do not create a client framework, custom-element layer, or JS-rendered markup factory for static mockups.
- Do not force semantically different controls into one visual variant.
- Do not change product content or information architecture during this consolidation.
