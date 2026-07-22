# Shared Text State Swap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one shared transitions.dev-style text-state swap and use it only for visible status/action labels in the Desk/Paper mockups.

**Architecture:** `docs/mockups/desk-paper-motion.js` owns the CSS, timing tokens, timer cleanup, reduced-motion behavior, and `DESK_PAPER_MOTION.textSwap.swap(element, nextText, options)` API. Each mockup calls that API at an existing semantic state change; icon swaps, counters, width morphs, and navigation transitions remain separate.

**Tech Stack:** Static HTML/CSS/JavaScript, Bun tests, Happy DOM.

## Global Constraints

- Work in the existing checkout; do not create a worktree.
- Keep the implementation mockup-only.
- Use `150ms`, `4px`, `2px`, and `ease-in-out` for the text swap.
- Preserve the transitions.dev class contract and reduced-motion guard.
- Do not animate navigation titles, tabs, model/effort values, numeric counters, or icon-only copy controls.
- Do not duplicate primitive CSS or orchestration in individual mockups.

---

### Task 1: Shared text-swap primitive

**Files:**
- Create: `apps/desktop/tests/mockupTextSwap.test.ts`
- Modify: `docs/mockups/desk-paper-motion.js:2-36, 389-439, 527-585, 594-622`

**Interfaces:**
- Consumes: `matchMedia`, DOM class lists, and the existing shared motion token injection.
- Produces: `DESK_PAPER_MOTION.textSwap.swap(element, nextText, { animate?, label? }): boolean`.

- [ ] **Step 1: Write the failing primitive tests**

Create a Happy DOM harness that evaluates `desk-paper-motion.js`. Assert that `textSwap.swap(label, "Done")` adds `.is-exit`, changes text after 150ms, removes transient classes, and lets a second request during exit replace the pending destination. Add reduced-motion and `animate: false` assertions for immediate updates. Assert the injected stylesheet contains `.t-text-swap`, `.is-enter-start`, and the reduced-motion rule.

- [ ] **Step 2: Run the primitive test and verify RED**

Run: `cd apps/desktop && bun test tests/mockupTextSwap.test.ts`

Expected: FAIL because `DESK_PAPER_MOTION.textSwap` and its CSS tokens do not exist.

- [ ] **Step 3: Implement the shared primitive**

Add `textSwap: 150` to `duration`, then add the semantic tokens:

```js
"--text-swap-dur": `${duration.textSwap}ms`,
"--text-swap-translate-y": "4px",
"--text-swap-blur": "2px",
"--text-swap-ease": "ease-in-out",
```

Implement one WeakMap-backed swap function. A request during the exit phase updates the pending destination; reduced motion and `animate: false` update immediately. Every completed run removes `.is-exit` and `.is-enter-start` while retaining `.t-text-swap`.

Paste the transitions.dev `.t-text-swap` CSS verbatim into the existing shared primitive stylesheet and add its reduced-motion rule. Export `textSwap` beside `iconSwap` and `spinningCounter`.

- [ ] **Step 4: Run the primitive test and verify GREEN**

Run: `cd apps/desktop && bun test tests/mockupTextSwap.test.ts`

Expected: PASS with no warnings.

---

### Task 2: Refresh and Memory integrations

**Files:**
- Modify: `apps/desktop/tests/mockupTextSwap.test.ts`
- Modify: `docs/mockups/desk-paper-settings.html:50, 207-219`
- Modify: `docs/mockups/desk-paper-motion.html:99-109, 352-360`
- Modify: `docs/mockups/desk-paper-plate.html:709-775`

**Interfaces:**
- Consumes: `DESK_PAPER_MOTION.textSwap.swap` from Task 1.
- Produces: shared `Refresh → Done → Refresh` and Memory edit/save status behavior.

- [ ] **Step 1: Write failing integration contracts**

Assert Settings and Motion call `textSwap.swap` for both `Done` and `Refresh`, and no longer hard-cut those labels with `textContent`. Assert Memory routes edit/save signature changes through `textSwap.swap`, with save producing `edited just now` directly rather than two consecutive destinations.

- [ ] **Step 2: Run the integration contracts and verify RED**

Run: `cd apps/desktop && bun test tests/mockupTextSwap.test.ts`

Expected: FAIL on the existing direct `textContent` assignments.

- [ ] **Step 3: Wire the approved surfaces**

In Settings, preserve the icon loop, icon swap, right anchor, and width morph; replace only visible label assignments with the shared API. Add the `t-text-swap` class to generated refresh labels.

In Motion, add `t-text-swap` to `.refresh-demo-label` and route both label changes through the shared API so the reference demo matches Settings.

In Memory, change `setEditing` to accept an optional final status and call the shared primitive once. Save uses `setEditing(false, { statusText: "edited just now" })`; cancel/exit uses the current page timestamp.

- [ ] **Step 4: Run the integration contracts and verify GREEN**

Run: `cd apps/desktop && bun test tests/mockupTextSwap.test.ts`

Expected: PASS.

---

### Task 3: Chat run and approval states

**Files:**
- Modify: `apps/desktop/tests/mockupTextSwap.test.ts`
- Modify: `docs/mockups/desk-paper-chat.html:130, 146`
- Modify: `docs/mockups/desk-paper-chat.js:249-262, 811`

**Interfaces:**
- Consumes: `DESK_PAPER_MOTION.textSwap.swap` from Task 1.
- Produces: `Working → Worked` at the end of the live tool tail and observable inline approval progress/result labels.

- [ ] **Step 1: Write failing Chat integration contracts**

Assert `.run-status` has `t-text-swap`, the live ticker stops shimmer before swapping to `Worked`, and re-entering Live resets the status to `Working` without animation. Assert approval buttons use the shared text swap for pending and result labels before returning to the previous scene.

- [ ] **Step 2: Run the Chat contracts and verify RED**

Run: `cd apps/desktop && bun test tests/mockupTextSwap.test.ts`

Expected: FAIL because the ticker currently ends without a status transition and approval buttons close immediately.

- [ ] **Step 3: Implement Chat state transitions**

After the final live tool call, settle the current row, remove `.shimmer`, and call `textSwap.swap(status, "Worked")`. When Live restarts, reset the label to `Working` with `animate: false` before restoring shimmer.

For approval, disable both controls, swap the chosen action to `Approving`/`Denying`, then `Approved`/`Denied`, briefly hold the result, and return to the previous scene. Keep the approval inline; do not add a modal or duplicate blocked state.

- [ ] **Step 4: Run the Chat contracts and verify GREEN**

Run: `cd apps/desktop && bun test tests/mockupTextSwap.test.ts`

Expected: PASS.

---

### Task 4: Cache and regression verification

**Files:**
- Modify: `docs/mockups/desk-paper-chat.html:9`
- Modify: `docs/mockups/desk-paper-settings.html:9`
- Modify: `docs/mockups/desk-paper-motion.html:219`
- Modify: `docs/mockups/desk-paper-plate.html:8`

**Interfaces:**
- Consumes: completed Tasks 1-3.
- Produces: refreshed mockups loading the same shared motion foundation.

- [ ] **Step 1: Bump the shared motion cache key once**

Change all four `desk-paper-motion.js?v=20260719-13` references to `desk-paper-motion.js?v=20260719-14`.

- [ ] **Step 2: Run focused and adjacent tests**

Run:

```bash
cd apps/desktop
bun test tests/mockupTextSwap.test.ts tests/iconSwap.test.tsx tests/progressiveBlur.test.tsx tests/mockupTypography.test.ts
```

Expected: all tests pass with zero failures.

- [ ] **Step 3: Run static integrity checks**

Run:

```bash
git diff --check -- docs/mockups apps/desktop/tests/mockupTextSwap.test.ts
rg -n 'desk-paper-motion.js\?v=20260719-13' docs/mockups/desk-paper-{chat,settings,motion,plate}.html
```

Expected: `git diff --check` succeeds and `rg` returns no matches.

- [ ] **Step 4: Verify visually when browser policy permits**

Exercise Settings refresh, Motion refresh, Memory edit/save, Chat live completion, and both approval actions in light/dark and reduced-motion modes. If the local `file://` page remains blocked from automation, report that limitation and do not claim browser verification.
