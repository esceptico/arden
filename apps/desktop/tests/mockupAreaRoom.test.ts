import { expect, test } from "bun:test";
import { existsSync, readFileSync } from "node:fs";
import { Window } from "happy-dom";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
const html = read("../../../docs/mockups/board-area-room.html");
const css = read("../../../docs/mockups/board-area-room.css");
const js = read("../../../docs/mockups/board-area-room.js");
const systemCss = read("../../../docs/mockups/board-system.css");
const chatHtml = read("../../../docs/mockups/board-chat.html");
const homeCss = read("../../../docs/mockups/board-home.css");
const appRailUrl = new URL("../../../docs/mockups/board-app-rail.css", import.meta.url);
const appRailCss = existsSync(appRailUrl) ? readFileSync(appRailUrl, "utf8") : "";
const attentionCss = read("../../../docs/mockups/board-attention.css");

test("Area Room uses the shared desktop shell and one resting workspace", () => {
  expect(html).toContain('board-area-room.css?v=20260722-11');
  expect(html).not.toMatch(/<html[^>]*data-theme=/);
  expect(html).toContain('class="dp-traffic');
  expect(html).toMatch(/class="dp-shell-toggle dp-shell-back" href="\.\/board-home\.html" aria-label="Back to Home"/);
  expect(html).toContain('class="rail dp-sidebar"');
  expect(html).toContain('class="rail-nav"');
  expect(html).toContain('class="area-list"');
  expect(html).toMatch(/class="area-row on" href="\.\/board-area-room\.html"[^>]* aria-current="page"[^>]*><span>Market launch<\/span>/);
  expect(html).toContain('class="rail-bottom"');
  expect(html).toContain('id="sidebar-toggle"');
  expect(html).not.toContain("Area settings");
  expect(html).not.toContain("New work");
    expect(html).toMatch(/class="[^"]*\barea-room\b[^"]*"/);
  expect(html).toContain('class="room-scroll"');
  expect(css).toMatch(/\.area-room\s*\{[^}]*overflow:\s*hidden/);
  expect(css).toMatch(/\.room-scroll\s*\{[^}]*overflow:\s*auto/);
  expect(css).toMatch(/\.workspace\s*\{[^}]*padding-left:\s*calc\(var\(--sidebar-width\) \+ var\(--sidebar-gap\)\)/);
  expect(css).not.toMatch(/\.area-composer\s*\{[^}]*position:\s*sticky/);
});

test("Home and Area Room share one app rail implementation", () => {
  expect(homeCss).toContain('@import "./board-app-rail.css');
  expect(css).toContain('@import "./board-app-rail.css');
  expect(appRailCss).toContain(".rail-nav");
  expect(appRailCss).toContain(".area-row");
  expect(appRailCss).toContain(".rail-bottom");
});

test("Area Room speaks Home's visual language", () => {
  expect(html).toContain('class="answer-line">Market launch</h1>');
  expect(html).toContain('class="answer-sub"');
  expect(html).toContain('class="deck-card"');
  expect(html).toContain('class="card-title"');
  expect(html).toContain('class="card-reason"');
  expect(html).not.toMatch(/class="eyebrow"/);
  expect(html).not.toContain("AREA ROOM");
  expect(attentionCss).toMatch(/\.answer-line\s*\{[^}]*font-size:\s*var\(--text-3xl\)[^}]*font-weight:\s*620/);
  expect(attentionCss).toMatch(/\.card-title\s*\{[^}]*font-size:\s*var\(--text-2xl\)[^}]*font-weight:\s*620/);
});

test("attention reads as one open list instead of stacked cards and accordions", () => {
  const asks = html.match(/<section class="deck-region"[\s\S]*?<\/section>/)?.[0] || "";
  expect(asks).not.toMatch(/dp-card|dp-chevron|dp-angle|Awaiting approval/);
  expect(css).toMatch(/\.deck-card\s*\{[^}]*background:\s*transparent[^}]*border:\s*0/);
  expect(html.match(/class="rest-row"/g)?.length).toBe(2);
  expect(html).not.toContain("queue-summary");
});

test("the queue and outcome stay visually adjacent", () => {
  expect(css).toMatch(/\.room-scroll\s*\{[^}]*margin-top:\s*12px[^}]*padding:\s*8px 2px 32px/);
});

test("the large Area Room scroll surface keeps a panel-shaped focus outline", () => {
  expect(css).toMatch(/\.room-scroll:focus-visible\s*\{[^}]*border-radius:\s*var\(--r-panel\)/);
  expect(css).not.toMatch(/\.room-scroll:focus-visible\s*\{[^}]*border-radius:\s*var\(--r-row\)/);
});

test("the attention block uses Mission Control's focused queue", () => {
  expect(attentionCss).toMatch(/\.deck-card\s*\{[^}]*padding:\s*0 2px/);
  expect(css).toMatch(/\.deck-card\s*\{[^}]*padding-bottom:\s*6px/);
  expect(html).toContain('class="deck-chrome"');
  expect(html).toContain('class="deck-pos"');
  expect(html).toContain('class="t-text-swap" data-request-position>1 of 3');
  expect(html).toContain('class="rest" data-request-rest');
  expect(attentionCss).toMatch(/\.rest-row,\s*\.strip-head\s*\{[^}]*border-radius:\s*var\(--r-row\)/s);
  expect(homeCss).not.toMatch(/\.rest-row\s*\{/);
  expect(css).not.toMatch(/\.deck-card\s*\{[^}]*padding:\s*0 8px 20px/);
});

test("focused and peer requests share one horizontal content rail", () => {
  expect(attentionCss).toMatch(/\.deck-card\s*\{[^}]*padding:\s*0 2px/);
  expect(attentionCss).toMatch(/\.deck-chrome\s*\{[^}]*padding:\s*0 2px/);
  expect(attentionCss).toMatch(/\.rest-row,\s*\.strip-head\s*\{[^}]*width:\s*calc\(100% \+ var\(--space-5\)\)/s);
  expect(attentionCss).toMatch(/\.rest-row,\s*\.strip-head\s*\{[^}]*margin-inline:\s*calc\(var\(--space-2-5\) \* -1\)/s);
  expect(attentionCss).toMatch(/\.rest-row,\s*\.strip-head\s*\{[^}]*padding-inline:\s*var\(--space-3\)/s);
});

test("peer requests change focus without leaving the attention collection", () => {
  expect(html).toMatch(/class="rest-row"[^>]*data-request-id="publish"[\s\S]*?Publish the evidence brief[\s\S]*?approval · 18m/);
  expect(html).toMatch(/class="rest-row"[^>]*data-request-id="notion"[\s\S]*?Reconnect Notion[\s\S]*?sign-in · 31m/);
  expect(js).toContain("const AREA_REQUESTS");
  expect(js).toContain("renderRequestQueue");
  expect(js).not.toMatch(/triggers:\s*\[[^\]]*queueSummary/);
});

test("focused request content uses the shared transitions.dev text swap", () => {
  expect(html.match(/class="t-text-swap"/g)?.length).toBe(7);
  expect(js).toContain("motion?.textSwap?.swap");
  expect(js).not.toContain("motion?.deck?.promote");
});

test("selecting a peer promotes it while keeping all requests in the same queue", () => {
  const window = new Window({ url: "http://127.0.0.1/board-area-room.html" });
  window.document.write(html);
  Object.assign(window, {
    BOARD_MOTION: {
      peek: { bind: () => ({ open() {} }) },
      tabPanels: { bind() {} },
      deck: { promote() {} },
    },
  });
  new Function("window", "document", "location", "matchMedia", js)(
    window,
    window.document,
    window.location,
    window.matchMedia.bind(window),
  );

  const publish = window.document.querySelector<HTMLButtonElement>('[data-request-id="publish"]');
  publish?.click();

  expect(window.document.querySelector("[data-request-title]")?.textContent).toBe("Publish the evidence brief");
  expect(window.document.querySelector("[data-request-position]")?.textContent).toBe("2 of 3");
  expect([...window.document.querySelectorAll("[data-request-rest] .rest-row")].map(row => row.getAttribute("data-request-id"))).toEqual(["audience", "notion"]);
});

test("the resting hierarchy keeps attention fixed and reference work scrollable", () => {
  for (const region of ["agent-presence", "area-asks", "area-work"]) {
    expect(html).toContain(`data-region="${region}"`);
  }
  expect(html).toContain('class="deck-region"');
  expect(html).toMatch(/class="deck-card"[\s\S]*?class="rest" data-request-rest/);
  expect(html.indexOf('data-region="area-asks"')).toBeLessThan(html.indexOf('class="room-scroll"'));
  expect(html.indexOf('data-region="area-work"')).toBeGreaterThan(html.indexOf('class="room-scroll"'));
  expect(html).not.toMatch(/data-region="(?:outcomes|open-loops|activity|related-areas)"/);
  expect(html.match(/class="room-section"/g)?.length).toBe(1);
  expect(html).not.toContain('data-region="area-composer"');
  expect(html).not.toMatch(/aria-label="Send/);
});

test("Area details use Chat's quiet two-view inspector pattern", () => {
  expect(html).toMatch(/<button[^>]*class="dp-shell-toggle dp-shell-toggle-right"[^>]*id="inspector-toggle"[^>]*aria-label="Show Area details"/);
  expect(html).toMatch(/<aside[^>]*id="area-inspector"[^>]*data-region="area-inspector"[^>]*hidden/);
  expect(html).not.toContain("Agent Hub");
  for (const className of ["dp-peek-header", "dp-peek-tabs", "dp-peek-tab", "dp-peek-tab-indicator", "dp-peek-actions"]) {
    expect(html).toContain(className);
    expect(chatHtml).toContain(className);
    expect(systemCss).toContain(`.${className}`);
  }
  expect(css).not.toContain(".inspector-tabs");
  expect(css).not.toContain("--inspector-width");
  expect(css).not.toContain(".peek-row");
  expect(systemCss).toMatch(/\.dp-peek\s*\{[^}]*width:\s*var\(--peek-width\)/);
  expect(html.match(/data-tab-value=/g)?.length).toBe(2);
  expect(html.match(/data-tab-panel=/g)?.length).toBe(2);
  expect(html).toContain("data-tab-panels");
  for (const tab of ["activity", "sources"]) {
    expect(html).toContain(`data-tab-value="${tab}"`);
    expect(html).toContain(`data-tab-panel="${tab}"`);
  }
  for (const group of ["approvals", "todos", "agents", "workflows", "automations"]) {
    expect(html).toContain(`data-inspector-group="${group}"`);
    expect(html).not.toContain(`data-inspector-tab="${group}"`);
  }
  expect(html).toContain("data-parent-session");
  expect(html).toContain("data-child-session");
});

test("Area Room motion uses restrained shared transitions", () => {
  expect(js).toContain("motion.tabPanels.bind");
  expect(js).toContain("motion.peek.bind");
  expect(js).not.toContain("changeInspectorView");
  expect(js).not.toContain("motion.surface.show");
  expect(js).not.toContain("motion.surface.hide");
  expect(js).toContain("motion.sidebar.sync");
  expect(js).toContain("motion.shellToggle.sync");
  expect(js).not.toContain(".animate(");
});

test("peek tabs use the neutral shared line treatment", () => {
  expect(systemCss).toMatch(/\.dp-peek-tabs\[data-tabs-variant="line"\] \.dp-peek-tab-indicator\s*\{[^}]*background:\s*var\(--ink\)[^}]*box-shadow:\s*none/);
});

test("the plate survives width and height pressure without private motion tokens", () => {
  expect(css).toContain("@media (max-width:");
  expect(css).toContain("@media (max-height:");
  expect(css).toContain("overflow-wrap: anywhere");
  expect(css).not.toMatch(/^\s*--[\w-]+\s*:/m);
  expect(css).not.toMatch(/cubic-bezier\(/);
  expect(css).not.toMatch(/\b(?:[1-9]\d*|0?\.\d+)(?:ms|s)\b/);
  expect(css).not.toMatch(/z-index:\s*-?\d+/);
});

test("runtime and recovery states remain visible words", () => {
  for (const state of ["running", "awaiting approval", "awaiting input", "auth required", "completed", "failed", "cancelled", "interrupted", "stale"]) {
    expect(html.toLowerCase()).toContain(state);
  }
  expect(html).not.toContain("●");
  expect(html).not.toMatch(/class=["'][^"']*(?:status-dot|status-pip)/);
});
