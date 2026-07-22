import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
const html = read("../../../docs/mockups/board-system-overlays.html");
const css = read("../../../docs/mockups/board-system-overlays.css");
const system = read("../../../docs/mockups/board-system.css");
const js = read("../../../docs/mockups/board-system-overlays.js");
const motion = read("../../../docs/mockups/board-motion.js");

test("the system plate covers every blocking and transient surface", () => {
  expect(html).toContain('href="./board-system.css');
  expect(html).toContain('src="./board-motion.js');
  for (const surface of [
    "command-palette",
    "quick-capture",
    "approval-review",
    "tool-viewer",
    "markdown-viewer",
    "mermaid-viewer",
    "system-tooltip",
  ]) expect(html).toContain(`data-surface="${surface}"`);
  expect(html).toContain("dp-scrim");
  expect(html).toContain("dp-sheet");
  expect(html).toContain("board-sonner.js?v=20260722-1");
  expect(js).toContain("BOARD_TOAST.show");
  expect(html).toContain("dp-tooltip");
});

test("the command navigation helper is a compact non-blocking overlay state", () => {
  expect(html).toContain('data-surface="command-helper"');
  expect(html).toContain('aria-modal="false"');
  expect(html).toContain('data-open="command-helper"');
  expect(html).toContain('data-helper-state="running"');
  expect(html).toContain('data-helper-state="approval"');
  expect(html).toContain('data-helper-state="choice"');
  expect(html).toContain('data-helper-state="done"');
  expect(html).toContain('class="helper-composer"');
  expect(html).toContain('aria-label="Reply to navigation helper"');
  expect(html).toContain('data-helper-action');
  expect(html).not.toContain('data-helper-stop');
  expect(html).toContain('href="#dp-chevron-down"');
  expect(css).toContain(".command-helper");
  expect(css).toContain("width: min(22.5rem");
  expect(css).not.toContain(".command-helper-foot { min-height: 2.375rem; padding: 0 .625rem 0 .75rem; display: flex; align-items: center; border-top:");
  expect(js).toContain("openCommandHelper");
  expect(js).toContain("event.metaKey || event.ctrlKey");
  expect(js).toContain("helperComposer.addEventListener");
  expect(js).toContain("helperComposer.requestSubmit");
  expect(js).toContain("syncHelperAction");
  expect(js).toContain("motion.iconSwap.swap(helperAction");
  expect(js).not.toContain('helperAction.querySelector("use").setAttribute');
  expect(system).toContain(".dp-running-text");
  expect(html).toContain('class="helper-status dp-running-text t-text-swap" data-helper-status role="status"');
  expect(html).not.toContain('<span class="helper-status"><i></i>');
  expect(js).toContain('helperStatus.classList.toggle("dp-running-text", running)');
});

test("overlay behavior has one stack, focus, and dismissal authority", () => {
  expect(motion).toContain("const overlayStack = []");
  expect(motion).toContain("function openOverlay");
  expect(motion).toContain("function closeTopOverlay");
  expect(motion).toContain("function trapOverlayFocus");
  expect(motion).toContain("restoreFocus");
  expect(motion).toContain("const overlay = Object.freeze");
  expect(js).toContain("motion.overlay.open");
  expect(js).toContain("motion.overlay.closeTop");
  expect(js).not.toContain(".animate(");
});

test("the plate demonstrates pressure and outcome states", () => {
  for (const state of ["default", "loading", "empty", "error", "long"]) {
    expect(html).toContain(`data-state="${state}"`);
  }
  expect(html).toContain('aria-modal="true"');
  expect(html).toContain('aria-busy="true"');
  expect(html).toContain("aria-invalid");
  expect(html).toContain("inert");
  expect(html).toContain("data-stacked");
  expect(css).toContain("@media (max-width:");
  expect(css).toContain("@media (max-height:");
  expect(css + system).toContain("overflow-wrap: anywhere");
});

test("the command palette follows the grouped cmdk interaction contract", () => {
  for (const part of ["cmdk-root", "cmdk-input", "cmdk-list", "cmdk-group", "cmdk-group-heading", "cmdk-item"]) {
    expect(html).toContain(part);
  }
  expect(html).toContain('placeholder="Search chats or run a command"');
  expect(html).toContain("New chat");
  expect(html).toContain("Open folder");
  expect(html).toContain("Search files");
  expect(html).toContain('data-command-state="default"');
  expect(html).not.toContain('class="state-bar"');
  expect(css).toContain("[cmdk-item][data-selected]");
  expect(html).toContain('data-slot="kbd-group"');
  expect(html).toContain('data-slot="kbd"');
  expect(css).not.toContain(".command-shortcut");
  expect(css).toContain("overflow: visible");
  expect(css).toContain("calc(50% - 20.625rem)");
  expect(css).toContain("border-radius: calc(var(--r-shell) * 1.25)");
  expect(css).toContain("padding: .625rem 1rem .25rem");
  expect(css).toContain("padding: 0 .375rem .625rem");
  expect(js).toContain('case "ArrowDown"');
  expect(js).toContain('case "ArrowUp"');
  expect(js).toContain('event.key.toLowerCase() === "k"');
});

test("the plate owns layout only, not tokens or motion", () => {
  expect(css).not.toMatch(/^\s*--[\w-]+\s*:/m);
  expect(css).not.toMatch(/cubic-bezier\(/);
  expect(css).not.toMatch(/\b(?:[1-9]\d*|0?\.\d+)(?:ms|s)\b/);
  expect(css).not.toMatch(/z-index:\s*-?\d+/);
});
