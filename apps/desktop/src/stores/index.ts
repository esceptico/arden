import { create } from "zustand";
import { createConversationActions } from "@/stores/conversation-actions";
import { DEFAULT_CONFIG } from "@/api/core";
import { DEFAULT_SHELL_LAYOUT, toggledSidebarPrefs } from "@/lib/shellOwnership";
import type { State, Actions, UiMessage } from "@/stores/types";
import { mergeSourceRefs } from "@/stores/sourceRefs";
import {
  isValidPrefValue,
  loadPrefs,
  loadSkipApprovals,
  persistPrefs,
  persistSkipApprovals,
} from "@/stores/prefs";
import {
  initialUsage,
} from "@/stores/session-cache";
import {
  createInitialSessionViewState,
} from "@/stores/session-view";
import {
  createAutomationStreamDomainState,
  reduceAutomationFinished,
  reduceAutomationProgress,
  reduceAutomationStreamConnected,
  reduceAutomationStreamConnecting,
  reduceAutomationStreamFailed,
  reduceAutomationStreamIdle,
  reduceAutomationStreamReset,
  reduceAutomationStreamStale,
  type AutomationStreamDomainState,
  type AutomationStreamPhase,
} from "@/stores/automation-domain";
import {
  createBackgroundAgentsDomainState,
  reduceBackgroundAgentUpsert,
  reduceBackgroundAgentsForSession,
  reduceBackgroundAgentsRefreshFailed,
  reduceBackgroundAgentsRefreshStarted,
  type BackgroundAgentsDomainState,
  type BackgroundAgentRefreshStatus,
} from "@/stores/background-agent-domain";
import { syncTranscriptAgentsFromRoster } from "@/stores/transcript-roster-sync";
import {
  appendDismissedWorkflow,
  createWorkflowsDomainState,
  reduceWorkflowStarted,
  reduceWorkflowFinished,
  reduceWorkflowTaskEvent,
  reduceWorkflowTokenUsage,
  workflowKey,
  type WorkflowsDomainState,
} from "@/stores/workflow-domain";
import {
  createAreasDomainState,
  reduceOverviewLoaded,
  reduceOverviewPhase,
  reduceDetailLoaded,
  reduceDetailPhase,
  reduceAskResolved,
  reduceOpenArea,
  reduceRecordsLoaded,
  reduceRecordUpserted,
  reduceRecordArchived,
  type AreasDomainState,
} from "@/stores/areas-domain";
import { appendMemoryVaultChange } from "@/stores/memory-vault-domain";
import {
  createTriageDomainState,
  reduceTriageSeen,
  reduceTriageProposal,
  reduceTriageCleared,
} from "@/stores/triage-domain";
import {
  reduceApprovalRequested,
  reduceApprovalResolved,
  reduceCancellingQueuedMessagesReset,
  reduceQueuedMessageAdded,
  reduceQueuedMessageRemoved,
  reduceQueuedMessagesCleared,
  reduceQueuedMessageStatus,
} from "@/stores/run-lifecycle";

// Re-export types so existing `import { X } from "@/stores"` keeps working.
export type {
  ActivityItem,
  ActivityLabel,
  ActivityState,
  Actions,
  ApprovalState,
  ApprovalStatus,
  PendingConnection,
  BackgroundAgent,
  BackgroundAgentStatus,
  CachedSessionState,
  CornerProfile,
  ImageBlock,
  MarkdownViewState,
  Prefs,
  QueuedMessage,
  QueuedMessageStatus,
  Role,
  ServerLoop,
  SessionUsage,
  SessionViewState,
  SourceRef,
  State,
  ThemeChoice,
  ThinkingIntensity,
  TodoListState,
  SessionTodo,
  TurnMeta,
  UiMessage,
} from "@/stores/types";
export {
  isThinkingIntensity,
  THINKING_INTENSITY_IDS,
} from "@/stores/types";
export type {
  AutomationStreamPhase,
  BackgroundAgentRefreshStatus,
  BackgroundAgentsDomainState,
  AutomationStreamDomainState,
  WorkflowsDomainState,
  AreasDomainState,
};
export type { Workflow, WorkflowAgent, WorkflowPhase } from "@/stores/workflow-domain";
export { selectWorkflowsForSession } from "@/stores/workflow-domain";
export {
  DEFAULT_PREFS,
  DEFAULT_QUICK_CAPTURE_SHORTCUT,
  FONT_SIZE_MAX,
  FONT_SIZE_MIN,
  RIGHT_PANEL_DEFAULT_WIDTH,
  RIGHT_PANEL_MAX_WIDTH,
  RIGHT_PANEL_MIN_WIDTH,
  RIGHT_PANEL_SNAP_POINTS,
  RIGHT_PANEL_SNAP_THRESHOLD_PX,
  SIDEBAR_MAX_WIDTH,
  SIDEBAR_MIN_WIDTH,
  SIDEBAR_SNAP_POINTS,
  SIDEBAR_SNAP_THRESHOLD_PX,
} from "@/stores/prefs";

/** Text of the user's sent messages in the current session, oldest → newest.
 *  Drives the composer's readline-style history recall. Meta/system user
 *  messages (loop ticks, etc.) are excluded — they were never typed. */
export function selectSentUserMessages(state: {
  order: string[];
  messages: Map<string, UiMessage>;
}): string[] {
  const out: string[] = [];
  for (const id of state.order) {
    const message = state.messages.get(id);
    if (!message || message.role !== "user" || message.isMeta) continue;
    const text = message.content.trim();
    if (text.length > 0) out.push(text);
  }
  return out;
}

export const useStore = create<State & Actions>((set) => ({
  config: { ...DEFAULT_CONFIG },
  sessions: [],
  sessionView: createInitialSessionViewState(),
  currentSessionId: null,
  pendingNewChatAreaId: null,
  pendingNewChatDraftId: 0,
  messages: new Map(),
  order: [],
  activeRunSessionIds: new Set(),
  backgroundedRunSessionIds: new Set(),
  unreadDoneSessionIds: new Set(),
  sessionCache: new Map(),
  connected: false,
  serverWarmup: null,
  running: false,
  error: null,
  draft: "",
  settingsOpen: false,
  settingsTab: null,
  connectionDraft: { ...DEFAULT_CONFIG },
  connectionError: null,
  connectionSaving: false,
  usage: initialUsage,
  editingId: null,
  activeActivityId: null,
  currentRunId: null,
  thinkingRunId: null,
  thinkingStatus: null,
  skipApprovals: loadSkipApprovals(),
  skills: [],
  commandPickerOpen: false,
  commandPickerIndex: 0,
  viewingMarkdown: null,
  viewingTool: null,
  workflowViewer: null,
  pendingImages: [],
  serverConfig: null,
  serverModels: null,
  automations: null,
  automationsOpen: false,
  automationTargetId: null,
  automationTargetRun: null,
  automationStream: createAutomationStreamDomainState(),
  archivedSessions: null,
  compacting: false,
  memoryOpen: false,
  memoryTargetPath: null,
  memoryTargetAnchor: null,
  memoryCurrentPath: null,
  automationCurrentId: null,
  sourceFocus: null,
  rightInspectorTab: "activity",
  sourceTurnId: null,
  sourceRefsRevision: 0,
  paletteOpen: false,
  pendingApprovals: [],
  pendingConnections: [],
  reviewingApprovalToolId: null,
  queuedMessages: [],
  pendingResume: null,
  stoppingRunId: null,
  terminalRunIds: new Set(),
  transportDiagnostics: {},
  streamReplaying: false,
  modalOrigin: null,
  loops: [],
  backgroundAgents: createBackgroundAgentsDomainState(),
  childAgentResultSnippets: {},
  workflows: createWorkflowsDomainState(),
  goals: {},
  sessionTodos: {},
  pendingGoalProposal: null,
  toasts: [],
  prefs: loadPrefs(),
  shellLayout: DEFAULT_SHELL_LAYOUT,
  areas: createAreasDomainState(),
  triage: createTriageDomainState(),

  setConfig: (config) => set({ config, connectionDraft: { ...config } }),
  setAreaRecords: (areaRecords) =>
    set((s) => ({ areas: reduceRecordsLoaded(s.areas, areaRecords) })),
  upsertAreaRecord: (area) =>
    set((s) => ({ areas: reduceRecordUpserted(s.areas, area) })),
  archiveAreaRecord: (areaId) =>
    set((s) => ({ areas: reduceRecordArchived(s.areas, areaId) })),
  ...createConversationActions(set),

  openSettings: (tab) =>
    set((s) => ({
      settingsOpen: true,
      automationsOpen: false,
      automationTargetId: null,
      automationTargetRun: null,
      memoryOpen: false,
      memoryTargetPath: null,
      memoryTargetAnchor: null,
      memoryCurrentPath: null,
  automationCurrentId: null,
      settingsTab: tab ?? null,
      connectionDraft: { ...s.config },
      connectionError: null,
      modalOrigin: null,
    })),
  closeSettings: () =>
    set((s) => {
      if (s.connectionSaving) return s;
      return { settingsOpen: false, connectionError: null };
    }),
  setConnectionDraft: (patch) =>
    set((s) => ({ connectionDraft: { ...s.connectionDraft, ...patch } })),
  setConnectionError: (connectionError) => set({ connectionError }),
  setConnectionSaving: (connectionSaving) => set({ connectionSaving }),

  setActiveActivityId: (activeActivityId) => set({ activeActivityId }),

  appendActivityItem: (activityId, item) =>
    set((s) => {
      const existing = s.messages.get(activityId);
      if (!existing || !existing.activity) return s;
      const messages = new Map(s.messages);
      const activity = existing.activity;
      const nextItem = item.status ? item : { ...item, status: "ongoing" as const };
      messages.set(activityId, {
        ...existing,
        activity: { ...activity, done: false, label: "Calling", items: [...activity.items, nextItem] },
      });
      return { messages, sourceRefsRevision: s.sourceRefsRevision + 1 };
    }),

  mergeActivityItem: (itemId, patch) => {
    let didTouch = false;
    set((s) => {
      let touched = false;
      const messages = new Map(s.messages);
      for (const [mid, msg] of messages) {
        if (!msg.activity) continue;
        const idx = msg.activity.items.findIndex((it) => it.id === itemId);
        if (idx < 0) continue;
        const items = msg.activity.items.slice();
        const existingItem = items[idx];
        const sourceRefs = mergeSourceRefs(existingItem.sourceRefs, patch.sourceRefs);
        items[idx] = {
          ...existingItem,
          ...patch,
          ...(sourceRefs === undefined ? {} : { sourceRefs }),
        };
        messages.set(mid, { ...msg, activity: { ...msg.activity, items } });
        touched = true;
        break;
      }
      didTouch = touched;
      return touched
        ? {
            messages,
            ...(patch.sourceRefs === undefined
              ? {}
              : { sourceRefsRevision: s.sourceRefsRevision + 1 }),
          }
        : s;
    });
    return didTouch;
  },

  finalizeActivity: (activityId, label = "Called") =>
    set((s) => {
      const existing = s.messages.get(activityId);
      if (!existing || !existing.activity) return s;
      const messages = new Map(s.messages);
      messages.set(activityId, {
        ...existing,
        activity: {
          ...existing.activity,
          done: true,
          label,
          items: existing.activity.items.map((item) => ({ ...item, status: "executed" as const })),
        },
      });
      return { messages };
    }),

  setSkipApprovals: (skipApprovals) => {
    persistSkipApprovals(skipApprovals);
    set({ skipApprovals });
  },

  setApprovalStatus: (id, status) =>
    set((s) => {
      const existing = s.messages.get(id);
      if (!existing || !existing.approval) return s;
      const messages = new Map(s.messages);
      messages.set(id, { ...existing, approval: { ...existing.approval, status } });
      return { messages };
    }),

  addPendingApproval: (approval) => set((s) => reduceApprovalRequested(s, approval)),
  resolvePendingApproval: (toolId) => set((s) => reduceApprovalResolved(s, toolId)),
  addPendingConnection: (connection) =>
    set((s) => ({
      pendingConnections: [...s.pendingConnections.filter((item) => item.toolId !== connection.toolId), connection],
    })),
  resolvePendingConnection: (toolId) =>
    set((s) => ({ pendingConnections: s.pendingConnections.filter((item) => item.toolId !== toolId) })),
  setReviewingApproval: (toolId, origin) =>
    set({ reviewingApprovalToolId: toolId, modalOrigin: toolId ? origin ?? null : null }),

  addQueuedMessage: (message) => set((s) => reduceQueuedMessageAdded(s, message)),
  setQueuedMessageStatus: (clientId, status) =>
    set((s) => reduceQueuedMessageStatus(s, clientId, status)),
  removeQueuedMessage: (clientId) =>
    set((s) => reduceQueuedMessageRemoved(s, clientId)),
  clearQueuedMessages: () => set(reduceQueuedMessagesCleared()),
  // After a run terminates without ingesting a queued message, the
  // server dropped its inject_queue. Any "cancelling" entries are now
  // stuck — flip them back to "pending" so the user can retry/cancel.
  resetCancellingQueuedMessages: () =>
    set((s) => reduceCancellingQueuedMessagesReset(s)),
  setLoops: (loops) => set({ loops }),
  backgroundAgentsRefreshStarted: () =>
    set((s) => ({
      backgroundAgents: reduceBackgroundAgentsRefreshStarted(s.backgroundAgents),
    })),
  backgroundAgentsRefreshFailed: (error) =>
    set((s) => ({
      backgroundAgents: reduceBackgroundAgentsRefreshFailed(
        s.backgroundAgents,
        error,
      ),
    })),
  setBackgroundAgentsForSession: (sessionId, agents) =>
    set((s) => {
      const backgroundAgents = reduceBackgroundAgentsForSession(
        s.backgroundAgents,
        sessionId,
        agents,
      );
      // Roster updates settle the matching transcript rows too, so the chat
      // trace never disagrees with the sidebar about a terminal agent.
      const messages = syncTranscriptAgentsFromRoster(
        s.messages,
        backgroundAgents.rows,
        s.currentSessionId,
      );
      return messages ? { backgroundAgents, messages } : { backgroundAgents };
    }),
  upsertBackgroundAgent: (agent) =>
    set((s) => {
      const backgroundAgents = reduceBackgroundAgentUpsert(s.backgroundAgents, agent);
      const messages = syncTranscriptAgentsFromRoster(
        s.messages,
        backgroundAgents.rows,
        s.currentSessionId,
      );
      return messages ? { backgroundAgents, messages } : { backgroundAgents };
    }),
  setChildAgentResultSnippet: (key, snippet) =>
    set((s) =>
      s.childAgentResultSnippets[key] === snippet
        ? s
        : { childAgentResultSnippets: { ...s.childAgentResultSnippets, [key]: snippet } },
    ),
  workflowStarted: (input, at) =>
    set((s) => ({
      workflows: reduceWorkflowStarted(s.workflows, input, at),
    })),
  workflowFinished: (input, at) =>
    set((s) => ({
      workflows: reduceWorkflowFinished(s.workflows, input, at),
    })),
  workflowTaskEvent: (input, at) =>
    set((s) => ({
      workflows: reduceWorkflowTaskEvent(s.workflows, input, at),
    })),
  workflowTokenUsage: (input, at) =>
    set((s) => ({
      workflows: reduceWorkflowTokenUsage(s.workflows, input, at),
    })),
  dismissWorkflow: (sessionId, workflowId) =>
    set((s) => {
      // Dismissal is sidebar visibility, not domain deletion: the row stays so
      // the chat-trace card keeps its phases/tokens (and its leaf agents stay
      // contained under it), while the persisted key keeps rehydration from
      // resurfacing the card in the hub after a reload.
      const dismissedWorkflows = appendDismissedWorkflow(
        s.prefs.dismissedWorkflows,
        workflowKey(sessionId, workflowId),
      );
      if (dismissedWorkflows === s.prefs.dismissedWorkflows) return s;
      const prefs = { ...s.prefs, dismissedWorkflows };
      persistPrefs(prefs);
      return { prefs };
    }),
  setGoal: (sessionId, goal) =>
    set((s) => {
      const goals = { ...s.goals };
      if (goal) goals[sessionId] = goal;
      else delete goals[sessionId];
      return { goals };
    }),
  setSessionTodo: (sessionId, todo) =>
    set((s) => {
      const sessionTodos = { ...s.sessionTodos };
      if (todo) sessionTodos[sessionId] = todo;
      else delete sessionTodos[sessionId];
      return { sessionTodos };
    }),
  setPendingGoalProposal: (pendingGoalProposal) => set({ pendingGoalProposal }),

  setSkills: (skills) => set({ skills }),
  setCommandPickerOpen: (commandPickerOpen) => set({ commandPickerOpen, commandPickerIndex: 0 }),
  setCommandPickerIndex: (commandPickerIndex) => set({ commandPickerIndex }),
  setViewingMarkdown: (viewingMarkdown) => set({ viewingMarkdown }),
  setViewingTool: (viewingTool) => set({ viewingTool }),
  setViewingWorkflow: (workflowViewer) => set({ workflowViewer }),

  addPendingImages: (images) =>
    set((s) => ({ pendingImages: [...s.pendingImages, ...images] })),
  removePendingImage: (index) =>
    set((s) => ({ pendingImages: s.pendingImages.filter((_, i) => i !== index) })),
  clearPendingImages: () => set({ pendingImages: [] }),

  setServerConfig: (serverConfig) => set({ serverConfig }),
  setServerModels: (serverModels) => set({ serverModels }),
  setAutomations: (automations) => set({ automations }),
  openAutomations: (taskId, options) =>
    set({
      automationsOpen: true,
      settingsOpen: false,
      memoryOpen: false,
      memoryTargetPath: null,
      memoryTargetAnchor: null,
      memoryCurrentPath: null,
  automationCurrentId: null,
      automationTargetId: taskId ?? null,
      automationTargetRun: taskId ? options?.run ?? null : null,
      modalOrigin: null,
    }),
  closeAutomations: () =>
    set({ automationsOpen: false, automationTargetId: null, automationTargetRun: null, automationCurrentId: null }),
  clearAutomationTarget: () => set({ automationTargetId: null, automationTargetRun: null }),
  automationStreamConnecting: () =>
    set((s) => ({
      automationStream: reduceAutomationStreamConnecting(s.automationStream),
    })),
  automationStreamConnected: () =>
    set((s) => ({
      automationStream: reduceAutomationStreamConnected(s.automationStream),
    })),
  automationStreamStale: () =>
    set((s) => ({
      automationStream: reduceAutomationStreamStale(s.automationStream),
    })),
  automationStreamFailed: (error) =>
    set((s) => ({
      automationStream: reduceAutomationStreamFailed(s.automationStream, error),
    })),
  automationStreamIdle: () =>
    set((s) => ({
      automationStream: reduceAutomationStreamIdle(s.automationStream),
    })),
  automationStreamReset: () =>
    set((s) => ({
      automationStream: reduceAutomationStreamReset(s.automationStream),
    })),
  automationProgress: (taskId, status) =>
    set((s) => ({
      automationStream: reduceAutomationProgress(s.automationStream, taskId, status),
    })),
  automationFinished: (taskId) =>
    set((s) => ({
      automationStream: reduceAutomationFinished(s.automationStream, taskId),
    })),
  memoryVaultVersion: 0,
  memoryVaultChanges: [],
  memoryVaultChanged: (change) =>
    set((s) => {
      const memoryVaultChanges = change
        ? appendMemoryVaultChange(s.memoryVaultChanges, change)
        : [];
      if (change && memoryVaultChanges === s.memoryVaultChanges) return {};
      return {
        memoryVaultVersion: s.memoryVaultVersion + 1,
        memoryVaultChanges,
      };
    }),
  pushToast: (toast) =>
    set((s) => (s.toasts.some((t) => t.id === toast.id) ? {} : { toasts: [...s.toasts, toast] })),
  dismissToast: (id) =>
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
  setArchivedSessions: (archivedSessions) => set({ archivedSessions }),
  setCompacting: (compacting) => set({ compacting }),
  openMemory: (targetPath, anchor) =>
    set({
      memoryOpen: true,
      memoryTargetPath: targetPath ?? null,
      memoryTargetAnchor: (targetPath && anchor) || null,
      settingsOpen: false,
      automationsOpen: false,
      automationTargetId: null,
      modalOrigin: null,
    }),
  clearMemoryTarget: () => set({ memoryTargetPath: null, memoryTargetAnchor: null }),
  setMemoryCurrentPath: (memoryCurrentPath) => set({ memoryCurrentPath }),
  setAutomationCurrentId: (automationCurrentId) => set({ automationCurrentId }),
  closeMemory: () =>
    set({ memoryOpen: false, memoryTargetPath: null, memoryTargetAnchor: null, memoryCurrentPath: null }),
  setSourceFocus: (sourceFocus) => set({ sourceFocus }),
  setRightInspectorTab: (rightInspectorTab) =>
    set({
      rightInspectorTab,
      ...(rightInspectorTab === "sources" ? { sourceTurnId: null } : {}),
    }),
  openSourcesForTurn: (sourceTurnId) =>
    set((s) => {
      const prefs = s.prefs.rightPanelCollapsed
        ? { ...s.prefs, rightPanelCollapsed: false }
        : s.prefs;
      if (prefs !== s.prefs) persistPrefs(prefs);
      return { rightInspectorTab: "sources", sourceTurnId, prefs };
    }),
  openPalette: () => set({ paletteOpen: true }),
  closePalette: () => set({ paletteOpen: false }),
  togglePalette: () => set((s) => ({ paletteOpen: !s.paletteOpen })),
  setPref: (key, value) =>
    set((s) => {
      if (!isValidPrefValue(key, value)) return {};
      const next = { ...s.prefs, [key]: value };
      persistPrefs(next);
      return { prefs: next };
    }),
  setShellLayout: (shellLayout) =>
    set((s) =>
      s.shellLayout.compact === shellLayout.compact &&
      s.shellLayout.workspace === shellLayout.workspace
        ? {}
        : { shellLayout },
    ),
  toggleSidebar: () =>
    set((s) => {
      const next = toggledSidebarPrefs(s.prefs);
      persistPrefs(next);
      return { prefs: next };
    }),
  areasOverviewLoaded: (overview) =>
    set((s) => ({ areas: reduceOverviewLoaded(s.areas, overview) })),
  setAreasOverviewPhase: (phase) =>
    set((s) => ({ areas: reduceOverviewPhase(s.areas, phase) })),
  areaDetailLoaded: (detail) =>
    set((s) => ({ areas: reduceDetailLoaded(s.areas, detail) })),
  setAreaDetailPhase: (key, phase) =>
    set((s) => ({ areas: reduceDetailPhase(s.areas, key, phase) })),
  areaAskResolved: (askId) =>
    set((s) => ({ areas: reduceAskResolved(s.areas, askId) })),
  openArea: (key) =>
    set((s) => ({ areas: reduceOpenArea(s.areas, key) })),
  markTriageSeen: (sessionId) =>
    set((s) => ({ triage: reduceTriageSeen(s.triage, sessionId) })),
  setTriageProposal: (sessionId, decision) =>
    set((s) => ({ triage: reduceTriageProposal(s.triage, sessionId, decision) })),
  clearTriageProposal: (sessionId) =>
    set((s) => ({ triage: reduceTriageCleared(s.triage, sessionId) })),
}));

// Dev-only: expose the store so connection-gated surfaces can be driven for
// manual UI/animation verification in a renderer with no backend. Tree-shaken
// out of production builds via the `import.meta.env.DEV` guard.
if ((import.meta as { env?: { DEV?: boolean } }).env?.DEV) {
  (globalThis as { __ardenStore?: typeof useStore }).__ardenStore = useStore;
}

// Helpers for use outside React (e.g. inside event-stream handlers).
export const getState = useStore.getState;
export const setState = useStore.setState;

// Console / preview-harness escape hatch: lets pixel verification and debugging
// seed state without a live server. The renderer runs no untrusted scripts, so
// exposing the store costs nothing.
if (typeof window !== "undefined") {
  (window as unknown as { __arden?: unknown }).__arden = { useStore, getState, setState };
}
