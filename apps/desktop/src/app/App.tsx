import { Suspense, lazy, useCallback, useEffect, useState } from "react";
import { AnimatePresence, MotionConfig, motion } from "motion/react";
import {
  DURATION_RIGHT_PANEL_HIDE,
  DISTANCE_RAIL_HIDE,
  EASE_EMPHASIZED,
  EASE_OUT,
} from "@/lib/tokens/motion";
import { IS_DESKTOP_MAC } from "@/lib/platform";
import { Sidebar } from "@/features/sessions/components/Sidebar";
import { Chat } from "@/features/chat/components/Chat";
import { Home } from "@/features/home/components/Home";
import { AreaRoom } from "@/features/areas/components/AreaRoom";
import { CommandPalette } from "@/features/command-palette/components/CommandPalette";
import { MarkdownViewer } from "@/components/ui/MarkdownViewer";
import { ApprovalReviewModal } from "@/features/chat/components/ApprovalReviewModal";
import { SidebarResizeHandle } from "@/components/workspace/SidebarResizeHandle";
import { SidebarToggle } from "@/components/ui/SidebarToggle";
import { ShellBackButton } from "@/components/workspace/ShellBackButton";
import { WorkspaceRouteHost } from "@/components/workspace/WorkspaceRouteHost";
import { WorkspaceStage } from "@/components/workspace/WorkspaceStage";
import { AgentRightSidebar } from "@/features/background-agents/components/AgentRightSidebar";
import { SourcesPanel } from "@/features/sources/components/SourcesPanel";
import { ErrorBoundary } from "@/app/ErrorBoundary";
import { Toaster } from "@/components/ui/Toaster";
import { ModalScrim } from "@/components/ui/ModalScrim";
import { Inspect } from "@/features/inspect/components/Inspect";
import { useStore } from "@/stores";
import { useEvents } from "@/hooks/useEvents";
import { useActiveRuns } from "@/features/background-agents/hooks/useActiveRuns";
import { useAutomationEvents } from "@/features/automations/hooks/useAutomationEvents";
import { useTaskResultToasts } from "@/hooks/useTaskResultToasts";
import { useThemeEffect } from "@/lib/theme";
import { useCornerProfileEffect } from "@/lib/cornerProfile";
import { useTypographyEffect } from "@/lib/typography";
import {
  COMPACT_SHELL_QUERY,
  resolveEffectiveSidebarHidden,
  type ShellLayout,
} from "@/lib/shellOwnership";
import { bootstrap, startServerConnectionPolling } from "@/actions/bootstrap";
import { createSession, goToNewSessionHome, switchSession } from "@/actions/sessions";
import { goHome } from "@/actions/navigation";
import { sendMessage } from "@/actions/messages";

// The five "open from chrome" modals only mount when the user actually
// opens them. Lazy boundaries here keep ~300 KB of MCP/Providers/Memory/
// Automations code out of the initial bundle. Suspense fallback is null
// — modals are conditional anyway, no spinner needed for the brief
// chunk-fetch in an Electron renderer.
const SettingsModal = lazy(() =>
  import("@/features/settings/components/SettingsModal").then((m) => ({ default: m.SettingsModal })),
);
const AutomationsModal = lazy(() =>
  import("@/features/automations/components/AutomationsModal").then((m) => ({ default: m.AutomationsModal })),
);
const MemorySurface = lazy(() =>
  import("@/features/memory/components/MemorySurface").then((m) => ({ default: m.MemorySurface })),
);
const ToolViewer = lazy(() =>
  import("@/features/chat/components/ToolViewer").then((m) => ({ default: m.ToolViewer })),
);

function useMediaQuery(queryText: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window.matchMedia === "function"
      ? window.matchMedia(queryText).matches
      : false,
  );
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia(queryText);
    const sync = () => setMatches(query.matches);
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, [queryText]);
  return matches;
}

function useHash(): string {
  const [hash, setHash] = useState(() => window.location.hash);
  useEffect(() => {
    const handler = () => setHash(window.location.hash);
    window.addEventListener("hashchange", handler);
    return () => window.removeEventListener("hashchange", handler);
  }, []);
  return hash;
}

function useFullscreenClass(): void {
  useEffect(() => {
    const root = document.documentElement;
    // Static flag: native macOS shell draws the traffic lights, the browser
    // does not. CSS keys the toggle's left inset off this (+ fullscreen).
    root.dataset.desktop = IS_DESKTOP_MAC ? "true" : "false";
    const setFullscreen = (isFullScreen: boolean) => {
      root.dataset.fullscreen = isFullScreen ? "true" : "false";
    };
    void window.ardenDesktop?.window?.isFullScreen?.().then(setFullscreen);
    const unsubscribe = window.ardenDesktop?.window?.onFullScreenChange?.(setFullscreen);
    return () => {
      unsubscribe?.();
      delete root.dataset.fullscreen;
      delete root.dataset.desktop;
    };
  }, []);
}

export function App() {
  const hash = useHash();
  const currentSessionId = useStore((s) => s.currentSessionId);
  const sidebarHidden = useStore((s) => s.prefs.sidebarHidden);
  const rightPanelCollapsed = useStore((s) => s.prefs.rightPanelCollapsed);
  const rightPanelDocked = useStore((s) => s.prefs.rightPanelDocked);
  const sidebarWidth = useStore((s) => s.prefs.sidebarWidth);
  const rightPanelWidth = useStore((s) => s.prefs.rightPanelWidth);
  const toggleSidebar = useStore((s) => s.toggleSidebar);
  const setShellLayout = useStore((s) => s.setShellLayout);
  const openSettings = useStore((s) => s.openSettings);
  // Home = no session selected, full stop. Since boot stopped auto-selecting
  // the latest session, an EMPTY-but-selected session is still a chat (its
  // own empty state), not Home — the old transcript-emptiness derivation
  // made a freshly opened session flash Home while history loaded.
  const showHome = useStore((s) => s.currentSessionId === null);
  const openAreaKey = useStore((s) => s.areas.openAreaKey);
  const openAreaDetail = useStore((s) =>
    openAreaKey ? s.areas.detailByKey[openAreaKey] ?? null : null,
  );
  const memoryOpen = useStore((s) => s.memoryOpen);
  const settingsOpen = useStore((s) => s.settingsOpen);
  const automationsOpen = useStore((s) => s.automationsOpen);
  const workspaceKind: ShellLayout["workspace"] = openAreaKey
    ? "area"
    : showHome
      ? "home"
      : "chat";
  const workspaceRouteKey = openAreaKey ? `area:${openAreaKey}` : workspaceKind;
  const compactShell = useMediaQuery(COMPACT_SHELL_QUERY);
  const [compactSidebarOpen, setCompactSidebarOpen] = useState(false);
  const [settingsRailOpen, setSettingsRailOpen] = useState(() => !compactShell);
  // Area detail is a floating peek in the mockup. Only a docked chat
  // inspector may claim shell width and move the reading lane.
  const workspaceRightDocked =
    workspaceKind === "chat" && !rightPanelCollapsed && rightPanelDocked;
  const shellLayout: ShellLayout = {
    compact: compactShell,
    workspace: workspaceKind,
  };
  // This is deliberately derived rather than persisted: closing/undocking
  // the inspector or leaving the breakpoint restores the user's rail choice.
  const effectiveSidebarHidden = resolveEffectiveSidebarHidden(
    shellLayout,
    { sidebarHidden },
    compactSidebarOpen,
  );
  const toggleEffectiveSidebar = useCallback(() => {
    if (settingsOpen) setSettingsRailOpen((visible) => !visible);
    else if (compactShell) setCompactSidebarOpen((visible) => !visible);
    else toggleSidebar();
  }, [compactShell, settingsOpen, toggleSidebar]);

  useEffect(() => {
    setShellLayout({
      compact: compactShell,
      workspace: workspaceKind,
    });
  }, [compactShell, setShellLayout, workspaceKind]);
  // Session identity dismisses the compact rail, but does not own the
  // workspace route: switching chats must not replay the full page entrance.
  useEffect(() => {
    if (compactShell) setCompactSidebarOpen(false);
  }, [compactShell, currentSessionId, openAreaKey]);
  useEffect(() => {
    if (settingsOpen) setSettingsRailOpen(!compactShell);
  }, [compactShell, settingsOpen]);

  // Publish dock widths as CSS vars so the chat shell can stay flush with
  // both sidebars as they resize. Drag handles update these imperatively
  // during pointer movement, then prefs re-sync them after release.
  useEffect(() => {
    document.documentElement.style.setProperty("--sidebar-width", `${sidebarWidth}px`);
  }, [sidebarWidth]);
  useEffect(() => {
    document.documentElement.style.setProperty("--right-panel-width", `${rightPanelWidth}px`);
  }, [rightPanelWidth]);

  useThemeEffect();
  useCornerProfileEffect();
  useTypographyEffect();
  useFullscreenClass();

  useEffect(() => {
    if (hash === "#trace-demo") return;
    const stopPolling = startServerConnectionPolling();
    void bootstrap();
    return stopPolling;
  }, [hash]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      if (mod && !e.altKey) {
        const k = e.key.toLowerCase();
        if (k === "b" && !e.shiftKey) {
          // Memory owns this advertised shortcut while its full-window rail is
          // visible; toggling the covered app shell would leave state hidden.
          if (memoryOpen) return;
          e.preventDefault();
          toggleEffectiveSidebar();
          return;
        }
        if (k === "n" && !e.shiftKey) {
          e.preventDefault();
          goToNewSessionHome();
          return;
        }
        if (k === "h" && e.shiftKey) {
          e.preventDefault();
          goHome();
          return;
        }
        if (e.key === ",") {
          e.preventDefault();
          openSettings();
          return;
        }
        return;
      }

      // Type-anywhere → focus composer. Apple Mail pattern: if the user
      // starts typing a printable character with nothing input-like
      // focused, jump focus into the composer and seed it with that
      // character so the keystroke isn't lost.
      if (e.altKey || e.metaKey || e.ctrlKey) return;
      if (e.key.length !== 1) return;
      const target = document.activeElement as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable)
      ) {
        return;
      }
      const composer = document.getElementById("message-input") as HTMLTextAreaElement | null;
      if (!composer) return;
      e.preventDefault();
      const state = useStore.getState();
      state.setDraft(state.draft + e.key);
      composer.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [memoryOpen, openSettings, toggleEffectiveSidebar]);

  useEvents(hash === "#trace-demo" ? null : currentSessionId);
  useActiveRuns();
  useAutomationEvents();
  useTaskResultToasts();

  // Receive submissions from the quick-capture floating window. The
  // Electron main process forwards each one via `quick:message`; we
  // route into the chosen chat (or a fresh area-less chat — Inbox,
  // NOT the current session's area) and send. Capture is silent —
  // this window is NOT brought forward — so the session (and its
  // streamed response) is simply waiting the next time the user
  // switches to arden.
  useEffect(() => {
    const unsubscribe = window.ardenDesktop?.quickCapture?.onMessage?.(async (payload) => {
      try {
        if (payload.sessionId) {
          await switchSession(payload.sessionId);
        } else {
          await createSession(null);
        }
        await sendMessage(payload.message, payload.images ?? []);
      } catch {
        /* surfaced via the store's error toast */
      }
    });
    return unsubscribe;
  }, []);

  // Push the user's chosen global shortcut to the main process every
  // time it changes. Main registers a default chord at startup but
  // doesn't know which key the user picked until the renderer pushes
  // it — prefs live in localStorage which the main process can't read.
  const quickCaptureShortcut = useStore((s) => s.prefs.quickCaptureShortcut);
  useEffect(() => {
    void window.ardenDesktop?.quickCapture?.setShortcut?.(quickCaptureShortcut);
  }, [quickCaptureShortcut]);

  return (
    /* `reducedMotion="user"` makes every motion component honor the OS
       prefers-reduced-motion setting without per-call plumbing. The CSS
       @media (prefers-reduced-motion) block neutralizes CSS keyframes;
       this covers the JS-driven side (motion.div springs, layout anims,
       AnimatePresence). */
    <MotionConfig reducedMotion="user">
      {/* The sidebar owns one symmetric, interruptible presentation layer:
          -48px ↔ 0, opacity and defocus on the same 400ms contract. */}
      <motion.div
        className="surface-panel surface-sidebar surface-radius-lg absolute top-2 left-2 bottom-2 z-[var(--z-shell)] w-[var(--sidebar-width,288px)] overflow-hidden"
        initial={false}
        animate={
          effectiveSidebarHidden
            ? { x: -DISTANCE_RAIL_HIDE, opacity: 0, filter: "blur(6px)" }
            : { x: 0, opacity: 1, filter: "blur(0px)" }
        }
        transition={
          {
            x: { duration: DURATION_RIGHT_PANEL_HIDE, ease: EASE_EMPHASIZED },
            opacity: { duration: DURATION_RIGHT_PANEL_HIDE, ease: EASE_OUT },
            filter: { duration: DURATION_RIGHT_PANEL_HIDE, ease: EASE_OUT },
          }
        }
        style={{ pointerEvents: effectiveSidebarHidden ? "none" : "auto" }}
        aria-hidden={effectiveSidebarHidden}
      >
        <Sidebar />
        <SidebarResizeHandle />
      </motion.div>
      {/* App-global sidebar toggle: fixed-viewport chrome (`.sidebar-toggle`),
          rendered once here so it is present on every screen — Chat, Home,
          and the area rooms — not only where Chat mounts. */}
      <SidebarToggle
        hidden={settingsOpen ? !settingsRailOpen : effectiveSidebarHidden}
        onToggle={toggleEffectiveSidebar}
      />
      <AnimatePresence initial={false}>
        {openAreaKey && !settingsOpen && !memoryOpen && !automationsOpen && (
          <ShellBackButton key="area-back" onClick={goToNewSessionHome} />
        )}
      </AnimatePresence>
      <ErrorBoundary>
        <main
          data-workspace={workspaceKind}
          data-sidebar-hidden={effectiveSidebarHidden ? "true" : "false"}
          data-right-open={workspaceRightDocked ? "true" : "false"}
          className="board-shell-content absolute inset-0 overflow-hidden bg-bg"
        >
          <WorkspaceStage
            geometryKey={`${effectiveSidebarHidden}:${workspaceKind}:${workspaceRightDocked}:${rightPanelDocked}`}
          >
            <WorkspaceRouteHost routeKey={workspaceRouteKey}>
              {workspaceKind === "area" && openAreaKey ? (
                <AreaRoom areaKey={openAreaKey} />
              ) : workspaceKind === "home" ? (
                <Home />
              ) : (
                <Chat />
              )}
            </WorkspaceRouteHost>
          </WorkspaceStage>
        </main>
      </ErrorBoundary>
      {(openAreaKey || !showHome) && (
        <AgentRightSidebar
          mode={openAreaKey ? "hub" : rightPanelDocked ? "docked" : "peek"}
          allowDocking={!openAreaKey}
          sourcesPanel={<SourcesPanel />}
          areaScope={openAreaDetail}
          compact={compactShell}
        />
      )}
      {/* Memory is a full-window takeover (Obsidian-style vault view): ONE
          left column — its own rail — layered over the app shell and its
          fixed sidebar toggles, so no chrome doubles up or overlaps. */}
      <AnimatePresence>
        {memoryOpen && (
          <Suspense key="memory" fallback={null}>
            <MemorySurface />
          </Suspense>
        )}
      </AnimatePresence>
      <ErrorBoundary>
        <Suspense fallback={null}>
          <SettingsModal
            railOpen={settingsRailOpen}
            compact={compactShell}
            onToggleRail={toggleEffectiveSidebar}
            onNavigate={() => {
              if (compactShell) setSettingsRailOpen(false);
            }}
          />
          <AutomationsModal />
          <ToolViewer />
        </Suspense>
      </ErrorBoundary>
      <CommandPalette />
      <ModalScrim />
      <MarkdownViewer />
      <ApprovalReviewModal />
      <Toaster />
      {(import.meta as { env?: { DEV?: boolean } }).env?.DEV && <Inspect />}
    </MotionConfig>
  );
}
