import { expect, test } from "bun:test";
import { existsSync, readFileSync } from "node:fs";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

const modal = read("../src/features/settings/components/SettingsModal.tsx");
const styles = read("../src/design/settings.css");
const appStyles = read("../src/styles.css");
const tokens = read("../src/design/tokens.css");
const setup = read("../src/features/settings/components/setup/SetupAssistant.tsx");
const googleServiceCard = read("../src/features/settings/components/GoogleServiceCard.tsx");
const providers = read("../src/features/settings/components/ProvidersTab.tsx");
const integrations = read("../src/features/settings/components/IntegrationsTab.tsx");
const integrationItem = read("../src/features/settings/components/IntegrationItem.tsx");
const serviceCard = read("../src/features/settings/components/ServiceCard.tsx");
const refreshAction = read("../src/features/settings/components/SettingsRefreshAction.tsx");
const toolPolicy = read("../src/features/settings/components/ToolPolicySelect.tsx");
const toolsTab = read("../src/features/settings/components/ToolsTab.tsx");
const mcpTab = read("../src/features/settings/components/mcp/MCPTab.tsx");
const serverForm = read("../src/features/settings/components/mcp/ServerForm.tsx");
const iconData = read("../src/components/iconData.ts");

test("settings takeover keeps the shared rail geometry and entrance targets", () => {
  expect(styles).toMatch(/\.board-settings\s*\{[\s\S]*?grid-template-columns:\s*calc\(var\(--sidebar-width\) \+ var\(--sidebar-gap\)\) minmax\(0, 1fr\);/);
  expect(styles).toMatch(/\.board-settings \.settings-sidebar-card\s*\{[\s\S]*?width:\s*var\(--sidebar-width\);[\s\S]*?background:\s*var\(--surface-2\);[\s\S]*?box-shadow:\s*var\(--shadow-2\);/);
  expect(styles).toMatch(/\.board-settings \.settings-nav-row > span\s*\{[\s\S]*?grid-template-columns:\s*16px minmax\(0, 1fr\) auto;/);
  expect(modal).toContain('data-page-enter-item="chrome"');
  expect(modal).toContain('className="board-settings-content focus:outline-none"');
  expect(modal).toContain("const contentRef = useRef<HTMLElement>(null)");
  expect(modal).toContain("requestAnimationFrame(() => contentRef.current?.focus())");
  expect(modal).toContain("ref={contentRef}");
  expect(modal).toContain('className="settings-page-frame"');
  expect(modal).toContain('direction={direction} axis="y" className="settings-page-frame"');
  expect(styles).toMatch(/\.settings-page-frame\s*\{\s*height:\s*100%;\s*\}/);
  expect(modal).toContain("autoFocus");
  expect(modal).toContain("<SidebarResizeHandle />");
  expect(modal).toContain("<SidebarToggle hidden={!railOpen} onToggle={onToggleRail} />");
  // Settings is the one surface that stays a Takeover: no internal
  // navigation, so it is a dialog over the stage rather than a route in the
  // history trail. An X, not a back arrow — closing it returns you to what
  // you were already doing, it does not step backwards through history.
  expect(modal).toContain('<ShellDismiss onClick={close} disabled={saving} label="Close settings" />');
  expect(modal).not.toContain("settings-mobile-nav");
  expect(styles).toMatch(/@media \(max-width: 46\.25rem\)[\s\S]*?\.board-settings \.settings-sidebar-card\s*\{[\s\S]*?position:\s*absolute;/);
  expect(styles).not.toMatch(/@media \(max-width: 46\.25rem\)[\s\S]*?\.settings-sidebar-card\s*\{\s*display:\s*none;/);
  expect(styles).toMatch(/\.board-settings\s*\{[\s\S]*?height:\s*100%;/);
  expect(styles).toMatch(/\.board-settings \.settings-sidebar-card\s*\{[\s\S]*?padding:\s*48px 10px 12px;/);
  expect(styles).toMatch(/\.settings-sidebar-title\s*\{[\s\S]*?height:\s*30px;[\s\S]*?padding:\s*0 var\(--space-2\);/);
  expect(styles).toMatch(/\.settings-sidebar-title h2\s*\{[\s\S]*?font-size:\s*var\(--text-xl\);[\s\S]*?line-height:\s*1;[\s\S]*?letter-spacing:\s*var\(--tracking-heading\);/);
  expect(styles).toMatch(/\.settings-nav-scroll\s*\{[\s\S]*?flex:\s*1;[\s\S]*?padding:\s*var\(--space-2\) 0;[\s\S]*?scrollbar-width:\s*none;/);
  expect(styles).toMatch(/\.settings-page-head\s*\{[\s\S]*?max-width:\s*38\.75rem;/);
  expect(styles).toMatch(/\.settings-page-intro\s*\{[\s\S]*?max-width:\s*36\.25rem;/);
  expect(styles).toMatch(/\.settings-page-body\s*\{[\s\S]*?margin-top:\s*30px;/);
  expect(styles).toMatch(/\.settings-page-body:has\(> \.settings-summary:first-child\)\s*\{\s*margin-top:\s*24px;/);
  expect(styles).toMatch(/\.settings-section\s*\{\s*display:\s*block;\s*\}/);
  expect(styles).toMatch(/\.settings-section-head\s*\{[\s\S]*?padding:\s*0 2px var\(--space-2\);/);
  expect(styles).toMatch(/\.settings-scope-note\s*\{[\s\S]*?margin-top:\s*var\(--space-2-5\);/);
  expect(styles).toMatch(/\.settings-section-actions\s*\{[\s\S]*?margin-top:\s*var\(--space-3\);/);
  expect(styles).toMatch(/\.settings-setting-title\s*\{[\s\S]*?font-size:\s*var\(--text-base\);/);
  expect(styles).toMatch(/\.settings-shortcut-record\s*\{[\s\S]*?height:\s*var\(--control-size-large\);[\s\S]*?padding:\s*0 \.8125rem;[\s\S]*?background:\s*var\(--surface-3\);/);
  expect(styles).toMatch(/\.settings-control-wide\s*\{[\s\S]*?min-width:\s*18\.75rem;/);
  expect(modal).toContain("data-page-skeleton");
  expect(modal).toContain("sidebarWidth + DISTANCE_RAIL_HIDE");
  expect(modal).toContain(": -DISTANCE_RAIL_HIDE;");
  expect(modal).toContain('filter: "blur(6px)"');
  expect(styles).not.toMatch(/\.board-settings \.settings-nav-row:hover\s*\{[^}]*background:/);
  expect(styles).not.toContain(".settings-accent-swatch:hover");
  expect(styles).toMatch(/@media \(max-width: 32\.5rem\)[\s\S]*?\.settings-setting-row\s*\{\s*padding:\s*var\(--space-3\);/);
  expect(styles).not.toMatch(/@media \(max-width: 32\.5rem\)[\s\S]*?\.settings-section-actions\s*\{/);
  // Settings reopens where you left off; Providers is the first-run landing
  // only, and a deep link still wins over both.
  expect(modal).toContain("useState<TabId>(() => loadLastTab() ?? FIRST_RUN_TAB)");
  expect(modal).toContain("setActive(requestedTab ?? loadLastTab() ?? FIRST_RUN_TAB)");
  expect(modal).toContain('const LAST_TAB_KEY = "arden.desktop.settings.tab"');
  expect(modal).toContain("localStorage.setItem(LAST_TAB_KEY, active)");
});

test("native Settings stays interactive above the draggable app shell", () => {
  expect(modal).toContain("<Takeover");
  expect(appStyles).toContain("#app > [data-overlay-layer] { -webkit-app-region: no-drag; }");
  expect(appStyles).toContain("#app > [data-overlay-layer] * { -webkit-app-region: no-drag; }");
});

test("settings navigation uses the exact mockup icon variants", () => {
  const canonical = [
    ["connection", "Plug", "Plug01Icon"],
    ["appearance", "PaintBoard", "PaintBoardIcon"],
    ["models", "AiMagic", "AiMagicIcon"],
    ["agent", "AiBrain01", "AiBrain01Icon"],
    ["context", "Database01", "Database01Icon"],
    ["providers", "Key01", "Key01Icon"],
    ["integrations", "Plug02", "Plug02Icon"],
    ["tools", "Wrench", "Wrench01Icon"],
    ["mcp", "Layers", "Layers01Icon"],
    ["archive", "Archive01", "Archive01Icon"],
  ] as const;

  for (const [id, component, source] of canonical) {
    expect(modal).toMatch(new RegExp(`id: "${id}",[\\s\\S]*?icon: ${component},`));
    expect(iconData).toContain(`@hugeicons/core-free-icons/${source}`);
  }
});

test("settings navigation exposes only its runtime-backed Open section context action", () => {
  expect(modal).toContain('import { ContextMenu, type ContextMenuPosition } from "@/components/ui/ContextMenu"');
  expect(modal).toContain("interface SettingsNavContextMenuState extends ContextMenuPosition");
  expect(modal).toContain("const selectSection = useCallback");
  expect(modal).toContain("const openNavContextMenu = useCallback");
  expect(modal).toContain('onContextMenu={(event) => {');
  expect(modal).toContain('openNavContextMenu(tab.id, trigger, "pointer", event.clientX, event.clientY)');
  expect(modal).toContain('event.key !== "ContextMenu" && !(event.shiftKey && event.key === "F10")');
  expect(modal).toContain('openNavContextMenu(tab.id, trigger, "keyboard", rect.left + 12, rect.bottom - 4)');
  expect(modal).toContain('ariaLabel="Settings section actions"');
  expect(modal).toContain('{ id: "open-section", label: "Open section", onSelect: () => selectSection(navContextMenu.tab) }');
  expect(modal).not.toContain("Copy deep link");
});

test("settings sidebar selection commits without an extra indicator animation", () => {
  const tabs = read("../src/components/ui/Tabs.tsx");
  const styles = read("../src/styles.css");
  expect(tabs).toContain('variant === "sidebar" || !ready || reducedMotion ? "none"');
  expect(styles).toContain('.arden-tab-indicator[data-tab-indicator="sidebar"]');
  expect(styles).toMatch(/\.arden-tab-indicator\[data-tab-indicator="sidebar"\]\s*\{[^}]*transition:\s*none;/s);
});

test("compact Settings keeps the mock's 520px top inset", () => {
  expect(styles).toMatch(/@media \(max-width: 32\.5rem\)\s*\{[\s\S]*?\.settings-page\s*\{\s*padding-top:\s*88px;/);
});

test("Settings summaries preserve the mock's 24px intro gap through provider grouping", () => {
  expect(styles).toMatch(/\.settings-page-body:has\(> \.settings-provider-flow:first-child > \.settings-summary:first-child\)\s*\{\s*margin-top:\s*24px;/);
});

test("Settings keeps the mock's 980px action lanes and compact summary", () => {
  const compact = styles.slice(
    styles.indexOf("@media (max-width: 61.25rem)"),
    styles.indexOf("@media (max-width: 46.25rem)"),
  );

  // The policy control carries words now, so the column follows the labels
  // instead of a three-icon-target width.
  expect(compact).toContain(".settings-tool-row {\n    grid-template-columns: minmax(150px, 1fr) auto;");
  expect(compact).toContain(".settings-tool-row .settings-data-row-end {\n    grid-column: 2;\n    grid-row: 1 / 3;");
  expect(compact).toContain(".settings-mcp-row {\n    grid-template-columns: minmax(150px, 1fr) 184px;");
  expect(compact).toContain(".settings-mcp-row .settings-data-row-end {\n    grid-column: 2;\n    grid-row: 1 / 3;");
  expect(compact).toContain(".settings-summary {\n    grid-template-columns: 1fr 110px;");
  expect(compact).toContain(".settings-summary-stat:last-child {\n    display: none;");
});

test("the tool policy names its three decisions instead of drawing them", () => {
  // A tick, a question mark and a slash in a capsule carried no visible label,
  // so the decision had to be decoded from shapes — the thing the app refuses
  // to do with status dots everywhere else.
  expect(toolPolicy).toContain('{ value: "approve", label: "Approve" }');
  expect(toolPolicy).toContain("{decision.label}");
  expect(toolPolicy).not.toContain("@/components/icons");
  expect(toolPolicy).not.toContain("aria-label={d.label}");
  expect(styles).toMatch(/\.settings-tool-policy\s*\{\s*width:\s*max-content;/);
  expect(styles).toMatch(/\.settings-tool-policy \[role="tab"\]\s*\{\s*color:\s*var\(--faint\);/);
  expect(styles).toMatch(/\.settings-tool-policy \[role="tab"\]:hover\s*\{\s*color:\s*var\(--muted\);/);
  expect(styles).toMatch(/\.settings-tool-policy \[role="tab"\]\[aria-selected="true"\]\s*\{\s*color:\s*var\(--ink\);/);
  expect(styles).not.toContain('.settings-tool-policy [role="tab"] svg');
});

test("tool counts stay grammatical for single-tool groups and searches", () => {
  expect(toolsTab).toContain("trailing={formatToolCount(filtered.length)}");
  expect(toolsTab).toContain("detail={formatToolCount(items.length)}");
  expect(toolsTab).toContain('return `${count} ${count === 1 ? "tool" : "tools"}`;');
  expect(toolsTab).toContain('className="settings-list-save-status"');
  expect(styles).toMatch(/\.settings-list-toolbar\s*\{\s*position:\s*relative;/);
  expect(styles).toMatch(/\.settings-list-save-status\s*\{[\s\S]*?position:\s*absolute;[\s\S]*?right:\s*2px;/);
});

test("Settings restores one-column tool and MCP rows at the mock's 520px pressure point", () => {
  const phone = styles.slice(styles.indexOf("@media (max-width: 32.5rem)"));

  expect(phone).toContain(".settings-tool-row .settings-data-row-copy,");
  expect(phone).toContain(".settings-mcp-row .settings-data-row-end {\n    grid-column: 1;\n    grid-row: auto;");
});

test("integration disclosures retain the mock's grid-row expansion lifecycle", () => {
  expect(styles).toMatch(/\.settings-integration-panel\s*\{[\s\S]*?grid-template-rows:\s*0fr;[\s\S]*?opacity:\s*0;[\s\S]*?grid-template-rows var\(--motion-content\) var\(--smooth-out\),/);
  expect(styles).toMatch(/\.settings-integration-item\.is-expanded \.settings-integration-panel\s*\{[\s\S]*?grid-template-rows:\s*1fr;[\s\S]*?opacity:\s*1;/);
  expect(styles).toMatch(/\.settings-integration-panel-clip\s*\{[\s\S]*?min-height:\s*0;[\s\S]*?overflow:\s*hidden;/);
  expect(integrationItem).toContain('className="settings-integration-panel-clip"');
  expect(integrationItem).not.toContain("canExpand && expanded &&");
  expect(serviceCard).toContain("<IntegrationItem");
  expect(serviceCard).not.toContain("AnimatePresence");
});

test("Google integrations stay one card per service with OAuth-detected accounts", () => {
  expect(integrations).toContain('const GOOGLE_INTEGRATIONS: GoogleIntegrationId[] = ["gmail", "calendar", "google_drive"]');
  expect(integrations).toContain("{GOOGLE_INTEGRATIONS.map((integrationId) => (");
  expect(integrations).toContain("connectGoogleServiceApi(config, integrationId)");
  expect(integrations).not.toContain("Google Workspace");
  expect(googleServiceCard).toContain("<IntegrationItem");
  expect(googleServiceCard).toContain('gmail: {');
  expect(googleServiceCard).toContain('label: "Google Calendar"');
  expect(googleServiceCard).toContain('label: "Google Drive"');
  expect(googleServiceCard).toContain('"Account detected at sign-in"');
  expect(googleServiceCard).toContain('actionLabel: "Disconnect"');
  expect(googleServiceCard).not.toContain("Use account");
  expect(existsSync(new URL("../src/features/settings/components/GoogleCard.tsx", import.meta.url))).toBe(false);
});

test("Settings refresh actions share the mock's collapse, spin, check, and hold choreography", () => {
  expect(providers).toContain("<SettingsRefreshAction");
  expect(integrations).toContain("<SettingsRefreshAction");
  expect(refreshAction).toContain('type RefreshPhase = "idle" | "refreshing" | "done"');
  expect(refreshAction).toContain("<DynamicIconSwap");
  expect(refreshAction).toContain("<BlurSwap");
  expect(refreshAction).toContain("MOTION.refreshDoneHold");
  expect(styles).toMatch(/\.settings-refresh-action\[data-phase="refreshing"\] \.settings-refresh-glyph\s*\{[\s\S]*?translateX\(var\(--refresh-spin-shift\)\);/);
  expect(styles).toMatch(/\.settings-refresh-action\[data-phase="refreshing"\] \.settings-refresh-label-slot\s*\{[\s\S]*?opacity:\s*0;[\s\S]*?blur\(var\(--refresh-label-blur\)\);/);
});

test("setup assistant keeps the mock's fixed sheet contract", () => {
  expect(setup).toContain('motionPreset="peek"');
  expect(setup).toContain("createPortal(");
  expect(setup).not.toContain("blocksInput");
  expect(setup).toContain('className="setup-sheet surface-sheet surface-radius-md"');
  expect(setup).toContain('className="sheet-head dp-sheet-header arden-peek-rule-below"');
  expect(setup).toContain('className="sheet-body dp-sheet-body scroll-fade"');
  expect(setup).toContain('className="sheet-actions dp-sheet-footer"');
  expect(setup).toContain('className="sheet-kicker"');
  expect(setup).toContain('className="sheet-step"');
  expect(setup).toContain('className="sheet-step-label"');
  expect(setup).toContain('className="step-num"');
  expect(setup).toContain('className="sheet-field"');
  expect(setup).toContain('className="arden-field dp-field"');
  expect(setup).toContain('className="scope-list"');
  // One ink slab per surface: the sheet commit is explicit primary, the
  // cancel rides the unmarked secondary default.
  expect(setup).toContain('variant="primary"\n          className="sheet-primary"');
  expect(setup).toContain('className="sheet-cancel"');
  expect(setup).not.toContain('variant="secondary"');
  expect(setup).not.toContain('title={`Close ${copy.title}`}');
  expect(setup).toContain('primaryLabel: "Continue to server form"');
  expect(setup).toContain('fieldPlaceholder: "URL, package name, or command"');
  expect(setup).toContain('"Channels + messages"');
  expect(setup).not.toContain("GoogleSetupAssistant");
  expect(setup).not.toContain('"google"');
  expect(serviceCard).toContain('label: "Run setup assistant"');
  expect(mcpTab).toContain("setSetupReference(value.trim())");
  expect(mcpTab).toContain("setupReference={setupReference}");
  expect(serverForm).toContain("Setup reference:");
  expect(existsSync(new URL("../src/features/settings/components/setup/GoogleSetupAssistant.tsx", import.meta.url))).toBe(false);
  expect(existsSync(new URL("../src/features/settings/components/setup/SlackSetupAssistant.tsx", import.meta.url))).toBe(false);
  expect(existsSync(new URL("../src/features/settings/components/setup/MCPSetupAssistant.tsx", import.meta.url))).toBe(false);
  expect(tokens).toContain("--setup-sheet-top: 6.375rem;");
  expect(tokens).toContain("--setup-sheet-bottom: 2.125rem;");
  expect(tokens).toContain("--setup-sheet-width: 26.25rem;");
  expect(styles).toMatch(/\.setup-sheet\s*\{[\s\S]*?position:\s*fixed;[\s\S]*?right:\s*var\(--floating-edge\);[\s\S]*?top:\s*var\(--setup-sheet-top\);[\s\S]*?bottom:\s*var\(--setup-sheet-bottom\);/);
  expect(styles).toMatch(/\.setup-sheet \.dp-sheet-footer\s*\{[\s\S]*?min-height:\s*3\.75rem;[\s\S]*?padding:\s*\.5rem \.75rem \.75rem;/);
  expect(styles).toMatch(/\.setup-sheet \.sheet-step\s*\{\s*margin-top:\s*24px;/);
  expect(styles).toMatch(/\.setup-sheet \.step-num\s*\{[\s\S]*?width:\s*20px;[\s\S]*?height:\s*20px;[\s\S]*?background:\s*var\(--panel-soft\);/);
  expect(styles).toMatch(/\.setup-sheet \.sheet-field label\s*\{[\s\S]*?margin-bottom:\s*7px;[\s\S]*?font-size:\s*var\(--text-label\);/);
  expect(styles).toMatch(/\.setup-sheet \.sheet-field \.dp-field\s*\{\s*height:\s*38px;/);
  expect(styles).toMatch(/\.setup-sheet \.scope-list\s*\{[\s\S]*?padding:\s*11px 12px;[\s\S]*?background:\s*var\(--surface-7\);[\s\S]*?line-height:\s*var\(--leading-reading\);/);
  expect(styles).toMatch(/@media \(max-width: 46\.25rem\)[\s\S]*?\.setup-sheet\s*\{[\s\S]*?left:\s*var\(--floating-edge\);[\s\S]*?width:\s*auto;/);
});
