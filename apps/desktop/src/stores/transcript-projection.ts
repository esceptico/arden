import type { HistoryMessage } from "@/api/chat";
import type { ServerEvent } from "@/api/events";
import { SEMANTIC_KIND_AGENT } from "@/lib/agent";
import { htmlWidgetFromHistory } from "@/lib/htmlWidget";
import { projectToolCallArguments } from "@/lib/toolCallMetadata";
import { childAgentFromToolResultData, type ToolResultData } from "@/stores/child-agent-metadata";
import { getState, setState, type ActivityItem, type UiMessage } from "@/stores/index";
import { normalizeSourceRefs, sourceRefsFromToolResultData } from "@/stores/sourceRefs";
import {
  reduceActiveActivityBackgrounded,
  reduceBackgroundedRunObserved,
  reduceForegroundRunCleared,
  reduceRunCompleted,
  reduceRunFailed,
  reduceRunOutputObserved,
  reduceRunStarted,
} from "@/stores/run-lifecycle";
import {
  activityInsertAnchor,
  activityItemFromPending,
  activityPatchFromPending,
  appendActivityItemImmediately,
  appendAssistantDelta,
  assistantIdFrom,
  bufferActivityPatch,
  childAgentFromTaskEvent,
  createProjectionContext,
  endActivity,
  endTurn,
  ensureAssistantMessage,
  formatCallTarget,
  isTodoToolName,
  liftedKind,
  mergeOrBufferActivityPatch,
  normalizeTodoItems,
  reconcileAssistantContent,
  reopenNewestHistoryActivity,
  setActiveAssistantMessageId,
  takePendingResultPatch,
  taskActivityItemId,
  todoStateFromArgs,
  workflowIdFromData,
} from "@/stores/transcript-projection-helpers";
import type {
  PendingToolCall,
  TranscriptProjectionResult,
  TranscriptProjectionRuntime,
  TranscriptProjectionState,
} from "@/stores/transcript-projection-types";

export { isTodoToolName, newestHistoryActivityId } from "@/stores/transcript-projection-helpers";
export type {
  PendingToolCall,
  TranscriptProjectionResult,
  TranscriptProjectionRuntime,
  TranscriptProjectionState,
} from "@/stores/transcript-projection-types";

export function createInitialTranscriptProjectionState(): TranscriptProjectionState {
  return {
    pendingResultPatches: new Map(),
    pendingToolCalls: new Map(),
    pendingActivityReplaySeqs: new Map(),
    delayedActivityTimers: new Set(),
    activeAssistantMessageId: null,
    nextItemRenderAt: 0,
  };
}

export function resetTranscriptProjectionState(
  state?: TranscriptProjectionState,
): TranscriptProjectionState {
  if (state) clearDelayedActivityTimers(state);
  return createInitialTranscriptProjectionState();
}

export function transientProjectionReplayFloor(
  state: TranscriptProjectionState,
): number | null {
  let replayFromSeq: number | null = null;
  for (const pending of state.pendingToolCalls.values()) {
    if (typeof pending.startSeq !== "number") continue;
    replayFromSeq =
      replayFromSeq === null ? pending.startSeq : Math.min(replayFromSeq, pending.startSeq);
  }
  for (const seq of state.pendingActivityReplaySeqs.values()) {
    replayFromSeq = replayFromSeq === null ? seq : Math.min(replayFromSeq, seq);
  }
  return replayFromSeq;
}

export function clearDelayedActivityTimers(state: TranscriptProjectionState): void {
  for (const timer of state.delayedActivityTimers) clearTimeout(timer);
}

export function applyChatEventToTranscript(
  state: TranscriptProjectionState,
  event: ServerEvent,
  runtime?: TranscriptProjectionRuntime,
): TranscriptProjectionResult {
  const context = createProjectionContext(state, runtime);

  const s = getState();
  const ts = event.timestamp ?? Date.now();
  const suppressEntryMotion = event.replay === true;
  switch (event.type) {
    case "RUN_STARTED":
      endActivity(s);
      setActiveAssistantMessageId(context, null);
      setState((state) => ({
        ...reduceRunStarted(state, { runId: event.run_id, sessionId: event.session_id }),
        error: null,
      }));
      if (event.is_meta_run) {
        s.appendMessage({
          id: `meta-user-${event.run_id}`,
          role: "user",
          content: "",
          isMeta: true,
          turn: { startedAt: ts, endedAt: null, durationMs: null },
          suppressEntryMotion,
        });
      }
      break;

    case "session_updated":
      setState((state) => ({
        sessions: state.sessions.map((session) =>
          session.session_id === event.session_id ? { ...session, name: event.name ?? null } : session,
        ),
      }));
      break;

    case "RUN_FINISHED":
      if (s.currentRunId && s.currentRunId !== event.run_id) break;
      if (event.usage) {
        s.accumulateUsage({
          ...event.usage,
          contextInputTokens: event.context_input_tokens,
          messageCount: event.message_count,
        });
      }
      endActivity(s);
      endTurn(s, ts);
      setActiveAssistantMessageId(context, null);
      setState((state) =>
        reduceRunCompleted(state, { runId: event.run_id, sessionId: event.session_id }),
      );
      break;

    case "token_usage":
      if (s.currentRunId && s.currentRunId !== event.run_id) break;
      s.updateLiveUsage({
        ...event.usage,
        cost: event.cost,
        messageCount: event.message_count ?? undefined,
        scope: event.scope,
      });
      break;

    case "run_cancelled":
      if (s.currentRunId && s.currentRunId !== event.run_id) break;
      endActivity(s, "Stopped");
      endTurn(s, ts);
      setActiveAssistantMessageId(context, null);
      setState((state) =>
        reduceRunCompleted(state, {
          runId: event.run_id,
          sessionId: event.session_id,
          clearApprovals: true,
        }),
      );
      break;

    case "run_backgrounded":
      if (s.currentRunId && s.currentRunId !== event.run_id) {
        if (event.session_id) {
          setState((state) => reduceBackgroundedRunObserved(state, { sessionId: event.session_id as string }));
        }
        break;
      }
      if (!s.currentRunId) {
        if (event.session_id) {
          setState((state) => reduceBackgroundedRunObserved(state, { sessionId: event.session_id as string }));
        }
        break;
      }
      setState((state) => reduceActiveActivityBackgrounded(state));
      endTurn(s, ts);
      setActiveAssistantMessageId(context, null);
      setState((state) =>
        reduceForegroundRunCleared(state, {
          runId: event.run_id,
          sessionId: event.session_id,
          clearApprovals: true,
          markBackgrounded: true,
        }),
      );
      break;

    case "RUN_ERROR":
      if (s.currentRunId && s.currentRunId !== event.run_id) break;
      endActivity(s);
      s.appendMessage({ id: crypto.randomUUID(), role: "error", content: event.message });
      endTurn(s, ts);
      setActiveAssistantMessageId(context, null);
      setState((state) =>
        reduceRunFailed(state, {
          runId: event.run_id,
          sessionId: event.session_id,
        }),
      );
      break;

    case "message_ingested": {
      break;
    }

    case "TEXT_MESSAGE_START":
      if (!event.depth) {
        setState((state) => reduceRunOutputObserved(state));
      }
      if (!event.depth) ensureAssistantMessage(context, assistantIdFrom(event), ts, suppressEntryMotion);
      break;

    case "TEXT_MESSAGE_CONTENT":
      if (!event.depth) {
        setState((state) => reduceRunOutputObserved(state));
      }
      if (!event.depth) appendAssistantDelta(context, assistantIdFrom(event), event.delta, ts, suppressEntryMotion);
      break;

    case "TEXT_MESSAGE_END":
      if (!event.depth) {
        setState((state) => reduceRunOutputObserved(state));
      }
      if (!event.depth && event.content !== undefined) {
        reconcileAssistantContent(context, assistantIdFrom(event), event.content, ts, suppressEntryMotion);
      }
      break;
    case "REASONING_START":
    case "REASONING_MESSAGE_END":
    case "REASONING_END":
      break;

    case "REASONING_MESSAGE_START":
      if (!event.depth) {
        s.appendMessage({
          id: event.message_id,
          role: "reasoning",
          title: "Reasoning",
          content: "",
          suppressEntryMotion,
        });
      }
      break;

    case "REASONING_MESSAGE_CONTENT": {
      if (event.depth) break;
      const message = s.messages.get(event.message_id);
      if (message) {
        s.mutateMessage(event.message_id, { content: message.content + event.delta });
      } else {
        s.appendMessage({
          id: event.message_id,
          role: "reasoning",
          title: "Reasoning",
          content: event.delta,
          suppressEntryMotion,
        });
      }
      break;
    }

    case "TOOL_CALL_START": {
      if (isTodoToolName(event.tool_call_name)) break;
      const pending: PendingToolCall = {
        name: event.tool_call_name,
        displayName: event.display_name,
        argsBuffer: "",
        depth: event.depth ?? 0,
        parentId: event.parent_id ?? null,
        semanticKind: event.kind ?? "tool",
        startSeq: typeof event.seq === "number" ? event.seq : undefined,
        icon: event.icon ?? undefined,
        noun: event.noun ?? undefined,
        source: event.source ?? undefined,
      };
      const pendingToolCalls = new Map(context.state.pendingToolCalls);
      pendingToolCalls.set(event.tool_call_id, pending);
      context.update({ ...context.state, pendingToolCalls });
      appendActivityItemImmediately(context, activityItemFromPending(event.tool_call_id, pending), suppressEntryMotion);
      break;
    }

    case "TOOL_CALL_ARGS": {
      const pending = context.state.pendingToolCalls.get(event.tool_call_id);
      if (!pending) break;
      if (isTodoToolName(pending.name)) break;
      const argsBuffer = pending.argsBuffer + event.delta;
      const projected = projectToolCallArguments(argsBuffer);
      const pendingToolCalls = new Map(context.state.pendingToolCalls);
      pendingToolCalls.set(event.tool_call_id, {
        ...pending,
        argsBuffer,
      });
      context.update({ ...context.state, pendingToolCalls });
      s.mergeActivityItem(event.tool_call_id, {
        args: projected.args,
        displayTitle: projected.displayTitle,
        target: formatCallTarget(pending.name, projected.args || "{}", pending.displayName),
      });
      break;
    }

    case "TOOL_CALL_END": {
      const pending = context.state.pendingToolCalls.get(event.tool_call_id);
      if (isTodoToolName(pending?.name)) {
        const pendingToolCalls = new Map(context.state.pendingToolCalls);
        pendingToolCalls.delete(event.tool_call_id);
        context.update({ ...context.state, pendingToolCalls });
        break;
      }
      const pendingToolCalls = new Map(context.state.pendingToolCalls);
      pendingToolCalls.delete(event.tool_call_id);
      context.update({ ...context.state, pendingToolCalls });
      if (!pending) break;

      const item = activityItemFromPending(event.tool_call_id, pending);
      const pendingPatch = takePendingResultPatch(context, item.id);
      const patch = pendingPatch
        ? { ...activityPatchFromPending(pending), ...pendingPatch }
        : activityPatchFromPending(pending);
      if (!s.mergeActivityItem(item.id, patch)) {
        appendActivityItemImmediately(context, pendingPatch ? { ...item, ...pendingPatch } : item, suppressEntryMotion);
      }
      break;
    }

    case "TOOL_CALL_RESULT": {
      if (isTodoToolName(event.name)) break;
      const result = event.content ?? event.preview ?? "";
      const patch: Partial<ActivityItem> = { result, status: "executed" };
      const sourceRefs = normalizeSourceRefs(event.source_refs, event.tool_call_id);
      if (sourceRefs.length > 0) patch.sourceRefs = sourceRefs;
      if (liftedKind(event.kind)) patch.semanticKind = liftedKind(event.kind);
      const wid = workflowIdFromData(event.data);
      if (wid) patch.workflowId = wid;
      // Agent/workflow lifecycle events may have replaced the generic tool label
      // ("Research") with the generated name; don't clobber it when the final
      // tool result arrives with static executor metadata.
      if (event.display_name && !liftedKind(event.kind)) patch.displayName = event.display_name;
      if (event.parent_id) patch.parentToolId = event.parent_id;
      if (event.depth != null) patch.depth = event.depth || undefined;
      if (event.is_error) patch.error = true;
      if (event.outcome) patch.outcome = event.outcome;
      if (typeof event.duration_ms === "number" && event.duration_ms > 0) {
        patch.durationMs = event.duration_ms;
      }
      const data = event.data as ToolResultData | null;
      if (data?.usage) patch.usage = data.usage;
      if (typeof data?.cost === "number") patch.cost = data.cost;
      const childAgent = childAgentFromToolResultData(data);
      if (childAgent) {
        patch.childAgent = childAgent;
        patch.semanticKind = SEMANTIC_KIND_AGENT;
      }
      const widget = event.data as { html?: unknown; title?: unknown; mode?: unknown } | null;
      if (
        liftedKind(event.kind) === "html_widget" &&
        typeof widget?.html === "string" &&
        typeof widget?.title === "string" &&
        (widget.mode === "display" || widget.mode === "input")
      ) {
        patch.htmlWidget = { html: widget.html, title: widget.title, mode: widget.mode };
      }
      if (!s.mergeActivityItem(event.tool_call_id, patch)) {
        bufferActivityPatch(context, event.tool_call_id, patch);
      }
      break;
    }

    case "input_needed": {
      mergeOrBufferActivityPatch(context, [event.tool_id], {
        htmlWidget: { html: event.html, title: event.title, mode: "input" },
      });
      break;
    }

    case "todo_updated": {
      if (s.currentRunId && s.currentRunId !== event.run_id) break;
      const items = normalizeTodoItems(event.items);
      s.upsertTodoList(
        {
          id: `todo-${event.run_id}`,
          role: "todo",
          content: "",
          todo: {
            items,
            explanation: event.explanation ?? null,
          },
          suppressEntryMotion,
        },
        activityInsertAnchor(context),
      );
      // The sidebar reads the session-level slot, not the transcript. An
      // all-completed list stays visible here for the retire linger; the
      // sidebar dismisses it after the pause (the server already cleared
      // its copy).
      if (s.currentSessionId) {
        s.setSessionTodo(s.currentSessionId, {
          items,
          explanation: event.explanation ?? null,
          edited: false,
        });
      }
      break;
    }

    case "task_started": {
      // Workflow leaf tasks render inside the workflow CARD (workflows
      // domain), not as item patches — taskActivityItemId resolves to the
      // workflow's own tool call, so patching would flip its semanticKind to
      // "agent" and let the leaf agents fight over its displayName.
      if (event.workflow_id) break;
      const patch: Partial<ActivityItem> = {
        runId: event.run_id,
        status: "ongoing",
        taskStatus: "running",
        progress: event.summary ?? "running",
      };
      const childAgent = childAgentFromTaskEvent(event, "running");
      if (childAgent) {
        patch.childAgent = childAgent;
        patch.semanticKind = SEMANTIC_KIND_AGENT;
      }
      if (event.name) patch.displayName = event.name;
      mergeOrBufferActivityPatch(context, [taskActivityItemId(event)], patch);
      break;
    }

    case "task_progress": {
      if (event.workflow_id) break; // see task_started — card-owned, not an item patch
      const taskStatus =
        event.status === "failed" || event.status === "cancelled" ? event.status : "running";
      const patch: Partial<ActivityItem> = {
        runId: event.run_id,
        status: taskStatus === "running" ? "ongoing" : "executed",
        taskStatus,
        progress: event.summary ?? event.status ?? "running",
      };
      const childAgent = childAgentFromTaskEvent(event, taskStatus);
      if (childAgent) {
        patch.childAgent = childAgent;
        patch.semanticKind = SEMANTIC_KIND_AGENT;
      }
      if (event.name) patch.displayName = event.name;
      mergeOrBufferActivityPatch(context, [taskActivityItemId(event)], patch);
      break;
    }

    case "task_finished": {
      if (event.workflow_id) break; // see task_started — card-owned, not an item patch
      const patch: Partial<ActivityItem> = {
        runId: event.run_id,
        status: "executed",
        taskStatus: event.status,
        progress: event.summary ?? event.status,
        cancelRequested: false,
      };
      const childAgent = childAgentFromTaskEvent(event, event.status);
      if (childAgent) {
        patch.childAgent = childAgent;
        patch.semanticKind = SEMANTIC_KIND_AGENT;
      }
      if (event.name) patch.displayName = event.name;
      mergeOrBufferActivityPatch(context, [taskActivityItemId(event)], patch);
      break;
    }

    case "compaction_started": {
      if (!event.parent_tool_call_id) break;
      mergeOrBufferActivityPatch(context, [event.parent_tool_call_id], {
        status: "ongoing",
        taskStatus: "running",
        progress: "compacting",
      });
      break;
    }

    case "compaction_finished": {
      if (!event.parent_tool_call_id) break;
      mergeOrBufferActivityPatch(context, [event.parent_tool_call_id], {
        progress: `compacted ${event.messages_before} -> ${event.messages_after} messages`,
      });
      break;
    }

    default:
      break;
  }

  return { state: context.state };
}

export function rebuildTranscriptFromHistory(
  messages: HistoryMessage[],
  options: { activeRunId?: string | null; isNewestPage?: boolean } = {},
): UiMessage[] {
  const resultsById = new Map<string, { content: string; data?: unknown }>();
  for (const msg of messages) {
    if (msg.role === "tool" && msg.tool_call_id) {
      resultsById.set(msg.tool_call_id, { content: msg.content, data: msg.data });
    }
  }

  const items: UiMessage[] = [];
  let activeActivityId: string | null = null;
  let activeTodoId: string | null = null;
  let turnOwner: UiMessage | null = null;

  const findActivity = (id: string) =>
    items.find((it) => it.id === id && it.role === "activity")?.activity;

  messages.forEach((msg, index) => {
    const sourceIndex = msg.seq ?? index;
    const sourceMessageId = msg.message_id ?? msg.id;
    const stableId = msg.id ?? msg.message_id ?? `history-${sourceIndex}`;
    const stampedAt = msg.created_at ? Date.parse(msg.created_at) : 0;

    // Turn duration is derivable from message stamps: the turn ran from the
    // user message to the last stamped message before the next user turn.
    if (msg.role !== "user" && turnOwner?.turn && stampedAt > turnOwner.turn.startedAt) {
      turnOwner.turn = {
        startedAt: turnOwner.turn.startedAt,
        endedAt: stampedAt,
        durationMs: stampedAt - turnOwner.turn.startedAt,
      };
    }

    if (msg.role === "user") {
      activeActivityId = null;
      activeTodoId = null;
      items.push({
        id: stableId,
        role: "user",
        sourceIndex,
        sourceMessageId,
        suppressEntryMotion: true,
        content: msg.content,
        turn: { startedAt: stampedAt, endedAt: stampedAt, durationMs: null },
        images: msg.images,
        isMeta: msg.is_meta,
      });
      turnOwner = stampedAt ? items[items.length - 1] : null;
      return;
    }

    if (msg.role === "tool") return;

    if (msg.reasoning_content) {
      items.push({
        id: `${stableId}-reasoning`,
        role: "reasoning",
        sourceIndex,
        sourceMessageId,
        suppressEntryMotion: true,
        title: "Reasoning",
        content: msg.reasoning_content,
      });
    }

    if (msg.content && msg.content.trim().length > 0) {
      activeActivityId = null;
      items.push({
        id: stableId,
        role: "assistant",
        sourceIndex,
        sourceMessageId,
        suppressEntryMotion: true,
        content: msg.content,
        turn: stampedAt
          ? { startedAt: stampedAt, endedAt: stampedAt, durationMs: null }
          : undefined,
      });
    }

    if (msg.tool_calls && msg.tool_calls.length > 0) {
      const todoCalls = msg.tool_calls.filter((toolCall) => isTodoToolName(toolCall.name));
      const activityCalls = msg.tool_calls.filter((toolCall) => !isTodoToolName(toolCall.name));

      for (const toolCall of todoCalls) {
        const todo = todoStateFromArgs(toolCall.arguments || "");
        if (!todo) continue;
        if (!activeTodoId) {
          activeTodoId = `${stableId}-todo`;
          items.push({
            id: activeTodoId,
            role: "todo",
            sourceIndex,
            sourceMessageId,
            suppressEntryMotion: true,
            content: "",
            todo,
          });
        } else {
          const existing = items.find((it) => it.id === activeTodoId);
          if (existing) existing.todo = todo;
        }
      }

      if (activityCalls.length === 0) return;

      if (!activeActivityId) {
        activeActivityId = `${stableId}-activity`;
        items.push({
          id: activeActivityId,
          role: "activity",
          sourceIndex,
          sourceMessageId,
          suppressEntryMotion: true,
          content: "",
          activity: { items: [], label: "Called", done: true },
        });
      }
      const activity = findActivity(activeActivityId);
      if (activity) {
        for (const toolCall of activityCalls) {
          const projected = projectToolCallArguments(toolCall.arguments || "");
          const args = projected.args;
          const result = resultsById.get(toolCall.id);
          const resultContent = toolCall.result ?? result?.content;
          const childAgent = childAgentFromToolResultData(result?.data);
          const sourceRefs = sourceRefsFromToolResultData(result?.data, toolCall.id);
          const htmlWidget = toolCall.kind === "html_widget" ? htmlWidgetFromHistory(args, resultContent) : undefined;
          activity.items.push({
            id: toolCall.id,
            kind: toolCall.name,
            semanticKind: liftedKind(toolCall.kind),
            displayTitle: projected.displayTitle,
            workflowId: workflowIdFromData(result?.data),
            target: formatCallTarget(toolCall.name, args || "{}", toolCall.display_name),
            args,
            result: resultContent,
            status: "executed",
            childAgent,
            sourceRefs: sourceRefs.length > 0 ? sourceRefs : undefined,
            outcome: toolCall.outcome,
            htmlWidget,
          });
        }
      }
    }
  });

  if (options.activeRunId && (options.isNewestPage ?? true)) {
    reopenNewestHistoryActivity(items);
  }

  return items;
}
