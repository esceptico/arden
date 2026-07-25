import type { HistoryMessage } from "@/api/chat";
import { apiWithConfig } from "@/api/core";
import type { ServerEvent, SessionRuntimeSnapshot } from "@/api/events";
import { getState, setState } from "@/stores";
import {
  clearReplayGapBlockForSession,
  lastEventSeqForSession,
  rehydrateWorkflows,
  setEventCursorForSession,
} from "@/stores/chat-stream";
import {
  isCurrentHistoryReplaceRequestVersion,
  nextHistoryReplaceRequestVersion,
  reduceHistoryLoadFailed,
  reduceHistoryLoadStarted,
} from "@/stores/session-view";
import {
  reduceForegroundRunCleared,
  reduceRunCompleted,
  reduceRunStarted,
} from "@/stores/run-lifecycle";
import {
  isTodoToolName,
} from "@/stores/transcript-projection";
import {
  cachedSessionFromHistory,
  historyMessagesToUi,
  pendingApprovalsFromRuntime,
  pendingConnectionsFromRuntime,
  areaHistoryResponse,
  runtimeView,
  type HistoryResponse,
} from "@/stores/history-response";
import { isForegroundRunStatus } from "@/lib/runStatus";
import { refreshChildAgents } from "@/actions/childAgents";
import { mergeSourceRefs, sourceRefsFromToolResultData } from "@/stores/sourceRefs";
import type { ActivityItem } from "@/stores/types";

export { historyMessagesToUi };

type HistoryLoadMode = "replace" | "prepend" | "append";
const pendingHistoryToolResultsBySession = new Map<
  string,
  Map<string, Partial<ActivityItem>>
>();
const cachedHistoryRefreshes = new Map<string, Promise<boolean>>();
const cachedHistoryRefreshedAt = new Map<string, number>();
const ACTIVE_SESSION_CACHE_REFRESH_MS = 5_000;

export interface LoadHistoryOptions {
  mode?: HistoryLoadMode;
  before?: string;
  after?: string;
  around?: string;
  aroundSeq?: number;
  limit?: number;
}

export interface LiveSessionSnapshot {
  runId?: string | null;
  sessionId: string;
  status?: string | null;
  backgrounded?: boolean;
}

export interface CachedHistoryRefreshOptions {
  force?: boolean;
  minIntervalMs?: number;
  now?: number;
}

function historyPath(sessionId: string, options: LoadHistoryOptions): string {
  const params = new URLSearchParams({ session_id: sessionId });
  if (options.limit) params.set("limit", String(options.limit));
  if (options.before) params.set("before", options.before);
  if (options.after) params.set("after", options.after);
  if (options.around) params.set("around", options.around);
  if (options.aroundSeq !== undefined) params.set("around_seq", String(options.aroundSeq));
  return `/session/history?${params.toString()}`;
}

function activeCacheSessionIds(
  runs: LiveSessionSnapshot[],
  currentSessionId: string | null,
): string[] {
  const ids = new Set<string>();
  for (const run of runs) {
    if (run.sessionId === currentSessionId) continue;
    if (
      run.backgrounded === true ||
      run.status === "backgrounded" ||
      isForegroundRunStatus(run.status)
    ) {
      ids.add(run.sessionId);
    }
  }
  return [...ids];
}

export function cachedHistoryRefreshSessionIds(runs: LiveSessionSnapshot[]): string[] {
  return activeCacheSessionIds(runs, getState().currentSessionId);
}

function historyBoundaryId(
  s: ReturnType<typeof getState>,
  edge: "first" | "last",
): string | null {
  const cursor = edge === "first" ? s.sessionView.historyBeforeCursor : s.sessionView.historyAfterCursor;
  if (cursor) return cursor;
  const order = edge === "first" ? s.order : [...s.order].reverse();
  for (const id of order) {
    const msg = s.messages.get(id);
    if (msg?.sourceMessageId) return msg.sourceMessageId;
  }
  return null;
}

function applyHistoryToolResults(sessionId: string, messages: HistoryMessage[]): void {
  const state = getState();
  const todoToolCallIds = new Set<string>();
  for (const msg of messages) {
    for (const toolCall of msg.tool_calls ?? []) {
      if (isTodoToolName(toolCall.name)) todoToolCallIds.add(toolCall.id);
    }
  }
  for (const msg of messages) {
    if (msg.role !== "tool" || !msg.tool_call_id) continue;
    if (todoToolCallIds.has(msg.tool_call_id)) continue;
    const sourceRefs = sourceRefsFromToolResultData(msg.data, msg.tool_call_id);
    const patch: Partial<ActivityItem> = {
      result: msg.content,
      status: "executed",
      ...(sourceRefs.length > 0 ? { sourceRefs } : {}),
    };
    if (state.mergeActivityItem(msg.tool_call_id, patch)) continue;
    let pending = pendingHistoryToolResultsBySession.get(sessionId);
    if (!pending) {
      pending = new Map();
      pendingHistoryToolResultsBySession.set(sessionId, pending);
    }
    const buffered = pending.get(msg.tool_call_id);
    const bufferedSourceRefs = mergeSourceRefs(buffered?.sourceRefs, patch.sourceRefs);
    pending.set(msg.tool_call_id, {
      ...buffered,
      ...patch,
      ...(bufferedSourceRefs === undefined ? {} : { sourceRefs: bufferedSourceRefs }),
    });
  }
}

function applyPendingHistoryToolResults(sessionId: string): void {
  const pending = pendingHistoryToolResultsBySession.get(sessionId);
  if (!pending) return;
  const state = getState();
  for (const [toolCallId, patch] of pending) {
    if (state.mergeActivityItem(toolCallId, patch)) {
      pending.delete(toolCallId);
    }
  }
  if (pending.size === 0) pendingHistoryToolResultsBySession.delete(sessionId);
}

function syncAutoForActiveRun(sessionId: string, value: boolean): void {
  const { config } = getState();
  void apiWithConfig(config, `/sessions/${sessionId}/auto`, {
    method: "POST",
    body: JSON.stringify({ value }),
  }).catch(() => {
    // Best-effort reconnect repair. The next user message still carries the
    // client-owned Auto value, so a transient sync miss is not fatal.
  });
}

function applyRuntimeSnapshot(sessionId: string, runtime: SessionRuntimeSnapshot | undefined): void {
  if (!runtime) return;
  setEventCursorForSession(
    sessionId,
    Math.max(runtime.checkpoint_seq, lastEventSeqForSession(sessionId) ?? 0),
  );
  const view = runtimeView(runtime.active_run?.run_id ?? null, runtime);
  if (getState().skipApprovals && view.hasForegroundRun) {
    syncAutoForActiveRun(sessionId, true);
  }
  setState((state) => {
    const lifecycle =
      view.currentRunId
        ? reduceRunStarted(state, { runId: view.currentRunId, sessionId })
        : reduceForegroundRunCleared(state, {
            runId: view.activeRunId ?? state.currentRunId,
            sessionId,
            markBackgrounded: view.markBackgrounded,
          });
    return {
      ...lifecycle,
      pendingApprovals: pendingApprovalsFromRuntime(runtime, view.hasForegroundRun, state.skipApprovals),
      pendingConnections: pendingConnectionsFromRuntime(runtime, view.hasForegroundRun),
      queuedMessages: state.queuedMessages,
    };
  });
}

export async function refreshCachedSessionHistory(
  sessionId: string,
  options: CachedHistoryRefreshOptions = {},
): Promise<boolean> {
  const now = options.now ?? Date.now();
  const minIntervalMs = options.minIntervalMs ?? ACTIVE_SESSION_CACHE_REFRESH_MS;
  const lastRefreshedAt = cachedHistoryRefreshedAt.get(sessionId) ?? 0;
  if (!options.force && lastRefreshedAt > 0 && now - lastRefreshedAt < minIntervalMs) return false;

  const existingRefresh = cachedHistoryRefreshes.get(sessionId);
  if (existingRefresh) return existingRefresh;

  const task = (async () => {
    const state = getState();
    const history = await apiWithConfig<HistoryResponse>(
      state.config,
      historyPath(sessionId, {}),
    );
    if (history.runtime) {
      setEventCursorForSession(
        sessionId,
        Math.max(history.runtime.checkpoint_seq, lastEventSeqForSession(sessionId) ?? 0),
      );
    }
    setState((current) => {
      if (current.currentSessionId === sessionId) return {};
      const sessionCache = new Map(current.sessionCache);
      sessionCache.set(
        sessionId,
        cachedSessionFromHistory(
          sessionId,
          history,
          sessionCache.get(sessionId),
          current.skipApprovals,
        ),
      );
      return { sessionCache };
    });
    cachedHistoryRefreshedAt.set(sessionId, Date.now());
    return true;
  })().catch((error) => {
    if (options.force) throw error;
    return false;
  }).finally(() => {
    cachedHistoryRefreshes.delete(sessionId);
  });

  cachedHistoryRefreshes.set(sessionId, task);
  return task;
}

export async function refreshCachedActiveSessionHistories(
  runs: LiveSessionSnapshot[],
  options: CachedHistoryRefreshOptions = {},
): Promise<void> {
  const sessionIds = cachedHistoryRefreshSessionIds(runs);
  await Promise.all(sessionIds.map((sessionId) => refreshCachedSessionHistory(sessionId, options)));
}

async function rehydrateSessionWorkflows(sessionId: string): Promise<void> {
  try {
    const { events } = await apiWithConfig<{ events: ServerEvent[] }>(
      getState().config,
      `/chat/${encodeURIComponent(sessionId)}/workflows`,
    );
    // Apply even if the user switched sessions while the fetch was in flight:
    // rows are keyed by session and token replays dedupe by seq, so this just
    // pre-warms the cards for when they switch back.
    if (events.length) rehydrateWorkflows(events);
  } catch {
    // Best-effort: if this fails the cards just won't rehydrate on reload.
  }
}

export async function loadHistory(sessionId: string, options: LoadHistoryOptions = {}): Promise<void> {
  const s = getState();
  const mode = options.mode ?? "replace";
  let replaceLoadVersion: number | null = null;
  if (mode === "replace") {
    replaceLoadVersion = nextHistoryReplaceRequestVersion(sessionId);
    const sessionView = reduceHistoryLoadStarted(s.sessionView, sessionId);
    setState({ sessionView });
  }

  let history: HistoryResponse;
  try {
    history = await apiWithConfig(s.config, historyPath(sessionId, options));
  } catch (error) {
    if (mode === "replace") {
      if (!isCurrentHistoryReplaceRequestVersion(sessionId, replaceLoadVersion ?? 0)) return;
      const state = getState();
      const sessionView = reduceHistoryLoadFailed(state.sessionView, sessionId);
      setState({ sessionView });
    }
    throw error;
  }

  if (getState().currentSessionId !== sessionId) return;
  if (
    mode === "replace" &&
    !isCurrentHistoryReplaceRequestVersion(sessionId, replaceLoadVersion ?? 0)
  ) {
    return;
  }
  if (mode === "replace") {
    clearReplayGapBlockForSession(sessionId);
    pendingHistoryToolResultsBySession.delete(sessionId);
  }

  const { messages, runtime, page, usage } = history;
  const isNewestPage = mode !== "prepend" && page?.has_more_after !== true;
  const { activeForegroundRunId, activeActivityId, items } = areaHistoryResponse(
    history,
    isNewestPage,
    getState(),
  );
  if (mode === "prepend") {
    s.prependHistory(items, page);
    applyHistoryToolResults(sessionId, messages);
  } else if (mode === "append") {
    s.appendHistoryPage(items, page, isNewestPage ? activeActivityId : null);
    applyHistoryToolResults(sessionId, messages);
  } else {
    s.setHistory(items, page);
    setState({ activeActivityId });
    // Only hydrate the budget snapshot on a fresh load — paging in older /
    // newer chunks doesn't change "what the agent's current context size
    // looks like" so we leave the dial alone.
    if (usage) {
      s.hydrateUsageSnapshot({
        lastPrompt: usage.last_input_tokens,
        messageCount: usage.message_count,
      });
    }
    // The workflows (FleetView) domain is in-memory, built only from live SSE
    // events, so a fresh transcript load leaves it empty and the cards collapse.
    // Replay the persisted workflow events to rebuild it. Non-blocking so the
    // transcript renders immediately; guarded against a session switch.
    void rehydrateSessionWorkflows(sessionId);
    // Same shape for the subagent roster: it's built from live events + a 5s
    // poll, so a freshly-loaded session shows an empty roster until the first
    // poll tick. Rehydrate it now (idempotent with the poll) to close that gap.
    void refreshChildAgents(sessionId);
  }
  applyPendingHistoryToolResults(sessionId);
  if (mode === "replace") {
    if (runtime) {
      applyRuntimeSnapshot(sessionId, runtime);
    } else {
      setState((state) =>
        activeForegroundRunId
          ? reduceRunStarted(state, { runId: activeForegroundRunId, sessionId })
          : reduceRunCompleted(state, { runId: state.currentRunId, sessionId }),
      );
    }
  } else if (activeForegroundRunId) {
    setState((state) => reduceRunStarted(state, { runId: activeForegroundRunId, sessionId }));
  }
}

export async function loadOlderHistory(): Promise<void> {
  const s = getState();
  if (!s.currentSessionId || !s.sessionView.historyHasMoreBefore || s.sessionView.historyLoadingBefore) return;
  const before = historyBoundaryId(s, "first");
  if (!before) return;
  s.setHistoryLoading("before", true);
  try {
    await loadHistory(s.currentSessionId, { mode: "prepend", before });
  } finally {
    getState().setHistoryLoading("before", false);
  }
}

export async function loadNewerHistory(): Promise<void> {
  const s = getState();
  if (!s.currentSessionId || !s.sessionView.historyHasMoreAfter || s.sessionView.historyLoadingAfter) return;
  const after = historyBoundaryId(s, "last");
  if (!after) return;
  s.setHistoryLoading("after", true);
  try {
    await loadHistory(s.currentSessionId, { mode: "append", after });
  } finally {
    getState().setHistoryLoading("after", false);
  }
}
