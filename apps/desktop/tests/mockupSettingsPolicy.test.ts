import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";

const settingsSource = readFileSync(
  new URL("../../../docs/mockups/board-settings.html", import.meta.url),
  "utf8",
);
const motionSource = readFileSync(
  new URL("../../../docs/mockups/board-motion.js", import.meta.url),
  "utf8",
);
const memorySource = readFileSync(
  new URL("../../../docs/mockups/board-memory.html", import.meta.url),
  "utf8",
);
const systemSource = readFileSync(
  new URL("../../../docs/mockups/board-system.css", import.meta.url),
  "utf8",
);
const settingsCompact = settingsSource.replace(/\s+/g, "");

test("Settings keeps fixed policy tabs while Memory labels adapt to available rail width", () => {
  expect(settingsCompact).toContain(".policy{position:relative;width:var(--tab-triplet-width);height:var(--tab-bar-height);display:inline-flex");
  expect(settingsCompact).toContain(".policybutton{position:relative;z-index:var(--z-raised);width:var(--tab-control-size);min-width:var(--tab-control-size)");
  expect(settingsCompact).toContain(".tool-list.row-end{width:var(--tab-triplet-width);");
  expect(systemSource).toContain("--tab-triplet-width: calc(");
  expect(settingsCompact).toContain('button.innerHTML=`<svgclass="policy-icon"');
  expect(settingsSource).not.toContain("policy-label");
  expect(settingsSource).not.toContain("expanding:group.classList.contains('policy')");
  expect(memorySource).toContain("MOTION.tabs.bind(railModes");
  expect(memorySource).toContain('indicatorSelector: ".seg-indicator"');
  expect(memorySource).not.toContain('labelSelector: ".sl"');
  expect(memorySource).not.toContain("expanding: {");
  expect(memorySource).toContain("@container rail (min-width:19rem)");
  expect(memorySource).toContain(".seg .sl{display:none");
  expect(memorySource).toContain("grid-template-columns:repeat(3,minmax(0,1fr))");
  expect(motionSource).toContain('length(config.collapsedWidth, "--tab-control-size")');
  expect(memorySource).not.toContain("function syncRailModeIndicator");
  expect(memorySource).not.toContain("railModes.style.gridTemplateColumns");
});

test("Memory file rows are visibly nested beneath directory labels", () => {
  expect(systemSource).toContain("--memory-tree-base-inset:");
  expect(systemSource).toContain("--memory-tree-indent-step: 1rem");
  expect(systemSource).toContain("--memory-tree-twisty-size: 1rem");
  expect(memorySource).toContain(".trow::before");
  expect(memorySource).toContain('class="fold subfold"');
  expect(memorySource).toContain('class="trow depth-2"');
  expect(memorySource).toMatch(/class="tree-nest depth-1(?: [^"]+)?"/);
  expect(memorySource).toMatch(/class="tree-nest depth-2(?: [^"]+)?"/);
});

test("Memory breadcrumbs preserve the complete directory path", () => {
  expect(memorySource).toContain('path: ["pages", "planning"]');
  expect(memorySource).toContain("page.path || [page.section]");
  expect(memorySource).toContain('crumbSegments.join(" / ")');
});

test("ordinary Settings segments use the shared sliding-tab controller", () => {
  expect(motionSource).toContain("tabsSliding: 250");
  expect(motionSource).toContain('"--tabs-dur": `${duration.tabsSliding}ms`');
  expect(motionSource).toContain('"--tabs-ease": motion.curve.smoothCss');
  expect(settingsSource).toContain("window.BOARD_MOTION.tabs.bind(group");
  expect(motionSource).toContain('button.classList.add("dp-tab")');
  expect(motionSource).toContain('const target = event.target.closest("button")');
  expect(motionSource).toContain("buttons().includes(target)");
  expect(motionSource).not.toContain("event.target.closest(tabSelector)");
  expect(settingsCompact).toContain('group.setAttribute("role","tablist")');
  expect(systemSource).toContain("transform var(--tabs-dur) var(--tabs-ease)");
  expect(settingsSource).not.toContain("button.classList.add('t-tab')");
  const segmentedButton = systemSource.match(/\.dp-segmented > button \{([^}]*)\}/)?.[1] ?? "";
  expect(segmentedButton).toContain("font-size: var(--text-control)");
  expect(segmentedButton).toContain("font-weight: 500");
  expect(segmentedButton).toContain("color: var(--muted)");
  expect(settingsSource).not.toContain(".seg button.on{color:var(--ink);font-weight:540}");
});

test("refresh morph reserves layout without clipping its control shadow", () => {
  expect(systemSource).toContain("--refresh-control-width: 6rem");
  const refreshRules = settingsCompact.match(/\.refresh-slot\{[\s\S]*?\.refresh-action:disabled\{[^}]*\}/)?.[0] ?? "";
  expect(settingsCompact).toContain(".refresh-slot{width:var(--refresh-control-width)");
  expect(settingsCompact).toContain("contain:layout");
  expect(settingsCompact).not.toContain("contain:layoutpaint");
  expect(settingsCompact).toContain("overflow:visible");
  expect(settingsCompact).toContain("display:grid;place-items:center");
  expect(settingsCompact).toContain("display:inline-flex;align-items:center;justify-content:center");
  expect(refreshRules).not.toContain("background:transparent");
  expect(refreshRules).not.toContain(".refresh-action::before");
  expect(systemSource).toMatch(/\.dp-button,\s*\n\.btn\s*\{[^}]*min-height:\s*var\(--control-size-large\);/s);
  expect(systemSource).toContain("background: var(--surface-3);");
  expect(systemSource).toContain("box-shadow: var(--control-shadow);");
});

test("floating setup sheets keep four corners and a quiet action footer", () => {
  const sheetRule = settingsCompact.match(/\.setup-sheet\{([^}]*)\}/)?.[1] ?? "";
  const footerRule = settingsCompact.match(/\.setup-sheet\.dp-sheet-footer\{([^}]*)\}/)?.[1] ?? "";
  expect(sheetRule).toContain("border-radius:var(--surface-radius)");
  expect(footerRule).toContain("border-top:0");
  expect(footerRule).toContain("padding:0.5rem0.75rem0.75rem");
});

test("Settings uses the shared compact grouped-sidebar rhythm", () => {
  for (const className of [
    "dp-sidebar-header",
    "dp-sidebar-nav",
    "dp-sidebar-nav-highlight",
    "dp-sidebar-group",
    "dp-sidebar-group-label",
    "dp-sidebar-nav-item",
  ]) {
    expect(systemSource).toContain(`.${className}`);
    expect(settingsSource).toContain(className);
  }
  expect(settingsSource).not.toContain(".nav-group{");
  expect(settingsSource).not.toContain(".nav-label{");
  expect(settingsSource).not.toContain(".nav-item{");
  for (const heading of ["General", "Intelligence", "Capabilities", "Data"]) {
    expect(settingsSource).toContain(`dp-sidebar-group-label\">${heading}</div>`);
  }
  const sidebarHighlight = systemSource.match(/\.dp-sidebar-nav-highlight \{([^}]*)\}/)?.[1] ?? "";
  expect(sidebarHighlight).toContain("transition: none");
  expect(sidebarHighlight).not.toContain("transform var(");
});

test("Settings slider fields use the full available form lane", () => {
  const comfortableRule = settingsCompact.match(/\.comfortable\{([^}]*)\}/)?.[1] ?? "";
  expect(comfortableRule).toContain("width:100%");
  expect(comfortableRule).not.toContain("390px");
  expect(settingsSource).not.toContain("width:min(100%,390px)");
});

test("MCP server rows present identity before status", () => {
  expect(settingsCompact).toContain('<divclass="row-title"><span>Obsidian</span><spanclass="dp-status"data-tone="success">Ready</span></div>');
  expect(settingsCompact).toContain('<divclass="row-title"><span>Linear</span><spanclass="dp-status"data-tone="warning">Authrequired</span></div>');
  expect(settingsSource).not.toContain('data-tone="success">Ready</span><span>Obsidian</span>');
  expect(settingsSource).not.toContain('data-tone="warning">Auth required</span><span>Linear</span>');
});
