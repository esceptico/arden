# Mission Control Open Canvas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle Mission Control as an open Memory-like canvas with compact Settings-like action rows without changing its information or behavior.

**Architecture:** Keep the existing HTML regions and JavaScript controllers. Consolidate the visual change directly into the existing `board-home.css` component rules, then protect the hierarchy and behavior with mockup regression tests.

**Tech Stack:** Static HTML, CSS custom properties, shared `board-motion.js`, Bun tests.

## Global Constraints

- Preserve all existing information, actions, and motion.
- Keep page scrolling disabled; action rows and the status drawer own local scrolling.
- Use shared Board tokens and primitives only.
- Do not modify Memory, Settings, Chat, or production application files.

---

### Task 1: Lock the open-canvas contract

**Files:**
- Modify: `apps/desktop/tests/mockupHome.test.ts`

**Interfaces:**
- Consumes: `board-home.html`, `board-home.css`, `board-home.js`
- Produces: regression coverage for open regions, compact rows, local scrolling, and preserved controls

- [ ] **Step 1: Write the failing test**

Add assertions that capture, focus, action list, and status switcher use transparent open surfaces without elevation; the focus and switcher use separators; action and drawer scrolling remains local; existing interaction hooks remain present.

- [ ] **Step 2: Run test to verify it fails**

Run: `bun test apps/desktop/tests/mockupHome.test.ts`

Expected: FAIL because the current regions still use raised surfaces.

### Task 2: Consolidate the hybrid visual system

**Files:**
- Modify: `docs/mockups/board-home.css`
- Test: `apps/desktop/tests/mockupHome.test.ts`

**Interfaces:**
- Consumes: shared Board surface, spacing, typography, shadow, radius, motion, and icon tokens
- Produces: open focus/capture regions, compact action rows, and quiet bottom status navigation

- [ ] **Step 1: Replace raised region styles in place**

Set `.capture`, `.focus-panel`, `.attention-list`, and `.status-ribbon` to transparent, unelevated regions. Use existing edge tokens for horizontal separators and keep the status drawer raised.

- [ ] **Step 2: Compact the action rows**

Reduce row height and padding, use plain icons except semantic urgency, and retain the measured action-button reveal unchanged.

- [ ] **Step 3: Run focused tests**

Run: `bun test apps/desktop/tests/mockupHome.test.ts`

Expected: PASS.

- [ ] **Step 4: Run full verification**

Run: `bun test apps/desktop/tests/mockupTypography.test.ts apps/desktop/tests/mockupSharedFoundation.test.ts apps/desktop/tests/mockupTextSwap.test.ts apps/desktop/tests/mockupSettingsPolicy.test.ts apps/desktop/tests/mockupHome.test.ts`

Expected: 0 failures. Also run JavaScript syntax checks and `git diff --check`.
