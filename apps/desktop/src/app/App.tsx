import { Suspense, lazy, useEffect, useState } from "react";
import { AnimatePresence, MotionConfig, motion } from "motion/react";
import { MOTION, EASE_EMPHASIZED, EASE_OUT, DURATION_RIGHT_PANEL_HIDE } from "@/lib/tokens/motion";
import { IS_DESKTOP_MAC } from "@/lib/platform";
import { Sidebar } from "@/features/sessions/components/Sidebar";
import { Chat } from "@/features/chat/components/Chat";
import { Home } from "@/features/home/components/Home";
import { SliceRoom } from "@/features/slices/components/SliceRoom";
import { CommandPalette } from "@/features/command-palette/components/CommandPalette";
import { MarkdownViewer } from "@/components/ui/MarkdownViewer";
import { ApprovalReviewModal } from "@/features/chat/components/ApprovalReviewModal";
import { SidebarResizeHandle } from "@/features/sessions/components/SidebarResizeHandle";
import { SidebarToggle } from "@/components/ui/SidebarToggle";
import { AgentRightSidebar } from "@/features/background-agents/components/AgentRightSidebar";
import { ErrorBoundary } from "@/app/ErrorBoundary";
import { Toaster } from "@/components/ui/Toaster";
import { useStore } from "@/stores";
import { useEvents } from "@/hooks/useEvents";
import { useActiveRuns } from "@/features/background-agents/hooks/useActiveRuns";
import { useAutomationEvents } from "@/features/automations/hooks/useAutomationEvents";
import { useTaskResultToasts } from "@/hooks/useTaskResultToasts";
import { useThemeEffect } from "@/lib/theme";
import { bootstrap, startServerConnectionPolling } from "@/actions/bootstrap";
import { createSession, goToNewSessionHome, switchSession } from "@/actions/sessions";
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
const MemoryModal = lazy(() =>
  import("@/features/memory/components/MemoryModal").then((m) => ({ default: m.MemoryModal })),
);
const ToolViewer = lazy(() =>
  import("@/features/chat/components/ToolViewer").then((m) => ({ default: m.ToolViewer })),
);

// Short leftward drift on hide — gives the fade/blur a direction without
// reading as a full slide-back (mirror of the right sidebar's drift).
const SIDEBAR_HIDE_DRIFT = 48;

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
    void window.ntrpDesktop?.window?.isFullScreen?.().then(setFullscreen);
    const unsubscribe = window.ntrpDesktop?.window?.onFullScreenChange?.(setFullscreen);
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
  const sidebarWidth = useStore((s) => s.prefs.sidebarWidth);
  const rightPanelWidth = useStore((s) => s.prefs.rightPanelWidth);
  const toggleSidebar = useStore((s) => s.toggleSidebar);
  const openSettings = useStore((s) => s.openSettings);
  // Home = no session selected, full stop. Since boot stopped auto-selecting
  // the latest session, an EMPTY-but-selected session is still a chat (its
  // own empty state), not Home — the old transcript-emptiness derivation
  // made a freshly opened session flash Home while history loaded.
  const showHome = useStore((s) => s.currentSessionId === null);
  const openSliceKey = useStore((s) => s.slices.openSliceKey);

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
          e.preventDefault();
          toggleSidebar();
          return;
        }
        if (k === "n" && !e.shiftKey) {
          e.preventDefault();
          goToNewSessionHome();
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
  }, [toggleSidebar, openSettings]);

  useEvents(hash === "#trace-demo" ? null : currentSessionId);
  useActiveRuns();
  useAutomationEvents();
  useTaskResultToasts();

  // Receive submissions from the quick-capture floating window. The
  // Electron main process forwards each one via `quick:message`; we
  // route into the chosen chat (or a fresh project-less chat — Inbox,
  // NOT the current session's project) and send. Capture is silent —
  // this window is NOT brought forward — so the session (and its
  // streamed response) is simply waiting the next time the user
  // switches to ntrp.
  useEffect(() => {
    const unsubscribe = window.ntrpDesktop?.quickCapture?.onMessage?.(async (payload) => {
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
    void window.ntrpDesktop?.quickCapture?.setShortcut?.(quickCaptureShortcut);
  }, [quickCaptureShortcut]);

  return (
    /* `reducedMotion="user"` makes every motion component honor the OS
       prefers-reduced-motion setting without per-call plumbing. The CSS
       @media (prefers-reduced-motion) block neutralizes CSS keyframes;
       this covers the JS-driven side (motion.div springs, layout anims,
       AnimatePresence). */
    <MotionConfig reducedMotion="user">
      {/* Asymmetric motion mirroring the right sidebar: SHOW slides in from
          the left edge (x: off-edge → 0); HIDE dissolves — a short leftward
          drift + fade + blur on the faster EASE_OUT, kept off pointer/focus
          while hidden. The chat's left-inset reflow (Chat.tsx) borrows the
          same curve on hide so the fading panel and the expanding edge read
          as one motion. */}
      <motion.div
        className="surface-panel surface-radius-md absolute top-2 left-2 bottom-2 z-30 w-[calc(var(--sidebar-width,272px)-16px)] overflow-hidden"
        initial={false}
        animate={
          sidebarHidden
            ? { x: -SIDEBAR_HIDE_DRIFT, opacity: 0, filter: "blur(6px)" }
            : { x: [-sidebarWidth, 0], opacity: 1, filter: "blur(0px)" }
        }
        transition={
          sidebarHidden
            ? { duration: DURATION_RIGHT_PANEL_HIDE, ease: EASE_OUT }
            : {
                x: { duration: MOTION.route, ease: EASE_EMPHASIZED },
                opacity: { duration: 0 },
                filter: { duration: 0 },
              }
        }
        style={{ pointerEvents: sidebarHidden ? "none" : "auto" }}
        aria-hidden={sidebarHidden}
      >
        <Sidebar />
        <SidebarResizeHandle />
      </motion.div>
      {/* App-global sidebar toggle: fixed-viewport chrome (`.sidebar-toggle`),
          rendered once here so it is present on every screen — Chat, Home,
          and the slice rooms — not only where Chat mounts. */}
      <SidebarToggle />
      <ErrorBoundary>
        {openSliceKey || showHome ? (
          /* Home/SliceRoom get the same pane geometry Chat's <main> claims
             (flush with both sidebars) plus a scroll container — they are
             full screens, not floating columns. */
          <main
            data-sidebar-hidden={sidebarHidden ? "true" : "false"}
            data-right-open={rightPanelCollapsed ? "false" : "true"}
            className="absolute top-0 right-0 bottom-0 left-[var(--sidebar-width,272px)] data-[sidebar-hidden=true]:left-0 data-[right-open=true]:right-[var(--right-panel-width,320px)] bg-bg overflow-hidden"
          >
            {/* Symmetric surface swap: the leaving surface dissolves while the
                arriving one rises, so Home↔room reads as one spatial move rather
                than a hard cut. Both surfaces are absolute inset-0 (their own
                className) so enter/exit overlap by construction — popLayout is
                NOT used because popping strips the exiting column's h-full
                height reference, collapsing it and teleporting the room's
                pinned composer to the top mid-dissolve. */}
            <AnimatePresence initial={false}>
              {openSliceKey ? (
                <SliceRoom key={openSliceKey} sliceKey={openSliceKey} />
              ) : (
                <Home key="home" />
              )}
            </AnimatePresence>
          </main>
        ) : (
          <Chat />
        )}
      </ErrorBoundary>
      <AgentRightSidebar />
      <ErrorBoundary>
        <Suspense fallback={null}>
          <SettingsModal />
          <AutomationsModal />
          <MemoryModal />
          <ToolViewer />
        </Suspense>
      </ErrorBoundary>
      <CommandPalette />
      <MarkdownViewer />
      <ApprovalReviewModal />
      <Toaster />
    </MotionConfig>
  );
}
