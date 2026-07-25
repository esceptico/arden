import { isActivityContinuationMessage } from "@/lib/messageVisibility";
import { projectToolCallArguments } from "@/lib/toolCallMetadata";
import { getState, type ActivityItem, type TodoListState, type UiMessage } from "@/stores/index";
import type { ChildAgentRef } from "@/stores/types";
import { mergeSourceRefs } from "@/stores/sourceRefs";
import {
  TODO_TOOL_NAME,
  type PendingToolCall,
  type ProjectionContext,
  type TaskLifecycleEvent,
  type TranscriptProjectionRuntime,
  type TranscriptProjectionState,
} from "@/stores/transcript-projection-types";

// Keep any non-"tool" semantic kind ("agent", "workflow") on the activity item;
// only plain tools collapse to undefined. The trace branches on this to render
// agent rows and workflow cards instead of generic tool rows.
export function liftedKind(kind: string | undefined): string | undefined {
  return kind && kind !== "tool" ? kind : undefined;
}

export function workflowIdFromData(data: unknown): string | undefined {
  const wid = (data as { workflow_id?: unknown } | null | undefined)?.workflow_id;
  return typeof wid === "string" ? wid : undefined;
}

export function isTodoToolName(name: string | null | undefined): boolean {
  return name === TODO_TOOL_NAME;
}

export function childAgentFromTaskEvent(event: TaskLifecycleEvent, status: string): ChildAgentRef | undefined {
  if (!event.child_run_id && !event.child_session_id && !event.agent_type) return undefined;
  return {
    childRunId: event.child_run_id || event.task_id,
    childSessionId: event.child_session_id || undefined,
    parentToolCallId: event.parent_tool_call_id || undefined,
    agentType: event.agent_type || "sub_agent",
    wait: typeof event.wait === "boolean" ? event.wait : true,
    status,
  };
}

export function reopenNewestHistoryActivity(items: UiMessage[]): void {
  const item = newestHistoryActivity(items);
  if (!item?.activity) return;
  item.activity = {
    ...item.activity,
    done: false,
    label: "Calling",
    items: item.activity.items.map((activityItem) =>
      activityItem.result == null
        ? { ...activityItem, status: "ongoing" as const }
        : activityItem,
    ),
  };
}

export function newestHistoryActivityId(items: UiMessage[]): string | null {
  return newestHistoryActivity(items)?.id ?? null;
}

function newestHistoryActivity(items: UiMessage[]): UiMessage | null {
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i];
    if (isActivityContinuationMessage(item)) continue;
    return item.role === "activity" && item.activity ? item : null;
  }
  return null;
}

export function todoStateFromArgs(argsJson: string): TodoListState | null {
  try {
    const parsed = JSON.parse(argsJson || "{}");
    if (!parsed || typeof parsed !== "object") return null;
    const items = normalizeTodoItems((parsed as { items?: unknown }).items);
    if (items.length === 0) return null;
    const explanation = (parsed as { explanation?: unknown }).explanation;
    return {
      items,
      explanation: typeof explanation === "string" ? explanation : null,
    };
  } catch {
    return null;
  }
}

export function normalizeTodoItems(rawItems: unknown): TodoListState["items"] {
  if (!Array.isArray(rawItems)) return [];
  return rawItems.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const content = (item as { content?: unknown }).content;
    const status = (item as { status?: unknown }).status;
    if (typeof content !== "string" || content.trim().length === 0) return [];
    if (status !== "pending" && status !== "in_progress" && status !== "completed") return [];
    return [{ content, status }];
  });
}

export function createProjectionContext(
  state: TranscriptProjectionState,
  runtime?: TranscriptProjectionRuntime,
): ProjectionContext {
  const context: ProjectionContext = {
    state: selectTranscriptProjectionState(state),
    runtime,
    update(next) {
      context.state = selectTranscriptProjectionState(next);
      return context.state;
    },
    latest() {
      return selectTranscriptProjectionState(runtime?.getProjectionState() ?? context.state);
    },
    commit(next) {
      context.state = selectTranscriptProjectionState(next);
      runtime?.setProjectionState(context.state);
      return context.state;
    },
  };
  return context;
}

export function selectTranscriptProjectionState(state: TranscriptProjectionState): TranscriptProjectionState {
  return {
    pendingResultPatches: state.pendingResultPatches,
    pendingToolCalls: state.pendingToolCalls,
    pendingActivityReplaySeqs: state.pendingActivityReplaySeqs,
    delayedActivityTimers: state.delayedActivityTimers,
    activeAssistantMessageId: state.activeAssistantMessageId,
    nextItemRenderAt: state.nextItemRenderAt,
  };
}

export function assistantIdFrom(event: { message_id?: string }): string {
  return event.message_id || crypto.randomUUID();
}

export function setActiveAssistantMessageId(
  context: ProjectionContext,
  activeAssistantMessageId: string | null,
): void {
  context.update({ ...context.state, activeAssistantMessageId });
}

export function ensureAssistantMessage(
  context: ProjectionContext,
  id: string,
  startedAt: number,
  suppressEntryMotion?: boolean,
): void {
  const state = getState();
  setActiveAssistantMessageId(context, id);
  const existing = state.messages.get(id);
  if (existing?.role === "assistant") return;

  state.appendMessage({
    id,
    role: "assistant",
    content: "",
    turn: { startedAt, endedAt: null, durationMs: null },
    suppressEntryMotion,
  });
}

export function appendAssistantDelta(
  context: ProjectionContext,
  id: string,
  delta: string,
  startedAt: number,
  suppressEntryMotion?: boolean,
): void {
  const state = getState();
  setActiveAssistantMessageId(context, id);
  const existing = state.messages.get(id);
  if (existing?.role === "assistant") {
    if (existing.content.trim().length === 0 && delta.trim().length > 0) {
      endActivity(state);
    }
    state.mutateMessage(id, { content: existing.content + delta });
    return;
  }

  endActivity(state);
  state.appendMessage({
    id,
    role: "assistant",
    content: delta,
    turn: { startedAt, endedAt: null, durationMs: null },
    suppressEntryMotion,
  });
}

export function reconcileAssistantContent(
  context: ProjectionContext,
  id: string,
  content: string,
  startedAt: number,
  suppressEntryMotion?: boolean,
): void {
  const state = getState();
  setActiveAssistantMessageId(context, id);
  const existing = state.messages.get(id);
  if (existing?.role === "assistant") {
    if (existing.content.trim().length === 0 && content.trim().length > 0) {
      endActivity(state);
    }
    if (existing.content !== content) state.mutateMessage(id, { content });
    return;
  }
  if (content.length === 0) return;

  endActivity(state);
  state.appendMessage({
    id,
    role: "assistant",
    content,
    turn: { startedAt, endedAt: null, durationMs: null },
    suppressEntryMotion,
  });
}

export function activityInsertAnchor(context: ProjectionContext): string | null {
  const state = getState();
  let assistantId = context.state.activeAssistantMessageId;
  if (!assistantId || state.messages.get(assistantId)?.role !== "assistant") {
    assistantId = null;
    for (let i = state.order.length - 1; i >= 0; i--) {
      const id = state.order[i];
      const message = state.messages.get(id);
      if (message?.role !== "assistant") continue;
      assistantId = id;
      break;
    }
  }
  if (!assistantId) return null;
  const message = state.messages.get(assistantId);
  if (message?.role !== "assistant") return null;
  if (message.content.trim().length !== 0) return null;
  setActiveAssistantMessageId(context, assistantId);
  return assistantId;
}

export function activityItemFromPending(id: string, pending: PendingToolCall): ActivityItem {
  const projected = projectToolCallArguments(pending.argsBuffer);
  return {
    id,
    kind: pending.name,
    semanticKind: liftedKind(pending.semanticKind),
    displayName: pending.displayName,
    displayTitle: projected.displayTitle,
    target: formatCallTarget(pending.name, projected.args || "{}", pending.displayName),
    args: projected.args,
    status: "ongoing",
    depth: pending.depth || undefined,
    parentToolId: pending.parentId ?? undefined,
    icon: pending.icon,
    noun: pending.noun,
    source: pending.source,
  };
}

export function activityPatchFromPending(pending: PendingToolCall): Partial<ActivityItem> {
  const projected = projectToolCallArguments(pending.argsBuffer);
  const patch: Partial<ActivityItem> = {
    target: formatCallTarget(pending.name, projected.args || "{}", pending.displayName),
    args: projected.args,
    displayTitle: projected.displayTitle,
    depth: pending.depth || undefined,
    parentToolId: pending.parentId ?? undefined,
  };
  if (liftedKind(pending.semanticKind)) patch.semanticKind = liftedKind(pending.semanticKind);
  if (pending.displayName) patch.displayName = pending.displayName;
  return patch;
}

function activityPatchFromItem(item: ActivityItem): Partial<ActivityItem> {
  const patch: Partial<ActivityItem> = {
    target: item.target,
    args: item.args,
    displayTitle: item.displayTitle,
    depth: item.depth,
    parentToolId: item.parentToolId,
    semanticKind: item.semanticKind,
    childAgent: item.childAgent,
    sourceRefs: item.sourceRefs,
  };
  if (item.displayName) patch.displayName = item.displayName;
  return patch;
}

export function appendActivityItemImmediately(
  context: ProjectionContext,
  item: ActivityItem,
  suppressEntryMotion?: boolean,
): void {
  const state = getState();
  const pendingPatch = takePendingResultPatch(context, item.id);
  const nextItem = pendingPatch ? { ...item, ...pendingPatch } : item;
  const mergePatch = pendingPatch
    ? { ...activityPatchFromItem(nextItem), ...pendingPatch }
    : activityPatchFromItem(nextItem);
  if (state.mergeActivityItem(nextItem.id, mergePatch)) {
    return;
  }

  const activityId = activeActivityIdForToolAppend(state.activeActivityId);
  if (!activityId) {
    const newId = crypto.randomUUID();
    state.insertMessageBefore(
      {
        id: newId,
        role: "activity",
        content: "",
        activity: { items: [nextItem], label: "Calling", done: false },
        suppressEntryMotion,
      },
      activityInsertAnchor(context),
    );
    state.setActiveActivityId(newId);
    context.update({ ...context.state, nextItemRenderAt: Date.now() });
    return;
  }

  if (activityId !== state.activeActivityId) state.setActiveActivityId(activityId);
  state.appendActivityItem(activityId, nextItem);
  context.update({ ...context.state, nextItemRenderAt: Date.now() });
}

function activeActivityIdForToolAppend(activeActivityId: string | null): string | null {
  const state = getState();
  const active = activeActivityId ? state.messages.get(activeActivityId) : null;
  if (active?.role === "activity" && active.activity) {
    return activeActivityId;
  }

  for (let i = state.order.length - 1; i >= 0; i -= 1) {
    const message = state.messages.get(state.order[i]);
    if (!message) continue;
    if (message.role === "activity" && message.activity) {
      return message.id;
    }
    if (isActivityContinuationMessage(message)) continue;
    return null;
  }
  return null;
}

export function bufferActivityPatch(
  context: ProjectionContext,
  itemId: string,
  patch: Partial<ActivityItem>,
) {
  const pendingResultPatches = new Map(context.state.pendingResultPatches);
  const existing = pendingResultPatches.get(itemId);
  const sourceRefs = mergeSourceRefs(existing?.sourceRefs, patch.sourceRefs);
  pendingResultPatches.set(itemId, {
    ...existing,
    ...patch,
    ...(sourceRefs === undefined ? {} : { sourceRefs }),
  });
  context.update({ ...context.state, pendingResultPatches });
}

export function mergeOrBufferActivityPatch(
  context: ProjectionContext,
  itemIds: Array<string | null | undefined>,
  patch: Partial<ActivityItem>,
) {
  const uniqueIds = Array.from(new Set(itemIds.filter((id): id is string => !!id)));
  const state = getState();
  for (const itemId of uniqueIds) {
    if (state.mergeActivityItem(itemId, patch)) return;
  }
  for (const itemId of uniqueIds) bufferActivityPatch(context, itemId, patch);
}

export function taskActivityItemId(event: {
  parent_tool_call_id?: string | null;
  task_id: string;
}): string {
  return event.parent_tool_call_id || event.task_id;
}

export function takePendingResultPatch(
  context: ProjectionContext,
  itemId: string,
): Partial<ActivityItem> | undefined {
  const pendingPatch = context.state.pendingResultPatches.get(itemId);
  if (!pendingPatch) return undefined;
  const pendingResultPatches = new Map(context.state.pendingResultPatches);
  pendingResultPatches.delete(itemId);
  context.update({ ...context.state, pendingResultPatches });
  return pendingPatch;
}

/** End the active activity (if any) and clear the marker. */
export function endActivity(s: ReturnType<typeof getState>, label: "Called" | "Stopped" = "Called") {
  if (s.activeActivityId) {
    s.finalizeActivity(s.activeActivityId, label);
    s.setActiveActivityId(null);
  }
}

/** Mark the most recent unfinished user message's turn as ended. */
export function endTurn(s: ReturnType<typeof getState>, endedAt: number) {
  for (let i = s.order.length - 1; i >= 0; i--) {
    const id = s.order[i];
    const msg = s.messages.get(id);
    if (msg?.role !== "user") continue;
    if (!msg.turn || msg.turn.endedAt != null) return;
    s.mutateMessage(id, {
      turn: { ...msg.turn, endedAt, durationMs: Math.max(0, endedAt - msg.turn.startedAt) },
    });
    return;
  }
}

function toolLabel(name: string, displayName?: string): string {
  return displayName || name;
}

function renderArgValue(value: unknown): string {
  const rendered = JSON.stringify(value);
  return rendered === undefined ? String(value) : rendered;
}

export function formatCallTarget(name: string, argsJson: string, displayName?: string): string {
  const label = toolLabel(name, displayName);
  try {
    const projected = projectToolCallArguments(argsJson);
    const parsed = JSON.parse(projected.args || "{}");
    if (parsed && typeof parsed === "object") {
      const args = parsed as Record<string, unknown>;
      const entries = Object.entries(args);
      if (entries.length === 0) return label;
      const parts = entries.map(([key, value]) => {
        return `${key}=${renderArgValue(value)}`;
      });
      return `${label}(${parts.join(", ")})`;
    }
  } catch {
    // Fall through to the display label.
  }
  return label;
}
