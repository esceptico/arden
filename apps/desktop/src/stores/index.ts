import { create } from "zustand";
import { DEFAULT_CONFIG } from "@/api/core";
import { isActivityContinuationMessage } from "@/lib/messageVisibility";
import { isLiveRunStatus } from "@/lib/runStatus";
import { DEFAULT_SHELL_LAYOUT, toggledSidebarPrefs } from "@/lib/shellOwnership";
import type { ActivityItem, State, Actions, UiMessage } from "@/stores/types";
import { mergeSourceRefs } from "@/stores/sourceRefs";
import {
  isValidPrefValue,
  loadPrefs,
  loadSkipApprovals,
  persistPrefs,
  persistSkipApprovals,
} from "@/stores/prefs";
import {
  blankSessionView,
  initialUsage,
  normalizeActivityGroups,
  normalizeCachedSessionState,
  snapshotSession,
} from "@/stores/session-cache";
import {
  createInitialSessionViewState,
  reduceCachePreviewRestored,
  reduceHistoryLoadSucceeded,
  reduceHistoryPageLoading,
  reduceSessionSelected,
} from "@/stores/session-view";
import {
  createAutomationStreamDomainState,
  reduceAutomationFinished,
  reduceAutomationProgress,
  reduceAutomationStreamConnected,
  reduceAutomationStreamConnecting,
  reduceAutomationStreamFailed,
  reduceAutomationStreamIdle,
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
  reduceForegroundRunCleared,
  reduceQueuedMessageAdded,
  reduceQueuedMessageRemoved,
  reduceQueuedMessagesCleared,
  reduceQueuedMessagesPersisted,
  reduceQueuedMessageStatus,
  reduceRunCompleted,
  reduceRunStarted,
  reduceRunStatus,
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
  ThinkingAnimation,
  ThinkingIntensity,
  TodoListState,
  TurnMeta,
  UiMessage,
} from "@/stores/types";
export {
  isThinkingAnimation,
  isThinkingIntensity,
  THINKING_ANIMATION_IDS,
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

function mergePagedHistoryMessage(existing: UiMessage | undefined, incoming: UiMessage): UiMessage {
  if (!existing?.activity || !incoming.activity) return incoming;
  return {
    ...incoming,
    activity: {
      ...incoming.activity,
      items: mergePagedActivityItems(existing.activity.items, incoming.activity.items),
    },
  };
}

function mergePagedActivityItems(
  existingItems: ActivityItem[],
  incomingItems: ActivityItem[],
): ActivityItem[] {
  const items = existingItems.slice();
  const indexById = new Map(items.map((item, index) => [item.id, index] as const));
  for (const incoming of incomingItems) {
    const index = indexById.get(incoming.id);
    if (index === undefined) {
      indexById.set(incoming.id, items.length);
      items.push(incoming);
      continue;
    }
    const existing = items[index];
    const sourceRefs = mergeSourceRefs(existing.sourceRefs, incoming.sourceRefs);
    items[index] = {
      ...existing,
      ...incoming,
      ...(sourceRefs === undefined ? {} : { sourceRefs }),
    };
  }
  return items;
}

function activeRunsFromSessions(sessions: import("@/api/types").SessionListItem[]) {
  return sessions
    .filter((session) => session.active_run_id && isLiveRunStatus(session.run_status))
    .map((session) => ({
      runId: session.active_run_id,
      sessionId: session.session_id,
      status: session.run_status,
    }));
}

function inputTokens(usage: {
  prompt: number;
  completion: number;
  total?: number;
  cache_read?: number;
  cache_write?: number;
}): number {
  return usage.total !== undefined
    ? Math.max(0, usage.total - usage.completion)
    : usage.prompt + (usage.cache_read ?? 0) + (usage.cache_write ?? 0);
}

function activeActivityIdFromMessages(messages: UiMessage[]): string | null {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (isActivityContinuationMessage(message)) continue;
    return message.role === "activity" && message.activity && !message.activity.done
      ? message.id
      : null;
  }
  return null;
}

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
  messages: new Map(),
  order: [],
  activeRunSessionIds: new Set(),
  backgroundedRunSessionIds: new Set(),
  unreadDoneSessionIds: new Set(),
  sessionCache: new Map(),
  connected: false,
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
  selectedSkill: null,
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
  workflows: createWorkflowsDomainState(),
  goals: {},
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
  setSessions: (sessions) =>
    set((s) => ({
      sessions,
      ...reduceRunStatus(s, { activeRuns: activeRunsFromSessions(sessions) }),
    })),
  prependSession: (session) =>
    set((s) => {
      // Dedupe by id: the same session can arrive both from bootstrap's
      // /sessions load and from a session_created SSE event (or a stream
      // replay), and we must not render two rows for one session.
      if (s.sessions.some((existing) => existing.session_id === session.session_id)) {
        return {};
      }
      const sessions = [session, ...s.sessions];
      return {
        sessions,
        ...reduceRunStatus(s, { activeRuns: activeRunsFromSessions(sessions) }),
      };
    }),
  patchSession: (session) =>
    set((s) => {
      // session_activity delta for a channel that got new content. Merge
      // over the existing row (preserving poll-maintained runtime fields the
      // delta omits) and move it to the front — the sidebar renders in array
      // order, so front = most recent activity.
      const existing = s.sessions.find((row) => row.session_id === session.session_id);
      const merged = existing ? { ...existing, ...session } : session;
      const rest = s.sessions.filter((row) => row.session_id !== session.session_id);
      const sessions = [merged, ...rest];
      return {
        sessions,
        ...reduceRunStatus(s, { activeRuns: activeRunsFromSessions(sessions) }),
      };
    }),
  syncActiveRuns: (runs) => set((s) => reduceRunStatus(s, { activeRuns: runs })),
  markRunStarted: (runId, sessionId) =>
    set((s) => reduceRunStarted(s, { runId, sessionId })),
  markRunCompleted: (runId, sessionId) =>
    set((s) => reduceRunCompleted(s, { runId, sessionId })),
  clearForegroundRun: (runId, sessionId, options) =>
    set((s) =>
      reduceForegroundRunCleared(s, {
        runId,
        sessionId,
        clearApprovals: options?.clearApprovals,
        markBackgrounded: options?.markBackgrounded,
      }),
    ),
  setCurrentSession: (currentSessionId) =>
    set((s) => {
      // Navigating to a chat session always closes the area room — a room
      // stays open across the *current* session but must not silently trail
      // along underneath a session switch (e.g. opening an Area session from
      // its Activity inspector while a room is open).
      const areas = s.areas.openAreaKey ? reduceOpenArea(s.areas, null) : s.areas;
      let unread = s.unreadDoneSessionIds;
      if (currentSessionId && unread.has(currentSessionId)) {
        unread = new Set(unread);
        unread.delete(currentSessionId);
      }
      // Re-selecting the same session is a no-op for view state — the
      // global slots ARE that session's live state. Touching cache here
      // would clobber it with a stale snapshot.
      if (s.currentSessionId === currentSessionId) {
        const patch: Partial<State> = {};
        if (unread !== s.unreadDoneSessionIds) patch.unreadDoneSessionIds = unread;
        if (areas !== s.areas) patch.areas = areas;
        return patch;
      }
      // Snapshot outgoing session into cache so a switch-back can
      // restore the UI instantly and the SSE replay (with the bus
      // checkpoint watermark) only fills in what's new.
      const cache = new Map(s.sessionCache);
      if (s.currentSessionId) {
        cache.set(s.currentSessionId, snapshotSession(s));
      }
      const restored = currentSessionId ? cache.get(currentSessionId) : undefined;
      const view = restored ? normalizeCachedSessionState(restored) : blankSessionView();
      let sessionView = reduceSessionSelected(s.sessionView, currentSessionId);
      if (currentSessionId && restored) {
        sessionView = reduceCachePreviewRestored(view.sessionView, currentSessionId);
      }
      return {
        areas,
        sessionView,
        currentSessionId: sessionView.currentSessionId,
        sourceTurnId: null,
        sourceRefsRevision: s.sourceRefsRevision + 1,
        sessionCache: cache,
        messages: view.messages,
        order: view.order,
        usage: view.usage,
        editingId: view.editingId,
        activeActivityId: view.activeActivityId,
        currentRunId: view.currentRunId,
        thinkingRunId: view.thinkingRunId,
        thinkingStatus: view.thinkingStatus,
        running: view.running,
        compacting: view.compacting,
        sourceFocus: view.sourceFocus,
        pendingApprovals: view.pendingApprovals,
        pendingConnections: view.pendingConnections,
        reviewingApprovalToolId: view.reviewingApprovalToolId,
        queuedMessages: view.queuedMessages,
        pendingResume: view.pendingResume,
        stoppingRunId: view.stoppingRunId,
        ...(unread !== s.unreadDoneSessionIds ? { unreadDoneSessionIds: unread } : {}),
      };
    }),

  setHistory: (messages, page) =>
    set((s) => {
      const map = new Map<string, UiMessage>();
      const order: string[] = [];
      for (const m of messages) {
        map.set(m.id, m);
        order.push(m.id);
      }
      const normalized = normalizeActivityGroups(map, order, activeActivityIdFromMessages(messages));
      const persistedIds = new Set(order);
      const sessionView = s.currentSessionId
        ? reduceHistoryLoadSucceeded(s.sessionView, s.currentSessionId, page)
        : s.sessionView;
      return {
        sessionView,
        messages: normalized.messages,
        order: normalized.order,
        activeActivityId: normalized.activeActivityId,
        sourceTurnId:
          s.sourceTurnId && !normalized.messages.has(s.sourceTurnId) ? null : s.sourceTurnId,
        sourceRefsRevision: s.sourceRefsRevision + 1,
        thinkingRunId: null,
        thinkingStatus: null,
        ...reduceQueuedMessagesPersisted(s, persistedIds),
      };
    }),

  prependHistory: (messages, page) =>
    set((s) => {
      const map = new Map(s.messages);
      const ids: string[] = [];
      for (const m of messages) {
        const existing = map.get(m.id);
        map.set(m.id, mergePagedHistoryMessage(existing, m));
        if (!existing) ids.push(m.id);
      }
      const sessionView = s.currentSessionId
        ? reduceHistoryLoadSucceeded(s.sessionView, s.currentSessionId, page, "prepend")
        : s.sessionView;
      const order = [...ids, ...s.order];
      const normalized = normalizeActivityGroups(map, order, s.activeActivityId);
      return {
        sessionView,
        messages: normalized.messages,
        order: normalized.order,
        activeActivityId: normalized.activeActivityId,
        sourceRefsRevision: s.sourceRefsRevision + 1,
      };
    }),

  appendHistoryPage: (messages, page, activeActivityId) =>
    set((s) => {
      const map = new Map(s.messages);
      const ids: string[] = [];
      for (const m of messages) {
        const existing = map.get(m.id);
        map.set(m.id, mergePagedHistoryMessage(existing, m));
        if (!existing) ids.push(m.id);
      }
      const sessionView = s.currentSessionId
        ? reduceHistoryLoadSucceeded(s.sessionView, s.currentSessionId, page, "append")
        : s.sessionView;
      const order = [...s.order, ...ids];
      const normalized = normalizeActivityGroups(map, order, activeActivityId ?? s.activeActivityId);
      return {
        sessionView,
        messages: normalized.messages,
        order: normalized.order,
        activeActivityId: normalized.activeActivityId,
        sourceRefsRevision: s.sourceRefsRevision + 1,
      };
    }),

  setHistoryLoading: (direction, loading) =>
    set((s) => {
      const sessionView = reduceHistoryPageLoading(s.sessionView, direction, loading);
      return { sessionView };
    }),

  appendMessage: (message) =>
    set((s) => {
      const messages = new Map(s.messages);
      messages.set(message.id, message);
      if (s.messages.has(message.id)) return { messages };
      return {
        messages,
        order: [...s.order, message.id],
        sourceRefsRevision: s.sourceRefsRevision + 1,
      };
    }),

  insertMessageBefore: (message, beforeId) =>
    set((s) => {
      const messages = new Map(s.messages);
      messages.set(message.id, message);
      if (s.messages.has(message.id)) return { messages };

      const beforeIndex = beforeId ? s.order.indexOf(beforeId) : -1;
      if (beforeIndex < 0) {
        return {
          messages,
          order: [...s.order, message.id],
          sourceRefsRevision: s.sourceRefsRevision + 1,
        };
      }

      const order = s.order.slice();
      order.splice(beforeIndex, 0, message.id);
      return { messages, order, sourceRefsRevision: s.sourceRefsRevision + 1 };
    }),

  mutateMessage: (id, patch) =>
    set((s) => {
      const existing = s.messages.get(id);
      if (!existing) return s;
      const messages = new Map(s.messages);
      messages.set(id, { ...existing, ...patch });
      return { messages };
    }),

  upsertTodoList: (message, beforeId = null) =>
    set((s) => {
      const existing = s.messages.get(message.id);
      const messages = new Map(s.messages);
      messages.set(message.id, existing ? { ...existing, ...message } : message);
      if (existing) return { messages };

      const beforeIndex = beforeId ? s.order.indexOf(beforeId) : -1;
      if (beforeIndex < 0) return { messages, order: [...s.order, message.id] };

      const order = s.order.slice();
      order.splice(beforeIndex, 0, message.id);
      return { messages, order };
    }),

  truncateFrom: (id) =>
    set((s) => {
      const idx = s.order.indexOf(id);
      if (idx < 0) {
        console.warn("[arden] truncateFrom: id not found in order", { id, order: s.order });
        return s;
      }
      const keep = s.order.slice(0, idx);
      const messages = new Map<string, UiMessage>();
      for (const k of keep) {
        const m = s.messages.get(k);
        if (m) messages.set(k, m);
      }
      return {
        messages,
        order: keep,
        sourceTurnId: s.sourceTurnId && !messages.has(s.sourceTurnId) ? null : s.sourceTurnId,
        sourceRefsRevision: s.sourceRefsRevision + 1,
      };
    }),

  setConnected: (connected) => set({ connected }),
  setError: (error) => set({ error }),
  setDraft: (draft) => set({ draft }),
  setEditingId: (editingId) => set({ editingId }),
  resetUsage: () => set({ usage: initialUsage }),
  accumulateUsage: ({ prompt, completion, total, cache_read, cache_write, cost, contextInputTokens, messageCount }) =>
    set((s) => ({
      usage: {
        lastPrompt: contextInputTokens ?? inputTokens({ prompt, completion, total, cache_read, cache_write }),
        totalTokens: s.usage.totalTokens + (total ?? prompt + completion + (cache_read ?? 0) + (cache_write ?? 0)),
        totalCost: s.usage.totalCost + cost,
        messageCount: messageCount ?? s.usage.messageCount,
      },
    })),
  updateLiveUsage: ({ prompt, completion, total, cache_read, cache_write, cost, messageCount, scope }) =>
    set((s) => ({
      usage:
        scope === "tool"
          ? {
              ...s.usage,
              totalTokens: s.usage.totalTokens + (total ?? prompt + completion + (cache_read ?? 0) + (cache_write ?? 0)),
              totalCost: s.usage.totalCost + (cost ?? 0),
            }
          : {
              ...s.usage,
              lastPrompt: inputTokens({ prompt, completion, total, cache_read, cache_write }),
              messageCount: messageCount ?? s.usage.messageCount,
            },
    })),
  hydrateUsageSnapshot: ({ lastPrompt, messageCount }) =>
    set((s) => ({
      usage: { ...s.usage, lastPrompt, messageCount },
    })),

  openSettings: (tab) =>
    set((s) => ({
      settingsOpen: true,
      automationsOpen: false,
      automationTargetId: null,
      automationTargetRun: null,
      memoryOpen: false,
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
    set((s) => ({
      backgroundAgents: reduceBackgroundAgentsForSession(
        s.backgroundAgents,
        sessionId,
        agents,
      ),
    })),
  upsertBackgroundAgent: (agent) =>
    set((s) => ({
      backgroundAgents: reduceBackgroundAgentUpsert(s.backgroundAgents, agent),
    })),
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
  setPendingGoalProposal: (pendingGoalProposal) => set({ pendingGoalProposal }),

  setSkills: (skills) => set({ skills }),
  setCommandPickerOpen: (commandPickerOpen) => set({ commandPickerOpen, commandPickerIndex: 0 }),
  setCommandPickerIndex: (commandPickerIndex) => set({ commandPickerIndex }),
  setSelectedSkill: (selectedSkill) => set({ selectedSkill }),
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
      automationTargetId: taskId ?? null,
      automationTargetRun: taskId ? options?.run ?? null : null,
      modalOrigin: null,
    }),
  closeAutomations: () => set({ automationsOpen: false, automationTargetId: null, automationTargetRun: null }),
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
        : s.memoryVaultChanges;
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
  openMemory: () =>
    set({
      memoryOpen: true,
      settingsOpen: false,
      automationsOpen: false,
      automationTargetId: null,
      modalOrigin: null,
    }),
  closeMemory: () => set({ memoryOpen: false }),
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
