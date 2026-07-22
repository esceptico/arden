import { useEffect, useRef, useState } from "react";
import { Archive, Boxes, Brain, Cable, Database, KeyRound, Palette, Plug, Sparkles, Wrench, X, type NtrpIcon } from "@/components/icons";
import { useStore } from "@/stores";
import type { SettingsTabId } from "@/stores/types";
import { saveAndReconnect, fetchServerConfig } from "@/actions/server";
import { ConnectionTab } from "@/features/settings/components/ConnectionTab";
import { ProvidersTab } from "@/features/settings/components/ProvidersTab";
import { IntegrationsTab } from "@/features/settings/components/IntegrationsTab";
import { ModelsTab } from "@/features/settings/components/ModelsTab";
import { AgentTab } from "@/features/settings/components/AgentTab";
import { ContextTab } from "@/features/settings/components/ContextTab";
import { MCPTab } from "@/features/settings/components/mcp/MCPTab";
import { ToolsTab } from "@/features/settings/components/ToolsTab";
import { AppearanceTab } from "@/features/settings/components/AppearanceTab";
import { ArchiveTab } from "@/features/settings/components/ArchiveTab";
import { PageModal } from "@/components/ui/PageModal";
import { IconButton } from "@/components/ui/IconButton";
import { BlurSwap } from "@/components/ui/BlurSwap";
import { ICON } from "@/lib/icons";
import { ScrollFadeBottom, ScrollFadeTop } from "@/components/ui/ScrollBlur";
import { Tab as TabItem, Tabs } from "@/components/ui/Tabs";
import { TabPanels, useTabDirection } from "@/components/ui/TabPanels";

type TabId = SettingsTabId;

interface Tab {
  id: TabId;
  label: string;
  icon: NtrpIcon;
}

const TABS: Tab[] = [
  { id: "connection", label: "Connection", icon: Plug },
  { id: "providers", label: "Providers", icon: KeyRound },
  { id: "integrations", label: "Integrations", icon: Cable },
  { id: "models", label: "Models", icon: Sparkles },
  { id: "agent", label: "Agent", icon: Brain },
  { id: "context", label: "Context", icon: Database },
  { id: "tools", label: "Tools", icon: Wrench },
  { id: "mcp", label: "MCP servers", icon: Boxes },
  { id: "appearance", label: "Appearance", icon: Palette },
  { id: "archive", label: "Archived", icon: Archive },
];

const SETTINGS_TAB_IDS = TABS.map((t) => t.id);

export function SettingsModal() {
  const open = useStore((s) => s.settingsOpen);
  const requestedTab = useStore((s) => s.settingsTab);
  const closeSettings = useStore((s) => s.closeSettings);
  const origin = useStore((s) => s.modalOrigin);
  const saving = useStore((s) => s.connectionSaving);
  const draft = useStore((s) => s.connectionDraft);
  const error = useStore((s) => s.connectionError);
  const setConnectionDraft = useStore((s) => s.setConnectionDraft);
  const serverConfig = useStore((s) => s.serverConfig);
  const formRef = useRef<HTMLFormElement>(null);

  const [active, setActive] = useState<TabId>("connection");
  const direction = useTabDirection(SETTINGS_TAB_IDS, active);

  useEffect(() => {
    if (!open) return;
    // Load (or refresh) server config every time the modal opens — getting
    // fresh values is cheap and keeps the form honest.
    void fetchServerConfig();
  }, [open]);

  // Deep-link: each time the modal opens, land on the requested tab (e.g.
  // ⌘K → Archived) or fall back to Connection. Resetting on every open keeps
  // a one-off deep-link from stickily hijacking the next normal open, while
  // manual tab switches still persist while the modal stays mounted.
  useEffect(() => {
    if (open) setActive(requestedTab ?? "connection");
  }, [open, requestedTab]);

  function close() {
    if (!saving) closeSettings();
  }

  function submitConnection(event: React.FormEvent) {
    event.preventDefault();
    void saveAndReconnect(draft);
  }

  return (
    <PageModal
      open={open}
      onClose={close}
      origin={origin}
      size="w-[min(1000px,calc(100vw-32px))] h-[min(740px,calc(100vh-32px))] sm:w-[min(1000px,calc(100vw-64px))] sm:h-[min(740px,calc(100vh-64px))]"
      grid="grid-cols-[224px_minmax(0,1fr)] grid-rows-[auto_minmax(0,1fr)]"
      disableEscape={saving}
      ariaLabel="Settings"
    >
        {/* Left column is ONE continuous sidebar: "Settings" is its header, the
            nav lives directly beneath it — not a title floating above a detached
            card. Spans both grid rows so the sidebar is full-height. The active
            section's title heads the content column on the right. */}
        <aside className="settings-sidebar-card col-start-1 row-span-2 m-2 flex min-h-0 flex-col overflow-hidden px-2.5">
          <h2 className="select-none px-2 pt-2.5 pb-2.5 text-lg font-semibold tracking-[-0.012em] text-ink">
            Settings
          </h2>
          <Tabs
            value={active}
            onChange={(v) => setActive(v as TabId)}
            variant="plain"
            orientation="vertical"
            className="gap-px pb-2 overflow-y-auto scroll-thin"
          >
            <ScrollFadeBottom />
            {TABS.map((tab) => {
              const Icon = tab.icon;
              return (
                <TabItem
                  key={tab.id}
                  value={tab.id}
                  className="settings-nav-row w-full py-1 rounded-lg text-base font-medium text-left tracking-[-0.005em] text-ink-soft transition-colors hover:text-ink data-[active=true]:text-ink hover:bg-[color-mix(in_oklab,var(--color-ink)_3%,transparent)] data-[active=true]:bg-[color-mix(in_oklab,var(--color-ink)_4%,transparent)] data-[active=true]:shadow-[inset_0_0_0_1px_color-mix(in_oklab,var(--color-ink)_10%,transparent)]"
                >
                  <span className="grid h-4 w-4 shrink-0 place-items-center">
                    <Icon size={ICON.LG} strokeWidth={2} />
                  </span>
                  <span className="truncate">{tab.label}</span>
                </TabItem>
              );
            })}
          </Tabs>
        </aside>

        <header className="modal-header col-start-2 row-start-1 flex items-start justify-between gap-2">
          <div className="min-w-0 text-lg font-semibold tracking-[-0.012em] text-ink">
            <BlurSwap swapKey={active} className="justify-items-start">
              {TABS.find((t) => t.id === active)?.label}
            </BlurSwap>
          </div>
          <IconButton onClick={close} disabled={saving} aria-label="Close">
            <X size={ICON.SM} strokeWidth={2} />
          </IconButton>
        </header>

        <div className="col-start-2 row-start-2 relative min-h-0 min-w-0">
          <div className="absolute inset-0 overflow-y-auto overflow-x-hidden scroll-thin px-5 pt-1 pb-4">
            <ScrollFadeTop />
            <TabPanels value={active} direction={direction} className="pt-1">
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
              {active === "models" && <ModelsTab />}
              {active === "agent" && <AgentTab serverConfig={serverConfig} />}
              {active === "context" && <ContextTab serverConfig={serverConfig} />}
              {active === "tools" && <ToolsTab />}
              {active === "mcp" && <MCPTab />}
              {active === "appearance" && <AppearanceTab />}
              {active === "archive" && <ArchiveTab />}
            </TabPanels>
          </div>
        </div>
    </PageModal>
  );
}
