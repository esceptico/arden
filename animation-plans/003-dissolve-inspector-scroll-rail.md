# 003 — Replace chat controls with a supplementary inspector and shared dissolve

- **Status**: DONE
- **Commit**: 57ec2d10
- **Severity**: HIGH
- **Category**: Information hierarchy; cohesion and tokens; state continuity
- **Estimated scope**: 2 source files, about 220 lines

## Problem

The chat header exposes `Activity`, `Sources`, their counts, and an overflow
control as a primary textual tab bar. That overstates supplementary material and
duplicates controls inside the Peek header. The Peek then behaves like a second
application surface instead of a quiet inspector.

State replacement is also inconsistent: scenes and disclosures snap through
`display:none`, Peek uses a one-off blur, and the mock does not reuse the real
desktop conversation scroll rail.

```html
<!-- docs/mockups/desk-paper-chat.html:215 — current -->
<nav class="instrument" aria-label="Chat instruments">
  <button data-peek-tab="activity">… Activity 8</button>
  <button data-peek-tab="sources">… Sources 7</button>
  <button class="icon-only">…</button>
</nav>
```

```css
/* docs/mockups/desk-paper-chat.html:46,56,82 — current */
.scene{display:none}.scene.on{display:block}
.trace.collapsed .steps{display:none}
.workflow.collapsed .workflow-progress,
.workflow.collapsed .workflow-detail{display:none}
```

## Target

### Inspector hierarchy

- Replace the text tab bar with a compact two-button toolbar at the same
  right-aligned header position.
- Use Phosphor-regular-style stroke icons:
  - `ListBullets` semantics: toggle the supplementary inspector.
  - `SidebarSimple` / panel-right semantics: overlay versus docked placement.
- The toolbar has no counts, labels, overflow button, or decorative activity
  waveform/book glyphs.
- Remove the Peek header, title, metadata, close button, and internal dock
  button. The toolbar owns open/close and dock state.
- Merge Activity and Sources into one scrollable inspector document per scene.
  Begin directly with the run summary and named sections, like the Codex
  Environment inspector reference.
- Existing message-level Activity/Sources entry points open the same inspector
  and scroll to the relevant section; they do not recreate tabs.
- Overlay is the default. Docking remains explicit.

### Conversation scroll rail

Port the existing behavior from
`apps/desktop/src/features/chat/components/ChatRail.tsx`, not a new scrollbar:

- 12px resting tick, 18px active tick, 32px hover tick, 2px height.
- One traveling label for the hovered/active logical block.
- Scroll-spy follows the visible scene's annotated blocks.
- Tick activation scrolls the chat container to the corresponding block.
- The rail lives in the reading-lane gutter, never changes lane width, and is
  hidden when the viewport lacks the gutter.
- Rebuild the rail after a scene change.

### Shared dissolve contract

Define one contract in `desk-paper-motion.js`:

```js
duration.dissolve = 220;
distance.dissolve = 4;
blur.dissolve = 2;
curve.dissolveCss = "cubic-bezier(0.23, 1, 0.32, 1)";
```

An object that enters or replaces another uses:

```js
opacity: 0 -> 1
filter: blur(2px) -> blur(0)
transform: translateY(4px) -> translateY(0)
duration: 220ms
```

An object that exits reverses opacity/blur and travels `-4px`. Crossfades may
overlap. Cancel in-flight WAAPI animations before retargeting and commit final
resting styles before animating.

Apply it to:

- scene replacement;
- inspector open/close and overlay/docked content replacement;
- trace and workflow disclosure content;
- the single scroll-rail label;
- sidebar show/hide where the existing behavior already has blur and travel.

Do not add blur to hover, pointer proximity, list selection, pressed feedback,
live progress, or the persistent scroll ticks. Those are continuous/frequent
states, not object replacement.

Reduced motion keeps the same 150ms opacity transition but removes blur and
translation.

## Repo conventions to follow

- Reuse `DESK_PAPER_MOTION`; do not hard-code duplicate timing in handlers.
- Keep type tokens, reading-lane width, sidebar geometry, Peek geometry, and
  responsive breakpoints unchanged.
- Use the current inline symbol sprite; add only the two required consistent
  stroke symbols.
- Preserve the existing chat rail interaction values from production.
- MDN confirms `filter: blur()` interpolates and Material Fade Through provides
  the state-replacement precedent. Keep blur at 2px so text remains legible and
  compositor cost stays small.

## Steps

1. Add the four dissolve tokens and CSS custom properties to
   `docs/mockups/desk-paper-motion.js`.
2. In `docs/mockups/desk-paper-chat.html`, replace the instrument markup and
   icons with the two-button toolbar. Remove obsolete tab/count/title code.
3. Flatten the four Peek bodies into two scene-context documents with Activity
   and Sources sections. Remove the Peek header controls.
4. Replace `setPeek(tab)` with inspector open/close/dock functions. Make
   message-level entry points open then scroll the requested section.
5. Add a small interruptible dissolve helper and use it for scene swaps,
   inspector state, and disclosures. Avoid `display:none` until an exit finishes.
6. Add annotated logical blocks and port the production scroll rail constants,
   scroll spy, tick activation, and one dissolving label.
7. Add reduced-motion handling that retains opacity only.

## Boundaries

- Do NOT redesign the conversation content, sidebar, composer, or type scale.
- Do NOT add a textual inspector tab bar, title bar, close button, counts, or
  overflow menu.
- Do NOT treat Automations or invented Spaces as chat navigation.
- Do NOT use filled decorative icons; keep Phosphor-regular stroke weight.
- Do NOT blur high-frequency hover or scroll feedback.
- Do NOT touch production React files.
- Do NOT add dependencies.

## Verification

- Serve `docs/mockups` and open `desk-paper-chat.html`; console must be clean.
- At wide width, the header shows exactly two quiet icon controls.
- Toggle inspector: it opens as an overlay with no internal header. Toggle the
  first icon again: it dissolves closed. Toggle docking: the lane yields exactly
  as before.
- Click message-level Sources and Activity entries: one inspector opens and
  scrolls to the requested section.
- Switch every Demo scene rapidly: outgoing content fades/blurs upward while
  incoming content fades/unblurs from 4px below, with no stale scene.
- Collapse/expand trace and workflow; contents dissolve instead of snapping.
- Scroll and hover the conversation rail: ticks track logical blocks, one label
  travels and dissolves, and clicking a tick scrolls to its target.
- Resize through 1100px and 880px breakpoints; toolbar, inspector, and rail do
  not overlap the reading lane.
- Emulate reduced motion: opacity remains, but no blur or travel occurs.
- Confirm computed dissolve duration is 220ms and blur never exceeds 2px.

## References

- MDN `blur()`: https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Values/filter-function/blur
- Material Fade Through: https://developer.android.com/reference/com/google/android/material/transition/MaterialFadeThrough
