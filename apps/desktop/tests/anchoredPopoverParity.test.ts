import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import {
  ANCHORED_POPOVER_EDGE,
  ANCHORED_POPOVER_GAP,
  placeAnchoredPopover,
  placeSidePopover,
} from "../src/components/ui/AnchoredPopover";
import { MOTION, POSE_PORTALED_POPOVER_IN } from "../src/lib/tokens/motion";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

const rect = (left: number, top: number, width: number, height: number) => ({
  left,
  top,
  bottom: top + height,
  width,
});

test("AnchoredPopover uses the canonical 16px edge, 6px gap, flip, and trigger-centred origin", () => {
  expect(ANCHORED_POPOVER_EDGE).toBe(16);
  expect(ANCHORED_POPOVER_GAP).toBe(6);

  expect(placeAnchoredPopover(rect(100, 100, 80, 32), 200, 120, { width: 500, height: 500 })).toEqual({
    left: 100,
    top: 138,
    transformOrigin: "40px 0",
  });

  expect(placeAnchoredPopover(rect(100, 400, 80, 32), 200, 120, { width: 500, height: 500 })).toEqual({
    left: 100,
    top: 274,
    transformOrigin: "40px 100%",
  });

  expect(placeAnchoredPopover(rect(470, 100, 20, 32), 200, 120, { width: 500, height: 500 })).toEqual({
    left: 284,
    top: 138,
    transformOrigin: "196px 0",
  });
});

test("AnchoredPopover can align an end edge with a custom gap", () => {
  expect(placeAnchoredPopover(
    rect(100, 100, 180, 32),
    120,
    80,
    { width: 500, height: 500 },
    { align: "end", gap: 8 },
  )).toEqual({
    left: 160,
    top: 140,
    transformOrigin: "30px 0",
  });
});

test("AnchoredPopover and Composer selectors share the exact popup lifecycle", () => {
  const anchored = read("../src/components/ui/AnchoredPopover.tsx");
  const composer = read("../src/components/ui/ModelPickers.tsx");
  const pickerCss = read("../src/design/model-pickers.css");

  expect(MOTION.popover).toBe(0.18);
  expect(MOTION.reduced).toBe(0.001);
  expect(POSE_PORTALED_POPOVER_IN).toEqual({
    opacity: 0,
    y: 5,
    scale: 0.985,
    filter: "blur(2px)",
  });
  expect(anchored).toContain("transition: POPOVER_ENTER_TRANSITION");
  expect(anchored).toContain("useReducedMotion");
  expect(composer).toContain("sideOffset: 6");
  expect(composer).toContain("collisionPadding: 16");
  expect(composer).toContain('side: "flip"');
  expect(composer).toContain("model-picker__popup-motion");
  expect(pickerCss).toContain("transform-origin: var(--transform-origin)");
  expect(pickerCss).toContain("opacity var(--motion-popover) var(--ease-out)");
  expect(pickerCss).toContain("transform var(--motion-popover) var(--ease-out)");
  expect(pickerCss).toContain("filter var(--motion-popover) var(--ease-out)");
  expect(pickerCss).toContain(":is([data-starting-style], [data-ending-style])");
  expect(pickerCss).toContain("translateY(var(--popover-offset))");
  expect(pickerCss).toContain("scale(var(--popover-scale))");
  expect(pickerCss).toContain("blur(var(--popover-blur))");
  expect(pickerCss).toContain('[data-side="top"]');
  expect(pickerCss).toContain("--popover-offset-above");
  expect(pickerCss).toContain("transform: scale(var(--press-scale))");
  expect(composer).not.toContain("active:scale-[0.97]");
  expect(composer).not.toContain("data-starting-style:scale-[0.98]");
});

test("the side placement clears the list it was raised from", () => {
  const side = (left: number, right: number, top: number) => ({ left, right, top });

  // A preview raised from a rail row opens to the RIGHT of it, top-aligned, so
  // the rows below stay reachable — the stacked placement covered them.
  expect(placeSidePopover(side(18, 286, 240), 336, 160, { width: 1000, height: 900 })).toEqual({
    left: 292,
    top: 240,
    transformOrigin: "0 0px",
  });

  // No room on the right and enough on the left: flip, and grow from the edge
  // nearest the trigger.
  expect(placeSidePopover(side(600, 700, 100), 200, 120, { width: 800, height: 600 })).toEqual({
    left: 394,
    top: 100,
    transformOrigin: "100% 0px",
  });

  // Short of room on either side it clamps to the gutter rather than hanging
  // off the edge — 500 wide, 400 panel, so the furthest right it may sit is 84.
  const cramped = placeSidePopover(side(120, 200, 100), 400, 120, { width: 500, height: 600 });
  expect(cramped.left).toBe(500 - 400 - ANCHORED_POPOVER_EDGE);

  // A row near the bottom lifts the panel so it stays inside the gutter, and
  // the origin follows the trigger rather than snapping to the panel corner.
  const low = placeSidePopover(side(18, 286, 799), 336, 272, { width: 1000, height: 900 });
  expect(low.top).toBe(612);
  expect(low.top + 272).toBe(900 - ANCHORED_POPOVER_EDGE);
  expect(low.transformOrigin).toBe("0 187px");
});
