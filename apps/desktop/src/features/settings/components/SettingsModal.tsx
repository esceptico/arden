import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import {
  AiBrain01,
  AiMagic,
  Archive01,
  Bell,
  Database01,
  Key01,
  Layers,
  PaintBoard,
  Plug,
  Plug02,
  Wrench,
  type ArdenIcon,
} from "@/components/icons";
import { useStore } from "@/stores";
import type { SettingsTabId } from "@/stores/types";
import { saveAndReconnect, fetchServerConfig } from "@/actions/server";
import { ConnectionTab } from "@/features/settings/components/ConnectionTab";
import { ProvidersTab } from "@/features/settings/components/ProvidersTab";
import { IntegrationsTab } from "@/features/settings/components/IntegrationsTab";
import { NotifiersTab } from "@/features/settings/components/NotifiersTab";
import { ModelsTab } from "@/features/settings/components/ModelsTab";
import { AgentTab } from "@/features/settings/components/AgentTab";
import { ContextTab } from "@/features/settings/components/ContextTab";
import { MCPTab } from "@/features/settings/components/mcp/MCPTab";
import { ToolsTab } from "@/features/settings/components/ToolsTab";
import { AppearanceTab } from "@/features/settings/components/AppearanceTab";
import { ArchiveTab } from "@/features/settings/components/ArchiveTab";
import { SettingsPage } from "@/features/settings/components/SettingsPage";
import { Takeover } from "@/components/workspace/Takeover";
import { SidebarToggle } from "@/components/ui/SidebarToggle";
import { SidebarResizeHandle } from "@/components/workspace/SidebarResizeHandle";
import { ShellBackButton } from "@/components/workspace/ShellBackButton";
import { ICON } from "@/lib/icons";
import { ScrollArea } from "@/components/ui/ScrollArea";
import { Tab as TabItem, Tabs } from "@/components/ui/Tabs";
import { TabPanels, useTabDirection } from "@/components/ui/TabPanels";
import { ContextMenu, type ContextMenuPosition } from "@/components/ui/ContextMenu";
import {
  DURATION_RIGHT_PANEL_HIDE,
  DISTANCE_RAIL_HIDE,
  EASE_EMPHASIZED,
  EASE_OUT,
} from "@/lib/tokens/motion";
import "@/design/settings.css";

type TabId = SettingsTabId;
type SettingsGroupId = "general" | "intelligence" | "capabilities" | "data";

interface TabDefinition {
  id: TabId;
  label: string;
  icon: ArdenIcon;
  intro: string;
}

interface SettingsNavGroup {
  id: SettingsGroupId;
  label: string;
  tabs: TabDefinition[];
}

interface SettingsNavContextMenuState extends ContextMenuPosition {
  tab: TabId;
}

/** One descriptor powers rail order and page context. */
const SETTINGS_NAV_GROUPS: SettingsNavGroup[] = [
  {
    id: "general",
    label: "General",
    tabs: [
      {
        id: "connection",
        label: "Connection",
        icon: Plug,
        intro: "Server URL and API key. Stored locally; encrypted with safeStorage when available.",
      },
      {
        id: "appearance",
        label: "Appearance",
        icon: PaintBoard,
        intro: "Theme, accent, global capture, and the agent's pre-response indicator.",
      },
    ],
  },
  {
    id: "intelligence",
    label: "Intelligence",
    tabs: [
      {
        id: "models",
        label: "Models",
        icon: AiMagic,
        intro: "Each chat keeps its own model. The default below applies to newly created chats; research and memory models handle background work.",
      },
      {
        id: "agent",
        label: "Agent",
        icon: AiBrain01,
        intro: "Bound how far Arden can recursively delegate work to sub-agents.",
      },
      {
        id: "context",
        label: "Context",
        icon: Database01,
        intro: "Controls when the agent compresses its conversation history and how aggressively old turns are summarised away.",
      },
    ],
  },
  {
    id: "capabilities",
    label: "Capabilities",
    tabs: [
      {
        id: "providers",
        label: "Providers",
        icon: Key01,
        intro: "Model providers and the agent's search engine. Server connection and tool integrations stay separate.",
      },
      {
        id: "integrations",
        label: "Integrations",
        icon: Plug02,
        intro: "Connect the data and action providers Arden can use as tools. Model providers stay in Providers; MCP servers stay in MCP.",
      },
      {
        id: "notifiers",
        label: "Notifiers",
        icon: Bell,
        intro: "Delivery channels for agent and automation notifications.",
      },
      {
        id: "tools",
        label: "Tools",
        icon: Wrench,
        intro: "Override tool approval behavior. Denied tools are hidden from the agent and blocked at execution.",
      },
      {
        id: "mcp",
        label: "MCP servers",
        icon: Layers,
        intro: "Connect external tools and data sources via Model Context Protocol.",
      },
    ],
  },
  {
    id: "data",
    label: "Data",
    tabs: [
      {
        id: "archive",
        label: "Archived",
        icon: Archive01,
        intro: "Archived sessions leave active navigation but remain searchable and restorable. Permanent deletion stays deliberate.",
      },
    ],
  },
];

const TABS = SETTINGS_NAV_GROUPS.flatMap((group) =>
  group.tabs.map((tab) => ({ ...tab, group: group.id, groupLabel: group.label })),
);
const SETTINGS_TAB_IDS = TABS.map((tab) => tab.id);

// Which section you were last in is session state, not a Prefs field — same
// treatment as the memory inspector's open/closed. Settings reopened on
// Providers every single time, which is the sixth row in the rail and reads as
// arbitrary once your providers are set up.
const LAST_TAB_KEY = "arden.desktop.settings.tab";
/** Providers only for a first-ever open: nothing works until one is connected. */
const FIRST_RUN_TAB: TabId = "providers";

function loadLastTab(): TabId | null {
  try {
    const stored = localStorage.getItem(LAST_TAB_KEY);
    return SETTINGS_TAB_IDS.includes(stored as TabId) ? (stored as TabId) : null;
  } catch {
    return null;
  }
}

export function SettingsModal({
  railOpen = true,
  compact = false,
  onToggleRail,
  onNavigate,
}: {
  railOpen?: boolean;
  compact?: boolean;
  onToggleRail?: () => void;
  onNavigate?: () => void;
}) {
  const open = useStore((s) => s.settingsOpen);
  const requestedTab = useStore((s) => s.settingsTab);
  const closeSettings = useStore((s) => s.closeSettings);
  const saving = useStore((s) => s.connectionSaving);
  const draft = useStore((s) => s.connectionDraft);
  const error = useStore((s) => s.connectionError);
  const setConnectionDraft = useStore((s) => s.setConnectionDraft);
  const serverConfig = useStore((s) => s.serverConfig);
  const connected = useStore((s) => s.connected);
  const sidebarWidth = useStore((s) => s.prefs.sidebarWidth);
  const formRef = useRef<HTMLFormElement>(null);
  const contentRef = useRef<HTMLElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const [active, setActive] = useState<TabId>(() => loadLastTab() ?? FIRST_RUN_TAB);
  const [navContextMenu, setNavContextMenu] = useState<SettingsNavContextMenuState | null>(null);
  const direction = useTabDirection(SETTINGS_TAB_IDS, active);
  const activeTab = TABS.find((tab) => tab.id === active) ?? TABS[0];

  useEffect(() => {
    if (!open) return;
    // Fresh config keeps the connection form honest whenever Settings reopens.
    void fetchServerConfig();
  }, [open]);

  // The takeover's generic focus trap runs before this feature knows which
  // region owns the page. Explicitly land focus on the quiet content plane so
  // the sidebar toggle does not open its tooltip over the Settings heading.
  useEffect(() => {
    if (!open) return;
    const frame = requestAnimationFrame(() => contentRef.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, [open]);

  // A deep link still wins; otherwise reopen where the user left off.
  useEffect(() => {
    if (open) setActive(requestedTab ?? loadLastTab() ?? FIRST_RUN_TAB);
  }, [open, requestedTab]);

  useEffect(() => {
    if (!open) return;
    try {
      localStorage.setItem(LAST_TAB_KEY, active);
    } catch {
      /* localStorage unavailable — non-fatal */
    }
  }, [active, open]);

  useLayoutEffect(() => {
    if (!open || !scrollRef.current) return;
    scrollRef.current.scrollTop = 0;
  }, [active, open]);

  function close() {
    if (!saving) closeSettings();
  }

  function submitConnection(event: React.FormEvent) {
    event.preventDefault();
    void saveAndReconnect(draft);
  }


  const railHiddenX = compact
    ? -(sidebarWidth + DISTANCE_RAIL_HIDE)
    : -DISTANCE_RAIL_HIDE;

  const selectSection = useCallback((tab: TabId) => {
    setActive(tab);
    if (compact) onNavigate?.();
  }, [compact, onNavigate]);

  const openNavContextMenu = useCallback((
    tab: TabId,
    trigger: HTMLElement,
    source: ContextMenuPosition["source"],
    x: number,
    y: number,
  ) => setNavContextMenu({ tab, trigger, source, x, y }), []);

  return (
    <Takeover
      open={open}
      onClose={close}
      disableEscape={saving}
      ariaLabel="Settings"
      className={`board-settings ${railOpen ? "settings-rail-open" : "settings-rail-hidden"}`}
    >
      <SidebarToggle hidden={!railOpen} onToggle={onToggleRail} />
      <ShellBackButton onClick={close} disabled={saving} />
      <motion.aside
        data-page-enter-item="chrome"
        className="settings-sidebar-card flex min-h-0 flex-col overflow-hidden"
        initial={false}
        animate={
          railOpen
            ? { x: 0, opacity: 1, filter: "blur(0px)" }
            : { x: railHiddenX, opacity: 0, filter: "blur(6px)" }
        }
        transition={
          {
            x: { duration: DURATION_RIGHT_PANEL_HIDE, ease: EASE_EMPHASIZED },
            opacity: { duration: DURATION_RIGHT_PANEL_HIDE, ease: EASE_OUT },
            filter: { duration: DURATION_RIGHT_PANEL_HIDE, ease: EASE_OUT },
          }
        }
        style={{ pointerEvents: railOpen ? "auto" : "none" }}
        aria-hidden={!railOpen}
        inert={railOpen ? undefined : true}
      >
        <SidebarResizeHandle />
        <div className="settings-sidebar-title select-none">
          <h2>Settings</h2>
        </div>
        <Tabs
          value={active}
          onChange={(value) => selectSection(value as TabId)}
          variant="sidebar"
          orientation="vertical"
          label="Settings sections"
          indicatorClassName="settings-nav-highlight"
          className="settings-nav-scroll scroll-thin scroll-fade"
        >
          {SETTINGS_NAV_GROUPS.map((group) => (
            <div className="settings-nav-group" key={group.id} role="group" aria-label={group.label}>
              <div className="settings-nav-group-label">{group.label}</div>
              {group.tabs.map((tab) => {
                const Icon = tab.icon;
                return (
                  <div
                    key={tab.id}
                    className="contents"
                    onContextMenu={(event) => {
                      event.preventDefault();
                      const trigger = event.target instanceof Element
                        ? event.target.closest<HTMLElement>('[role="tab"]')
                        : null;
                      if (trigger) openNavContextMenu(tab.id, trigger, "pointer", event.clientX, event.clientY);
                    }}
                    onKeyDown={(event) => {
                      if (event.key !== "ContextMenu" && !(event.shiftKey && event.key === "F10")) return;
                      const trigger = event.target instanceof Element
                        ? event.target.closest<HTMLElement>('[role="tab"]')
                        : null;
                      if (!trigger) return;
                      event.preventDefault();
                      const rect = trigger.getBoundingClientRect();
                      openNavContextMenu(tab.id, trigger, "keyboard", rect.left + 12, rect.bottom - 4);
                    }}
                  >
                    <TabItem value={tab.id} className="settings-nav-row w-full">
                      <span className="grid h-4 w-4 shrink-0 place-items-center">
                        <Icon size={ICON.LG} />
                      </span>
                      <span className="truncate">{tab.label}</span>
                      {tab.id === "connection" && (
                        <span className="settings-nav-meta" data-tone={connected ? "ok" : "idle"}>
                          {connected ? "online" : "offline"}
                        </span>
                      )}
                    </TabItem>
                  </div>
                );
              })}
            </div>
          ))}
        </Tabs>
      </motion.aside>
      <ContextMenu
        state={navContextMenu}
        onClose={() => setNavContextMenu(null)}
        ariaLabel="Settings section actions"
        entries={navContextMenu ? [
          { id: "open-section", label: "Open section", onSelect: () => selectSection(navContextMenu.tab) },
        ] : []}
      />

      <main
        ref={contentRef}
        className="board-settings-content focus:outline-none"
        data-page-enter-item
        data-page-skeleton
        tabIndex={-1}
        autoFocus
      >
        <ScrollArea
          className="size-full"
          viewportRef={scrollRef}
          viewportClassName="settings-scroll scroll-fade"
        >
          <TabPanels value={active} direction={direction} axis="y" className="settings-page-frame">
            <SettingsPage
              section={activeTab.groupLabel.toLowerCase()}
              title={activeTab.label}
              intro={activeTab.intro}
              wide={activeTab.group === "capabilities" || activeTab.id === "archive"}
            >
              {active === "connection" && (
                <ConnectionTab
                  formRef={formRef}
                  draft={draft}
                  error={error}
                  saving={saving}
                  onUpdate={setConnectionDraft}
                  onSubmit={submitConnection}
                />
              )}
              {active === "providers" && <ProvidersTab />}
              {active === "integrations" && <IntegrationsTab />}
              {active === "notifiers" && <NotifiersTab />}
              {active === "models" && <ModelsTab />}
              {active === "agent" && <AgentTab serverConfig={serverConfig} />}
              {active === "context" && <ContextTab serverConfig={serverConfig} />}
              {active === "tools" && <ToolsTab />}
              {active === "mcp" && <MCPTab />}
              {active === "appearance" && <AppearanceTab />}
              {active === "archive" && <ArchiveTab />}
            </SettingsPage>
          </TabPanels>
        </ScrollArea>
      </main>
    </Takeover>
  );
}
