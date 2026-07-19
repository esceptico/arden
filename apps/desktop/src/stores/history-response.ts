import type { HistoryMessage, HistoryPage } from "@/api/chat";
import type { SessionRuntimeSnapshot } from "@/api/events";
import { isForegroundRunStatus } from "@/lib/runStatus";
import {
  newestHistoryActivityId,
  rebuildTranscriptFromHistory,
} from "@/stores/transcript-projection";
import {
  blankSessionView,
  initialUsage,
  normalizeActivityGroups,
} from "@/stores/session-cache";
import {
  createInitialSessionViewState,
  reduceHistoryLoadSucceeded,
} from "@/stores/session-view";
import { mergeSourceRefs } from "@/stores/sourceRefs";
import type {
  ActivityItem,
  ApprovalState,
  CachedSessionState,
  QueuedMessage,
  UiMessage,
} from "@/stores/types";

export interface HistoryResponse {
  messages: HistoryMessage[];
  active_run_id: string | null;
  runtime?: SessionRuntimeSnapshot;
  page?: HistoryPage;
  usage?: { last_input_tokens: number; message_count: number };
}

export interface HistoryProjection {
  activeForegroundRunId: string | null;
  activeActivityId: string | null;
  items: UiMessage[];
}

export interface RuntimeView {
  hasForegroundRun: boolean;
  currentRunId: string | null;
  activeRunId: string | null;
  markBackgrounded: boolean;
}

export function historyMessagesToUi(
  messages: HistoryMessage[],
  activeRunId: string | null,
  options: { isNewestPage?: boolean } = {},
): UiMessage[] {
  const isNewestPage = options.isNewestPage ?? true;
  const items = rebuildTranscriptFromHistory(messages, { activeRunId, isNewestPage });

  // If the server has an active run for this session, the latest user
  // turn is still in flight. Clear its `endedAt` so TurnGroup doesn't
  // collapse it under "Worked for Xs" — the SSE replay + live events
  // build the in-flight UI on top of the history.
  if (activeRunId && isNewestPage) {
    for (let i = items.length - 1; i >= 0; i--) {
      const it = items[i];
      if (it.role === "user" && it.turn) {
        it.turn = { ...it.turn, endedAt: null, durationMs: null };
        break;
      }
    }
  }

  return items;
}

export function areaHistoryResponse(
  history: HistoryResponse,
  isNewestPage: boolean,
  existing?: Pick<CachedSessionState, "messages" | "order" | "activeActivityId">,
): HistoryProjection {
  const activeForegroundRunId = foregroundActiveRunId(history.active_run_id, history.runtime);
  let rawItems = historyMessagesToUi(history.messages, activeForegroundRunId, { isNewestPage });
  if (activeForegroundRunId && isNewestPage && existing) {
    rawItems = mergeActiveLiveProjection(rawItems, existing);
  }
  const rawActiveActivityId = activeForegroundRunId ? newestHistoryActivityId(rawItems) : null;
  const { items, activeActivityId } = normalizeHistoryItems(rawItems, rawActiveActivityId);
  return { activeForegroundRunId, activeActivityId, items };
}

export function runtimeView(
  activeForegroundRunId: string | null,
  runtime: SessionRuntimeSnapshot | undefined,
): RuntimeView {
  const activeRun = runtime?.active_run;
  const hasForegroundRun = runtime
    ? isForegroundRunStatus(activeRun?.status)
    : Boolean(activeForegroundRunId);
  return {
    hasForegroundRun,
    currentRunId: hasForegroundRun ? activeRun?.run_id ?? activeForegroundRunId : null,
    activeRunId: activeRun?.run_id ?? activeForegroundRunId,
    markBackgrounded: activeRun?.status === "backgrounded",
  };
}

export function pendingApprovalsFromRuntime(
  runtime: SessionRuntimeSnapshot | undefined,
  hasForegroundRun: boolean,
  skipApprovals: boolean,
): ApprovalState[] {
  if (!runtime || skipApprovals || !hasForegroundRun) return [];
  return runtime.pending_approvals.map((approval) => ({
    toolId: approval.tool_id,
    toolName: approval.tool_name,
    preview: approval.preview ?? undefined,
    diff: approval.diff ?? undefined,
    status: "pending" as const,
  }));
}

export function pendingConnectionsFromRuntime(
  runtime: SessionRuntimeSnapshot | undefined,
  hasForegroundRun: boolean,
) {
  if (!runtime || !hasForegroundRun) return [];
  return (runtime.pending_connections ?? []).map((connection) => ({
    runId: connection.run_id,
    toolId: connection.tool_id,
    integrationId: connection.integration_id,
    connectionId: connection.connection_id,
    label: connection.label,
    reason: connection.reason,
    detail: connection.detail,
    capability: connection.capability,
    action: connection.action,
    settingsTab: connection.settings_tab,
    requiredScopes: connection.required_scopes,
    source: connection.source,
  }));
}

export function queuedMessagesFromRuntime(
  runtime: SessionRuntimeSnapshot | undefined,
  hasForegroundRun: boolean,
): QueuedMessage[] {
  if (!runtime || !hasForegroundRun) return [];
  return runtime.queued_messages.map((message) => ({
    clientId: message.client_id,
    text: message.text,
    images: message.images,
    status: message.status,
    enqueuedAt: message.enqueued_at ? Date.parse(message.enqueued_at) : Date.now(),
  }));
}

export function cachedSessionFromHistory(
  sessionId: string,
  history: HistoryResponse,
  existing: CachedSessionState | undefined,
  skipApprovals: boolean,
): CachedSessionState {
  const base = existing ?? blankSessionView();
  const { runtime, page, usage } = history;
  const { activeForegroundRunId, activeActivityId, items } = areaHistoryResponse(
    history,
    page?.has_more_after !== true,
    existing,
  );
  const view = runtimeView(activeForegroundRunId, runtime);
  const map = new Map<string, UiMessage>();
  const order: string[] = [];
  for (const item of items) {
    // `order` is the render-key list — guard against a duplicate id slipping
    // through (it would trigger React's "two children with the same key").
    if (!map.has(item.id)) order.push(item.id);
    map.set(item.id, { ...item, suppressEntryMotion: true });
  }

  const sessionView = reduceHistoryLoadSucceeded(
    { ...createInitialSessionViewState(), currentSessionId: sessionId },
    sessionId,
    page,
  );

  return {
    ...base,
    sessionView,
    messages: map,
    order,
    running: view.hasForegroundRun,
    currentRunId: view.currentRunId,
    usage: usage
      ? {
          ...(base.usage ?? initialUsage),
          lastPrompt: usage.last_input_tokens,
          messageCount: usage.message_count,
        }
      : base.usage,
    activeActivityId,
    compacting: false,
    pendingApprovals: pendingApprovalsFromRuntime(runtime, view.hasForegroundRun, skipApprovals),
    pendingConnections: pendingConnectionsFromRuntime(runtime, view.hasForegroundRun),
    reviewingApprovalToolId: null,
    queuedMessages: queuedMessagesFromRuntime(runtime, view.hasForegroundRun),
  };
}

function mergeActiveLiveProjection(
  historyItems: UiMessage[],
  existing: Pick<CachedSessionState, "messages" | "order" | "activeActivityId">,
): UiMessage[] {
  const historyIds = new Set(historyItems.map((item) => item.id));
  const historyById = new Map(historyItems.map((item) => [item.id, item]));
  const out = historyItems.slice();
  let targetActivityId = newestHistoryActivityId(out);

  for (const id of existing.order) {
    const cached = existing.messages.get(id);
    if (!cached) continue;

    if (
      cached.role === "activity" &&
      cached.activity &&
      shouldPreserveCachedActivity(cached, existing.activeActivityId)
    ) {
      if (!targetActivityId) {
        out.push({ ...cached, suppressEntryMotion: true });
        targetActivityId = cached.id;
        historyIds.add(cached.id);
        historyById.set(cached.id, cached);
        continue;
      }

      const target = historyById.get(targetActivityId);
      if (!target?.activity) continue;
      const merged: UiMessage = {
        ...target,
        suppressEntryMotion: true,
        activity: {
          ...target.activity,
          label: target.activity.done && cached.activity.done ? target.activity.label : "Calling",
          done: target.activity.done && cached.activity.done,
          items: mergeActivityItems(target.activity.items, cached.activity.items),
        },
      };
      historyById.set(targetActivityId, merged);
      const index = out.findIndex((item) => item.id === targetActivityId);
      if (index >= 0) out[index] = merged;
      continue;
    }

    if (!historyIds.has(id) && isUnsavedLiveMessage(cached)) {
      out.push({ ...cached, suppressEntryMotion: true });
      historyIds.add(id);
      historyById.set(id, cached);
    }
  }

  return out;
}

function shouldPreserveCachedActivity(message: UiMessage, activeActivityId: string | null): boolean {
  if (!message.activity) return false;
  if (message.id === activeActivityId) return true;
  if (!message.activity.done) return true;
  return message.activity.items.some((item) => item.status === "ongoing" || item.result == null);
}

function isUnsavedLiveMessage(message: UiMessage): boolean {
  if (message.sourceMessageId) return false;
  return message.role === "assistant" || message.role === "reasoning" || message.role === "status";
}

function mergeActivityItems(historyItems: ActivityItem[], liveItems: ActivityItem[]): ActivityItem[] {
  const byId = new Map<string, ActivityItem>();
  const ids: string[] = [];
  for (const item of historyItems) {
    byId.set(item.id, item);
    ids.push(item.id);
  }
  for (const item of liveItems) {
    const existing = byId.get(item.id);
    if (!existing) {
      byId.set(item.id, item);
      ids.push(item.id);
      continue;
    }
    byId.set(item.id, mergeActivityItem(existing, item));
  }
  return ids.flatMap((id) => {
    const item = byId.get(id);
    return item ? [item] : [];
  });
}

function mergeActivityItem(historyItem: ActivityItem, liveItem: ActivityItem): ActivityItem {
  const hasHistoryResult = historyItem.result != null && historyItem.result !== "";
  return {
    ...historyItem,
    ...liveItem,
    result: liveItem.result ?? historyItem.result,
    status:
      hasHistoryResult && liveItem.status === "ongoing"
        ? historyItem.status
        : liveItem.status ?? historyItem.status,
    durationMs: liveItem.durationMs ?? historyItem.durationMs,
    error: liveItem.error ?? historyItem.error,
    usage: liveItem.usage ?? historyItem.usage,
    cost: liveItem.cost ?? historyItem.cost,
    sourceRefs: mergeSourceRefs(historyItem.sourceRefs, liveItem.sourceRefs),
  };
}

function foregroundActiveRunId(
  activeRunId: string | null,
  runtime: SessionRuntimeSnapshot | undefined,
): string | null {
  if (!runtime) return activeRunId;
  return isForegroundRunStatus(runtime.active_run?.status) ? activeRunId : null;
}

function normalizeHistoryItems(
  items: UiMessage[],
  activeActivityId: string | null,
): { items: UiMessage[]; activeActivityId: string | null } {
  const map = new Map<string, UiMessage>();
  const order: string[] = [];
  for (const item of items) {
    map.set(item.id, item);
    order.push(item.id);
  }
  const normalized = normalizeActivityGroups(map, order, activeActivityId);
  return {
    items: normalized.order.flatMap((id) => {
      const item = normalized.messages.get(id);
      return item ? [item] : [];
    }),
    activeActivityId: normalized.activeActivityId,
  };
}
