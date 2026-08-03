import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

test("Automations carries the redesign master-detail hierarchy into production", () => {
  const detail = read("../src/features/automations/components/AutomationDetail.tsx");
  const rail = read("../src/features/automations/components/AutomationRail.tsx");
  const workspace = read("../src/features/automations/components/AutomationsModal.tsx");
  const triggerPeek = read("../src/features/automations/components/ScheduleTriggerPeek.tsx");
  const scheduleEditor = read("../src/features/automations/components/ScheduleEditor.tsx");
  const css = read("../src/features/automations/components/automations-workspace.css");
  const tokens = read("../src/design/tokens.css");

  // Group headings collapse like the chat sidebar's (Area agents + System
  // fold by default; search overrides folding).
  expect(rail).toContain('new Set(["Area agents", "System"])');
  expect(rail).toContain("const isCollapsed = !normalizedQuery && collapsedGroups.has(label);");
  expect(rail).toContain('data-collapsed={collapsed ? "true" : undefined}');
  expect(css).toMatch(/\.automation-rail__group-label\[data-collapsed\] > svg \{ transform: rotate\(-90deg\); \}/);
  expect(detail).toContain('className="automation-detail__crumb"');
  expect(detail).toContain('className="automation-detail__summary"');
  expect(detail).not.toContain("automation-detail__header-actions");
  expect(detail).toContain("<ScheduleTriggerPeek");
  expect(detail).toContain("automation-detail__control-row--button");
  expect(detail).toContain("Trigger & permissions");
  expect(detail).not.toContain("Trigger and permissions");
  expect(detail).not.toContain("<ScheduleChip");
  expect(detail).toContain("automation-detail__run-status");
  expect(detail).toContain("<MoreHorizontal");
  expect(workspace).toContain("<TabPanels");
  expect(workspace).toContain('data-page-enter-item="chrome"');
  expect(workspace).toContain('data-page-enter-item>');

  for (const token of [
    "--automation-detail-max-width",
    "--automation-footer-size",
    "--automation-row-size",
    "--automation-ledger-row-size",
    "--automation-pause-control-width",
    "--peek-header-size",
    "--surface-blur",
  ]) {
    expect(tokens).toContain(`${token}:`);
  }

  expect(css).toContain("grid-template-rows: auto minmax(0, 1fr) var(--automation-footer-size)");
  expect(css).toContain("grid-template-columns: .5rem max-content max-content minmax(0, 1fr) max-content .75rem");
  expect(css).toContain("grid-template-columns: subgrid");
  expect(css).toContain(".automation-detail__pause");
  expect(css).toContain(".automation-delete-dialog");
  expect(css).toContain(".automation-trigger-peek");
  expect(css).toContain("top: 4.5rem");
  expect(css).toContain("bottom: var(--space-4)");
  expect(css).toContain(".automation-trigger-peek__actions");
  expect(css).toContain(".automation-trigger-editor__body");
  expect(css).toContain("@media (max-width: 46.25rem)");

  expect(triggerPeek).toContain("<PeekSurface");
  expect(triggerPeek).toContain('ariaLabel="Edit trigger"');
  expect(triggerPeek).toContain("Save trigger");
  expect(triggerPeek).toContain("rowsFromDraft");
  expect(triggerPeek).toContain('variant="quiet" size="md" onClick={onClose}>Cancel</Button>');
  expect(triggerPeek).toContain("disabled={!valid}");
  expect(triggerPeek).toContain("closeOnOutsidePointerDown");
  expect(detail).toContain("data-trigger-peek-owner");
  expect(detail).toContain("data-run-peek-owner");
  expect(detail).toContain("current ?? { ...form.schedule }");
  expect(detail).toContain("setTriggerDraft(null)");
  expect(triggerPeek).toContain('outsidePointerDownIgnoreSelector=".surface-popover, [data-trigger-peek-owner]"');
  expect(workspace).toContain("onOpenTrigger={() => setRunPeek(null)}");
  expect(workspace).toContain('outsidePointerDownIgnoreSelector=".surface-popover, [data-run-peek-owner]"');
  expect(triggerPeek).not.toContain("focusOnOpen");
  expect(scheduleEditor).toContain("export function ScheduleEditor");
  expect(scheduleEditor).toContain('label="Channel"');
  expect(scheduleEditor).toContain('hint="Comma-separated · without # · e.g. release, launch"');
  expect(scheduleEditor).toContain('label="Calendar event"');
  expect(scheduleEditor).toContain('label="Lead time"');
  expect(scheduleEditor).toContain("<TabPanels");
  expect(scheduleEditor).toContain("useTabDirection");
  expect(scheduleEditor).not.toContain("createPortal");
  expect(scheduleEditor).not.toContain("ScheduleChip");
  expect(css).toContain(".automation-trigger-editor__form");
  expect(css).toContain(".automation-trigger-editor__field");
  expect(css).toContain("min-height: 2.375rem");
  expect(css).toContain(".automation-workspace:has(.automation-trigger-peek) .automation-workspace__rail");
  expect(css).toContain(".automation-workspace:has(.automation-trigger-peek) .automation-workspace__main");
  const triggerActions = css.match(/\.automation-trigger-peek__actions\s*\{([\s\S]*?)\n\}/)?.[1] ?? "";
  expect(triggerActions).toContain("padding: var(--space-3) calc(var(--space-3) + var(--space-0-5))");
  expect(triggerActions).toContain("justify-content: flex-end");
  expect(css).toContain(".automation-result-peek__body");
  expect(css).toContain("padding: var(--space-4)");
  expect(css).toContain("translateX(calc(-1 * var(--motion-distance-sidebar-hide)))");
  expect(css).toContain("blur(var(--motion-blur-sidebar))");
  expect(css).toContain(".automation-workspace .sidebar-toggle { display: none; }");
  expect(css).toMatch(/@media \(max-width: 46\.25rem\)[\s\S]*?\.automation-workspace__back\s*\{[\s\S]*?top:\s*var\(--chrome-control-inset\);[\s\S]*?left:\s*var\(--chrome-sidebar-toggle-left\);[\s\S]*?gap:\s*var\(--space-1-5\);[\s\S]*?padding-left:\s*\.8125rem;/);
  expect(css).toMatch(/@media \(max-width: 46\.25rem\)[\s\S]*?\.automation-detail\s*\{[\s\S]*?padding-top:\s*3\.5rem;/);
  expect(css).toMatch(/@media \(max-width: 46\.25rem\)[\s\S]*?\.automation-detail__header\s*\{[\s\S]*?min-height:\s*7rem;[\s\S]*?flex-direction:\s*column;/);
  expect(css).toMatch(/@media \(max-width: 46\.25rem\)[\s\S]*?\.automation-detail__control-value\s*\{\s*max-width:\s*8\.5rem;\s*\}/);
  expect(workspace).not.toMatch(/className="automation-workspace__back"[\s\S]{0,120}?size="sm"/);
  expect(css).not.toContain("var(--sidebar-hide-distance)");
  expect(css).not.toContain("var(--sidebar-hide-blur)");
});

test("Automation surface transitions stay on composited properties", () => {
  const css = read("../src/features/automations/components/automations-workspace.css");
  const declarations = css.matchAll(/transition(?:-property)?\s*:\s*([^;}]*)/g);

  for (const [, value] of declarations) {
    expect(value).not.toMatch(/\b(?:width|max-width|min-width|height|max-height|min-height|grid|inset|top|right|bottom|left|margin|padding|gap)\b/);
  }
});

test("Automation detail never renders execution instructions as its description", () => {
  const detail = read("../src/features/automations/components/AutomationDetail.tsx");
  const summaryStart = detail.indexOf('className="automation-detail__summary"');
  const summaryEnd = detail.indexOf("</p>", summaryStart);
  const summary = detail.slice(summaryStart, summaryEnd);

  expect(summary).toContain("form.description?.trim()");
  expect(summary).not.toContain("form.prompt");
  expect(detail).toContain("value={form.prompt}");
  expect(detail).toContain('className="automation-detail__prompt"');
});

test("Automation description generation cannot cancel itself on idle-to-loading state", () => {
  const detail = read("../src/features/automations/components/AutomationDetail.tsx");

  expect(detail).toContain("descriptionRequestTaskRef");
  expect(detail).toContain("descriptionActiveTaskRef");
  expect(detail).not.toContain("let cancelled = false;");
  expect(detail).not.toMatch(
    /\}, \[descriptionState,\s*form\.description,\s*seed\.kind,\s*taskId\]\);/,
  );
});

test("Automations keeps its mockup-specific schedule instrument and directional detail swaps", () => {
  const workspace = read("../src/features/automations/components/AutomationsModal.tsx");
  const scheduleEditor = read("../src/features/automations/components/ScheduleEditor.tsx");
  const tabPanels = read("../src/components/ui/TabPanels.tsx");
  const motion = read("../src/lib/tokens/motion.ts");
  const css = read("../src/features/automations/components/automations-workspace.css");
  const shared = read("../src/styles.css");

  expect(workspace).toContain("setDetailAxis(\"y\")");
  expect(workspace).toContain("setDetailAxis(\"x\")");
  expect(workspace).toContain("axis={detailAxis}");
  expect(workspace).toContain("type RunPeek = { run: AutomationRun; runTitle: string }");
  // Title moved to the one-line sheet header; see the run-peek test below.
  expect(workspace).toContain("<strong title={visibleView.runTitle}>");
  expect(workspace).not.toContain("focusOnOpen");

  expect(tabPanels).toContain('axis?: "x" | "y"');
  expect(tabPanels).toContain('axis === "y"');
  expect(tabPanels).toContain("CONTENT_SWAP_VERTICAL_VARIANTS");
  expect(tabPanels).toContain("CONTENT_ENTER_TRANSITION");
  expect(tabPanels).toContain("CONTENT_EXIT_TRANSITION");
  expect(motion).toContain("export const CONTENT_SWAP_VERTICAL_VARIANTS");
  expect(motion).toContain("y: direction * DISTANCE.contentSwap");

  expect(scheduleEditor).toContain("const DAY_RULER = Object.freeze({ width: 320, inset: 8, axisY: 34");
  expect(scheduleEditor).toContain('"automation-trigger-editor__day-ruler"');
  expect(scheduleEditor).toContain("MOTION.reduced");
  expect(scheduleEditor).toContain("motion.g");
  expect(scheduleEditor).not.toContain("const TAPE_H = 64");
  expect(scheduleEditor).not.toContain("rounded-[6px]");
  expect(scheduleEditor).toContain('value={segment}');
  expect(scheduleEditor).toContain('className="automation-trigger-editor__panel"');

  const detail = read("../src/features/automations/components/AutomationDetail.tsx");
  expect(detail).toContain("<BlurSwap");
  expect(detail).toContain("<DynamicIconSwap");
  expect(detail).toContain("<TabPanels value={ledgerKey} direction={-1} axis=\"y\">");

  expect(css).toContain(".automation-trigger-editor__schedule-form");
  expect(css).toContain("height: var(--automation-schedule-tape-size)");
  expect(css).toContain(".automation-trigger-editor__day-ruler-axis");
  expect(css).toContain(".automation-trigger-editor__day-ruler-caption");
  expect(css).toContain("background: var(--panel)");
  expect(css).toContain("border-radius: var(--r-panel)");
  expect(shared).toMatch(/\.surface-peek\s*\{[^}]*box-shadow:\s*var\(--shadow-3\)/);
});

test("Automations owns the mock shell controls and hands a hidden rail's full width to the workspace", () => {
  const workspace = read("../src/features/automations/components/AutomationsModal.tsx");
  const css = read("../src/features/automations/components/automations-workspace.css");
  const toggle = read("../src/components/ui/SidebarToggle.tsx");

  expect(workspace).toContain("<SidebarToggle");
  expect(workspace).toContain('hide: "Hide automation list"');
  expect(workspace).toContain('show: "Show automation list"');
  // The rail is this screen's left column, so it carries the same ⌘B as every
  // other rail. It was the one surface that opted out of the chord.
  expect(workspace).not.toContain("shortcut: false");
  expect(workspace).toContain('event.key.toLowerCase() === "b"');
  // Automations is a route, so leaving it walks history back to wherever it
  // was opened from. It no longer evicts the user to Home, and the shell owns
  // the back control rather than each surface drawing its own.
  expect(workspace).not.toContain("ShellBackButton");
  expect(workspace).not.toContain('kind: "home"');
  expect(workspace).not.toContain("goToNewSessionHome");
  expect(workspace).not.toContain("<Takeover");
  expect(workspace).toContain("goBack();");
  expect(workspace).toContain("aria-hidden={railHidden}");
  // The rail "New" button is the unmarked secondary default (fill policy).
  expect(workspace).not.toContain('variant="primary"\n            size="md"\n            leadingIcon={Plus}');
  expect(workspace).toContain('size="md"\n            leadingIcon={Plus}');
  expect(workspace).not.toContain('size="sm"\n            leadingIcon={Plus}');
  expect(css).toMatch(/\.automation-workspace__rail-header > \.arden-button\s*\{[\s\S]*?padding-inline:\s*\.625rem;/);

  const detail = read("../src/features/automations/components/AutomationDetail.tsx");
  expect(detail).toMatch(/size="md"\s+className="automation-detail__pause"/);
  expect(detail).toMatch(/variant=\{dirty \? "secondary" : "primary"\}\s+size="md"/);

  expect(css).toMatch(/\.automation-workspace__rail\s*\{[\s\S]*?transition:[\s\S]*?transform var\(--motion-sidebar-hide\) var\(--motion-drawer-ease\),[\s\S]*?opacity var\(--motion-sidebar-hide\) var\(--ease-out\),[\s\S]*?filter var\(--motion-sidebar-hide\) var\(--ease-out\);/);
  const hiddenRail = css.match(/\.automation-workspace--rail-hidden \.automation-workspace__rail\s*\{([\s\S]*?)\}/)?.[1] ?? "";
  expect(hiddenRail).toContain("opacity: 0;");
  expect(hiddenRail).toContain("translateX(calc(-1 * var(--motion-distance-sidebar-hide)))");
  expect(hiddenRail).toContain("filter: blur(var(--motion-blur-sidebar));");
  expect(css).toMatch(/\.automation-workspace__main\s*\{[\s\S]*?padding-left: calc\(var\(--sidebar-width\) \+ var\(--sidebar-gap\)\);/);
  expect(css).toContain(".automation-workspace--rail-hidden .automation-workspace__main { padding-left: 0; }");
  expect(css).toContain(".automation-workspace .sidebar-toggle { display: none; }");
  expect(toggle).toContain("aria-expanded={!panelHidden}");
});

test("New automation menu keeps the template popover contract", () => {
  const menu = read("../src/features/automations/components/NewAutomationMenu.tsx");
  const item = read("../src/components/ui/MenuItem.tsx");
  expect(menu).toContain('className="w-[320px] max-w-[calc(100vw-2rem)] max-h-[calc(100vh-2rem)] overflow-auto !bg-[var(--surface-5)] p-[5px] !shadow-[var(--shadow-3)]"');
  expect(menu).toContain("<SectionLabel>Templates</SectionLabel>");
  expect(menu).toContain("description={t.blurb}");
  expect(menu).toContain("text-xs font-medium leading-[var(--leading-label)]");
  expect(menu).toContain("text-2xs leading-[var(--leading-metadata)] text-faint");
  expect(menu).toContain("Start from scratch");
  expect(menu).not.toContain("Start from scratch…");
  expect(menu).not.toContain("leading={<t.icon");
  expect(item).toContain("rich?: boolean");
  expect(item).toContain("{rich ? children");
});

test("the zero-automations pane only stands in for an actually empty workspace", () => {
  const workspace = read("../src/features/automations/components/AutomationsModal.tsx");

  // Deleting one of many cleared the selection, and the unselected pane read
  // "No automations yet." while the rest sat in the rail.
  expect(workspace).toContain("automations?.length === 0 ? (");
  expect(workspace).toMatch(/automations\?\.length === 0 \? \([\s\S]*?<h2>No automations yet\.<\/h2>/);
  expect(workspace).toContain("No automations yet.");

  // Deleting hands the pane straight to the neighbouring row — routing through
  // an unselected pane costs a blank swap in and another straight back out.
  expect(workspace).toContain("setSelectedId(successorTo(selectedId));");
  expect(workspace).toContain("return orderedIds[index + 1] ?? orderedIds[index - 1] ?? null;");

  // And the open-time fallback lands before paint, so that gap never shows either.
  expect(workspace).toMatch(
    /useLayoutEffect\(\(\) => \{\s*if \(draft \|\| !automations \|\| selected\) return;/,
  );
  expect(workspace).toContain("const first = groups.user[0] ?? groups.area[0] ?? groups.system[0];");
});


test("the run result peek reads as a sheet: one-line header, its own width, its own measure", () => {
  const workspace = read("../src/features/automations/components/AutomationsModal.tsx");
  const css = read("../src/features/automations/components/automations-workspace.css");

  // A "Run result" kicker stacked over the title made a two-line block that
  // could not centre against the close button — the same fault the notifier
  // and key peeks already had flattened out of them.
  expect(workspace).toContain("<strong title={visibleView.runTitle}>{visibleView.runTitle}</strong>");
  expect(workspace).not.toContain("<span>Run result</span>");
  expect(css).toMatch(/\.automation-result-peek header strong \{[^}]*white-space: nowrap;/);

  // Run output is arbitrary — wide tables, diffs, logs — so the pane drags
  // like the memory context pane and the width persists.
  expect(workspace).toContain('cssVar="--automation-result-peek-w"');
  expect(workspace).toContain('edge="start"');
  expect(workspace).toContain('storageKey={RESULT_PEEK_WIDTH_KEY}');
  expect(css).toContain("width: var(--automation-result-peek-w, var(--peek-width));");

  // It is reference material in a side panel, not prose at the reading width,
  // and a table wider than the pane scrolls inside its own box.
  // --typeset-size is the knob the whole scale hangs off; setting font-size on
  // the container left the prose at 15px because the typeset root restates it.
  expect(css).toMatch(/\.automation-result-peek__body \.md\.typeset \{[^}]*--typeset-size: var\(--text-sm\);/);
  expect(css).toMatch(/\.automation-result-peek__body \.md table \{[^}]*display: block;[^}]*overflow-x: auto;/);
});
