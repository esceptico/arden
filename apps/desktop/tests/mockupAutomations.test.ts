import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
const html = read("../../../docs/mockups/board-automations.html");
const css = read("../../../docs/mockups/board-automations.css");
const js = read("../../../docs/mockups/board-automations.js");
const system = read("../../../docs/mockups/board-system.css");
const motion = read("../../../docs/mockups/board-motion.js");

test("Automations is a local master-detail instrument", () => {
  expect(css.startsWith('@import "./board-system.css')).toBe(true);
  expect(html).toContain("board-icons.js");
  expect(html).toContain("board-motion.js");
  expect(html).toContain('class="automation-rail dp-sidebar"');
  expect(css).toContain("overflow: hidden;");
  expect(css).toContain(".detail-scroll");
  expect(css).toContain("overflow: auto;");
  expect(css).toContain("body.compact-detail .automation-rail");
  expect(css).toContain("body.compact-detail .workspace");
  expect(js).toContain('const groupOrder = ["Yours", "Area agents", "System"]');
  expect(js).not.toContain('group: "Suggested"');
});

test("two-line automation rows use shared dense geometry and stack spacing", () => {
  expect(system).toContain("--r-dense-row: var(--r-panel);");
  expect(system).toContain(".dp-dense-row { border-radius: var(--r-dense-row); }");
  expect(system).toContain(".dp-row-stack { display: grid; gap: var(--interactive-row-gap); }");
  expect(js).toContain('rows.className = "automation-rows dp-row-stack"');
  expect(js).toContain('button.className = "automation-row dp-dense-row"');
  expect(css).not.toMatch(/\.automation-row\s*\{[^}]*border-radius:/s);
});

test("automation search and rows share one horizontal edge", () => {
  expect(css).toMatch(/\.rail-head\s*\{[^}]*padding:\s*0 var\(--space-0-5\) 0 \.5rem;/s);
  expect(css).toMatch(/\.rail-search\s*\{[^}]*width:\s*auto;[^}]*margin:\s*\.625rem var\(--space-0-5\) \.25rem;/s);
  expect(css).toMatch(/\.automation-groups\s*\{[^}]*padding:\s*0 var\(--space-0-5\) \.5rem;/s);
});

test("layout dimensions live in the shared Board token file", () => {
  for (const token of [
    "--automation-detail-max-width",
    "--automation-window-gutter",
    "--automation-footer-size",
    "--automation-row-size",
    "--automation-ledger-row-size",
    "--automation-content-inset",
    "--automation-schedule-row-size",
    "--automation-schedule-token-size",
    "--automation-schedule-tape-size",
    "--automation-schedule-day-size",
    "--automation-pause-control-width",
  ]) {
    expect(system).toContain(`${token}:`);
    expect(css).not.toMatch(new RegExp(`${token}:\\s*`));
  }
  expect(system).not.toContain("--automation-rail-width:");
  expect(css).toContain("width: var(--sidebar-width)");
  expect(css).toContain("padding-left: calc(var(--sidebar-width) + var(--sidebar-gap))");
  expect(motion).toContain("display:block;position:absolute;visibility:hidden");
  expect(js).toContain('const compact = motion.geometry.maxWidthQuery("--breakpoint-compact")');
  expect(js).not.toContain("matchMedia(motion.geometry.maxWidthQuery");
});

test("editing preserves explicit commit boundaries", () => {
  expect(html).toContain('data-draft-create hidden>Create</button>');
  expect(html).toContain('data-draft-cancel hidden>Cancel</button>');
  expect(html).not.toContain("saved on blur");
  expect(js).not.toContain("Nothing is created until you confirm");
  expect(js).toContain('showSaveState(draft ? "Draft" : "Saved"');
  expect(html).toContain("data-schedule-save>Save trigger</button>");
  expect(html).toContain("data-schedule-cancel>Cancel</button>");
  expect(js).toContain("stagedSchedule = { ...DEFAULT_SCHEDULE, ...item.schedule }");
  expect(js).toContain("function saveSchedule()");
  expect(html).not.toContain('class="head-actions"');
  expect(html).toMatch(/<footer class="detail-footer">[\s\S]*pause-action[\s\S]*run-action/);
  expect(js).toContain('document.addEventListener("pointerdown"');
  expect(js).toContain('if (event.key === "Escape")');
  expect(js).toContain("automations.splice(3, 0, created)");
  expect(js).toContain('$(".runs-section").hidden = Boolean(draft)');
});

test("run states, safety, and results remain actionable", () => {
  expect(html).not.toContain("data-run-ruler");
  expect(html).not.toContain("data-run-orbit");
  expect(html).not.toContain('class="run-instrument"');
  expect(html).toContain('class="run-ledger"');
  expect(html).toContain('class="safety-warning"');
  expect(html).toContain('class="result-peek dp-floating-surface dp-peek"');
  expect(html).toContain('class="trigger-peek dp-floating-surface dp-peek"');
  expect(js).toContain("item.runs.slice(0, 5)");
  expect(js).toContain("motion.peek.bind(resultPeek");
  expect(js).toContain("motion.peek.bind(triggerPeek");
  expect(js).toContain("motion.tabPanels.bind($(\".schedule-tabs\")");
  expect(js).toContain("role=\"combobox\"");
  expect(js).toContain("role=\"listbox\"");
  expect(js).not.toContain("<select");
  expect(js).toContain("document.body.append(menu)");
  expect(js).toContain('kind: "at"');
  expect(js).toContain('kind: "every"');
  expect(js).not.toContain('kind: "schedule"');
  expect(js).toContain('aria-label="${mode === "every" ? "Interval" : "Time"}"');
  expect(js).toContain("function renderDayRuler");
  expect(js).toContain("function syncDayRuler");
  expect(js).toContain('class="day-ruler"');
  expect(js).not.toContain("renderIntervalDial");
  expect(js).not.toContain('class="interval-dial"');
  expect(js).not.toContain("automation-pause-core");
  expect(js).toContain("function runsPerDay");
  expect(js).toContain("motion.iconSwap.swap(runAction");
  expect(js).toContain("motion.textSwap.swap");
  expect(js).toContain("motion.content.swap(runLedger");
  expect(js).not.toContain("function rulerPosition(index, count)");
  expect(js).not.toContain("function renderRunRuler(item)");
  expect(css).toContain("grid-template-columns: subgrid");
  expect(css).not.toContain("repeating-conic-gradient");
});

test("trigger identity is not repeated and shared press feedback stays restrained", () => {
  expect(html).not.toContain("data-trigger-title");
  expect(js).not.toContain("[data-trigger-title]");
  expect(system).toContain("--press-scale: .997;");
  expect(system).toContain("--press-scale-subtle: .999;");
  expect(css).toContain("scale(var(--press-scale-subtle))");
});

test("recent runs align day and time as separate computed columns", () => {
  expect(js).toContain('class="run-day"');
  expect(js).toContain('class="run-time"');
  expect(js).toContain("function formatRunStamp(run)");
  expect(css).toContain("grid-template-columns: .5rem max-content max-content minmax(0, 1fr) max-content .75rem;");
  expect(css).not.toContain(".run-stamp,");
});

test("trigger receipt separates cadence from schedule details", () => {
  expect(html).toContain("data-schedule-receipt-main");
  expect(html).toContain("data-schedule-receipt-meta");
  expect(js).toContain('receipt.dataset.label = label');
  expect(js).toContain('item.schedule = { ...stagedSchedule, kind, label: $("[data-schedule-receipt]").dataset.label }');
  expect(css).toContain(".trigger-receipt");
  expect(css).toContain("overflow-wrap: anywhere");
});

test("message trigger fields explain their accepted format", () => {
  expect(js).toContain('aria-describedby="message-channel-hint"');
  expect(js).toContain('aria-describedby="message-sender-hint"');
  expect(js).toContain('aria-describedby="message-matching-hint"');
  expect(js).toContain('id="message-channel-hint"');
  expect(js).toContain("Comma-separated · without #");
  expect(js).toContain("Optional · without @");
  expect(js).toContain("Optional · comma-separated words or phrases");
  expect(css).toContain(".field-hint");
});

test("automation shapes use the shared Board radius vocabulary", () => {
  expect(css).toContain(".rail-search {");
  expect(html).toContain('class="rail-search dp-search-shell dp-search-shell-compact"');
  expect(css).not.toMatch(/\.rail-search \{[^}]*(?:border|background|box-shadow|padding):/);
  expect(css).toMatch(/\.prompt-field \{[\s\S]*?border-radius: var\(--surface-radius\)/);
  expect(css).toMatch(/\.control-list, \.run-ledger \{[\s\S]*?border-radius: var\(--surface-radius\)/);
  expect(css).toMatch(/\.safety-warning \{[\s\S]*?border-radius: var\(--surface-radius\)/);
  expect(css).toMatch(/\.schedule-form input, \.schedule-select-trigger \{[\s\S]*?border-radius: var\(--r-control\)/);
  expect(css).toMatch(/\.schedule-token-field \{[\s\S]*?border-radius: var\(--r-control\)/);
  expect(css).toMatch(/\.schedule-days button \{[\s\S]*?border-radius: var\(--r-control\)/);
  expect(css).toMatch(/\.ruler-window-fields \{[\s\S]*?border-radius: var\(--surface-radius\)/);
  expect(css).toMatch(/\.schedule-select-menu \{[\s\S]*?border-radius: var\(--surface-radius\)/);
});

test("automation typography follows shared semantic roles", () => {
  expect(system).toMatch(/body \{[\s\S]*?font: var\(--text-base\)\/var\(--leading-body\) var\(--sans\)/);
  expect(css).toMatch(/\.rail-head h1 \{[\s\S]*?font-size: var\(--text-section\)/);
  expect(css).toMatch(/\.rail-head \{[^}]*height: 1\.875rem;[^}]*padding: 0 var\(--space-0-5\) 0 \.5rem/);
  expect(css).toMatch(/\.detail-title \{[\s\S]*?font-size: var\(--text-display\)/);
  expect(css).toMatch(/\.prompt-field \{[\s\S]*?font-size: var\(--text-base\)/);
  expect(css).not.toMatch(/font-size:\s*\d+(?:\.\d+)?(?:px|rem)/);
});

test("model menu shows one selected indicator", () => {
  expect(html).not.toContain("<span>✓</span>");
  expect(js).toContain('const MODEL_OPTIONS = Object.freeze(["session default", "claude-opus-4.6", "claude-sonnet-4.6", "gpt-5.6-sol"])');
  expect(js).toContain("function renderModelOptions()");
  expect(js).toContain("modelMenu.replaceChildren(label, ...options)");
  expect(js).toContain("function syncModelOptions(model)");
  expect(js).toContain('check?.toggleAttribute("hidden", !selected)');
});

test("shared geometry places popovers and rendered markup has no inline styles", () => {
  expect(motion).toContain("function placePopover");
  expect(js).toContain("motion.popover.place(trigger, panel");
  expect(js).not.toContain("panel.style.left");
  expect(js).not.toContain("panel.style.top");
  expect(js).not.toContain('style="');
  expect(js).toContain("renderDayRuler(ruler)");
});

test("outside click boundaries survive controls that rerender during pointerdown", () => {
  expect(js).toContain("function eventHits(event, selector)");
  expect(js).toContain("event.composedPath()");
  expect(js).toMatch(/eventHits\(event, "[^"]*\.trigger-peek/);
  expect(js).not.toContain('event.target.closest(".new-menu,.new-trigger,.trigger-peek');
});

test("committed controls preserve safety and motion state", () => {
  expect(js).not.toContain("item.schedule.from)");
  expect(js).toContain("!item.schedule.fromUser");
  expect(js).toContain('showSaveState("Saved")');
  const pauseHandler = js.match(/pauseAction\.addEventListener\("click", \(\) => \{([\s\S]*?)\n  \}\);/)?.[1] ?? "";
  expect(pauseHandler).toContain("motion.textSwap.swap");
  expect(pauseHandler).not.toContain("renderDetail()");
  expect(css).toContain("min-width: var(--automation-pause-control-width)");
});

test("motion uses the shared Board vocabulary", () => {
  expect(js).toContain("motion.content.swap(detail");
  expect(js).toContain("motion.popover.sync");
  expect(js).toContain("motion.sidebar.sync");
  expect(js).toContain("motion.theme.bindToggle");
  expect(css).not.toMatch(/transition[^;]*\b\d+(?:\.\d+)?m?s\b/);
  expect(css).not.toMatch(/animation[^;]*\b\d+(?:\.\d+)?m?s\b/);
  expect(css).not.toMatch(/cubic-bezier\(/);
  expect(css).toContain("@media (prefers-reduced-motion: reduce)");
});

test("automation detail follows the rail's vertical selection direction", () => {
  const selection = js.match(/async function selectAutomation\(id\) \{[\s\S]*?\n  \}/)?.[0] ?? "";
  expect(selection).toContain("automations.findIndex(item => item.id === id)");
  expect(selection).toContain('motion.content.swap(detail, commit, { axis: "y", direction })');
  expect(selection).not.toContain('axis: "x"');
});

test("trigger tab indicator has one positioning authority", () => {
  expect(html).toContain('class="schedule-tabs dp-segmented dp-tabs"');
  expect(html).toContain('class="dp-tab-indicator"');
  expect(system).toMatch(/\.dp-tab-indicator\s*\{[^}]*top:\s*0/);
  expect(css).not.toContain("schedule-tab-indicator");
});

test("run results use an adaptive peek instead of the full-height trigger editor", () => {
  expect(html).toContain('<header class="dp-peek-header"><div><span>Run result</span>');
  expect(css).toMatch(/\.trigger-peek\s*\{[^}]*bottom:\s*1rem/);
  expect(css).toMatch(/\.result-peek\s*\{[^}]*bottom:\s*auto[^}]*max-height:/);
  expect(css).not.toMatch(/\.trigger-peek,\s*\.result-peek\s*\{[^}]*bottom:\s*1rem/);
  expect(js).toContain('$("[data-result-title]").textContent = formatRunStamp(run)');
  expect(js).not.toContain('`${item.name} · ${formatRunStamp(run)}`');
});
