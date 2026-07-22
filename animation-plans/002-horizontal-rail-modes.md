# 002 — Make rail mode content move horizontally

- **Status**: DONE
- **Commit**: 57ec2d10
- **Severity**: MEDIUM
- **Category**: Purpose and frequency; physicality and origin
- **Estimated scope**: 1 file, about 35 lines

## Problem

`Files`, `Notebook`, and `Facts` form a horizontally ordered segmented control,
but switching modes vertically staggers every replacement row. This is a
frequent navigation action, so the stagger adds unnecessary duration and
communicates the wrong spatial relationship.

```js
/* docs/mockups/desk-paper-plate.html:530 — current */
document.querySelectorAll(".seg button").forEach(b => b.addEventListener("click", () => {
  document.querySelectorAll(".seg button").forEach(x => x.classList.toggle("on", x === b));
  tree.innerHTML = TREES[b.dataset.mode];
  const rows = [...tree.children];
  rows.forEach(r => { r.style.opacity = "1"; r.style.filter = "none"; r.style.transform = "none"; });
  if (!reduce) animate(rows, { opacity: [0,1], y: [4,0], filter: ["blur(2px)","blur(0px)"] },
    { delay: stagger(0.02), type: "spring", stiffness: 200, damping: 26 });
}));
```

## Target

Treat the rail body as one content plane:

- Order: `files`, `notebook`, `facts`.
- Selecting a mode to the right: new plane enters from `translateX(8px)`.
- Selecting a mode to the left: new plane enters from `translateX(-8px)`.
- Timing: the peek's existing `210/26` entrance spring, about `450ms` to settle.
- Opacity: `0 → 1`.
- Blur: `2px → 0px`.
- No child-row stagger during mode navigation.
- Reduced motion: `150ms` opacity crossfade only.

Use full transform strings and cancel an in-flight swap:

```js
const RAIL_MODE_ORDER = ["files", "notebook", "facts"];
let railMode = "files";
let railSwapAnimation = null;

function animateRailSwap(direction){
  railSwapAnimation?.cancel();
  tree.style.opacity = "1";
  tree.style.filter = "none";
  tree.style.transform = "translateX(0px)";

  railSwapAnimation = reduce
    ? animate(tree, { opacity: [0, 1] }, { duration: 0.15, ease: EMPH })
    : animate(tree, {
        opacity: [0, 1],
        transform: [`translateX(${direction * 8}px)`, "translateX(0px)"],
        filter: ["blur(2px)", "blur(0px)"],
      }, PEEK_SPRING);
}
```

## Repo conventions to follow

- Reuse `EMPH` and `reduce` from `docs/mockups/desk-paper-plate.html:313`.
- Follow the resting-state-before-animation rule at
  `docs/mockups/desk-paper-plate.html:316`.
- The product is a dense workbench; a mode users may switch tens of times a day
  gets one short content-plane transition, not a decorative row cascade.
- Use Motion's returned animation controls and cancel before retargeting.

## Steps

1. In the rail-mode section of `docs/mockups/desk-paper-plate.html`, add
   `RAIL_MODE_ORDER`, `railMode`, `railSwapAnimation`, and the local
   `animateRailSwap(direction)` function exactly as specified above.
2. In the segmented-control click handler, return early when the selected mode
   equals `railMode`.
3. Before replacing `tree.innerHTML`, compute the previous and next indices.
   Update `railMode`, replace the content, update the selected button, then call
   `animateRailSwap(nextIndex > previousIndex ? 1 : -1)`.
4. Remove the per-row `y` spring and stagger from this handler. Do not alter
   vertical motion used by actual row insertion, disclosure, or review lists.

## Boundaries

- Do NOT animate rail width, height, padding, or scroll position.
- Do NOT animate each replacement row.
- Do NOT alter the segmented-control dimensions or selected-state styling.
- Do NOT create a shared tab component or reuse production `TabPanels`.
- Do NOT touch peek motion, document tabs, or production React files.
- Do NOT add dependencies.
- If the cited code has drifted, STOP and report instead of improvising.

## Verification

- **Mechanical**: serve the artifact with
  `python3 -m http.server 6904 --directory docs/mockups` and open
  `http://localhost:6904/desk-paper-plate.html`. Confirm the console has no
  errors.
- **Feel check**:
  - Switch `Files → Notebook → Facts`: the rail body enters from the right.
  - Switch `Facts → Notebook → Files`: it enters from the left.
  - Click modes rapidly. Motion retargets without queued row cascades.
  - Confirm the rail itself never moves and its scroll container dimensions do
    not change during the transition.
  - In DevTools Animations at 10% playback, confirm a single horizontal content
    plane moves; no row has a `translateY` transform.
  - Emulate `prefers-reduced-motion: reduce`; confirm opacity changes but there
    is no transform or blur.
- **Done when**: the rail uses one interruptible `8px` horizontal plane swap in
  tab order, repeated selection is a no-op, and reduced motion has no travel.
