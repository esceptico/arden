import type { AppConfig } from "@/api/core";
import type { ServerEvent } from "@/api/events";
import { getState, setState } from "@/stores/index";
import {
  applyChatEventToTranscript,
} from "@/stores/transcript-projection";
import { reduceRunThinking } from "@/stores/run-lifecycle";
import {
  backgroundAgentKey,
  normalizeBackgroundAgentStatus,
  type BackgroundAgentUpsert,
} from "@/stores/background-agent-domain";
import type { WorkflowTaskEventKind } from "@/stores/workflow-domain";
import type { BackgroundAgentStatus } from "@/stores/types";
import type {
  ChatStreamState,
  EventCursorInput,
  TransportDiagnostics,
} from "@/stores/chat-stream-types";
import {
  clearReplayBlock,
  createInitialChatStreamState,
  recordTransportEventDiagnostics,
  reduceEventCursor,
  reduceReplayGap,
  reduceStreamConnected,
  reduceStreamConnecting,
  reduceStreamDisconnected,
  reduceStreamReconnecting,
} from "@/stores/chat-stream-reducers";

export { REPLAY_MUTATION_HOLD_MS } from "@/stores/chat-stream-types";
export type { ChatStreamState, TransportDiagnostics } from "@/stores/chat-stream-types";
export {
  clearReplayBlock,
  createInitialChatStreamState,
  reduceEventCursor,
  reduceReplayGap,
  reduceStreamConnected,
  reduceStreamConnecting,
  reduceStreamDisconnected,
  reduceStreamReconnecting,
} from "@/stores/chat-stream-reducers";

import { REPLAY_MUTATION_HOLD_MS } from "@/stores/chat-stream-types";

type ServerEventEffect =
  | { type: "replay_gap"; sessionId: string }
  | { type: "dispatch_queued_head"; sessionId: string };
type HistoryReloader = (sessionId: string) => Promise<void>;
type QueueDispatcher = (sessionId: string) => Promise<void> | void;
type TaskLifecycleEvent = Extract<
  ServerEvent,
  { type: "task_started" | "task_progress" | "task_finished" }
>;
type TokenUsageEvent = Extract<ServerEvent, { type: "token_usage" }>;
let chatStreamState = createInitialChatStreamState();

function updateChatStreamState(next: ChatStreamState): ChatStreamState {
  chatStreamState = next;
  setState({
    transportDiagnostics: Object.fromEntries(chatStreamState.transportDiagnosticsBySession),
  });
  return chatStreamState;
}

export function markStreamConnecting(sessionId: string, afterSeq?: number): void {
  updateChatStreamState(reduceStreamConnecting(chatStreamState, sessionId, afterSeq));
}

export function markStreamConnected(sessionId: string): void {
  updateChatStreamState(reduceStreamConnected(chatStreamState, sessionId));
}

export function markStreamReconnecting(
  sessionId: string,
  reason?: string | null,
  error?: string | null,
): void {
  updateChatStreamState(reduceStreamReconnecting(chatStreamState, sessionId, reason, error));
  clearReplayMutationDomMarker();
}

export function markStreamDisconnected(
  sessionId: string | null = chatStreamState.sessionId,
  reason?: string | null,
  error?: string | null,
): void {
  updateChatStreamState(reduceStreamDisconnected(chatStreamState, sessionId, reason, error));
  clearReplayMutationDomMarker();
}

export function recordTransportEventForDiagnostics(event: EventCursorInput): void {
  updateChatStreamState(recordTransportEventDiagnostics(chatStreamState, event));
}

function acceptEventCursor(event: ServerEvent): boolean {
  const result = reduceEventCursor(chatStreamState, event);
  updateChatStreamState(result.state);
  return result.accepted;
}


export function setEventCursorForSession(sessionId: string, seq: number): void {
  updateChatStreamState({
    ...chatStreamState,
    lastEventSeqBySession: new Map(chatStreamState.lastEventSeqBySession).set(
      sessionId,
      Math.max(0, seq),
    ),
  });
}

export function lastEventSeqForSession(sessionId: string): number | undefined {
  return chatStreamState.lastEventSeqBySession.get(sessionId);
}

export function transportDiagnosticsForSession(sessionId: string): TransportDiagnostics | undefined {
  return chatStreamState.transportDiagnosticsBySession.get(sessionId);
}

export function forgetEventSeqForSession(sessionId: string): void {
  const lastEventSeqBySession = new Map(chatStreamState.lastEventSeqBySession);
  lastEventSeqBySession.delete(sessionId);
  updateChatStreamState({ ...chatStreamState, lastEventSeqBySession });
}

export function clearReplayGapBlockForSession(sessionId: string): void {
  updateChatStreamState(clearReplayBlock(chatStreamState, sessionId));
}

export function reloadHistoryAfterReplayGap(
  sessionId: string,
  reload: HistoryReloader,
): Promise<void> | null {
  if (chatStreamState.replayGapReloadingSessions.has(sessionId)) return null;
  const task = reload(sessionId)
    .then(() => {
      clearReplayGapBlockForSession(sessionId);
      return true;
    })
    .catch((error) => {
      setState({ error: error instanceof Error ? error.message : String(error) });
      return false;
    })
    .finally(() => {
      const replayGapReloadingSessions = new Map(chatStreamState.replayGapReloadingSessions);
      replayGapReloadingSessions.delete(sessionId);
      updateChatStreamState({ ...chatStreamState, replayGapReloadingSessions });
    });

  const replayGapReloadingSessions = new Map(chatStreamState.replayGapReloadingSessions);
  replayGapReloadingSessions.set(sessionId, task);
  updateChatStreamState({ ...chatStreamState, replayGapReloadingSessions });
  return task.then(() => undefined);
}

export function handleIncomingServerEvent(
  event: ServerEvent,
  reload?: HistoryReloader,
  dispatchQueuedHead?: QueueDispatcher,
): Promise<void> | null {
  const sessionId = event.session_id ?? getState().currentSessionId;
  const activeSessionId = getState().currentSessionId;
  if (event.session_id && activeSessionId !== event.session_id) return null;

  const pendingReload =
    event.type === "stream_reset" || !sessionId
      ? undefined
      : chatStreamState.replayGapReloadingSessions.get(sessionId);
  if (pendingReload) {
    return pendingReload.then(async (loaded) => {
      if (!loaded) return;
      if (getState().currentSessionId !== sessionId) return;
      await handleIncomingServerEvent(event, reload, dispatchQueuedHead);
    });
  }

  const effect = event.replay ? handleReplayServerEvent(event) : handleServerEvent(event);
  if (!effect) return null;
  if (effect.type === "replay_gap") {
    if (!reload) {
      setState({ error: "history reload callback is required after replay gap" });
      return null;
    }
    return reloadHistoryAfterReplayGap(effect.sessionId, reload);
  }
  if (effect.type === "dispatch_queued_head") {
    return Promise.resolve(dispatchQueuedHead?.(effect.sessionId));
  }
  return null;
}

export function handleServerEvent(event: ServerEvent): ServerEventEffect | undefined {
  if (!acceptEventCursor(event)) return;
  return applyServerEvent(event);
}

export function handleReplayServerEvent(event: ServerEvent): ServerEventEffect | undefined {
  if (!acceptEventCursor(event)) return;
  markReplayMutation();
  return applyServerEvent({ ...event, replay: true });
}

function markReplayMutation(): void {
  setReplayMotionSuppressed(true);
  if (chatStreamState.replayMutationTimer !== null) {
    clearTimeout(chatStreamState.replayMutationTimer);
  }
  const replayMutationTimer = setTimeout(() => {
    updateChatStreamState({
      ...chatStreamState,
      replayMutationTimer: null,
      replayMutationActive: false,
    });
    clearReplayMutationDomMarker();
  }, REPLAY_MUTATION_HOLD_MS);
  updateChatStreamState({
    ...chatStreamState,
    replayMutationTimer,
    replayMutationActive: true,
  });
}

function setReplayMotionSuppressed(active: boolean): void {
  if (typeof document !== "undefined") {
    if (active) document.documentElement.dataset.streamReplaying = "true";
    else delete document.documentElement.dataset.streamReplaying;
  }
  setState({ streamReplaying: active });
}

function clearReplayMutationDomMarker(): void {
  setReplayMotionSuppressed(false);
}

function applyServerEvent(event: ServerEvent): ServerEventEffect | undefined {
  const s = getState();
  const ts = event.timestamp ?? Date.now();
  const applyTranscriptEvent = (transcriptEvent: ServerEvent): ServerEventEffect | undefined => {
    const result = applyChatEventToTranscript(chatStreamState, transcriptEvent, {
      getProjectionState: () => chatStreamState,
      setProjectionState: (projection) => {
        updateChatStreamState({ ...chatStreamState, ...projection });
      },
    });
    updateChatStreamState({ ...chatStreamState, ...result.state });
    if (
      !transcriptEvent.replay
      && (
        transcriptEvent.type === "RUN_FINISHED"
        || transcriptEvent.type === "RUN_ERROR"
        || transcriptEvent.type === "run_cancelled"
      )
      && getState().queuedMessages.some((message) => message.status !== "sending")
    ) {
      const sessionId = transcriptEvent.session_id ?? getState().currentSessionId;
      if (sessionId) return { type: "dispatch_queued_head", sessionId };
    }
    return undefined;
  };

  switch (event.type) {
    case "stream_reset": {
      const activeActivity = s.activeActivityId ? s.messages.get(s.activeActivityId) : null;
      if (s.activeActivityId && activeActivity && !activeActivity.sourceMessageId) {
        s.truncateFrom(s.activeActivityId);
      }
      s.setActiveActivityId(null);
      const resetSessionId = event.session_id ?? s.currentSessionId;
      if (!resetSessionId) return;
      updateChatStreamState(reduceReplayGap(chatStreamState, resetSessionId));
      clearReplayMutationDomMarker();
      return { type: "replay_gap", sessionId: resetSessionId };
    }
    case "stream_keepalive":
      return;
    case "thinking": {
      const runId = event.run_id;
      const sessionId = event.session_id;
      if (!runId || !sessionId) return;
      setState((state) =>
        reduceRunThinking(state, {
          runId,
          sessionId,
          status: event.status,
        }),
      );
      return;
    }
    case "approval_needed":
      s.addPendingApproval({
        toolId: event.tool_id,
        toolName: event.name,
        path: event.path ?? undefined,
        diff: event.diff ?? undefined,
        preview: event.content_preview ?? undefined,
        status: "pending",
        runId: event.run_id ?? undefined,
        sessionId: event.session_id ?? undefined,
        agentType: event.agent_type ?? undefined,
        agentName: event.agent_name ?? undefined,
        action: event.action ?? undefined,
        expiresAt: event.expires_at ?? undefined,
      });
      return;
    case "connection_needed":
      s.addPendingConnection({
        runId: event.run_id,
        toolId: event.tool_id,
        integrationId: event.integration_id,
        connectionId: event.connection_id,
        label: event.label,
        reason: event.reason,
        detail: event.detail,
        capability: event.capability,
        action: event.action,
        settingsTab: event.settings_tab,
        requiredScopes: event.required_scopes,
        source: event.source,
      });
      return;
    case "background_task":
      const agent: BackgroundAgentUpsert = {
        taskId: event.task_id,
        sessionId: event.session_id ?? s.currentSessionId ?? "",
        command: event.command,
        status: normalizeBackgroundAgentStatus(event.status),
        detail: event.detail ?? undefined,
        resultRef: event.result_ref ?? undefined,
        updatedAt: ts,
      };
      if (event.child_session_id) agent.childSessionId = event.child_session_id;
      if (event.parent_tool_call_id) agent.parentToolCallId = event.parent_tool_call_id;
      if (event.agent_type) agent.agentType = event.agent_type;
      if (event.wait != null) agent.wait = event.wait;
      s.upsertBackgroundAgent(agent);
      return;
    case "task_started":
      upsertTaskLifecycleAgent(event, ts);
      routeWorkflowTaskEvent(event, "started", ts);
      return applyTranscriptEvent(event);
    case "task_progress":
      upsertTaskLifecycleAgent(event, ts);
      routeWorkflowTaskEvent(event, "progress", ts);
      return applyTranscriptEvent(event);
    case "task_finished":
      upsertTaskLifecycleAgent(event, ts);
      routeWorkflowTaskEvent(event, "finished", ts);
      return applyTranscriptEvent(event);
    case "workflow_started": {
      const sessionId = event.session_id ?? s.currentSessionId ?? chatStreamState.projectionSessionId;
      if (sessionId) {
        s.workflowStarted(
          {
            workflowId: event.workflow_id,
            sessionId,
            runId: event.run_id,
            parentToolCallId: event.parent_tool_call_id ?? undefined,
            name: event.name,
            description: event.description,
            phases: event.phases,
          },
          ts,
        );
      }
      return applyTranscriptEvent(event);
    }
    case "workflow_finished": {
      const sessionId = event.session_id ?? s.currentSessionId ?? chatStreamState.projectionSessionId;
      if (sessionId) {
        s.workflowFinished(
          {
            workflowId: event.workflow_id,
            sessionId,
            status: event.status,
            summary: event.summary,
            agentCount: event.agent_count,
          },
          ts,
        );
      }
      return applyTranscriptEvent(event);
    }
    case "token_usage":
      routeWorkflowTokenUsage(event, ts);
      return applyTranscriptEvent(event);
    case "compaction_started":
      if (event.scope === "agent") return applyTranscriptEvent(event);
      if (event.replay) return;
      s.setCompacting(true);
      return;
    case "compaction_finished":
      if (event.scope === "agent") return applyTranscriptEvent(event);
      if (event.replay) return;
      s.setCompacting(false);
      return;
    case "goal_updated":
      s.setGoal(event.session_id, event.goal);
      return;
    case "goal_cleared":
      s.setGoal(event.session_id, null);
      return;
    default:
      return applyTranscriptEvent(event);
  }
}

function upsertTaskLifecycleAgent(event: TaskLifecycleEvent, updatedAt: number): void {
  const s = getState();
  const sessionId = event.session_id ?? s.currentSessionId ?? chatStreamState.projectionSessionId;
  if (!sessionId) return;
  const taskId = event.child_run_id || event.task_id;
  const prev = s.backgroundAgents.rows[backgroundAgentKey(sessionId, taskId)];
  const status =
    event.type === "task_finished"
      ? event.status
      : event.type === "task_progress" && (event.status === "failed" || event.status === "cancelled")
        ? event.status
        : "running";
  const agent: BackgroundAgentUpsert = {
    taskId,
    sessionId,
    command: event.name || prev?.command || event.summary || event.task_id,
    status,
    detail: event.summary ?? prev?.detail,
    updatedAt,
  };
  if (event.child_session_id) agent.childSessionId = event.child_session_id;
  if (event.parent_tool_call_id) agent.parentToolCallId = event.parent_tool_call_id;
  if (event.agent_type) agent.agentType = event.agent_type;
  if (event.wait != null) agent.wait = event.wait;
  s.upsertBackgroundAgent(agent);
}

function routeWorkflowTaskEvent(event: TaskLifecycleEvent, kind: WorkflowTaskEventKind, ts: number): void {
  if (!event.workflow_id) return;
  const s = getState();
  const sessionId = event.session_id ?? s.currentSessionId ?? chatStreamState.projectionSessionId;
  if (!sessionId) return;
  const taskId = event.child_run_id || event.task_id;
  const status: BackgroundAgentStatus | undefined =
    event.type === "task_finished"
      ? event.status
      : event.type === "task_progress" && (event.status === "failed" || event.status === "cancelled")
        ? event.status
        : undefined;
  s.workflowTaskEvent(
    {
      kind,
      workflowId: event.workflow_id,
      sessionId,
      taskId,
      phase: event.phase,
      name: event.name,
      agentType: event.agent_type ?? undefined,
      childSessionId: event.child_session_id ?? undefined,
      toolCount: (event as { tool_count?: number | null }).tool_count ?? undefined,
      detail: event.summary,
      status,
    },
    ts,
  );
}

/** token_usage carries `run_id` for the run-level budget. The workflow domain
 *  only consumes per-agent spend, which arrives tagged with the originating
 *  `task_id`/`workflow_id` (Phase 1 server tagging). Untagged usage events are
 *  run-level and are ignored here — the transcript projection still consumes
 *  them for the budget gauge. */
function routeWorkflowTokenUsage(event: TokenUsageEvent, ts: number): void {
  const tagged = event as TokenUsageEvent & {
    task_id?: string | null;
    child_run_id?: string | null;
    workflow_id?: string | null;
    session_id?: string | null;
    phase?: string | null;
  };
  // Per-agent spend keys by the same id the lifecycle path uses (child_run_id),
  // so it lands on the matching WorkflowAgent — see routeWorkflowTaskEvent.
  const taskId = tagged.child_run_id || tagged.task_id;
  if (!taskId || !tagged.workflow_id) return;
  const s = getState();
  const sessionId = tagged.session_id ?? s.currentSessionId ?? chatStreamState.projectionSessionId;
  if (!sessionId) return;
  s.workflowTokenUsage(
    {
      workflowId: tagged.workflow_id,
      sessionId,
      taskId,
      phase: tagged.phase,
      seq: typeof event.seq === "number" ? event.seq : undefined,
      usage: event.usage,
      cost: event.cost,
    },
    ts,
  );
}

/** Rebuild the in-memory workflows domain from persisted workflow/task/usage
 *  events fetched on history load. Mirrors the live workflow routing in
 *  handleServerEvent, but does NOT touch the transcript (loadHistory already
 *  rebuilt that). Each event carries its original timestamp so durations and
 *  completedAt reflect when the work actually happened. Idempotent — re-applying
 *  the same events converges to the same domain state. */
export function rehydrateWorkflows(events: ServerEvent[]): void {
  const s = getState();
  for (const event of events) {
    const ts = typeof event.timestamp === "number" ? event.timestamp : Date.now();
    switch (event.type) {
      case "workflow_started":
        if (event.session_id) {
          s.workflowStarted(
            {
              workflowId: event.workflow_id,
              sessionId: event.session_id,
              runId: event.run_id,
              parentToolCallId: event.parent_tool_call_id ?? undefined,
              name: event.name,
              description: event.description,
              phases: event.phases,
            },
            ts,
          );
        }
        break;
      case "workflow_finished":
        if (event.session_id) {
          s.workflowFinished(
            {
              workflowId: event.workflow_id,
              sessionId: event.session_id,
              status: event.status,
              summary: event.summary,
              agentCount: event.agent_count,
            },
            ts,
          );
        }
        break;
      case "task_started":
        routeWorkflowTaskEvent(event, "started", ts);
        break;
      case "task_progress":
        routeWorkflowTaskEvent(event, "progress", ts);
        break;
      case "task_finished":
        routeWorkflowTaskEvent(event, "finished", ts);
        break;
      case "token_usage":
        routeWorkflowTokenUsage(event, ts);
        break;
    }
  }
}

export function eventStreamUrl(config: AppConfig, sessionId: string): string {
  const params = new URLSearchParams({ stream: "true" });
  const lastSeq = lastEventSeqForSession(sessionId);
  if (lastSeq !== undefined) params.set("after_seq", String(lastSeq));
  return `${config.serverUrl}/chat/events/${encodeURIComponent(sessionId)}?${params.toString()}`;
}

export function resetStreamStateForTest(): void {
  updateChatStreamState(reduceStreamDisconnected(chatStreamState));
  setState({ thinkingRunId: null, thinkingStatus: null });
  clearReplayMutationDomMarker();
}

export function resetEventSeqStateForTest(): void {
  updateChatStreamState({
    ...chatStreamState,
    lastEventSeqBySession: new Map(),
  });
}

export function resetReplayGapReloadStateForTest(): void {
  updateChatStreamState({
    ...chatStreamState,
    replayGapReloadingSessions: new Map(),
    replayGapBlockedSessions: new Set(),
  });
}
