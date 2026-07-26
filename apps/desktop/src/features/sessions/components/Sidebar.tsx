import {
  Brain01,
  BubbleChat,
  House,
  Plus,
  Settings as SettingsIcon,
  SlidersHorizontal,
  X,
  ZapIcon,
} from "@/components/icons";
import { useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { useStore } from "@/stores";
import { EASE_OUT, MOTION, ROW_EXIT, SPRING_LAYOUT } from "@/lib/tokens/motion";
import { archiveArea, goToChat, goToNewSessionHome } from "@/actions/sessions";
import { acceptAreaSuggestion, dismissAreaSuggestion } from "@/actions/areas";
import { fetchAutomations } from "@/actions/automations";
import { humanizeSlug } from "@/lib/format";
import { ICON } from "@/lib/icons";
import { useVisibilityPoll } from "@/lib/hooks";
import { NavRow } from "@/features/sessions/components/NavRow";
import { SessionList } from "@/features/sessions/components/SessionList";
import { IconButton } from "@/components/ui/IconButton";
import { ContextMenu, type ContextMenuEntry, type ContextMenuPosition } from "@/components/ui/ContextMenu";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import {
  WorkspaceRail,
  WorkspaceRailFooter,
  WorkspaceRailNav,
} from "@/components/workspace/WorkspaceRail";

type SidebarContextMenuState =
  | (ContextMenuPosition & { kind: "nav"; onOpen: () => void })
  | (ContextMenuPosition & { kind: "area"; areaId: string });

export function Sidebar() {
  const openSettings = useStore((s) => s.openSettings);
  const openAutomations = useStore((s) => s.openAutomations);
  const openMemory = useStore((s) => s.openMemory);
  const currentSessionId = useStore((s) => s.currentSessionId);
  const openAreaKey = useStore((s) => s.areas.openAreaKey);
  const settingsOpen = useStore((s) => s.settingsOpen);
  const automationsOpen = useStore((s) => s.automationsOpen);
  const memoryOpen = useStore((s) => s.memoryOpen);
  const sessions = useStore((s) => s.sessions);
  const areasOverview = useStore((s) => s.areas.overview);
  const automationState = useStore((s) => s.automations);
  const areas = areasOverview?.areas ?? [];
  const suggestedAreas = areasOverview?.suggested ?? [];
  const automations = automationState ?? [];
  const [contextMenu, setContextMenu] = useState<SidebarContextMenuState | null>(null);
  useVisibilityPoll(fetchAutomations, 20_000);

  const contextEntries = useMemo<ContextMenuEntry[]>(() => {
    if (!contextMenu) return [];
    if (contextMenu.kind === "nav") {
      return [{ id: "open", label: "Open", onSelect: contextMenu.onOpen }];
    }
    return [
      {
        id: "open-area",
        label: "Open area",
        onSelect: () => useStore.getState().openArea(contextMenu.areaId),
      },
      {
        id: "set-aside",
        label: "Set aside",
        onSelect: () => void archiveArea(contextMenu.areaId),
      },
    ];
  }, [contextMenu]);

  const openNavMenu = (onOpen: () => void) => (position: ContextMenuPosition) => {
    setContextMenu({ kind: "nav", onOpen, ...position });
  };

  const openAreaMenu = (areaId: string, position: ContextMenuPosition) => {
    setContextMenu({ kind: "area", areaId, ...position });
  };

  const homeActive =
    currentSessionId === null &&
    openAreaKey === null &&
    !settingsOpen &&
    !automationsOpen &&
    !memoryOpen;
  // ONE app menu for every rail variant: same four rows, same order, same
  // name — the highlight moves, rows never appear or disappear.
  const appNav = (
    <WorkspaceRailNav label="App">
      <NavRow
        icon={<House size={ICON.MD} />}
        label="Home"
        active={homeActive}
        onClick={() => goToNewSessionHome()}
        onMenu={openNavMenu(() => goToNewSessionHome())}
      />
      <NavRow
        icon={<BubbleChat size={ICON.MD} />}
        label="Chat"
        active={currentSessionId !== null && openAreaKey === null}
        onClick={() => void goToChat()}
        onMenu={openNavMenu(() => void goToChat())}
      />
      <NavRow
        icon={<ZapIcon size={ICON.MD} />}
        label="Automations"
        trailing={automations.length || undefined}
        active={automationsOpen}
        onClick={() => openAutomations()}
        onMenu={openNavMenu(() => openAutomations())}
      />
      <NavRow
        icon={<Brain01 size={ICON.MD} />}
        label="Memory"
        active={memoryOpen}
        onClick={() => openMemory()}
        onMenu={openNavMenu(() => openMemory())}
      />
    </WorkspaceRailNav>
  );

  const settingsFooter = (
    <WorkspaceRailFooter label="Settings">
      <div>
        <NavRow
          icon={<SettingsIcon size={ICON.MD} />}
          label="Settings"
          active={settingsOpen}
          onClick={() => openSettings()}
        />
      </div>
      <ThemeToggle />
    </WorkspaceRailFooter>
  );

  if (currentSessionId === null || openAreaKey !== null) {
    return (
      <WorkspaceRail label={openAreaKey ? "Area workspace" : "Home"}>
        {appNav}
        <div className="workspace-rail__sessions">
          <div className="workspace-rail__section-header">
            <h2 className="workspace-rail__section-title">Areas</h2>
            <IconButton
              aria-label="Manage areas"
              title="Open an Area to manage it"
              disabled={areas.length === 0}
              onClick={() => areas[0] && useStore.getState().openArea(areas[0].key)}
            >
              <SlidersHorizontal size={ICON.SM} />
            </IconButton>
          </div>
          <nav className="home-area-list scroll-fade" aria-label="Areas">
            {areas.map((area) => (
              <button
                key={area.key}
                type="button"
                className="home-area-list__row"
                data-active={area.key === openAreaKey ? "true" : undefined}
                aria-current={area.key === openAreaKey ? "page" : undefined}
                onClick={() => useStore.getState().openArea(area.key)}
                onContextMenu={(event) => {
                  event.preventDefault();
                  openAreaMenu(area.key, {
                    x: event.clientX,
                    y: event.clientY,
                    trigger: event.currentTarget,
                    source: "pointer",
                  });
                }}
                onKeyDown={(event) => {
                  if (event.key !== "ContextMenu" && !(event.shiftKey && event.key === "F10")) return;
                  event.preventDefault();
                  const rect = event.currentTarget.getBoundingClientRect();
                  openAreaMenu(area.key, {
                    x: rect.left + 12,
                    y: rect.bottom - 4,
                    trigger: event.currentTarget,
                    source: "keyboard",
                  });
                }}
              >
                <span>{area.title}</span>
                <small data-tone={area.ask_count > 0 ? "warning" : area.live ? "live" : undefined}>
                  {area.ask_count > 0 ? "needs you" : area.live ? "running" : "quiet"}
                </small>
              </button>
            ))}
            <AnimatePresence mode="popLayout" initial={false}>
            {suggestedAreas.map((suggestion) => (
              <motion.div
                key={suggestion.key}
                layout
                layoutDependency={suggestedAreas.map((s) => s.key).join(":")}
                exit={{ ...ROW_EXIT, transition: { duration: MOTION.row, ease: EASE_OUT } }}
                transition={{ layout: SPRING_LAYOUT }}
                className="home-area-list__row home-area-list__row--suggested"
                title={suggestion.rationale}
              >
                <span>{humanizeSlug(suggestion.title)}</span>
                {/* The status word and the accept/dismiss cluster share one
                    grid cell: idle rows keep the exact status column of the
                    real rows above; hover swaps word → actions in place. */}
                <span className="home-area-list__suggestion-slot">
                  <small>suggested</small>
                  <span className="home-area-list__suggestion-cluster">
                    <IconButton
                      aria-label={`Create area from ${humanizeSlug(suggestion.title)}`}
                      title="Create this area"
                      size="xs"
                      onClick={() => void acceptAreaSuggestion(suggestion)}
                    >
                      <Plus size={ICON.XS} />
                    </IconButton>
                    <IconButton
                      aria-label={`Dismiss suggestion ${humanizeSlug(suggestion.title)}`}
                      title="Dismiss"
                      size="xs"
                      onClick={() => void dismissAreaSuggestion(suggestion.key)}
                    >
                      <X size={ICON.XS} />
                    </IconButton>
                  </span>
                </span>
              </motion.div>
            ))}
            </AnimatePresence>
          </nav>
        </div>
        {settingsFooter}
        <ContextMenu
          state={contextMenu}
          onClose={() => setContextMenu(null)}
          entries={contextEntries}
          ariaLabel={contextMenu?.kind === "area" ? "Area actions" : "App actions"}
        />
      </WorkspaceRail>
    );
  }

  const currentTitle =
    sessions.find((session) => session.session_id === currentSessionId)?.name || "Untitled";

  return (
    <WorkspaceRail label={currentTitle}>
      {appNav}
      <SessionList />
      {settingsFooter}
      <ContextMenu
        state={contextMenu}
        onClose={() => setContextMenu(null)}
        entries={contextEntries}
        ariaLabel={contextMenu?.kind === "area" ? "Area actions" : "App actions"}
      />
    </WorkspaceRail>
  );
}
