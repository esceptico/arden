import type { StoreApi } from "zustand";
import { isActivityContinuationMessage } from "@/lib/messageVisibility";
import { isLiveRunStatus } from "@/lib/runStatus";
import type { ActivityItem, Actions, State, UiMessage } from "@/stores/types";
import { mergeSourceRefs } from "@/stores/sourceRefs";
import {
  blankSessionView,
  initialUsage,
  normalizeActivityGroups,
  normalizeCachedSessionState,
  snapshotSession,
} from "@/stores/session-cache";
import {
  reduceCachePreviewRestored,
  reduceHistoryLoadSucceeded,
  reduceHistoryPageLoading,
  reduceSessionSelected,
} from "@/stores/session-view";
import { reduceOpenArea } from "@/stores/areas-domain";
import {
  reduceForegroundRunCleared,
  reduceQueuedMessagesPersisted,
  reduceRunCompleted,
  reduceRunStarted,
  reduceRunStatus,
} from "@/stores/run-lifecycle";

type StoreSet = StoreApi<State & Actions>["setState"];

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

export function createConversationActions(set: StoreSet) {
  return {
    setSessions: (sessions: Parameters<Actions["setSessions"]>[0]) =>
      set((state) => ({
        sessions,
        ...reduceRunStatus(state, { activeRuns: activeRunsFromSessions(sessions) }),
      })),
    prependSession: (session: Parameters<Actions["prependSession"]>[0]) =>
      set((state) => {
        if (state.sessions.some((existing) => existing.session_id === session.session_id)) return {};
        const sessions = [session, ...state.sessions];
        return {
          sessions,
          ...reduceRunStatus(state, { activeRuns: activeRunsFromSessions(sessions) }),
        };
      }),
    patchSession: (session: Parameters<Actions["patchSession"]>[0]) =>
      set((state) => {
        const existing = state.sessions.find((row) => row.session_id === session.session_id);
        const merged = existing ? { ...existing, ...session } : session;
        const rest = state.sessions.filter((row) => row.session_id !== session.session_id);
        const sessions = [merged, ...rest];
        return {
          sessions,
          ...reduceRunStatus(state, { activeRuns: activeRunsFromSessions(sessions) }),
        };
      }),
    syncActiveRuns: (runs: Parameters<Actions["syncActiveRuns"]>[0]) =>
      set((state) => reduceRunStatus(state, { activeRuns: runs })),
    markRunStarted: (
      runId: Parameters<Actions["markRunStarted"]>[0],
      sessionId: Parameters<Actions["markRunStarted"]>[1],
    ) =>
      set((state) => reduceRunStarted(state, { runId, sessionId })),
    markRunCompleted: (
      runId: Parameters<Actions["markRunCompleted"]>[0],
      sessionId: Parameters<Actions["markRunCompleted"]>[1],
    ) =>
      set((state) => reduceRunCompleted(state, { runId, sessionId })),
    clearForegroundRun: (
      runId: Parameters<Actions["clearForegroundRun"]>[0],
      sessionId: Parameters<Actions["clearForegroundRun"]>[1],
      options?: NonNullable<Parameters<Actions["clearForegroundRun"]>[2]>,
    ) =>
      set((state) => reduceForegroundRunCleared(state, {
        runId,
        sessionId,
        clearApprovals: options?.clearApprovals,
        markBackgrounded: options?.markBackgrounded,
      })),
    setCurrentSession: (currentSessionId: string | null) =>
      set((state) => {
        const areas = state.areas.openAreaKey ? reduceOpenArea(state.areas, null) : state.areas;
        let unread = state.unreadDoneSessionIds;
        if (currentSessionId && unread.has(currentSessionId)) {
          unread = new Set(unread);
          unread.delete(currentSessionId);
        }
        if (state.currentSessionId === currentSessionId) {
          const patch: Partial<State> = {};
          if (unread !== state.unreadDoneSessionIds) patch.unreadDoneSessionIds = unread;
          if (areas !== state.areas) patch.areas = areas;
          if (currentSessionId && state.pendingNewChatAreaId !== null) patch.pendingNewChatAreaId = null;
          return patch;
        }
        const cache = new Map(state.sessionCache);
        if (state.currentSessionId) cache.set(state.currentSessionId, snapshotSession(state));
        const restored = currentSessionId ? cache.get(currentSessionId) : undefined;
        const view = restored ? normalizeCachedSessionState(restored) : blankSessionView();
        let sessionView = reduceSessionSelected(state.sessionView, currentSessionId);
        if (currentSessionId && restored) {
          sessionView = reduceCachePreviewRestored(view.sessionView, currentSessionId);
        }
        return {
          areas,
          sessionView,
          currentSessionId: sessionView.currentSessionId,
          pendingNewChatAreaId: currentSessionId ? null : state.pendingNewChatAreaId,
          sourceTurnId: null,
          sourceRefsRevision: state.sourceRefsRevision + 1,
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
          ...(unread !== state.unreadDoneSessionIds ? { unreadDoneSessionIds: unread } : {}),
        };
      }),
    beginNewChatDraft: (pendingNewChatAreaId: string | null) =>
      set((state) => ({
        pendingNewChatAreaId,
        pendingNewChatDraftId: state.pendingNewChatDraftId + 1,
      })),
    setHistory: (
      messages: Parameters<Actions["setHistory"]>[0],
      page: Parameters<Actions["setHistory"]>[1],
    ) => set((state) => {
      const map = new Map<string, UiMessage>();
      const order: string[] = [];
      for (const message of messages) {
        map.set(message.id, message);
        order.push(message.id);
      }
      const normalized = normalizeActivityGroups(map, order, activeActivityIdFromMessages(messages));
      const persistedIds = new Set(order);
      const sessionView = state.currentSessionId
        ? reduceHistoryLoadSucceeded(state.sessionView, state.currentSessionId, page)
        : state.sessionView;
      return {
        sessionView,
        messages: normalized.messages,
        order: normalized.order,
        activeActivityId: normalized.activeActivityId,
        sourceTurnId: state.sourceTurnId && !normalized.messages.has(state.sourceTurnId) ? null : state.sourceTurnId,
        sourceRefsRevision: state.sourceRefsRevision + 1,
        thinkingRunId: null,
        thinkingStatus: null,
        ...reduceQueuedMessagesPersisted(state, persistedIds),
      };
    }),
    prependHistory: (
      messages: Parameters<Actions["prependHistory"]>[0],
      page: Parameters<Actions["prependHistory"]>[1],
    ) => set((state) => {
      const map = new Map(state.messages);
      const ids: string[] = [];
      for (const message of messages) {
        const existing = map.get(message.id);
        map.set(message.id, mergePagedHistoryMessage(existing, message));
        if (!existing) ids.push(message.id);
      }
      const sessionView = state.currentSessionId
        ? reduceHistoryLoadSucceeded(state.sessionView, state.currentSessionId, page, "prepend")
        : state.sessionView;
      const normalized = normalizeActivityGroups(map, [...ids, ...state.order], state.activeActivityId);
      return {
        sessionView,
        messages: normalized.messages,
        order: normalized.order,
        activeActivityId: normalized.activeActivityId,
        sourceRefsRevision: state.sourceRefsRevision + 1,
      };
    }),
    appendHistoryPage: (
      messages: Parameters<Actions["appendHistoryPage"]>[0],
      page: Parameters<Actions["appendHistoryPage"]>[1],
      activeActivityId: Parameters<Actions["appendHistoryPage"]>[2],
    ) => set((state) => {
      const map = new Map(state.messages);
      const ids: string[] = [];
      for (const message of messages) {
        const existing = map.get(message.id);
        map.set(message.id, mergePagedHistoryMessage(existing, message));
        if (!existing) ids.push(message.id);
      }
      const sessionView = state.currentSessionId
        ? reduceHistoryLoadSucceeded(state.sessionView, state.currentSessionId, page, "append")
        : state.sessionView;
      const normalized = normalizeActivityGroups(map, [...state.order, ...ids], activeActivityId ?? state.activeActivityId);
      return {
        sessionView,
        messages: normalized.messages,
        order: normalized.order,
        activeActivityId: normalized.activeActivityId,
        sourceRefsRevision: state.sourceRefsRevision + 1,
      };
    }),
    setHistoryLoading: (
      direction: Parameters<Actions["setHistoryLoading"]>[0],
      loading: boolean,
    ) => set((state) => ({
      sessionView: reduceHistoryPageLoading(state.sessionView, direction, loading),
    })),
    appendMessage: (message: UiMessage) => set((state) => {
      const messages = new Map(state.messages);
      messages.set(message.id, message);
      if (state.messages.has(message.id)) return { messages };
      return {
        messages,
        order: [...state.order, message.id],
        sourceRefsRevision: state.sourceRefsRevision + 1,
      };
    }),
    insertMessageBefore: (message: UiMessage, beforeId: string | null) => set((state) => {
      const messages = new Map(state.messages);
      messages.set(message.id, message);
      if (state.messages.has(message.id)) return { messages };
      const beforeIndex = beforeId ? state.order.indexOf(beforeId) : -1;
      if (beforeIndex < 0) {
        return { messages, order: [...state.order, message.id], sourceRefsRevision: state.sourceRefsRevision + 1 };
      }
      const order = state.order.slice();
      order.splice(beforeIndex, 0, message.id);
      return { messages, order, sourceRefsRevision: state.sourceRefsRevision + 1 };
    }),
    mutateMessage: (id: string, patch: Partial<UiMessage>) => set((state) => {
      const existing = state.messages.get(id);
      if (!existing) return state;
      const messages = new Map(state.messages);
      messages.set(id, { ...existing, ...patch });
      return { messages };
    }),
    upsertTodoList: (
      message: Parameters<Actions["upsertTodoList"]>[0],
      beforeId: string | null = null,
    ) => set((state) => {
      const existing = state.messages.get(message.id);
      const messages = new Map(state.messages);
      messages.set(message.id, existing ? { ...existing, ...message } : message);
      if (existing) return { messages };
      const beforeIndex = beforeId ? state.order.indexOf(beforeId) : -1;
      if (beforeIndex < 0) return { messages, order: [...state.order, message.id] };
      const order = state.order.slice();
      order.splice(beforeIndex, 0, message.id);
      return { messages, order };
    }),
    truncateFrom: (id: string) => set((state) => {
      const index = state.order.indexOf(id);
      if (index < 0) {
        console.warn("[arden] truncateFrom: id not found in order", { id, order: state.order });
        return state;
      }
      const order = state.order.slice(0, index);
      const messages = new Map<string, UiMessage>();
      for (const key of order) {
        const message = state.messages.get(key);
        if (message) messages.set(key, message);
      }
      return {
        messages,
        order,
        sourceTurnId: state.sourceTurnId && !messages.has(state.sourceTurnId) ? null : state.sourceTurnId,
        sourceRefsRevision: state.sourceRefsRevision + 1,
      };
    }),
    setConnected: (connected: boolean) => set({ connected }),
    setServerWarmup: (serverWarmup: State["serverWarmup"]) => set({ serverWarmup }),
    setError: (error: string | null) => set({ error }),
    setDraft: (draft: string) => set({ draft }),
    setEditingId: (editingId: string | null) => set({ editingId }),
    resetUsage: () => set((state) => ({
      usage: { ...initialUsage, contextBudget: state.usage.contextBudget },
    })),
    accumulateUsage: (usage: Parameters<Actions["accumulateUsage"]>[0]) => set((state) => ({
      usage: {
        contextInputTokens: usage.contextInputTokens ?? inputTokens(usage),
        observedPromptTokens: state.usage.observedPromptTokens + usage.prompt,
        observedCompletionTokens: state.usage.observedCompletionTokens + usage.completion,
        observedCacheReadTokens: state.usage.observedCacheReadTokens + (usage.cache_read ?? 0),
        observedCacheWriteTokens: state.usage.observedCacheWriteTokens + (usage.cache_write ?? 0),
        totalTokens: state.usage.totalTokens + (usage.total ?? usage.prompt + usage.completion + (usage.cache_read ?? 0) + (usage.cache_write ?? 0)),
        totalCost: state.usage.totalCost + (usage.exclusive_cost ?? usage.cost),
        messageCount: usage.messageCount ?? state.usage.messageCount,
        contextBudget: state.usage.contextBudget,
      },
    })),
    updateLiveUsage: (usage: Parameters<Actions["updateLiveUsage"]>[0]) => set((state) => ({
      usage: usage.scope === "tool"
        ? {
            ...state.usage,
            observedPromptTokens: state.usage.observedPromptTokens + usage.prompt,
            observedCompletionTokens: state.usage.observedCompletionTokens + usage.completion,
            observedCacheReadTokens: state.usage.observedCacheReadTokens + (usage.cache_read ?? 0),
            observedCacheWriteTokens: state.usage.observedCacheWriteTokens + (usage.cache_write ?? 0),
            totalTokens: state.usage.totalTokens + (usage.total ?? usage.prompt + usage.completion + (usage.cache_read ?? 0) + (usage.cache_write ?? 0)),
            totalCost: state.usage.totalCost + (usage.cost ?? 0),
          }
        : {
            ...state.usage,
            contextInputTokens: usage.contextInputTokens ?? inputTokens(usage),
            messageCount: usage.messageCount ?? state.usage.messageCount,
          },
    })),
    hydrateUsageSnapshot: (usage: Parameters<Actions["hydrateUsageSnapshot"]>[0]) => set((state) => ({
      usage: {
        ...state.usage,
        contextInputTokens: usage.contextInputTokens,
        messageCount: usage.messageCount,
        contextBudget: usage.contextBudget === undefined ? state.usage.contextBudget : usage.contextBudget,
      },
    })),
  };
}
