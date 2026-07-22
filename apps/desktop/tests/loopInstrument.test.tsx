import { expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import {
  LoopInstrument,
  angleDelta,
  valueFromAngle,
} from "@/components/ui/LoopInstrument";

test("angleDelta follows the short path across the 360 degree seam", () => {
  expect(angleDelta(-179, 179)).toBe(2);
  expect(angleDelta(179, -179)).toBe(-2);
});

test("cycle values clamp while timeline values wrap", () => {
  expect(valueFromAngle(-30, "cycle")).toBe(0);
  expect(valueFromAngle(450, "cycle")).toBe(1);
  expect(valueFromAngle(-90, "timeline")).toBe(0.75);
  expect(valueFromAngle(450, "timeline")).toBe(0.25);
});

test("renders three keyboard-accessible rings with useful values", () => {
  const html = renderToStaticMarkup(<LoopInstrument value={[0.25, 0.5, 0.75]} />);
  expect(html.match(/role="slider"/g)?.length).toBe(3);
  expect(html).toContain('aria-label="Plan"');
  expect(html).toContain('aria-label="Build"');
  expect(html).toContain('aria-label="Review"');
  expect(html).toContain('aria-valuenow="50"');
  expect(html).toContain("50%");
});

test("timeline mode exposes time-scale labels", () => {
  const html = renderToStaticMarkup(<LoopInstrument mode="timeline" value={[0.25, 0.5, 0.75]} />);
  expect(html).toContain('aria-label="Weeks"');
  expect(html).toContain('aria-label="Days"');
  expect(html).toContain('aria-label="Hours"');
});
