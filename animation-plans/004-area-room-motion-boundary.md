# 004 — Give Area Room one spatial transition and remove decorative tab motion

- **Status**: DONE
- **Commit**: 6a0f7df2
- **Severity**: HIGH
- **Category**: Purpose and frequency; spatial consistency; cohesion
- **Estimated scope**: 3 source files, about 45 lines

## Problem

The Area Room opens with Area details permanently covering the reading lane at a
958px viewport. Its only spatial transition runs after the user closes it, so
the initial state has no connection to a trigger. Inside the Hub, every frequent
tab change dissolves and translates the entire information panel.

```js
/* docs/mockups/board-area-room.js:12-30 — current */
if (matchMedia("(max-width: 46.25rem)").matches && query.get("hub") !== "open") {
  hub.hidden = true;
  reopen.hidden = false;
}
motion.content.swap(hubBody, commit, { axis: "x" });
close.addEventListener("click", () => motion.surface.hide(hub, {
  afterClose: () => { hub.hidden = true; reopen.hidden = false; },
}));
```

At 958x758, the rendered Hub is 352px wide and covers the right 360px of a
646px Area Room. The room scroll height is 1437px. This is a hierarchy failure
before it is an animation problem.

## Target

- Area details is closed by default at every viewport width.
- The shared icon-only details control opens it from the right edge.
- Open/close uses the existing `BOARD_MOTION.surface` implementation with
  `axis: "x"`; it already provides interruptible WAAPI motion, the shared peek
  spring, a strong `cubic-bezier(0.23, 1, 0.32, 1)` exit, and reduced-motion
  handling.
- Hub tab content commits immediately. The shared sliding tab indicator may
  move, but the information itself does not dissolve, blur, or translate.
- The Area Room, keyboard navigation, list selection, and work data never gain
  decorative entrance or hover motion.

## Repo conventions to follow

- Reuse `BOARD_MOTION.surface.show/hide`; do not add a second motion runtime.
- Reuse `BOARD_MOTION.tabs.bind` for measured tab state and ARIA updates.
- Shared press feedback already lives in `board-system.css`.
- `board-motion.js` branches on `prefers-reduced-motion`; do not duplicate the
  media query in the Area Room JavaScript.

## Steps

1. In `docs/mockups/board-area-room.html`, hide Area details by default and add a
   header trigger with `aria-controls="agent-hub"` and `aria-expanded="false"`.
2. In `docs/mockups/board-area-room.css`, keep the Hub fixed to the right edge,
   remove it from resting page geometry, and preserve the shared floating
   surface treatment.
3. In `docs/mockups/board-area-room.js`, open and close via
   `motion.surface.show/hide(..., { axis: "x" })`, synchronize both trigger
   attributes, and remove `motion.content.swap` from Hub tabs.
4. Keep the Hub content swap synchronous: toggle each `[data-hub-panel]` and
   update `active` in the tab callback.

## Boundaries

- Do NOT animate Area navigation, Hub tab content, work rows, evidence rows, or
  keyboard-initiated actions.
- Do NOT add keyframes, new easing values, or dependencies.
- Do NOT change production React files in this plan.
- Do NOT make Area details part of the resting layout at any breakpoint.

## Verification

- **Mechanical**: run `bun test apps/desktop/tests/mockupAreaRoom.test.ts` and
  the full `bun test apps/desktop/tests/mockup*.test.ts`; expect zero failures.
- **Feel check**: at 958x758, confirm the room is fully readable before Hub is
  opened. Open Hub and confirm it enters from the right; rapidly alternate open
  and close and confirm motion retargets without jumping.
- Switch all Hub tabs rapidly. The indicator may glide; content must update
  immediately without blur, translation, or double exposure.
- Emulate `prefers-reduced-motion`: Hub must appear without translation while
  visibility and focus remain correct.
- **Done when**: the Hub has one clear spatial story and no high-frequency
  information motion remains.

## Verification result

- `bun test apps/desktop/tests/mockup*.test.ts`: 87 passed, 0 failed.
- Live at 1043 x 758: Hub is absent from resting geometry, opens from the
  right, survives rapid close/open retargeting, and restores focus to its
  trigger when closed.
- Activity/Sources content changes synchronously; only the shared measured tab
  indicator moves.
- Reduced motion remains owned by `BOARD_MOTION.surface`; Area Room adds no
  local timing or animation branch.
