import { expect, test } from "bun:test";
import { existsSync, readFileSync } from "node:fs";

const production = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
const chat = readFileSync(
  new URL("../../../docs/mockups/board-chat.css", import.meta.url),
  "utf8",
);
const chatHtml = readFileSync(
  new URL("../../../docs/mockups/board-chat.html", import.meta.url),
  "utf8",
);
const chatScript = readFileSync(
  new URL("../../../docs/mockups/board-chat.js", import.meta.url),
  "utf8",
);
const system = readFileSync(
  new URL("../../../docs/mockups/board-system.css", import.meta.url),
  "utf8",
);
const memoryHtml = readFileSync(
  new URL("../../../docs/mockups/board-memory.html", import.meta.url),
  "utf8",
);
const settingsHtml = readFileSync(
  new URL("../../../docs/mockups/board-settings.html", import.meta.url),
  "utf8",
);
const iconSync = readFileSync(
  new URL("../scripts/sync-mockup-icons.mjs", import.meta.url),
  "utf8",
);
const mockupTypesetUrl = new URL("../../../docs/mockups/typeset.css", import.meta.url);
const desktopTypesetUrl = new URL("../src/typeset.css", import.meta.url);
const desktopPackage = JSON.parse(
  readFileSync(new URL("../package.json", import.meta.url), "utf8"),
);

test("Chat uses the shipped desktop type scale and semantic roles", () => {
  for (const token of ["2xs", "xs", "sm", "base", "md", "lg", "xl", "2xl", "3xl"]) {
    const value = production.match(new RegExp(`--text-${token}:\\s*([^;]+);`))?.[1];
    expect(value).toBeDefined();
    expect(system).toContain(`--text-${token}: ${value};`);
  }

  expect(system).toMatch(/body \{[\s\S]*?font: var\(--text-base\)\/var\(--leading-body\) var\(--sans\)/);
  expect(system).toContain("--text-reading: var(--text-md);");
  expect(system).toContain("--text-section: var(--text-xl);");
  expect(chat).not.toMatch(/--text-(?:2xs|xs|sm|base|md|lg|xl|2xl|3xl):/);
});

test("Typeset is configured once and applied to mockup rich text", () => {
  expect(existsSync(mockupTypesetUrl)).toBe(true);
  expect(readFileSync(mockupTypesetUrl, "utf8")).toContain("shadcn/typeset");
  expect(system.startsWith('@import "./typeset.css";')).toBe(true);
  expect(system).toContain(`.typeset-notes {
  --typeset-font-body: var(--font-geist);
  --typeset-font-heading: var(--font-geist);
  --typeset-font-mono: var(--font-geist-mono);
  --typeset-size: var(--text-md);
  --typeset-leading: 1.6;
  --typeset-flow: 1em;
  font-size: var(--typeset-size);
}`);
  expect(chatHtml).toMatch(/class="[^"]*\btypeset\b/);
  expect(memoryHtml).toMatch(/class="[^"]*\btypeset\b/);
  expect(settingsHtml).toContain("board-system.css");

  expect(existsSync(desktopTypesetUrl)).toBe(false);
  expect(production).not.toContain("typeset.css");
  expect(desktopPackage.dependencies["@fontsource-variable/geist-mono"]).toBeUndefined();
});

test("Chat title is workspace-aligned while conversation content stays centered", () => {
  expect(chat).toContain(
    ".chat-head-inner { width: 100%; max-width: none; height: 52px; margin: 0; padding: 0 18px;",
  );
  expect(chat).toContain(
    ".chat-lane { width: 100%; max-width: var(--content-max-width); min-height: 100%; margin: 0 auto;",
  );
  expect(chat).toContain("body.compact .chat-head-inner { padding-left: 128px; }");
});

test("Chat session rows use shared pill geometry", () => {
  expect(chat).toMatch(/\.session\s*\{[^}]*height:\s*28px;[^}]*border-radius:\s*var\(--r-control\);/s);
  expect(chat).toMatch(/\.session:hover\s*\{[^}]*background:\s*var\(--state-hover-bg\);/s);
  expect(chat).toMatch(/\.session\.on\s*\{[^}]*background:\s*var\(--state-selected-bg\);/s);
});

test("Interactive row stacks use one shared physical gap", () => {
  expect(system).toContain("--interactive-row-gap: var(--space-1);");
  expect(system).toMatch(/\.dp-row-stack\s*\{[^}]*display:\s*grid;[^}]*gap:\s*var\(--interactive-row-gap\);/s);
  expect(chatHtml).toContain('class="rail-nav dp-row-stack"');
  expect(chatHtml.match(/class="session-group dp-row-stack"/g)?.length).toBe(3);
});

test("Chat shell toggles share computed chrome geometry", () => {
  expect(system).toContain("--shell-control-size: 1.375rem;");
  expect(system).toContain("--icon-size: 1rem;");
  expect(system).toContain("--chrome-control-inset: calc(var(--chrome-light-top) + (var(--chrome-light-size) - var(--shell-control-size)) / 2);");
  expect(system).toContain(".dp-shell-toggle {");
  expect(system).toContain(".dp-shell-toggle > svg,\n.dp-shell-toggle > .t-icon-swap {");
  expect(system).toMatch(/\.dp-shell-toggle-right\s*\{[^}]*z-index:\s*var\(--z-shell\)/);
  expect(chatHtml).toContain("dp-shell-toggle dp-shell-toggle-left");
  expect(chatHtml).toContain("dp-shell-toggle dp-shell-toggle-right");
  expect(chatHtml.indexOf('class="inspector-toolbar"')).toBeLessThan(chatHtml.indexOf('class="workspace"'));
  expect(chat).not.toMatch(/svg[^{}]*\{[^{}]*(?:width|height):\s*\d+(?:\.\d+)?px/);
  expect(chat).not.toContain(".inspector-toolbar { right:");
});

test("Chat composer uses a single theme-aware elevation layer", () => {
  expect(system).toContain("--composer-shadow:");
  expect(chat).toContain("box-shadow: var(--composer-shadow)");
  expect(chat).toMatch(/\.tool-btn\s*\{[^}]*background:\s*transparent;[^}]*box-shadow:\s*none;/);
  expect(chat).toMatch(/\.model-config-trigger\s*\{[^}]*background:\s*var\(--surface-3\);[^}]*box-shadow:\s*var\(--shadow-1\);/);
});

test("Chat composer uses the shipped ntrp icon contract", () => {
  expect(iconSync).toContain('"i-attach": Hugeicons.ImageAdd01Icon');
  expect(iconSync).toContain('"i-shield-slash": Hugeicons.ShieldBanIcon');
  expect(iconSync).toContain('"i-arrow-up": Hugeicons.ArrowUp01Icon');
  expect(iconSync).toContain('"i-stop": Hugeicons.StopIcon');
  expect(iconSync).toContain('"i-chevron": Hugeicons.ArrowDown01Icon');
  expect(chatHtml).toContain('aria-label="Attach image"');
  expect(chatHtml).toContain('href="#dp-attach"');
  expect(chatHtml).toContain('href="#dp-shield-slash"');
  expect(chatHtml).toContain('href="#dp-arrow-up"');
});

test("Chat sources share the response action row with copy and branch", () => {
  const actionRows = chatHtml.match(
    /<div class="response-actions" aria-label="Response actions">\s*<button class="source-footer-action"[^>]*>[\s\S]*?<button class="response-action dp-icon-button" data-response-action="copy"[\s\S]*?<button class="response-action dp-icon-button" data-response-action="branch"/g,
  );
  expect(actionRows?.length).toBe(2);
  expect(chatHtml).not.toMatch(/<div class="answer-footer(?: not-typeset)?">\s*<button class="source-footer-action"[\s\S]*?<div class="response-actions"/);
});

test("Chat source actions use the shared compact interaction states", () => {
  expect(chat).toMatch(/\.source-footer-action\s*\{[^}]*border-radius:\s*var\(--r-control\);[^}]*var\(--ease-out\)/s);
  expect(chat).toMatch(/\.source-footer-action:hover\s*\{[^}]*background:\s*var\(--state-hover-bg\);/s);
  expect(chat).toMatch(/\.source-footer-action\.on\s*\{[^}]*background:\s*var\(--state-selected-bg\);/s);
  expect(chat).not.toMatch(/\.source-footer-action[^}]*background:\s*var\(--surface-2\)/s);
});

test("Chat model picker exposes models directly with one compact effort submenu", () => {
  expect(chatHtml).toContain('class="model-config-menu dp-popover"');
  expect(chatHtml).toContain('class="model-effort-menu dp-popover"');
  expect(chatHtml).toContain('data-model="claude-opus-4.6"');
  expect(chatHtml).toContain('data-model-effort="claude-opus-4.6"');
  expect(chatHtml).not.toContain('data-config-panel="summary"');
  expect(chatHtml).not.toContain('Search models');
  expect(chatHtml).not.toContain('data-auto-model');
  expect(chat).toMatch(/\.model-config-menu\s*\{[^}]*width:\s*264px;/s);
  expect(chat).toMatch(/\.model-config-menu, \.model-effort-menu\s*\{[^}]*display:\s*grid;[^}]*gap:\s*var\(--space-0-5\);[^}]*border-radius:\s*var\(--surface-radius\);/s);
  expect(chat).toMatch(/\.model-config-option\s*\{[^}]*min-height:\s*32px;[^}]*border-radius:\s*var\(--r-control\);/s);
  expect(chat).toMatch(/\.model-config-option:is\(:hover, :focus-within\)\s*\{[^}]*background:\s*var\(--state-hover-bg\);/s);
  expect(chat).toMatch(/\.model-config-option:has\(\.model-choice\[aria-checked="true"\]\)\s*\{[^}]*background:\s*var\(--state-selected-bg\);/s);
  expect(chat).toMatch(/\.model-effort-menu button\s*\{[^}]*border-radius:\s*var\(--r-control\);/s);
  expect(chat).toMatch(/\.model-effort-menu button:is\(:hover, :focus-visible\)\s*\{[^}]*background:\s*var\(--state-hover-bg\);/s);
  expect(chat).toMatch(/\.model-effort-menu button\[aria-checked="true"\]\s*\{[^}]*background:\s*var\(--state-selected-bg\);/s);
  expect(chatHtml).not.toContain('class="model-check"');
  expect(chat).toMatch(/\.model-config-option\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\) auto;/s);
  expect(chat).not.toContain(".model-check");
  expect(chat).toMatch(/\.model-effort-trigger:is\(:hover, :focus-visible\), \.model-config-option\.effort-active \.model-effort-trigger\s*\{[^}]*background:\s*var\(--state-hover-bg\);/s);
});

test("Chat model changes use the shared reduced-motion-safe text swap", () => {
  expect(chatHtml).toContain('class="model-current t-text-swap"');
  expect(chatHtml).toContain('class="effort-current t-text-swap"');
  expect(chatScript).toMatch(/function syncConfig\(\{ animateTrigger = false \} = \{\}\)/);
  expect(chatScript).toMatch(/motion\.textSwap\.swap\(\$\('\.model-current'\), state\.model\)/);
  expect(chatScript).toMatch(/motion\.textSwap\.swap\(\$\('\.effort-current'\), state\.effort\)/);
  expect(chatScript).toMatch(/syncConfig\(\{ animateTrigger: true \}\)/);
  expect(chatScript).toMatch(/setEffortOpen\(false, activeEffortModel, \{ animateTrigger: true \}\)/);
});

test("Board maps raised surfaces to the shared elevation ladder", () => {
  expect(system).toContain("--border-width: 1px;");
  expect(system).toMatch(/\.dp-floating-surface\s*\{[^}]*background:\s*var\(--surface-3\);[^}]*box-shadow:\s*var\(--shadow-3\);/s);
  expect(system).toMatch(/\.dp-sidebar\s*\{[^}]*background:\s*var\(--surface-2\);[^}]*box-shadow:\s*var\(--shadow-2\);/s);
  expect(chat).toMatch(/\.composer\s*\{[^}]*background:\s*var\(--surface-3\);[^}]*box-shadow:\s*var\(--composer-shadow\);/s);
  expect(chat).toMatch(/\.model-config-menu, \.model-effort-menu\s*\{[^}]*background:[^;}]*var\(--surface-3\)[^}]*box-shadow:\s*var\(--shadow-3\);/s);
  expect(settingsHtml).toContain('class="dp-floating-surface dp-sheet setup-sheet"');
  expect(system).toMatch(/\.dp-sheet\s*\{[^}]*background:\s*var\(--surface-5\);[^}]*box-shadow:\s*var\(--shadow-5\);/s);
  expect(system).not.toContain("--bordered-shadow");
  expect(chat).not.toContain("--bordered-shadow");
  expect(chat).not.toContain("1px solid");
});
