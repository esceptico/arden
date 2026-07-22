import { expect, test } from "bun:test";
import { existsSync, readFileSync } from "node:fs";

const url = (name: string) => new URL(`../../../docs/mockups/${name}`, import.meta.url);
const read = (name: string) => existsSync(url(name)) ? readFileSync(url(name), "utf8") : "";

const html = read("board-home.html");
const css = read("board-home.css");
const js = read("board-home.js");
const system = read("board-system.css");
const attention = read("board-attention.css");

test("Mission Control uses the shared Board foundation", () => {
  expect(existsSync(url("board-home.html"))).toBe(true);
  expect(html).toContain("board-icons.js");
  expect(html).toContain("board-motion.js");
  expect(css.startsWith('@import "./board-system.css')).toBe(true);
});

test("Mission Control is one self-describing attention desk", () => {
  expect(html).toContain("Mission Control");
  expect(html).toContain('data-region="universal-capture"');
  expect(html).toContain('data-region="deck"');
  expect(html).toContain('data-region="ambient"');
  expect(html).toContain("Needs you");
  expect(html).toContain("Agent activity");
  for (const strip of ["working", "scheduled", "done", "aside"]) expect(html).toContain(`data-strip="${strip}"`);
});

test("the entrypoint has no competing dashboard navigation", () => {
  expect(html).not.toContain("Continuous");
  expect(html).not.toContain(">Today<");
  expect(html).not.toContain("status-ribbon");
  expect(html).not.toContain("status-drawer");
  expect(html).not.toContain("data-group-toggle");
  expect(html).not.toContain("That's it for today");
  expect(html).not.toContain("Gmail");
  expect(html).not.toContain("Calendar");
});

test("the attention deck is backed by real ask semantics and reversible handling", () => {
  expect(js).toContain("kind:");
  expect(js).toContain("reason:");
  expect(js).toContain("next:");
  expect(js).toContain("function setAside");
  expect(js).toContain("function undo");
  expect(js).toContain("state.last");
  expect(html).not.toContain('data-not-today');
  expect(js).not.toContain("notTodayEl");
  expect(html).toContain('data-undo');
});

test("peer request metadata uses stable semantic columns", () => {
  expect(js).toContain('class="rest-meta"');
  expect(js).toContain('<span>${ask.kind}</span>');
  expect(js).toContain('<span>${ask.area}</span>');
  expect(js).toContain('<span>${fmtAge(ask.waitedMin)}</span>');
  expect(attention).toMatch(/\.rest-meta\s*\{[^}]*display:\s*grid[^}]*grid-template-columns:\s*var\(--rest-meta-columns\)/s);
  expect(js).toContain('<span class="rest-meta-separator">·</span>');
  expect(attention).toMatch(/\.rest-meta-separator\s*\{[^}]*text-align:\s*center/s);
});

test("the cleared attention state is a compact confirmation with adjacent undo", () => {
  expect(html).toContain('class="zero-status">All requests handled</span>');
  expect(html).not.toContain('data-zero-mark');
  expect(html).not.toContain('data-zero-recap');
  expect(css).toMatch(/\.deck-slot:has\(\.zero:not\(\[hidden\]\)\)[^{]*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\) auto/);
  expect(css).toMatch(/\.zero\s*\{[^}]*min-height:\s*44px[^}]*display:\s*flex/);
  expect(js).not.toContain("buildZeroMark");
  expect(js).not.toContain("recapLine");
});

test("the page never scrolls and expanded ambient details own short-window overflow", () => {
  expect(css).toMatch(/\.workspace\s*\{[^}]*overflow:\s*hidden/s);
  expect(css).toMatch(/@media \(max-height: 47rem\)[\s\S]*\.strip-body\s*\{[^}]*overflow:\s*auto/s);
  expect(html).toContain('data-region="ambient"');
  expect(css).not.toMatch(/\.workspace\s*\{[^}]*overflow-y:\s*auto/s);
  expect(css).not.toMatch(/\.page\s*\{[^}]*height:\s*auto/s);
});

test("delegated work remains ambient but actionable", () => {
  expect(html).toContain('aria-labelledby="agent-activity-title"');
  expect(html).toContain('class="dp-section-label" id="agent-activity-title">Agent activity</h2>');
  expect(system).toMatch(/\.dp-section-label\s*\{[^}]*text-transform:\s*uppercase/);
  expect(html).not.toContain('agent-activity-hint');
  expect(html).not.toContain('aria-label="Working without you"');
  expect(html).toContain('data-rows-working');
  expect(html).toContain('data-rows-scheduled');
  expect(html).toContain('data-rows-done');
  expect(js).toContain("SCHEDULED");
  expect(js).toContain("syncStrips");
  expect(js).toContain("Opening —");
  expect(js).not.toContain("row.remove()");
});

test("Mission Control keeps fixed product typography and shared pressure", () => {
  expect(css).not.toMatch(/font-size:\s*(?:clamp|min|max|calc)\(/);
  expect(system).toContain("--press-scale-subtle:");
  expect(css).toContain("var(--press-scale-subtle)");
});

test("demo controls stay out of the product surface unless explicitly requested", () => {
  expect(css).toMatch(/\.plate\s*\{[^}]*display:\s*none/s);
  expect(css).toContain("body.demo-mode .plate");
  expect(js).toContain('searchParams.has("demo")');
});
