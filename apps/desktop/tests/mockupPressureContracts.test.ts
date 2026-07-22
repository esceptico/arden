import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
const system = read("../../../docs/mockups/board-system.css");
const overlayCss = read("../../../docs/mockups/board-system-overlays.css");
const areaCss = read("../../../docs/mockups/board-area-room.css");
const pages = [
  read("../../../docs/mockups/board-home.html"),
  read("../../../docs/mockups/board-chat.html"),
  read("../../../docs/mockups/board-automations.html"),
  read("../../../docs/mockups/board-memory.html"),
  read("../../../docs/mockups/board-settings.html"),
  read("../../../docs/mockups/board-system-overlays.html"),
  read("../../../docs/mockups/board-area-room.html"),
];

const rgb = (hex: string) => hex.match(/[a-f\d]{2}/gi)!.map(value => Number.parseInt(value, 16) / 255);
const luminance = (hex: string) => rgb(hex).map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4).reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
const contrast = (a: string, b: string) => {
  const [bright, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (bright + .05) / (dark + .05);
};

test("essential light and dark metadata clears 4.5:1", () => {
  expect(contrast("#707070", "#FAFAFA")).toBeGreaterThanOrEqual(4.5);
  expect(contrast("#737373", "#FFFFFF")).toBeGreaterThanOrEqual(4.5);
  expect(contrast("#A3A3A3", "#171717")).toBeGreaterThanOrEqual(4.5);
  expect(contrast("#9F9F9F", "#252525")).toBeGreaterThanOrEqual(4.5);
});

test("keyboard and reduced-motion rules are global", () => {
  expect(system).toContain("button:focus-visible");
  expect(system).toContain("input:focus-visible");
  expect(system).toContain("@media (prefers-reduced-motion: reduce)");
  expect(system).toContain("transition-duration: var(--motion-reduced) !important");
  for (const page of pages) expect(page).toContain('name="viewport"');
});

test("blocking surfaces constrain width, height, scrolling, and long content", () => {
  for (const css of [overlayCss, areaCss]) {
    expect(css).toContain("@media (max-width:");
    expect(css).toContain("@media (max-height:");
    expect(css + system).toContain("overflow-wrap: anywhere");
  }
  expect(overlayCss).toContain("overflow: auto");
  expect(overlayCss).toContain("calc(100vw -");
  expect(overlayCss).toContain("calc(100vh -");
  expect(areaCss).toContain("overflow: auto");
});

test("focus restoration and inert stacking remain part of the shared overlay contract", () => {
  const motion = read("../../../docs/mockups/board-motion.js");
  expect(motion).toContain("restoreFocus.focus");
  expect(motion).toContain('toggleAttribute("inert"');
  expect(motion).toContain('event.key !== "Tab"');
  expect(motion).toContain('event.key === "Escape"');
});
