# Shared Mockup Layout Guides Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one discoverable alignment-guide overlay to all primary Board mockups.

**Architecture:** Shared CSS renders the grid and guide lines. Shared motion code creates and toggles the review-only DOM, while each page continues to consume the same two shared assets.

**Tech Stack:** HTML, CSS, vanilla JavaScript, Bun tests.

## Global Constraints

- Off by default and pointer transparent.
- Toggle via `G`, review menu, and `?guides=1`.
- No page-local guide implementation.

---

### Task 1: Shared guide overlay

**Files:**
- Modify: `apps/desktop/tests/mockupSharedFoundation.test.ts`
- Modify: `docs/mockups/board-system.css`
- Modify: `docs/mockups/board-motion.js`
- Modify: primary `docs/mockups/board-*.html` pages

**Interfaces:**
- Consumes: shared Board tokens and `.dp-review-nav`.
- Produces: `bindLayoutGuides(root)` and `.dp-layout-guides`.

- [x] Add a failing shared-foundation test for query, keyboard, review-menu, and shared CSS behavior.
- [x] Run the focused test and confirm it fails because layout guides are absent.
- [x] Implement the shared overlay and bump the shared motion asset version.
- [x] Run focused and full mockup tests.
- [x] Verify `?guides=1`, the shared review control, and the keyboard binding in the in-app browser.
