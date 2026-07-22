# Mockup Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every primary Board mockup reachable through native links from every other primary mockup.

**Architecture:** Existing navigation controls become native anchors. A shared, static `<details class="dp-review-nav">` switcher provides all seven destinations; its styling lives in `board-system.css`, while each HTML file retains real `href` values so navigation works without JavaScript.

**Tech Stack:** Static HTML, shared CSS, Bun tests.

## Global Constraints

- Preserve existing visual classes and page-local button behavior.
- Cross-page navigation must work without JavaScript.
- Current destinations use `aria-current="page"`.
- No route registry or client-side router.

---

### Task 1: Navigation contract

**Files:**
- Create: `apps/desktop/tests/mockupNavigation.test.ts`
- Modify: `docs/mockups/board-system.css`
- Modify: `docs/mockups/board-home.html`
- Modify: `docs/mockups/board-chat.html`
- Modify: `docs/mockups/board-chat.js`
- Modify: `docs/mockups/board-automations.html`
- Modify: `docs/mockups/board-memory.html`
- Modify: `docs/mockups/board-settings.html`
- Modify: `docs/mockups/board-area-room.html`
- Modify: `docs/mockups/board-system-overlays.html`

**Interfaces:**
- Consumes: relative sibling mockup URLs.
- Produces: native anchors and `.dp-review-nav` shared styling.

- [x] **Step 1: Write the failing contract test**

Create a Bun test that loads each primary HTML file and asserts these exact route pairs are present:

```ts
const routes = {
  "Home": "./board-home.html",
  "Chat": "./board-chat.html",
  "Automations": "./board-automations.html",
  "Memory": "./board-memory.html",
  "Settings": "./board-settings.html",
  "Area Room": "./board-area-room.html",
  "Overlays": "./board-system-overlays.html",
};
```

For every page, assert each destination appears in `.dp-review-nav`, the page's own link has `aria-current="page"`, and no `.dp-review-nav` destination is a `<button>`. Also assert Home and Chat rail labels use the expected native anchors, Home Area rows link to Area Room, Chat session rows link to Chat, plain Chat session clicks prevent navigation while switching scenes, and Automation settings links to Settings.

- [x] **Step 2: Verify RED**

Run:

```bash
bun test apps/desktop/tests/mockupNavigation.test.ts
```

Expected: FAIL because `.dp-review-nav` and native rail anchors do not exist.

- [x] **Step 3: Implement native links**

Add the same static switcher to all seven pages:

```html
<details class="dp-review-nav">
  <summary>Mockups</summary>
  <nav aria-label="Mockup pages">
    <a href="./board-home.html">Home</a>
    <a href="./board-chat.html">Chat</a>
    <a href="./board-automations.html">Automations</a>
    <a href="./board-memory.html">Memory</a>
    <a href="./board-settings.html">Settings</a>
    <a href="./board-area-room.html">Area Room</a>
    <a href="./board-system-overlays.html">Overlays</a>
  </nav>
</details>
```

Set `aria-current="page"` on the matching anchor in each file. Convert existing cross-page buttons to anchors while preserving their classes. Chat session anchors keep their scene behavior by calling `event.preventDefault()` for ordinary clicks before `setScene(...)`; browser-modified clicks retain native link behavior. Keep disclosure buttons, settings subpages, theme controls, and current-page scene controls unchanged.

- [x] **Step 4: Add centralized switcher styling**

In `board-system.css`, style `.dp-review-nav` as a compact fixed review-only control using existing surface, spacing, radius, z-index, focus, and motion tokens. Its closed state must not cover primary content; its menu must stay within the viewport.

- [x] **Step 5: Verify GREEN**

Run:

```bash
bun test apps/desktop/tests/mockupNavigation.test.ts apps/desktop/tests/mockup*.test.ts
```

Expected: all mockup tests pass.

- [x] **Step 6: Browser verification**

Serve `docs/mockups`, open Home, follow the switcher through all seven pages, and verify each destination loads, the matching item is current, and the switcher remains usable at 720×600.

- [x] **Step 7: Preserve the dirty mockup worktree**

Every implementation target is part of the user's untracked redesign work. Leave the navigation changes uncommitted so completing this task does not stage or commit unrelated mockup content.
