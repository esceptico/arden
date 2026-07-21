# Board Component Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every equivalent component circumstance in the seven primary Board mockups consume one shared static primitive and one shared behavior controller.

**Architecture:** `board-system.css` remains the only static primitive authority and `board-motion.js` becomes the only interaction authority. Page files retain content, composition, and product callbacks while declaring shared roles through `dp-*` classes and data attributes.

**Tech Stack:** Static HTML/CSS, browser Web Animations through `BOARD_MOTION`, Bun tests, in-app browser verification.

## Global Constraints

- Primary scope is Home, Chat, Automations, Memory, Settings, Area Room, and System Overlays.
- Experimental and reference plates remain unchanged.
- Page files contain no direct Web Animations calls, local timing/easing, primitive state machines, focus/inert forks, or shared token declarations.
- Preserve product content and information architecture.
- Preserve unrelated dirty-worktree changes.
- Use TDD for each behavior or contract change.

---

### Task 1: Add a shared tab-panel controller

**Files:**
- Modify: `docs/mockups/board-motion.js`
- Modify: `apps/desktop/tests/mockupSharedFoundation.test.ts`

**Interfaces:**
- Consumes: `tabs.bind(container, options)` and `content.swap(target, update, options)`.
- Produces: `BOARD_MOTION.tabPanels.bind(container, options)` returning `{ select, sync, destroy, value }`.

- [ ] **Step 1: Write the failing contract test**

Add assertions proving `board-motion.js` exports one tab-panel controller and that primary consumers do not implement their own panel-swap state machines:

```ts
expect(motion).toContain("function bindTabPanels");
expect(motion).toContain("const tabPanels = Object.freeze({ bind: bindTabPanels })");
expect(motion).toContain("tabPanels,");
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `bun test apps/desktop/tests/mockupSharedFoundation.test.ts`

Expected: FAIL because `bindTabPanels` and `tabPanels` do not exist.

- [ ] **Step 3: Implement the controller**

Add `bindTabPanels(container, options)` after the shared content engine. It must:

```js
function bindTabPanels(container, options = {}) {
  const body = options.body || container?.parentElement?.querySelector("[data-tab-panels]");
  const panelSelector = options.panelSelector || "[data-tab-panel]";
  const valueAttribute = options.valueAttribute || "data-tab-value";
  const panelValueAttribute = options.panelValueAttribute || "data-tab-panel";
  const values = () => [...container.querySelectorAll(options.tabSelector || '[role="tab"]')]
    .map(tab => tab.getAttribute(valueAttribute));
  let intent = container.querySelector('[aria-selected="true"]')?.getAttribute(valueAttribute) || values()[0];

  const commit = (value, previousValue) => {
    if (options.render) options.render(value, previousValue);
    else body?.querySelectorAll(panelSelector).forEach(panel => {
      panel.hidden = panel.getAttribute(panelValueAttribute) !== value;
    });
    body?.setAttribute("data-tab", value);
    options.onChange?.({ value, previousValue });
  };

  const tabsController = tabs.bind(container, {
    ...options,
    valueAttribute,
    onChange: ({ value, previousValue }) => {
      if (!value || value === intent) return;
      const previousIntent = intent;
      intent = value;
      const order = values();
      const direction = order.indexOf(value) >= order.indexOf(previousIntent) ? 1 : -1;
      const update = () => commit(value, previousValue || previousIntent);
      if (!body || options.animate === false) update();
      else void content.swap(body, update, { axis: options.axis || "x", direction });
    },
  });

  return Object.freeze({
    select: tabsController.select,
    sync: tabsController.sync,
    destroy: tabsController.destroy,
    get value() { return intent; },
  });
}
const tabPanels = Object.freeze({ bind: bindTabPanels });
```

Export `tabPanels` from the final `motion` object.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `bun test apps/desktop/tests/mockupSharedFoundation.test.ts apps/desktop/tests/mockupSystemContracts.test.ts`

Expected: PASS.

---

### Task 2: Add a shared peek lifecycle and structure

**Files:**
- Modify: `docs/mockups/board-motion.js`
- Modify: `docs/mockups/board-system.css`
- Modify: `apps/desktop/tests/mockupSharedFoundation.test.ts`

**Interfaces:**
- Consumes: `surface.show`, `surface.hide`, `tabPanels.bind`.
- Produces: `BOARD_MOTION.peek.bind(root, options)` with `open`, `close`, `toggle`, `setOpen`, `destroy`, and `isOpen`.
- Produces shared classes `dp-peek-body`, `dp-peek-section`, `dp-peek-section-head`, `dp-peek-list`, and `dp-peek-row`.

- [ ] **Step 1: Write failing lifecycle and styling contracts**

Require:

```ts
expect(motion).toContain("function bindPeek");
expect(motion).toContain("const peek = Object.freeze({ bind: bindPeek })");
for (const name of ["dp-peek-body", "dp-peek-section", "dp-peek-section-head", "dp-peek-list", "dp-peek-row"]) {
  expect(system).toContain(`.${name}`);
}
```

- [ ] **Step 2: Verify RED**

Run: `bun test apps/desktop/tests/mockupSharedFoundation.test.ts`

Expected: FAIL because shared peek lifecycle and row structure are missing.

- [ ] **Step 3: Implement `peek.bind`**

The controller must centralize hidden/aria/inert, trigger `aria-expanded`, Escape, surface motion, first focus, and restoration:

```js
function bindPeek(root, options = {}) {
  const triggers = [...(options.triggers || [])].filter(Boolean);
  const closes = [...(options.closeButtons || root.querySelectorAll("[data-peek-close]"))].filter(Boolean);
  let restoreTarget = null;
  let openIntent = !root.hidden && root.getAttribute("aria-hidden") !== "true";

  const syncTriggers = open => triggers.forEach(trigger => trigger.setAttribute("aria-expanded", String(open)));
  const focusTarget = () => root.querySelector(options.focusSelector || "[data-peek-close], [role=tab], button, a[href], [tabindex]:not([tabindex='-1'])");
  const setOpen = async (open, change = {}) => {
    if (open === openIntent && change.force !== true) return open;
    openIntent = open;
    if (open) {
      restoreTarget = change.trigger || document.activeElement;
      const beforeOpen = () => {
        root.hidden = false;
        root.inert = false;
        root.setAttribute("aria-hidden", "false");
        syncTriggers(true);
        options.beforeOpen?.();
      };
      const shown = await surface.show(root, { axis: options.axis || "x", beforeOpen });
      if (shown && change.focus !== false) focusTarget()?.focus();
      options.afterOpen?.();
      return shown;
    }
    const afterClose = () => {
      root.hidden = true;
      root.inert = true;
      root.setAttribute("aria-hidden", "true");
      syncTriggers(false);
      options.afterClose?.();
      if (change.restoreFocus !== false && restoreTarget?.isConnected) restoreTarget.focus();
    };
    return surface.hide(root, { axis: options.axis || "x", afterClose });
  };
  const open = change => setOpen(true, change);
  const close = change => setOpen(false, change);
  const toggle = change => setOpen(!openIntent, change);
  const onTrigger = event => toggle({ trigger: event.currentTarget });
  const onClose = () => close();
  const onKeyDown = event => { if (event.key === "Escape" && openIntent) close(); };
  triggers.forEach(trigger => trigger.addEventListener("click", onTrigger));
  closes.forEach(button => button.addEventListener("click", onClose));
  document.addEventListener("keydown", onKeyDown);
  syncTriggers(openIntent);
  return Object.freeze({ open, close, toggle, setOpen, destroy() {
    triggers.forEach(trigger => trigger.removeEventListener("click", onTrigger));
    closes.forEach(button => button.removeEventListener("click", onClose));
    document.removeEventListener("keydown", onKeyDown);
  }, get isOpen() { return openIntent; } });
}
const peek = Object.freeze({ bind: bindPeek });
```

- [ ] **Step 4: Promote shared peek content CSS**

Move repeated Chat/Area geometry into `board-system.css` under the `dp-peek` block. Shared rows use the existing token vocabulary and no local dimensions or timing.

- [ ] **Step 5: Verify GREEN**

Run: `bun test apps/desktop/tests/mockupSharedFoundation.test.ts apps/desktop/tests/mockupPressureContracts.test.ts`

Expected: PASS.

---

### Task 3: Migrate Chat and Area Room

**Files:**
- Modify: `docs/mockups/board-chat.html`
- Modify: `docs/mockups/board-chat.css`
- Modify: `docs/mockups/board-chat.js`
- Modify: `docs/mockups/board-area-room.html`
- Modify: `docs/mockups/board-area-room.css`
- Modify: `docs/mockups/board-area-room.js`
- Modify: `apps/desktop/tests/mockupAreaRoom.test.ts`
- Modify: `apps/desktop/tests/mockupSharedFoundation.test.ts`

**Interfaces:**
- Consumes: `motion.peek.bind`, `motion.tabPanels.bind`, and shared `dp-peek-*` classes.
- Produces: no page-local peek/tab state machine or repeated peek row CSS.

- [ ] **Step 1: Write failing migration contracts**

Require both pages to contain the shared structural classes and controller calls. Reject `.peek-row`, `.peek-section-head`, and Area's `changeInspectorView` implementations from local source.

- [ ] **Step 2: Verify RED**

Run: `bun test apps/desktop/tests/mockupAreaRoom.test.ts apps/desktop/tests/mockupSharedFoundation.test.ts`

Expected: FAIL on local selectors and local tab switching.

- [ ] **Step 3: Migrate markup and behavior**

Use `data-tab-value`, `data-tab-panels`, and `data-tab-panel` consistently. Bind each tab set once:

```js
motion.tabPanels.bind(inspectorTabs, {
  body: inspectorBody,
  variant: "line",
  tabSelector: ".dp-peek-tab",
  indicatorSelector: ".dp-peek-tab-indicator",
  indicatorClass: "dp-peek-tab-indicator",
  activeClass: "on",
});
```

Area uses `motion.peek.bind` directly. Chat routes its existing dock-aware state through the shared controller hooks while retaining only docking composition.

- [ ] **Step 4: Remove local primitive CSS**

Delete local peek row, list, section-header, tab, and header implementations now owned by `board-system.css`.

- [ ] **Step 5: Verify GREEN**

Run: `bun test apps/desktop/tests/mockupAreaRoom.test.ts apps/desktop/tests/mockupSharedFoundation.test.ts apps/desktop/tests/mockupTypography.test.ts`

Expected: PASS.

---

### Task 4: Migrate Memory and Automations peeks/tabs

**Files:**
- Modify: `docs/mockups/board-memory.html`
- Modify: `docs/mockups/board-automations.html`
- Modify: `docs/mockups/board-automations.css`
- Modify: `docs/mockups/board-automations.js`
- Modify: `apps/desktop/tests/mockupAutomations.test.ts`
- Modify: `apps/desktop/tests/mockupSharedFoundation.test.ts`

**Interfaces:**
- Consumes: `motion.peek.bind` and `motion.tabPanels.bind`.
- Produces: dynamic Memory render callbacks and Automations schedule render callbacks without local tab/panel motion.

- [ ] **Step 1: Add failing consumer inventory assertions**

Require Memory page peek, Automations trigger peek, and Automations result peek to use `dp-peek-body`; require Memory and Automations scripts to call shared controllers; reject local `schedule-tab-indicator` styling and direct peek lifecycle state.

- [ ] **Step 2: Verify RED**

Run: `bun test apps/desktop/tests/mockupAutomations.test.ts apps/desktop/tests/mockupSharedFoundation.test.ts`

Expected: FAIL on custom schedule tabs and incomplete peek structure.

- [ ] **Step 3: Migrate Memory**

Use the shared line-tab markup in the peek header. Bind the dynamic direction selector with `tabPanels.bind({ body: peekBody, render(value) { renderPeekDirection(value); } })`. Bind the shell with `peek.bind`; keep only product-specific page data and navigation history.

- [ ] **Step 4: Migrate Automations**

Use shared peek lifecycle for trigger and result surfaces. Use a documented shared tab variant for trigger type and route `renderSchedulePanel` through `tabPanels.bind`.

- [ ] **Step 5: Verify GREEN**

Run: `bun test apps/desktop/tests/mockupAutomations.test.ts apps/desktop/tests/mockupSharedFoundation.test.ts apps/desktop/tests/mockupSettingsPolicy.test.ts`

Expected: PASS.

---

### Task 5: Unify menus, sheets, and segmented controls

**Files:**
- Modify: `docs/mockups/board-system.css`
- Modify: `docs/mockups/board-motion.js`
- Modify: `docs/mockups/board-settings.html`
- Modify: `docs/mockups/board-automations.html`
- Modify: `docs/mockups/board-automations.css`
- Modify: `docs/mockups/board-system-overlays.html`
- Modify: `docs/mockups/board-system-overlays.css`
- Modify: `apps/desktop/tests/mockupSharedFoundation.test.ts`
- Modify: `apps/desktop/tests/mockupSettingsPolicy.test.ts`
- Modify: `apps/desktop/tests/mockupSystemOverlays.test.ts`

**Interfaces:**
- Produces shared `dp-menu`, `dp-menu-label`, `dp-menu-item`, `dp-sheet-header`, `dp-sheet-body`, and `dp-sheet-footer` classes.
- Consumes the existing `popover`, `overlay`, `tabs`, and `disclosure` controllers.

- [ ] **Step 1: Write failing primitive-ownership tests**

Require the new shared classes in `board-system.css`, require every primary menu/sheet consumer to use them, and reject local `.model-option`, `.effort-option`, `.sheet-head`, `.sheet-body`, `.sheet-actions`, and equivalent menu-item state rules.

- [ ] **Step 2: Verify RED**

Run: `bun test apps/desktop/tests/mockupSharedFoundation.test.ts apps/desktop/tests/mockupSettingsPolicy.test.ts apps/desktop/tests/mockupSystemOverlays.test.ts`

Expected: FAIL because menu/sheet structures are local.

- [ ] **Step 3: Add shared styling and migrate consumers**

Move only primitive geometry/state to `board-system.css`; retain page-specific grid composition and product text locally. Settings setup uses `dp-sheet`; Automations and Settings menus use `dp-menu`; System Overlays uses shared header/body/footer classes.

- [ ] **Step 4: Normalize segmented controls**

Settings policy and Automations trigger modes consume `dp-segmented` plus the shared measured tabs controller. Remove private indicators and selected-state CSS.

- [ ] **Step 5: Verify GREEN**

Run: `bun test apps/desktop/tests/mockupSharedFoundation.test.ts apps/desktop/tests/mockupSettingsPolicy.test.ts apps/desktop/tests/mockupSystemOverlays.test.ts apps/desktop/tests/mockupAutomations.test.ts`

Expected: PASS.

---

### Task 6: Audit every remaining shared primitive

**Files:**
- Modify: `apps/desktop/tests/mockupSharedFoundation.test.ts`
- Modify: `apps/desktop/tests/mockupSystemContracts.test.ts`
- Modify primary mockup files only where the audit finds a duplicate primitive.

**Interfaces:**
- Produces a complete consumer matrix enforced by tests.

- [ ] **Step 1: Add a primary-source matrix**

Represent the seven primary pages and shared primitive expectations in test data. Cover buttons, icon buttons, fields/search/textareas, switches, tabs/segmented controls, sidebars/resizers, menus/popovers, peeks, sheets/scrims, toasts/tooltips, skeleton/empty/error/status, disclosures, theme, and content swaps.

- [ ] **Step 2: Add duplicate-authority rejection**

Reject local primitive selectors, numeric timing/easing/z-index, direct Web Animations, shared token declarations, and local aria/inert/focus lifecycle functions. Allow page-specific composition selectors only.

- [ ] **Step 3: Verify RED and fix each proven duplicate**

Run: `bun test apps/desktop/tests/mockupSharedFoundation.test.ts apps/desktop/tests/mockupSystemContracts.test.ts`

Expected: initial FAIL for any remaining duplicate; migrate each to the shared implementation until PASS.

- [ ] **Step 4: Run all mockup tests**

Run: `bun test apps/desktop/tests/mockup*.test.ts`

Expected: all tests pass with zero failures.

---

### Task 7: Browser and completion audit

**Files:**
- Modify: `docs/mockups/REDESIGN_LEDGER.md`

**Interfaces:**
- Consumes the completed shared foundation.
- Produces current visual and contract evidence.

- [ ] **Step 1: Browser-verify representative consumers**

Verify Chat and Area Room line tabs; Memory peek switching; Automations trigger/result peeks; Settings setup sheet/menu; stacked System Overlays. For each, check light/dark, keyboard, reduced motion, narrow width, short height, and long content where available.

- [ ] **Step 2: Verify geometry and behavior**

Measure stable peek width, viewport containment, selected indicator style, panel visibility, focus restoration, and Escape order. Confirm the same shared change appears in every matching consumer.

- [ ] **Step 3: Update the redesign ledger**

Replace stale completion claims with current consumer/controller evidence and exact test/browser results.

- [ ] **Step 4: Run final gates**

Run:

```bash
bun test apps/desktop/tests/mockup*.test.ts
git diff --check
```

Expected: zero failures and no whitespace errors.
