import { Suspense, lazy, useCallback, useEffect, useState } from "react";
import { MotionConfig, motion } from "motion/react";
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
import { MemorySurface } from "@/features/memory/components/MemorySurface";
import { AutomationsModal } from "@/features/automations/components/AutomationsModal";
import { AreaRoom } from "@/features/areas/components/AreaRoom";
import { CommandPalette } from "@/features/command-palette/components/CommandPalette";
import { MarkdownViewer } from "@/components/ui/MarkdownViewer";
import { ApprovalReviewModal } from "@/features/chat/components/ApprovalReviewModal";
import { SidebarResizeHandle } from "@/components/workspace/SidebarResizeHandle";
import { SidebarToggle } from "@/components/ui/SidebarToggle";
import { ShellNav } from "@/components/workspace/ShellNav";
import { WorkspaceRouteHost } from "@/components/workspace/WorkspaceRouteHost";
import { WorkspaceStage } from "@/components/workspace/WorkspaceStage";
import { AgentRightSidebar } from "@/features/background-agents/components/AgentRightSidebar";
import { SourcesPanel } from "@/features/sources/components/SourcesPanel";
import { ErrorBoundary } from "@/app/ErrorBoundary";
import { Toaster } from "@/components/ui/Toaster";
import { ModalScrim } from "@/components/ui/ModalScrim";
import { Inspect } from "@/features/inspect/components/Inspect";
import { useStore } from "@/stores";
import { hasBlockingOverlay } from "@/lib/overlayStack";
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
  workspaceOwnsRail,
  type ShellLayout,
} from "@/lib/shellOwnership";
import { bootstrap, startServerConnectionPolling } from "@/actions/bootstrap";
import { createSession, switchSession } from "@/actions/sessions";
import { goBack, goForward, navigateHome, recordCurrentDestination } from "@/actions/navigation";
import { sendMessage } from "@/actions/messages";

// Takeovers stay lazy: they mount outside the route host, so a chunk fetch
// costs nothing but a beat before the sheet appears.
//
// The Memory and Automations ROUTES are eager. A route may not suspend: the
// route host swaps with AnimatePresence mode="wait", which holds the outgoing
// room until the incoming one mounts, and a child that suspends never gets
// there — the swap deadlocks on the old page. They were preloaded a second
// after boot anyway, so the deferral bought a second of bundle, not a page.
const SettingsModal = lazy(() =>
  import("@/features/settings/components/SettingsModal").then((m) => ({ default: m.SettingsModal })),
);
const ToolViewer = lazy(() =>
  import("@/features/chat/components/ToolViewer").then((m) => ({ default: m.ToolViewer })),
);

/** Fetch the takeover chunk once the app is idle. Without this the FIRST
 *  open of Settings spends ~300ms fetching it behind a null Suspense
 *  fallback: the click lands, nothing moves, and the surface arrives
 *  mid-animation. */
function usePreloadTakeovers(): void {
  useEffect(() => {
    const warm = () => {
      void import("@/features/settings/components/SettingsModal");
    };
    const idle = window.requestIdleCallback;
    if (typeof idle === "function") {
      const handle = idle(warm, { timeout: 2_000 });
      return () => window.cancelIdleCallback?.(handle);
    }
    const timer = window.setTimeout(warm, 1_000);
    return () => window.clearTimeout(timer);
  }, []);
}

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

/** Whether a keystroke is landing in something the user is typing into.
 *  Route history must not steal a chord out from under an editor. */
function isTextEntry(target: EventTarget | null): boolean {
  return target instanceof HTMLElement
    && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));
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
  usePreloadTakeovers();
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
  const automationCurrentId = useStore((s) => s.automationCurrentId);
  const memoryCurrentPath = useStore((s) => s.memoryCurrentPath);
  // Memory and Automations are routes, not dialogs: each brings its own rail
  // and its own internal navigation, so they replace the stage rather than
  // covering it. Settings stays a Takeover — you enter, change one thing,
  // leave. Route order here is precedence, not nesting.
  const workspaceKind: ShellLayout["workspace"] = memoryOpen
    ? "memory"
    : automationsOpen
      ? "automations"
      : openAreaKey
        ? "area"
        : showHome
          ? "home"
          : "chat";
  const workspaceRouteKey = openAreaKey && workspaceKind === "area"
    ? `area:${openAreaKey}`
    : workspaceKind;
  const routeOwnsRail = workspaceOwnsRail(workspaceKind);
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

  // History records the route the shell landed on, not the call that got it
  // there. Sidebar clicks, ⌘N, area cards, agent destinations and toasts all
  // reach the store by different paths; watching the outcome catches every
  // one, including whichever is added next.
  useEffect(() => {
    recordCurrentDestination();
  }, [workspaceRouteKey, currentSessionId, automationCurrentId, memoryCurrentPath]);
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
          // A route that owns the rail owns this shortcut too: it is the only
          // left column on screen, and toggling the shell's hidden one would
          // move nothing the user can see.
          if (routeOwnsRail) return;
          e.preventDefault();
          toggleEffectiveSidebar();
          return;
        }
        if (k === "n" && !e.shiftKey) {
          e.preventDefault();
          void createSession();
          return;
        }
        if (k === "h" && e.shiftKey) {
          e.preventDefault();
          navigateHome();
          return;
        }
        if (e.key === ",") {
          e.preventDefault();
          openSettings();
          return;
        }
        // Route history. Memory registers the same chord for its own page
        // trail and calls preventDefault, so while the vault is open it wins
        // — the innermost history owns the key, as in a browser.
        if (e.key === "[" || e.key === "]") {
          if (hasBlockingOverlay() || isTextEntry(e.target) || isTextEntry(document.activeElement)) return;
          e.preventDefault();
          if (e.key === "[") goBack();
          else goForward();
          return;
        }
        return;
      }

      // Memory and Automations used to be dialogs, and Escape-to-leave is
      // muscle memory from that. Preserve it for exactly those two routes —
      // Escape in a chat or an area room navigates nowhere, same as a browser.
      // Overlays layered above (dialogs, peeks, menus) consume Escape first
      // via the overlay stack.
      if (e.key === "Escape" && routeOwnsRail && !hasBlockingOverlay()) {
        e.preventDefault();
        goBack();
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
          and the area rooms — not only where Chat mounts.
          A route that owns the rail renders its own toggle for its own column;
          both would land on the same fixed coordinates, leaving two controls
          stacked where the user sees one. */}
      {!routeOwnsRail && (
        <SidebarToggle
          hidden={settingsOpen ? !settingsRailOpen : effectiveSidebarHidden}
          onToggle={toggleEffectiveSidebar}
        />
      )}
      {!settingsOpen && <ShellNav />}
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
              {workspaceKind === "memory" ? (
                <MemorySurface />
              ) : workspaceKind === "automations" ? (
                <AutomationsModal />
              ) : workspaceKind === "area" && openAreaKey ? (
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
      {/* The inspector belongs to a chat or an area room — it shows that
          session's agents, approvals and sources. Memory and Automations have
          none of it, and as takeovers they simply covered it; as routes they
          sit beside it, leaving its trigger on a page it cannot open. */}
      {!routeOwnsRail && (openAreaKey || !showHome) && (
        <AgentRightSidebar
          mode={openAreaKey ? "hub" : rightPanelDocked ? "docked" : "peek"}
          allowDocking={!openAreaKey}
          sourcesPanel={<SourcesPanel />}
          areaScope={openAreaDetail}
          compact={compactShell}
        />
      )}
      <ErrorBoundary>
        <Suspense fallback={null}>
          {/* Settings stays a Takeover: you enter, change one thing, and
              leave. No internal navigation, so it is a dialog over the
              stage rather than a route that replaces it. */}
          <SettingsModal
            railOpen={settingsRailOpen}
            compact={compactShell}
            onToggleRail={toggleEffectiveSidebar}
            onNavigate={() => {
              if (compactShell) setSettingsRailOpen(false);
            }}
          />
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
