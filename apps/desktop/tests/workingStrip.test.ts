import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { textLaneOpacity } from "@/lib/textLane";
import { sliderMarkerOpacity } from "@/components/ui/Slider";

const source = readFileSync(
  new URL("../src/features/chat/components/WorkingStrip.tsx", import.meta.url),
  "utf8",
);

test("the sweep is never restarted by a label change", () => {
  // Listing the label in the deps re-runs the effect on every tool call,
  // which resets the sweep's clock — the field stutters against the work it
  // is reporting. Its width is remeasured each frame instead.
  const deps = source.match(/\}, \[([^\]]*)\]\);/)?.[1] ?? "";
  expect(deps.trim()).toBe("active");
});

test("a label change must not wipe the canvas", () => {
  // Assigning width/height clears the canvas even when the size is unchanged,
  // costing a blank frame on every remeasure.
  expect(source).toContain("if (canvas.width !== nextWidth || canvas.height !== nextHeight)");
});

test("the field stops rather than idles when unseen or unwanted", () => {
  expect(source).toContain("prefers-reduced-motion: reduce");
  expect(source).toContain("document.hidden");
  expect(source).toContain("document.hasFocus()");
  // Not merely slowed: no frame is scheduled at all in the still case.
  const still = source.match(/const paintStill[\s\S]*?\n {4}\};/)?.[0] ?? "";
  expect(still).not.toContain("requestAnimationFrame");
});

test("strip and slider protect their text lanes with one shared rule", () => {
  expect(sliderMarkerOpacity).toBe(textLaneOpacity);
  // Inside the band, clear of both lanes.
  expect(textLaneOpacity(100, 40, 200, 10)).toBe(1);
  // Ramping out of the left lane rather than cutting at it.
  expect(textLaneOpacity(45, 40, 200, 10)).toBeCloseTo(0.5, 5);
  // Fully suppressed on and beyond either lane.
  expect(textLaneOpacity(40, 40, 200, 10)).toBe(0);
  expect(textLaneOpacity(200, 40, 200, 10)).toBe(0);
  expect(textLaneOpacity(260, 40, 200, 10)).toBe(0);
});
