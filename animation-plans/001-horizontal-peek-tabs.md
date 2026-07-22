# 001 — Make peek tab content move horizontally

- **Status**: DONE
- **Commit**: 57ec2d10
- **Severity**: MEDIUM
- **Category**: Physicality and origin; cohesion and tokens
- **Estimated scope**: 2 files, about 55 lines

## Problem

The peek uses horizontally ordered controls (`outgoing` / `incoming` and the
`links` / `records` / `activity` instrument row), but every content change
replays a vertical row entrance. The motion says “new rows arrived below”
instead of “the user moved to an adjacent horizontal tab.”

```js
/* docs/mockups/desk-paper-plate.html:395 — current */
/* one movement at a time: opening slides the CARD (rows already in place);
   switching tabs inside an open peek staggers only the ROWS */
function staggerRows(){
  const rows = [...peekBody.querySelectorAll(".grp, .arow")];
  rows.forEach(r => { r.style.opacity = "1"; r.style.filter = "none"; r.style.transform = "none"; });
  if (!reduce && rows.length) animate(rows, { opacity: [0,1], y: [6,0], filter: ["blur(3px)","blur(0px)"] },
    { delay: stagger(0.03), type: "spring", stiffness: 200, damping: 26 });
}
```

```js
/* docs/mockups/desk-paper-plate.html:432 — current */
peekTabs.addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b || b.dataset.pk === peekTab) return;
  peekTab = b.dataset.pk;
  renderPeekBody(); syncInstruments(); staggerRows();
});
```

## Target

Use direction to encode the ordered control relationship:

- Selecting a control to the right: new content enters from `translateX(8px)`.
- Selecting a control to the left: new content enters from `translateX(-8px)`.
- Timing: the peek's existing `210/26` entrance spring, about `450ms` to settle.
- Opacity: `0 → 1`.
- Blur: `2px → 0px`.
- Animate the peek content plane once; do not stagger its child rows during a
  tab change.
- Under reduced motion, use the same `150ms` opacity crossfade without
  transform or blur.
- First opening the peek keeps its existing card spring. Closing keeps its
  existing `160ms` tween.

Use full transform strings:

```js
// target shape
let peekSwapAnimation = null;

function animatePeekSwap(direction){
  peekSwapAnimation?.cancel();
  peekBody.style.opacity = "1";
  peekBody.style.filter = "none";
  peekBody.style.transform = "translateX(0px)";

  peekSwapAnimation = reduce
    ? animate(peekBody, { opacity: [0, 1] }, { duration: 0.15, ease: EMPH })
    : animate(peekBody, {
        opacity: [0, 1],
        transform: [`translateX(${direction * 8}px)`, "translateX(0px)"],
        filter: ["blur(2px)", "blur(0px)"],
      }, PEEK_SPRING);
}
```

For ordered instruments use exactly:

```js
const PEEK_KIND_ORDER = ["links", "records", "activity"];
```

`person` is contextual, not part of that order. When an already-open peek
changes to `person`, use a neutral opacity/blur swap with no horizontal travel.

## Repo conventions to follow

- `docs/mockups/desk-paper-plate.html:313` defines `EMPH` and the reduced-motion
  boolean. Reuse them; do not add another curve.
- `docs/mockups/desk-paper-plate.html:316` documents the resting-state rule:
  commit final inline styles before calling `animate()`.
- `docs/mockups/desk-paper-language.html:413` requires reduced motion to
  crossfade with no travel.
- The plate already uses Motion's `animate()` controls. Cancel the previous
  content-plane animation before starting another so rapid tab clicks retarget
  cleanly.

## Steps

1. In `docs/mockups/desk-paper-plate.html`, replace `staggerRows()` with the
   local `animatePeekSwap(direction)` implementation shown above and add
   `peekSwapAnimation` next to the other peek state.
2. Add `PEEK_KIND_ORDER` beside `PEEK_KINDS`. In `openPeek(kind)`, capture the
   previous kind before assigning `peekKind`. When the peek is already open and
   both kinds exist in `PEEK_KIND_ORDER`, compute `direction` from their indices
   and call `animatePeekSwap(direction)` after rendering. For `person`, call the
   neutral no-travel variant.
3. In the `peekTabs` click handler, find the previous and next indices from the
   current kind's `tabs` array before updating `peekTab`. After rendering, call
   `animatePeekSwap(nextIndex > previousIndex ? 1 : -1)` instead of
   `staggerRows()`.
4. Keep the initial peek-card spring and close tween unchanged.
5. In `docs/mockups/desk-paper-language.html`, extend the Section 03 Motion
   paragraph with this settled law: “Axis follows the controller: horizontally
   ordered tabs swap their content plane by 8px in the selected direction over
   the same 210/26 entrance spring as the peek; vertical RISE_IN is reserved
   for row arrival and disclosure.”

## Boundaries

- Do NOT change peek size, placement, header layout, or close-button position.
- Do NOT change the initial peek spring (`210/26`) or close tween (`160ms`).
- Do NOT animate every child row on a tab switch.
- Do NOT introduce `TabPanels`, shared variants, or another reusable motion
  abstraction. Keep this fix local to the peek interaction.
- Do NOT touch production React files under `apps/desktop/` in this plan.
- Do NOT add dependencies.
- If the cited code has drifted, STOP and report instead of improvising.

## Verification

- **Mechanical**: serve the artifact with
  `python3 -m http.server 6904 --directory docs/mockups` and open
  `http://localhost:6904/desk-paper-plate.html`. Confirm the console has no
  errors.
- **Feel check**:
  - Open Links, then switch `outgoing → incoming`: content enters from the right.
  - Switch `incoming → outgoing`: content enters from the left.
  - While the peek stays open, switch `links → records → activity`, then back;
    direction follows the instrument order.
  - Click rapidly between tabs. The current animation cancels and retargets;
    it never queues or restarts from a stale position.
  - In DevTools Animations, use 10% playback and confirm the content plane has
    horizontal travel only; child rows do not rise independently.
  - Emulate `prefers-reduced-motion: reduce`; confirm opacity changes but there
    is no transform or blur.
- **Done when**: every ordered peek tab change uses one `8px` horizontal plane
  transition, initial open/close motion is unchanged, and reduced motion has no
  travel.
