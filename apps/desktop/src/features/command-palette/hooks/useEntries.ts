import { useMemo } from "react";
import {
  Archive,
  Bot,
  Brain,
  Copy,
  Eraser,
  FolderPlus,
  GitBranch,
  MessageSquare,
  Monitor,
  Moon,
  PanelLeft,
  Pencil,
  Power,
  RotateCw,
  Settings as SettingsIcon,
  ShieldCheck,
  ShieldOff,
  Sparkles,
  Square,
  Sun,
  Zap,
} from "lucide-react";
import { useStore } from "@/stores";
import { loadHistory } from "@/actions/history";
import { archiveSession, branchAtMessage, createProject, goToNewSessionHome, renameSession, switchSession } from "@/actions/sessions";
import { stopRun } from "@/actions/messages";
import { runBuiltinCommand } from "@/actions/builtins";
import { toggleAuto } from "@/actions/loops";
import { compactSessionApi } from "@/api/core";
import { formatRelativePast } from "@/lib/format";
import { copyText } from "@/lib/clipboard";
import { lastAssistantId } from "@/features/command-palette/lib/filter";
import { buildProviderView, buildThemeView } from "@/features/command-palette/lib/views";
import type { CommandEntry } from "@/features/command-palette/types";

export function useEntries(): CommandEntry[] {
  const sessions = useStore((s) => s.sessions);
  const currentSessionId = useStore((s) => s.currentSessionId);
  const config = useStore((s) => s.config);
  const openSettings = useStore((s) => s.openSettings);
  const openAutomations = useStore((s) => s.openAutomations);
  const openMemory = useStore((s) => s.openMemory);
  const toggleSidebar = useStore((s) => s.toggleSidebar);
  const sidebarHidden = useStore((s) => s.prefs.sidebarHidden);
  const setPref = useStore((s) => s.setPref);
  const currentTheme = useStore((s) => s.prefs.theme);
  const skipApprovals = useStore((s) => s.skipApprovals);
  const running = useStore((s) => s.running);
  const order = useStore((s) => s.order);
  const serverModels = useStore((s) => s.serverModels);
  const serverConfig = useStore((s) => s.serverConfig);
  const currentChatModel = serverConfig?.chat_model;

  return useMemo(() => {
    const entries: CommandEntry[] = [];

    // Suggested
    entries.push({
      id: "suggested:new-session",
      section: "suggested",
      label: "New session",
      icon: Pencil,
      shortcut: "⌘N",
      run: () => goToNewSessionHome(),
      search: "new session create chat",
    });
    entries.push({
      id: "suggested:new-project",
      section: "suggested",
      label: "New slice",
      icon: FolderPlus,
      run: () => void createProject(),
      search: "new project create folder group",
    });
    entries.push({
      id: "suggested:toggle-sidebar",
      section: "suggested",
      label: sidebarHidden ? "Show sidebar" : "Hide sidebar",
      icon: PanelLeft,
      shortcut: "⌘B",
      run: toggleSidebar,
      search: "sidebar panel toggle hide show",
    });
    entries.push({
      id: "suggested:compact",
      section: "suggested",
      label: "Compact context",
      icon: Sparkles,
      run: async () => {
        if (!currentSessionId) return;
        try {
          const result = await compactSessionApi(config, currentSessionId);
          if (result.status === "compacted" && useStore.getState().currentSessionId === currentSessionId) {
            await loadHistory(currentSessionId);
          }
        } catch {
          /* surfaced via the global error path */
        }
      },
      search: "compact context summarize",
    });
    if (currentSessionId) {
      entries.push({
        id: "suggested:archive-current",
        section: "suggested",
        label: "Archive current session",
        icon: Archive,
        run: async () => {
          if (!confirm("Archive this session? You can restore it later.")) return;
          try {
            await archiveSession(currentSessionId);
          } catch {
            /* ignore */
          }
        },
        search: "archive current session",
      });
      // Branch from the most recent assistant message.
      const lastAssistant = lastAssistantId(order, useStore.getState().messages);
      if (lastAssistant) {
        entries.push({
          id: "suggested:branch-last",
          section: "suggested",
          label: "Branch from last assistant message",
          icon: GitBranch,
          run: () => branchAtMessage(lastAssistant),
          search: "branch fork split",
        });
      }
    }

    // Navigation
    entries.push({
      id: "open:memory",
      section: "open",
      label: "Memory",
      icon: Brain,
      run: openMemory,
      search: "memory knowledge activation review procedures actions artifacts",
    });
    entries.push({
      id: "open:automations",
      section: "open",
      label: "Automations",
      icon: Zap,
      run: openAutomations,
      search: "automations cron scheduled",
    });
    entries.push({
      id: "open:archive",
      section: "open",
      label: "Archived sessions",
      icon: Archive,
      run: () => openSettings(null, "archive"),
      search: "archive archived",
    });
    entries.push({
      id: "open:settings",
      section: "open",
      label: "Settings",
      icon: SettingsIcon,
      shortcut: "⌘,",
      run: openSettings,
      search: "settings preferences config mcp models",
    });

    // Switch model — drill-down. Hidden when /models hasn't returned
    // anything yet so the chevron doesn't lie about navigable content.
    if (serverModels && serverModels.groups.length > 0) {
      entries.push({
        id: "open:switch-model",
        section: "open",
        label: "Switch model",
        hint: currentChatModel,
        icon: Bot,
        children: () => buildProviderView(serverModels.groups, currentChatModel),
        search: "switch model chat provider anthropic openai",
      });
    }

    // Session-scoped actions (only meaningful when a session is open).
    if (currentSessionId) {
      const currentSession = sessions.find((s) => s.session_id === currentSessionId);
      const currentName = currentSession?.name?.trim() || "untitled";
      entries.push({
        id: "suggested:rename-current",
        section: "suggested",
        label: "Rename current session",
        hint: currentName,
        icon: Pencil,
        run: async () => {
          const next = window.prompt("Rename session", currentName);
          if (next && next.trim() && next.trim() !== currentName) {
            await renameSession(currentSessionId, next.trim());
          }
        },
        search: "rename session title",
      });
      entries.push({
        id: "suggested:clear-current",
        section: "suggested",
        label: "Clear session messages",
        icon: Eraser,
        run: async () => {
          if (!confirm("Clear all messages in this session? This cannot be undone.")) return;
          await runBuiltinCommand("clear", "");
        },
        search: "clear reset wipe messages",
      });
      entries.push({
        id: "suggested:copy-session-id",
        section: "suggested",
        label: "Copy session ID",
        hint: currentSessionId.slice(0, 8),
        icon: Copy,
        run: async () => {
          await copyText(currentSessionId);
        },
        search: "copy session id identifier",
      });
    }
    entries.push({
      id: "suggested:toggle-auto",
      section: "suggested",
      label: skipApprovals ? "Disable Auto-approve" : "Enable Auto-approve",
      hint: skipApprovals ? "currently on" : undefined,
      icon: skipApprovals ? ShieldOff : ShieldCheck,
      run: () => toggleAuto(!skipApprovals),
      search: "auto approve approval toggle",
    });
    if (running) {
      entries.push({
        id: "suggested:stop-run",
        section: "suggested",
        label: "Stop current run",
        icon: Square,
        shortcut: "Esc",
        run: () => stopRun(),
        search: "stop cancel halt run",
      });
    }

    // Appearance — theme and palette as drill-downs.
    entries.push({
      id: "appearance:theme",
      section: "appearance",
      label: "Theme",
      hint: currentTheme,
      icon: currentTheme === "dark" ? Moon : currentTheme === "light" ? Sun : Monitor,
      children: () => buildThemeView(currentTheme, setPref),
      search: "theme dark light system mode",
    });
    // System — Electron-only utilities.
    if (window.ntrpDesktop?.app) {
      entries.push({
        id: "system:reload",
        section: "system",
        label: "Reload window",
        icon: RotateCw,
        shortcut: "⌘R",
        run: () => window.ntrpDesktop!.app.reload(),
        search: "reload refresh restart window",
      });
      entries.push({
        id: "system:quit",
        section: "system",
        label: "Quit ntrp",
        icon: Power,
        shortcut: "⌘Q",
        run: () => window.ntrpDesktop!.app.quit(),
        search: "quit exit close app",
      });
    }

    // Sessions — recent first, skip the active one.
    for (const s of sessions) {
      if (s.session_id === currentSessionId) continue;
      const label = s.name?.trim() || "untitled";
      entries.push({
        id: `session:${s.session_id}`,
        section: "session",
        label,
        hint: formatRelativePast(s.last_activity),
        icon: MessageSquare,
        run: () => switchSession(s.session_id),
        search: `${label.toLowerCase()} session`,
      });
    }

    return entries;
  }, [
    sessions,
    currentSessionId,
    config,
    openSettings,
    openAutomations,
    openMemory,
    toggleSidebar,
    sidebarHidden,
    setPref,
    currentTheme,
    skipApprovals,
    running,
    order,
    serverModels,
    currentChatModel,
  ]);
}
