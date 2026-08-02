import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

const tokens = read("../src/design/tokens.css");
const shell = read("../src/design/shell.css");
const chatStyles = read("../src/design/chat.css");
const styles = read("../src/styles.css");
const app = read("../src/app/App.tsx");
const shellOwnership = read("../src/lib/shellOwnership.ts");
const chat = read("../src/features/chat/components/Chat.tsx");
const chatRail = read("../src/features/chat/components/ChatRail.tsx");
const home = read("../src/features/home/components/WorkBrief.tsx");
const homeStyles = read("../src/features/home/components/Home.css");
const inspector = read("../src/features/background-agents/components/AgentRightSidebar.tsx");
const shellNav = read("../src/components/workspace/ShellNav.tsx");
const foundation = read("../src/design/foundation.css");
const sidebar = read("../src/features/sessions/components/Sidebar.tsx");
const sessionList = read("../src/features/sessions/components/SessionList.tsx");

const token = (name: string) => {
  const match = tokens.match(new RegExp(`--${name}:\\s*(\\d+);`));
  if (!match) throw new Error(`Missing numeric token: --${name}`);
  return Number(match[1]);
};

test("shell controls stay operable above both rails but below blocking overlays", () => {
  expect(token("z-shell-rail")).toBeLessThan(token("z-peek"));
  expect(token("z-peek")).toBeLessThan(token("z-shell-toggle"));
  expect(token("z-shell-toggle")).toBeLessThan(token("z-scrim"));

  expect(styles).toMatch(/\.sidebar-toggle,[\s\S]*?z-index:\s*var\(--z-shell-toggle\);/);
  expect(app).toContain("z-[var(--z-shell)]");
  expect(inspector).toContain("z-[var(--z-peek)]");
  expect(shellNav).toContain("ArrowLeft02");
  expect(shellNav).toContain("ArrowRight02");
  expect(app).not.toContain("z-30");
  expect(inspector).not.toContain("z-40");
  // Route history is shell chrome, present on every route. It hides only
  // under Settings, which is a Takeover and brings its own dismiss control.
  expect(app).toContain("{!settingsOpen && <ShellNav />}");
  expect(shellNav).toContain("goBack");
  expect(shellNav).toContain("goForward");
  // Home sits with back/forward, not only in the sidebar: rail-owning routes
  // hide that rail, leaving one-entry-at-a-time as the only way out.
  expect(shellNav).toContain("navigateHome");
  expect(shellNav).toContain('aria-label="Home"');
  expect(shellNav).toContain("disabled={atHome}");
  // Memory and Automations draw their own rail toggle for their own column.
  // The shell's lands on the same fixed coordinates, so rendering both leaves
  // two controls stacked where the user sees one — and clicks reach whichever
  // painted last. The shell yields the slot, and the ⌘B chord with it.
  expect(app).toContain("{!routeOwnsRail && (");
  expect(app).toContain("if (routeOwnsRail) return;");
  // The inspector shows a chat's agents, approvals and sources. Memory and
  // Automations have none, so its trigger must not sit on those pages —
  // as takeovers they covered it, as routes they would sit beside it.
  expect(app).toContain("{!routeOwnsRail && (openAreaKey || !showHome) && (");
  expect(shellOwnership).toContain("workspaceOwnsRail");
});

test("every collapsible rail shares one control and one icon vocabulary", () => {
  const toggle = read("../src/components/ui/SidebarToggle.tsx");
  const inspector = read("../src/features/background-agents/components/AgentRightSidebar.tsx");
  const memory = read("../src/features/memory/components/ArtifactMemoryView.tsx");
  const automations = read("../src/features/automations/components/AutomationsModal.tsx");
  const settings = read("../src/features/settings/components/SettingsModal.tsx");

  // The glyph points at the panel's edge and always swaps with state. The
  // inspector used to hand-roll a button that rendered PanelRightClose in
  // BOTH states — it said "close" while the panel was already closed.
  expect(toggle).toContain("PanelLeftClose");
  expect(toggle).toContain("PanelLeftOpen");
  expect(toggle).toContain("PanelRightClose");
  expect(toggle).toContain("PanelRightOpen");
  expect(toggle).toContain("<IconSwap");

  // No surface draws its own. One primitive, or it drifts.
  for (const surface of [inspector, memory, automations, settings]) {
    expect(surface).toContain("<SidebarToggle");
    expect(surface).not.toContain("PanelLeftClose");
    expect(surface).not.toContain("PanelRightClose");
  }
  // One name everywhere: the first nav row is "Home" in every rail variant,
  // and both variants render the single shared appNav block.
  expect(sidebar).toContain('label="Home"');
  expect(sidebar).not.toContain("Mission Control");
  expect(sidebar.split("{appNav}").length).toBe(3);
  expect(sidebar).toContain('label="Chat"');
  expect(sidebar).toContain('data-active={area.key === openAreaKey ? "true" : undefined}');
});

test("all primary shell content drops rail insets at the shared narrow pressure point", () => {
  expect(shell).toMatch(/\.board-shell-content\s*\{[\s\S]*?left:\s*calc\(var\(--sidebar-width\) \+ var\(--sidebar-gap\)\);[\s\S]*?right:\s*0;/);
  expect(shell).toMatch(/@media \(max-width: 55rem\)\s*\{[\s\S]*?\.board-shell-content\[data-workspace\]\[data-right-open="true"\]\s*\{[\s\S]*?left:\s*0;[\s\S]*?right:\s*0;/);
  expect(app).toContain('className="board-shell-content absolute inset-0 overflow-hidden bg-bg"');
  expect(app).toContain("<WorkspaceStage");
  expect(app).toContain("<WorkspaceRouteHost");
  expect(chat).not.toContain("data-right-open");
  expect(chat).toContain('className="board-chat"');
  expect(chatStyles).toMatch(/\.board-chat\s*\{[^}]*position:\s*absolute;[^}]*inset:\s*0;/);
});

test("compact shell auto-hides the rail without overwriting the persisted preference", () => {
  expect(shellOwnership).toContain('COMPACT_SHELL_QUERY = "(max-width: 46.25rem)"');
  expect(app).toContain("resolveEffectiveSidebarHidden(");
  expect(app).toContain("hidden={settingsOpen ? !settingsRailOpen : effectiveSidebarHidden}");
  expect(app).toContain("if (settingsOpen) setSettingsRailOpen((visible) => !visible)");
  expect(app).toContain('data-sidebar-hidden={effectiveSidebarHidden ? "true" : "false"}');
  expect(app).toContain("compact={compactShell}");
  expect(inspector).toContain("const effectiveCollapsed = compact ? !compactPanelOpen : collapsed");
  expect(inspector).toContain("if (compact) setCompactPanelOpen(false)");
});

test("compact Home and Chat follow the reference hierarchy", () => {
  expect(tokens).toContain("--home-compact-top-inset: 5.75rem");
  expect(homeStyles).toMatch(/@media \(max-width: 46\.25rem\)\s*\{[\s\S]*?padding-top:\s*var\(--home-compact-top-inset\);/);
  expect(homeStyles).toMatch(/\.mission-control__capture\s*\{[\s\S]*?margin-top:\s*var\(--space-5\);/);
  expect(homeStyles).not.toMatch(/@media \(max-width: 46\.25rem\)\s*\{[\s\S]*?\.mission-control__capture\s*\{/);
  expect(home).toContain('data-region="deck" aria-label="Needs you"');
  expect(home).not.toContain('id="mission-control-needs-you"');
  expect(home.indexOf('className="mission-control__deck-chrome"')).toBeGreaterThan(
    home.indexOf("<FocusRow"),
  );
  expect(chatStyles).toMatch(/@container \(max-width: 55rem\)\s*\{\s*\.board-chat-rail\s*\{\s*display:\s*none;/);
  expect(chatRail).toContain("@[55rem]:flex");
  expect(chatStyles).toMatch(/\.board-composer-stack\s*\{[\s\S]*?padding:\s*0 var\(--content-gutter\) var\(--sidebar-edge\);/);
});

test("Chat keeps the mock's 620px reading-lane geometry", () => {
  expect(chatStyles).toMatch(/@media \(max-width: 38\.75rem\)\s*\{[\s\S]*?\.board-chat__scroll\s*\{\s*padding-top:\s*70px;\s*\}[\s\S]*?\.chat-head-inner\s*\{\s*padding-left:\s*calc\(var\(--chrome-chat-title-inset\) - \.125rem\);[\s\S]*?\.board-user__images\s*\{\s*max-inline-size:\s*90%;/);
  expect(chat).toContain("board-chat__header-content chat-head-inner flex min-w-0 flex-1 items-center gap-1.5");
});

test("hidden-sidebar Chat reserves the native chrome lane for its title", () => {
  expect(chatStyles).toMatch(
    /\.board-shell-content\[data-workspace="chat"\]\[data-sidebar-hidden="true"\] \.chat-head-inner\s*\{\s*padding-left:\s*var\(--chrome-chat-title-inset\);\s*\}/,
  );
  expect(tokens).toContain(
    "--chrome-sidebar-toggle-resolved-left: var(--chrome-sidebar-toggle-fallback-left);",
  );
  expect(tokens).toContain(
    "--chrome-back-control-resolved-left: calc(var(--chrome-sidebar-toggle-resolved-left) + var(--shell-control-size) + var(--chrome-control-gap));",
  );
  expect(tokens).toContain(
    "--chrome-chat-title-inset: calc(var(--chrome-back-control-resolved-left) + var(--space-2-5));",
  );
  expect(styles).toMatch(
    /html\[data-desktop="true"\]:not\(\[data-fullscreen="true"\]\)\s*\{\s*--chrome-sidebar-toggle-resolved-left:\s*var\(--chrome-sidebar-toggle-left\);/,
  );
  expect(styles).toContain(".sidebar-toggle { left: var(--chrome-sidebar-toggle-resolved-left); }");
  expect(styles).toContain(".shell-nav { left: var(--chrome-back-control-resolved-left); }");
  // Shell chrome is made of controls, so its radius follows the corner
  // profile via --r-icon. A hardcoded --r-circle derives from nothing
  // switchable: under Soft square every other control squared off while
  // back/forward/Home and the rail toggles stayed round.
  expect(foundation).not.toMatch(/\.shell-control\s*\{[^}]*border-radius/);
});

test("shared rail geometry separates navigation, groups, and repeated Chat rows", () => {
  expect(shell).toMatch(/\.workspace-rail\s*\{[\s\S]*?--workspace-rail-nav-row-height:\s*1\.875rem;/);
  expect(shell).toMatch(/\.workspace-rail\s*\{[\s\S]*?--workspace-rail-group-row-height:\s*1\.625rem;/);
  expect(shell).toMatch(/\.workspace-rail\s*\{[\s\S]*?--workspace-rail-session-row-height:\s*1\.75rem;/);
  expect(shell).not.toContain("--workspace-chat-row-height");
  expect(shell).toMatch(/\.workspace-rail \.workspace-rail__nav-row\s*\{[\s\S]*?height:\s*var\(--workspace-rail-nav-row-height\);/);
  expect(shell).toMatch(/\.home-area-list__row\s*\{[\s\S]*?min-height:\s*var\(--workspace-rail-nav-row-height\);[\s\S]*?padding-inline:\s*var\(--interactive-row-inline-padding\);/);
  expect(shell).toMatch(/\.home-area-list__row small\s*\{[\s\S]*?font:\s*var\(--weight-medium\) var\(--workspace-rail-meta-size\)\/var\(--workspace-rail-meta-leading\) var\(--sans\);/);
  expect(shell).toMatch(/\.workspace-rail \.workspace-rail__group-heading\s*\{[\s\S]*?height:\s*var\(--workspace-rail-group-row-height\);/);
  expect(shell).toMatch(/\.workspace-rail \.workspace-rail__group-heading\s*\{[\s\S]*?grid-template-columns:\s*var\(--workspace-rail-icon-lane\) minmax\(0, 1fr\) \.75rem;/);
  expect(shell).toMatch(/\.workspace-rail \.workspace-rail__session\s*\{[\s\S]*?height:\s*var\(--workspace-rail-session-row-height\);/);
  expect(shell).toMatch(/\.workspace-rail \.workspace-rail__show-more\s*\{[\s\S]*?height:\s*var\(--workspace-rail-session-row-height\);/);

  // The hovered title dissolves at ITS OWN trailing edge. The title and the
  // time + pin + archive cluster are flex siblings, so revealing the cluster
  // already shrinks the title — measuring the fade from the cluster's width
  // subtracted it twice and masked the title away almost entirely.
  const appStyles = read("../src/styles.css");
  expect(appStyles).toContain(
    "mask-image: linear-gradient(to right, #000 calc(100% - var(--session-title-fade)), transparent);",
  );
  expect(appStyles).not.toContain("#000 calc(100% - 6.25rem)");
  expect(sessionList).not.toContain("workspace-rail__group-actions");
  expect(sessionList).not.toContain("RowAction");
});

test("chat peek, chat dock, and area hub have distinct geometry and state", () => {
  expect(app).toContain('mode={openAreaKey ? "hub" : rightPanelDocked ? "docked" : "peek"}');
  expect(app).toContain('workspaceKind === "area"');
  expect(app).toContain('workspaceKind === "chat" && !rightPanelCollapsed && rightPanelDocked');
  expect(inspector).toContain('mode: "peek" | "docked" | "hub"');
  expect(inspector).toContain("dataMode={mode}");
  expect(inspector).toContain('mode === "hub"');
  expect(inspector).toContain('mode === "hub" || mode === "peek"');
  expect(inspector).toContain('"var(--peek-width)"');
  expect(shell).not.toContain('data-workspace="area"][data-right-open="true"]');
  expect(chatStyles).toMatch(/\.board-inspector\[data-mode="hub"\]\s*\{[^}]*border-radius:\s*var\(--r-shell\);/s);
  expect(chatStyles).not.toContain("backdrop-filter: blur(var(--surface-blur)) saturate(1.08)");
  expect(chatStyles).toMatch(/body:has\(\.board-inspector\[data-mode="hub"\]\) :is\(\.sidebar-toggle, \.shell-nav, \.right-sidebar-toggle\)\s*\{[^}]*z-index:\s*var\(--z-shell\);/s);
  expect(chatStyles).toMatch(/\.board-inspector\[data-mode="peek"\]\s*\{[^}]*top:\s*3\.875rem;[^}]*right:\s*var\(--floating-edge, 1rem\);[^}]*bottom:\s*auto;/s);
  expect(chatStyles).toMatch(/@media \(max-width: 55rem\)[\s\S]*?\.board-inspector\[data-mode="peek"\]\s*\{[^}]*top:\s*3\.25rem;[^}]*bottom:\s*auto;[^}]*max-height:\s*calc\(100vh - 3\.875rem\);/s);
  expect(inspector).toContain('className="board-inspector__action board-inspector__dock-action"');
  expect(chatStyles).toMatch(/@media \(max-width: 55rem\)[\s\S]*?\.board-inspector__dock-action\s*\{\s*display:\s*none;/);
  expect(inspector).toContain('label="Area detail sections"');
  expect(inspector).toContain('id="area-hub-activity-tab"');
  expect(inspector).toContain('id="area-hub-sources-tab"');
  expect(chatStyles).toContain(".board-inspector__hub-provenance");
});
