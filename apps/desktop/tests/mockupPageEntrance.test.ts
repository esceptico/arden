import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

const pages = new Map([
  ["Home", read("../../../docs/mockups/board-home.html")],
  ["Chat", read("../../../docs/mockups/board-chat.html")],
  ["Automations", read("../../../docs/mockups/board-automations.html")],
  ["Memory", read("../../../docs/mockups/board-memory.html")],
  ["Settings", read("../../../docs/mockups/board-settings.html")],
  ["Area Room", read("../../../docs/mockups/board-area-room.html")],
  ["Overlays", read("../../../docs/mockups/board-system-overlays.html")],
]);

const motion = read("../../../docs/mockups/board-motion.js");
const system = read("../../../docs/mockups/board-system.css");
const localStyles = new Map([
  ["Home", read("../../../docs/mockups/board-home.css")],
  ["Chat", read("../../../docs/mockups/board-chat.css")],
  ["Automations", read("../../../docs/mockups/board-automations.css")],
  ["Area Room", read("../../../docs/mockups/board-area-room.css")],
]);

test("every primary mockup uses the shared page entrance contract", () => {
  for (const [name, page] of pages) {
    expect(page, `${name} must opt into page entrance`).toContain("data-page-enter");
    expect(page, `${name} must mark major entrance blocks`).toContain("data-page-enter-item");
    expect(page, `${name} must use the current shared motion asset`).toContain(
      'board-motion.js?v=20260722-42',
    );
    expect(page + (localStyles.get(name) || ""), `${name} must use the current shared component asset`).toContain(
      'board-system.css?v=20260722-31',
    );
  }
});

test("skeleton bridges are limited to the four dense views", () => {
  for (const name of ["Chat", "Automations", "Memory", "Settings"]) {
    expect(pages.get(name), `${name} needs a skeleton bridge`).toContain("data-page-skeleton");
  }
  for (const name of ["Home", "Area Room", "Overlays"]) {
    expect(pages.get(name), `${name} must enter without a skeleton`).not.toContain("data-page-skeleton");
  }
});

test("page entrance motion is shared, restrained, and reduced-motion safe", () => {
  for (const token of [
    "pageEnter: 240",
    "pageChrome: 180",
    "pageReduced: 160",
    "pageStagger: 36",
    "pageSkeletonHold: 180",
    "pageSkeletonReveal: 220",
  ]) expect(motion).toContain(token);

  expect(motion).toContain("pageEnter: 6");
  expect(motion).toContain("function bindPageEntrance");
  expect(motion).toContain('querySelectorAll("[data-page-enter-item]")');
  expect(motion).toContain("Math.min(index, 3) * duration.pageStagger");
  expect(motion).toContain("const reducedFrames = [{ opacity: 0 }, { opacity: 1 }]");
  expect(motion).toContain("const pageEntrance = Object.freeze({ bind: bindPageEntrance })");
  expect(motion).toContain("motion.pageEntrance.bind(document)");
});

test("the skeleton overlay is shared and never animates layout", () => {
  expect(system).toContain(".dp-page-skeleton {");
  expect(system).toContain("pointer-events: none;");
  expect(system).toContain("@keyframes dp-page-skeleton-pulse");
  expect(system).toMatch(/@media \(prefers-reduced-motion: reduce\)[\s\S]*\.dp-page-skeleton-line[\s\S]*animation:\s*none/);

  const entrance = motion.match(/function bindPageEntrance[\s\S]*?const pageEntrance = Object\.freeze/)?.[0] || "";
  expect(entrance).toContain('const plainItems = items.filter(element => !element.matches("[data-page-skeleton]"))');
  expect(entrance).toContain("const skeletonContent = skeletonTargets.flatMap");
  expect(entrance).not.toMatch(/\b(?:width|height|top|right|bottom|left|margin|padding):/);
});
