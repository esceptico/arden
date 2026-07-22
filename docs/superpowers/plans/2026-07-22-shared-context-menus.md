# Shared Context Menus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one accessible context-menu system and consistent size-based surface geometry to all active Board mockups.

**Architecture:** `board-motion.js` provides one delegated controller driven by explicit `data-context-actions` contracts. `board-system.css` provides shared compact-menu and large-panel radius roles; active pages only mark meaningful targets.

**Tech Stack:** Static HTML/CSS/JavaScript, Bun tests, in-app browser.

## Global Constraints

- Context menus duplicate visible actions; they are not the sole route.
- Use explicit action metadata, never selector-text or keyword heuristics.
- Compact menus use 10px radius; large panels use 12px radius.
- Preserve all unrelated dirty-worktree changes.

---

### Task 1: Shared geometry and context-menu controller

**Files:**
- Modify: `apps/desktop/tests/mockupSharedFoundation.test.ts`
- Modify: `docs/mockups/board-system.css`
- Modify: `docs/mockups/board-motion.js`

**Interfaces:**
- Consumes: `data-context-actions="action-id:Label,..."` on an element.
- Produces: one `.dp-context-menu` and a bubbling `dp:context-action` custom event with `{ action, target }`.

- [ ] **Step 1: Write failing contract tests** for `--r-menu`, `--r-panel`, shared `.dp-context-menu`, explicit metadata parsing, keyboard opening, viewport clamping, and dismissal.
- [ ] **Step 2: Run** `cd apps/desktop && bun test tests/mockupSharedFoundation.test.ts` and confirm the new assertions fail because the controller and tokens do not exist.
- [ ] **Step 3: Implement shared CSS and JavaScript** with a single delegated menu instance, roving focus, Escape/outside dismissal, and focus restoration.
- [ ] **Step 4: Re-run** the focused test and confirm it passes.

### Task 2: Opt every active mockup into the shared contract

**Files:**
- Modify: `apps/desktop/tests/mockupSharedFoundation.test.ts`
- Modify: `docs/mockups/board-home.html`
- Modify: `docs/mockups/board-chat.html`
- Modify: `docs/mockups/board-automations.js`
- Modify: `docs/mockups/board-memory.html`
- Modify: `docs/mockups/board-settings.html`
- Modify: `docs/mockups/board-area-room.html`
- Modify: `docs/mockups/board-system-overlays.html`

**Interfaces:**
- Consumes: the shared `data-context-actions` controller from Task 1.
- Produces: explicit context menus on at least one meaningful entity class per active page.

- [ ] **Step 1: Write failing coverage tests** requiring explicit context targets in all seven active pages and prohibiting page-local context-menu CSS.
- [ ] **Step 2: Run** `cd apps/desktop && bun test tests/mockupSharedFoundation.test.ts` and confirm failure identifies pages without targets.
- [ ] **Step 3: Add explicit metadata** to stable rows/entities on each page, using page scripts only where markup is generated dynamically.
- [ ] **Step 4: Run** the focused test, then `cd apps/desktop && bun test tests/mockup*.test.ts`.
- [ ] **Step 5: Verify in browser** on Automations and Memory: right-click, keyboard open, menu shape, viewport placement, Escape, outside click, and action feedback.
