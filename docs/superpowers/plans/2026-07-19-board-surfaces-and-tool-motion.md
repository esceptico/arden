# Board Surfaces and Tool Motion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the mockup system to Board, adopt Fluid Functionalism's shared eight-level surface ladder, and make the existing three-row tool ticker smoother without adding novel motion.

**Architecture:** `board-surfaces.css` owns raw surface and shadow values. `board-system.css` owns semantic aliases, dimensions, and shared components. `board-motion.js` owns measured list motion and exposes the same global controller used by every mockup.

**Tech Stack:** Static HTML/CSS/JavaScript mockups, Web Animations API, Bun tests, happy-dom.

## Global Constraints

- Work in `/Users/escept1co/src/arden`; do not create a worktree.
- Preserve unrelated dirty-worktree changes.
- Surface colors and shadow recipes live only in `docs/mockups/board-surfaces.css`.
- Keep exactly three visible live tool rows.
- Do not animate the live list container height.
- Respect `prefers-reduced-motion`.

---

### Task 1: Rename the mockup system to Board

**Files:**
- Rename: `docs/mockups/desk-paper-chat.{html,css,js}` to `docs/mockups/board-chat.{html,css,js}`
- Rename: `docs/mockups/desk-paper-system.css` to `docs/mockups/board-system.css`
- Rename: `docs/mockups/desk-paper-motion.{html,js}` to `docs/mockups/board-motion.{html,js}`
- Rename: `docs/mockups/desk-paper-icons.js` to `docs/mockups/board-icons.js`
- Rename: `docs/mockups/desk-paper-plate.html` to `docs/mockups/board-memory.html`
- Rename: `docs/mockups/desk-paper-settings.html` to `docs/mockups/board-settings.html`
- Rename: `docs/mockups/desk-paper-language.html` to `docs/mockups/board-language.html`
- Rename: `docs/mockups/desk-paper-redesign-references.md` to `docs/mockups/board-references.md`
- Modify: `apps/desktop/tests/mockup*.test.ts`, `apps/desktop/tests/iconSwap.test.tsx`, `apps/desktop/tests/icons.test.tsx`, `apps/desktop/tests/progressiveBlur.test.tsx`

**Interfaces:**
- Produces: `window.BOARD_MOTION` and `board-*` asset references.

- [ ] Replace every internal asset path and global name from `desk-paper` / `DESK_PAPER_MOTION` to `board` / `BOARD_MOTION`.
- [ ] Update test fixture paths and assertions to the Board names.
- [ ] Run `rg -n "desk-paper|DESK_PAPER_MOTION" docs/mockups apps/desktop/tests` and expect hits only in historical specs/plans.
- [ ] Run the four mockup tests and expect all to pass.

### Task 2: Add the canonical shared surface ladder

**Files:**
- Create: `docs/mockups/board-surfaces.css`
- Modify: `docs/mockups/board-system.css`
- Modify: `apps/desktop/tests/mockupSharedFoundation.test.ts`

**Interfaces:**
- Produces: `--surface-1` through `--surface-8`, `--shadow-1` through `--shadow-8`, `--surface-muted`, and semantic aliases in `board-system.css`.

- [ ] Add failing assertions that all eight surface/shadow levels exist in `board-surfaces.css` and do not appear in local mockups.
- [ ] Add the exact Fluid Functionalism light and dark ladders, including dark inset highlights/rings and stacked drops.
- [ ] Import `board-surfaces.css` from `board-system.css` immediately after `typeset.css`.
- [ ] Map `--paper` to level 1, `--panel` to level 2, `--card` to level 3, and dialog surfaces to level 5.
- [ ] Map `--e2`, `--e3`, and `--e5` to their paired canonical shadows; remove custom low/mid/float shadow recipes.
- [ ] Run `bun test apps/desktop/tests/mockupSharedFoundation.test.ts` and expect PASS.

### Task 3: Apply semantic elevation without doubled outlines

**Files:**
- Modify: `docs/mockups/board-system.css`
- Modify: `docs/mockups/board-chat.css`
- Modify: `docs/mockups/board-settings.html`
- Modify: `docs/mockups/board-memory.html`
- Modify: `apps/desktop/tests/mockupTypography.test.ts`

**Interfaces:**
- Consumes: canonical levels from Task 2.
- Produces: page-level panels at levels 2–3, floating popovers at level 3, and sheets/dialogs at level 5.

- [ ] Add failing tests for sidebar level 2, card/popover level 3, and sheet level 5 mappings.
- [ ] Remove explicit borders from elevated surfaces that already receive a canonical shadow ring.
- [ ] Keep explicit borders only on separators, inputs, status accents, and intentionally outlined content.
- [ ] Replace component-specific bordered shadow branches with paired `--shadow-N` tokens.
- [ ] Run typography and shared-foundation tests and expect PASS.

### Task 4: Smooth the existing live tool ticker

**Files:**
- Modify: `docs/mockups/board-motion.js`
- Modify: `docs/mockups/board-chat.js`
- Modify: `apps/desktop/tests/mockupTextSwap.test.ts`
- Modify: `apps/desktop/tests/mockupSharedFoundation.test.ts`

**Interfaces:**
- Extends: `layout.animateListChange({ before, staying, entering, leaving, distance, blur })`.
- Keeps: `limits.traceTail === 3`.

- [ ] Add failing assertions for a `4px` trace distance, shared blur, three-row limit, and text-swap suffix handoff.
- [ ] Change trace-row entry to opacity `0`, `translateY(4px)`, and light blur; settle to opacity `1`, zero translation, zero blur.
- [ ] Change exit to opacity `0`, `translateY(-4px)`, and light blur while retained rows continue using FLIP transforms.
- [ ] Use `motion.textSwap.swap()` for `now` to elapsed-time settlement.
- [ ] Preserve immediate container reflow and reduced-motion instant updates.
- [ ] Run mockup motion tests and expect PASS.

### Task 5: Verify the complete Board mockups

**Files:**
- Test: all files above.

**Interfaces:**
- Produces: a clean static/mockup test result and residual naming/surface audit.

- [ ] Run `bun test apps/desktop/tests/mockupSharedFoundation.test.ts apps/desktop/tests/mockupTypography.test.ts apps/desktop/tests/mockupSettingsPolicy.test.ts apps/desktop/tests/mockupTextSwap.test.ts` and expect zero failures.
- [ ] Run `rg -n "desk-paper|DESK_PAPER_MOTION|bordered-shadow" docs/mockups apps/desktop/tests` and inspect every residual hit.
- [ ] Run `git diff --check` for all Board and mockup test files and expect no output.
- [ ] Verify light/dark and narrow/wide layouts in the browser when file-page automation permits; otherwise report the browser limitation without claiming visual verification.
