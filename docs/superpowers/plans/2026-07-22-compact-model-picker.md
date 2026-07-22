# Compact Model Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the nested summary picker with a direct compact model list and adjacent effort submenu.

**Architecture:** Keep the existing static mockup files and shared motion/popover primitives. The main menu owns model selection; a small sibling submenu owns effort selection for the active model.

**Tech Stack:** Static HTML, CSS tokens, vanilla JavaScript, Bun tests, in-app browser.

## Global Constraints

- Main menu width is 264px and rows are 32px high.
- Use shared popover surface, radius, shadow, typography, and inset row states.
- No search, Auto mode, provider badges, large headings, or summary page.
- Keyboard-opened surfaces appear immediately; pointer opening uses existing popover motion.

---

### Task 1: Direct model and effort menus

**Files:**
- Modify: `docs/mockups/board-chat.html`
- Modify: `docs/mockups/board-chat.css`
- Modify: `docs/mockups/board-chat.js`
- Modify: `apps/desktop/tests/mockupTypography.test.ts`

**Interfaces:**
- Consumes: existing `motion.popover.sync`, `state.model`, and `state.effort`.
- Produces: `[data-model]` model rows and `[data-effort]` submenu rows synchronized by `syncConfig()`.

- [ ] **Step 1: Add failing structural assertions**

Assert that the picker contains direct model rows, an effort submenu, shared popover classes, no summary panel, and no search or Auto model control.

- [ ] **Step 2: Run the focused tests**

Run: `bun test tests/mockupTypography.test.ts tests/mockupSystemContracts.test.ts`

Expected: FAIL because the current picker still contains `data-config-panel="summary"`.

- [ ] **Step 3: Replace the picker markup and styles**

Render four compact model rows directly. Each row contains the model name, its effort button, and a selected checkmark. Add one adjacent `.model-effort-menu.dp-popover` containing Off, Low, Medium, and High rows. Style the main menu at 264px and both menus with 32px inset rows and shared tokens.

- [ ] **Step 4: Replace nested-panel behavior**

Keep `setConfigOpen()` for the main popover. Add `setEffortOpen(open, model)` to position and synchronize the effort submenu. Model row activation changes the active model; effort activation changes the active model's configured effort. Escape closes effort first, then the main picker.

- [ ] **Step 5: Run tests and browser verification**

Run: `bun test tests/mockupTypography.test.ts tests/mockupSystemContracts.test.ts`

Expected: PASS.

Browser-check light and dark themes, direct model selection, effort selection, outside click, Escape, and compact geometry.
