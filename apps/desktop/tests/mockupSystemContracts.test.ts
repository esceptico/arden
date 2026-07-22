import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

const system = read("../../../docs/mockups/board-system.css");
const primaryPages = [
  read("../../../docs/mockups/board-home.css"),
  read("../../../docs/mockups/board-chat.css"),
  read("../../../docs/mockups/board-automations.css"),
  read("../../../docs/mockups/board-memory.html"),
  read("../../../docs/mockups/board-settings.html"),
];
const motionDemo = read("../../../docs/mockups/board-motion.html");
const language = read("../../../docs/mockups/board-language.html");
const pageScripts = [
  read("../../../docs/mockups/board-home.js"),
  read("../../../docs/mockups/board-chat.js"),
  read("../../../docs/mockups/board-automations.js"),
  read("../../../docs/mockups/board-memory.html"),
  read("../../../docs/mockups/board-settings.html"),
];
const motion = read("../../../docs/mockups/board-motion.js");
const primaryMarkup = [
  read("../../../docs/mockups/board-home.html"),
  read("../../../docs/mockups/board-chat.html"),
  read("../../../docs/mockups/board-automations.html"),
  read("../../../docs/mockups/board-memory.html"),
  read("../../../docs/mockups/board-settings.html"),
];
const inlineCss = (source: string) => source.match(/<style>([\s\S]*?)<\/style>/)?.[1] ?? "";
const pageCss = [
  read("../../../docs/mockups/board-home.css"),
  read("../../../docs/mockups/board-chat.css"),
  read("../../../docs/mockups/board-automations.css"),
  inlineCss(read("../../../docs/mockups/board-memory.html")),
  inlineCss(read("../../../docs/mockups/board-settings.html")),
];

test("overlay depth uses one named z-index scale", () => {
  for (const role of [
    "below",
    "base",
    "raised",
    "sticky",
    "shell",
    "popover",
    "peek",
    "scrim",
    "sheet",
    "nested",
    "toast",
    "tooltip",
    "demo",
  ]) {
    expect(system).toContain(`--z-${role}:`);
  }

  for (const source of [system, ...primaryPages]) {
    expect(source).not.toMatch(/z-index:\s*-?\d+/);
  }
});

test("shared primitives own the complete component state vocabulary", () => {
  for (const primitive of [
    "dp-field",
    "dp-search",
    "dp-search-shell",
    "dp-switch",
    "dp-segmented",
    "dp-status",
    "dp-skeleton",
    "dp-empty-state",
    "dp-error-state",
    "dp-scrim",
    "dp-sheet",
    "dp-tooltip",
  ]) {
    expect(system).toContain(`.${primitive}`);
  }

  expect(system).toContain(":disabled");
  expect(system).toContain('[aria-busy="true"]');
  expect(system).toContain('[aria-invalid="true"]');
  expect(system).toContain("[inert]");
});

test("production-shaped Board CSS never animates layout properties", () => {
  const layoutTransition = /transition(?:-property)?\s*:[^;}]*\b(?:width|height|padding(?:-[\w-]+)?|margin(?:-[\w-]+)?|top|right|bottom|left)\b/;
  for (const source of [system, ...primaryPages, motionDemo]) {
    expect(source).not.toMatch(layoutTransition);
  }
});

test("the design language contains no overshooting easing", () => {
  expect(language).not.toMatch(/cubic-bezier\([^)]*,\s*1\.\d+/);
  expect(motionDemo).not.toMatch(/cubic-bezier\([^)]*,\s*1\.\d+/);
});

test("page files delegate Web Animations and timing to the shared motion engine", () => {
  for (const source of pageScripts) expect(source).not.toContain(".animate(");
  for (const source of pageCss) {
    expect(source).not.toMatch(/cubic-bezier\(/);
    expect(source).not.toMatch(/\b(?:[1-9]\d*|0?\.\d+)(?:ms|s)\b/);
  }
  expect(motion).toContain("async function overlapContent");
  expect(motion).toContain("overlap: overlapContent");
});

test("the shared motion contract never animates layout dimensions", () => {
  expect(motion).not.toContain("function animateWidth");
  expect(motion).not.toMatch(/\.animate\(\[\{\s*width:/);
  expect(motionDemo).not.toMatch(/\.animate\(\[\{\s*width:/);
});

test("operational state is written as text rather than decorative dots", () => {
  for (const source of primaryMarkup) {
    expect(source).not.toContain("●");
    expect(source).not.toMatch(/class=["'][^"']*(?:status-dot|status-pip|nav-dot|budget-dot)/);
  }
});
