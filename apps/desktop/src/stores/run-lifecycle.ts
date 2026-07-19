import type {
  ApprovalState,
  QueuedMessage,
  QueuedMessageStatus,
  State,
} from "@/stores/types";

type RunLifecyclePatch = Partial<
  Pick<
    State,
    | "running"
    | "currentRunId"
    | "thinkingRunId"
    | "thinkingStatus"
    | "activeRunSessionIds"
    | "backgroundedRunSessionIds"
    | "unreadDoneSessionIds"
    | "pendingApprovals"
    | "pendingConnections"
    | "reviewingApprovalToolId"
    | "queuedMessages"
    | "pendingResume"
    | "stoppingRunId"
    | "terminalRunIds"
    | "activeActivityId"
    | "messages"
  >
>;

export interface RunStatusSnapshot {
  runId?: string | null;
  sessionId: string;
  status?: string | null;
  backgrounded?: boolean;
}

export function reduceRunStarted(
  state: State,
  input: { runId: string | null; sessionId: string },
): RunLifecyclePatch {
  if (input.runId && state.terminalRunIds.has(input.runId)) {
    return state.stoppingRunId === input.runId ? { stoppingRunId: null } : {};
  }

  const appliesToCurrentSession =
    state.currentSessionId === null || state.currentSessionId === input.sessionId;
  const activeRunSessionIds = new Set(state.activeRunSessionIds);
  activeRunSessionIds.add(input.sessionId);

  return {
    running: appliesToCurrentSession ? true : state.running,
    currentRunId: appliesToCurrentSession ? input.runId : state.currentRunId,
    thinkingRunId: appliesToCurrentSession ? null : state.thinkingRunId,
    thinkingStatus: appliesToCurrentSession ? null : state.thinkingStatus,
    activeRunSessionIds,
    pendingResume: null,
    stoppingRunId: input.runId && state.stoppingRunId === input.runId ? null : state.stoppingRunId,
  };
}

export function reduceRunThinking(
  state: State,
  input: { runId: string; sessionId: string; status: string },
): RunLifecyclePatch {
  const appliesToCurrentSession =
    state.currentSessionId === null || state.currentSessionId === input.sessionId;
  if (!appliesToCurrentSession) return {};
  if (!state.running && state.currentRunId !== input.runId) return {};
  if (state.currentRunId && state.currentRunId !== input.runId) return {};

  const activeRunSessionIds = new Set(state.activeRunSessionIds);
  activeRunSessionIds.add(input.sessionId);

  return {
    running: true,
    currentRunId: input.runId,
    activeRunSessionIds,
    thinkingRunId: input.runId,
    thinkingStatus: input.status,
  };
}

export function reduceRunOutputObserved(state: State): RunLifecyclePatch {
  if (!state.thinkingRunId) return {};
  return {
    thinkingRunId: null,
    thinkingStatus: null,
  };
}

export function reduceRunStatus(
  state: State,
  input: { activeRuns: RunStatusSnapshot[] },
): RunLifecyclePatch {
  let terminalRunIds = state.terminalRunIds;
  const activeRuns: RunStatusSnapshot[] = [];
  const terminalRuns: RunStatusSnapshot[] = [];
  const backgroundedRuns: RunStatusSnapshot[] = [];

  for (const run of input.activeRuns) {
    if (run.runId && isTerminalStatus(run.status)) {
      terminalRunIds = addTerminalRunId(terminalRunIds, run.runId);
      terminalRuns.push(run);
      continue;
    }
    if (isBackgroundedStatus(run)) {
      backgroundedRuns.push(run);
      continue;
    }
    if (!isForegroundStatus(run.status)) continue;
    if (run.runId && terminalRunIds.has(run.runId)) continue;
    activeRuns.push(run);
  }

  const activeRunSessionIds = new Set(activeRuns.map((run) => run.sessionId));
  const backgroundedRunSessionIds = new Set(backgroundedRuns.map((run) => run.sessionId));
  let current = state.currentSessionId
    ? activeRuns.find((run) => run.sessionId === state.currentSessionId)
    : undefined;
  const terminalCurrentRun = current ? null : terminalRuns.find((run) => matchesCurrentRun(state, run)) ?? null;
  const backgroundedCurrentRun = current
    ? null
    : backgroundedRuns.find((run) => matchesCurrentRun(state, run)) ?? null;
  if (
    !current &&
    !terminalCurrentRun &&
    !backgroundedCurrentRun &&
    state.currentSessionId &&
    state.running &&
    state.currentRunId &&
    input.activeRuns.length > 0
  ) {
    activeRunSessionIds.add(state.currentSessionId);
    current = {
      runId: state.currentRunId,
      sessionId: state.currentSessionId,
      status: "running",
    };
  }
  const unreadDoneSessionIds = unreadAfterLiveSetChange(
    state,
    activeRunSessionIds,
    backgroundedRunSessionIds,
  );

  if (terminalCurrentRun) {
    return {
      activeRunSessionIds,
      backgroundedRunSessionIds,
      unreadDoneSessionIds,
      running: false,
      currentRunId: null,
      thinkingRunId: null,
      thinkingStatus: null,
      pendingResume: null,
      queuedMessages: [],
      stoppingRunId:
        terminalCurrentRun.runId && state.stoppingRunId === terminalCurrentRun.runId
          ? null
          : state.stoppingRunId,
      ...(terminalRunIds !== state.terminalRunIds ? { terminalRunIds } : {}),
    };
  }

  if (backgroundedCurrentRun) {
    return {
      activeRunSessionIds,
      backgroundedRunSessionIds,
      unreadDoneSessionIds,
      running: false,
      currentRunId: null,
      thinkingRunId: null,
      thinkingStatus: null,
      pendingResume: null,
      queuedMessages: [],
      stoppingRunId:
        backgroundedCurrentRun.runId && state.stoppingRunId === backgroundedCurrentRun.runId
          ? null
          : state.stoppingRunId,
      pendingApprovals: [],
      pendingConnections: [],
      reviewingApprovalToolId: null,
      ...backgroundActiveActivity(state),
      ...(terminalRunIds !== state.terminalRunIds ? { terminalRunIds } : {}),
    };
  }

  if (
    state.currentSessionId &&
    state.running &&
    state.currentRunId &&
    !current &&
    input.activeRuns.length === 0
  ) {
    return {
      activeRunSessionIds,
      backgroundedRunSessionIds,
      unreadDoneSessionIds,
      running: false,
      currentRunId: null,
      thinkingRunId: null,
      thinkingStatus: null,
      pendingResume: null,
      queuedMessages: [],
      stoppingRunId:
        state.stoppingRunId === state.currentRunId ? null : state.stoppingRunId,
      ...(terminalRunIds !== state.terminalRunIds ? { terminalRunIds } : {}),
    };
  }

  return {
    activeRunSessionIds,
    backgroundedRunSessionIds,
    unreadDoneSessionIds,
    ...(current
      ? {
          running: true,
          currentRunId: current.runId ?? state.currentRunId,
        }
      : {}),
    ...(terminalRunIds !== state.terminalRunIds ? { terminalRunIds } : {}),
  };
}

export function reduceRunCompleted(
  state: State,
  input: { runId: string | null; sessionId?: string | null; clearApprovals?: boolean },
): RunLifecyclePatch {
  return reduceTerminalRun(state, input, "completed");
}

export function reduceRunFailed(
  state: State,
  input: { runId: string | null; sessionId?: string | null },
): RunLifecyclePatch {
  return reduceTerminalRun(state, input, "failed");
}

export function reduceForegroundRunCleared(
  state: State,
  input: {
    runId: string | null;
    sessionId?: string | null;
    clearApprovals?: boolean;
    markBackgrounded?: boolean;
  },
): RunLifecyclePatch {
  return reduceForegroundInactiveRun(state, input, false);
}

export function reduceBackgroundedRunObserved(
  state: State,
  input: { sessionId: string },
): RunLifecyclePatch {
  const backgroundedRunSessionIds = new Set(state.backgroundedRunSessionIds);
  backgroundedRunSessionIds.add(input.sessionId);
  return { backgroundedRunSessionIds };
}

export function reduceActiveActivityBackgrounded(state: State): RunLifecyclePatch {
  return backgroundActiveActivity(state);
}

export function reduceApprovalRequested(
  state: State,
  approval: ApprovalState,
): RunLifecyclePatch {
  const pendingApprovals = state.pendingApprovals.filter(
    (item) => item.toolId !== approval.toolId,
  );
  pendingApprovals.push(approval);
  return { pendingApprovals };
}

export function reduceApprovalResolved(
  state: State,
  toolId: string,
): RunLifecyclePatch {
  return {
    pendingApprovals: state.pendingApprovals.filter((item) => item.toolId !== toolId),
    reviewingApprovalToolId:
      state.reviewingApprovalToolId === toolId ? null : state.reviewingApprovalToolId,
  };
}

export function reduceQueuedMessageAdded(
  state: State,
  message: QueuedMessage,
): RunLifecyclePatch {
  return { queuedMessages: [...state.queuedMessages, message] };
}

export function reduceQueuedMessageStatus(
  state: State,
  clientId: string,
  status: QueuedMessageStatus,
): RunLifecyclePatch {
  return {
    queuedMessages: state.queuedMessages.map((message) =>
      message.clientId === clientId ? { ...message, status } : message,
    ),
  };
}

export function reduceQueuedMessageRemoved(
  state: State,
  clientId: string,
): RunLifecyclePatch {
  return {
    queuedMessages: state.queuedMessages.filter((message) => message.clientId !== clientId),
  };
}

export function reduceQueuedMessagesCleared(): RunLifecyclePatch {
  return { queuedMessages: [] };
}

export function reduceQueuedMessagesPersisted(
  state: State,
  persistedIds: Set<string>,
): RunLifecyclePatch {
  return {
    queuedMessages: state.queuedMessages.filter(
      (message) => !persistedIds.has(message.clientId),
    ),
  };
}

export function reduceCancellingQueuedMessagesReset(state: State): RunLifecyclePatch {
  return {
    queuedMessages: state.queuedMessages.map((message) =>
      message.status === "cancelling" ? { ...message, status: "pending" } : message,
    ),
  };
}

export function reduceRunStopRequested(
  state: State,
  runId: string,
): RunLifecyclePatch {
  if (state.currentRunId && state.currentRunId !== runId) return {};
  return { stoppingRunId: runId };
}

export function reduceRunStopCleared(
  state: State,
  runId: string,
): RunLifecyclePatch {
  if (state.stoppingRunId !== runId) return {};
  return { stoppingRunId: null };
}

function reduceTerminalRun(
  state: State,
  input: { runId: string | null; sessionId?: string | null; clearApprovals?: boolean },
  _phase: "completed" | "failed",
): RunLifecyclePatch {
  return reduceForegroundInactiveRun(state, input, true);
}

function reduceForegroundInactiveRun(
  state: State,
  input: {
    runId: string | null;
    sessionId?: string | null;
    clearApprovals?: boolean;
    markBackgrounded?: boolean;
  },
  terminal: boolean,
): RunLifecyclePatch {
  if (
    !input.runId &&
    input.sessionId &&
    state.currentSessionId &&
    input.sessionId !== state.currentSessionId
  ) {
    const { activeRunSessionIds, backgroundedRunSessionIds } = clearForegroundSession(state, input.sessionId, input.markBackgrounded);
    return {
      activeRunSessionIds,
      backgroundedRunSessionIds,
      unreadDoneSessionIds: terminal
        ? unreadAfterLiveSetChange(state, activeRunSessionIds, backgroundedRunSessionIds)
        : state.unreadDoneSessionIds,
    };
  }

  if (
    input.runId &&
    input.sessionId &&
    state.currentSessionId &&
    input.sessionId !== state.currentSessionId &&
    state.currentRunId !== input.runId
  ) {
    const { activeRunSessionIds, backgroundedRunSessionIds } = clearForegroundSession(state, input.sessionId, input.markBackgrounded);
    const terminalRunIds = terminal
      ? addTerminalRunId(state.terminalRunIds, input.runId)
      : state.terminalRunIds;
    return {
      activeRunSessionIds,
      backgroundedRunSessionIds,
      unreadDoneSessionIds: terminal
        ? unreadAfterLiveSetChange(state, activeRunSessionIds, backgroundedRunSessionIds)
        : state.unreadDoneSessionIds,
      ...(terminalRunIds !== state.terminalRunIds ? { terminalRunIds } : {}),
    };
  }

  if (input.runId && state.currentRunId && state.currentRunId !== input.runId) {
    const terminalRunIds = terminal
      ? addTerminalRunId(state.terminalRunIds, input.runId)
      : state.terminalRunIds;
    if (!input.sessionId || input.sessionId === state.currentSessionId) {
      if (!input.markBackgrounded || !input.sessionId) {
        return terminalRunIds !== state.terminalRunIds ? { terminalRunIds } : {};
      }
      const backgroundedRunSessionIds = new Set(state.backgroundedRunSessionIds);
      backgroundedRunSessionIds.add(input.sessionId);
      return {
        backgroundedRunSessionIds,
        ...(terminalRunIds !== state.terminalRunIds ? { terminalRunIds } : {}),
      };
    }

    const { activeRunSessionIds, backgroundedRunSessionIds } = clearForegroundSession(state, input.sessionId, input.markBackgrounded);
    return {
      activeRunSessionIds,
      backgroundedRunSessionIds,
      unreadDoneSessionIds: terminal
        ? unreadAfterLiveSetChange(state, activeRunSessionIds, backgroundedRunSessionIds)
        : state.unreadDoneSessionIds,
      ...(terminalRunIds !== state.terminalRunIds ? { terminalRunIds } : {}),
    };
  }

  const sessionId = input.sessionId ?? state.currentSessionId;
  const { activeRunSessionIds, backgroundedRunSessionIds } = clearForegroundSession(state, sessionId, input.markBackgrounded);

  const unreadDoneSessionIds = terminal
    ? unreadAfterLiveSetChange(state, activeRunSessionIds, backgroundedRunSessionIds)
    : state.unreadDoneSessionIds;
  const terminalRunIds = terminal && input.runId
    ? addTerminalRunId(state.terminalRunIds, input.runId)
    : state.terminalRunIds;

  return {
    running: false,
    currentRunId: null,
    thinkingRunId: null,
    thinkingStatus: null,
    activeRunSessionIds,
    backgroundedRunSessionIds,
    unreadDoneSessionIds,
    pendingResume: null,
    queuedMessages: [],
    stoppingRunId:
      input.runId && state.stoppingRunId === input.runId ? null : state.stoppingRunId,
    ...(terminalRunIds !== state.terminalRunIds ? { terminalRunIds } : {}),
    ...(input.clearApprovals
      ? { pendingApprovals: [], reviewingApprovalToolId: null }
      : {}),
    ...((terminal || input.clearApprovals) ? { pendingConnections: [] } : {}),
  };
}

function clearForegroundSession(
  state: State,
  sessionId: string | null | undefined,
  markBackgrounded?: boolean,
): { activeRunSessionIds: Set<string>; backgroundedRunSessionIds: Set<string> } {
  const activeRunSessionIds = new Set(state.activeRunSessionIds);
  const backgroundedRunSessionIds = new Set(state.backgroundedRunSessionIds);
  if (sessionId) {
    activeRunSessionIds.delete(sessionId);
    if (markBackgrounded) backgroundedRunSessionIds.add(sessionId);
  }
  return { activeRunSessionIds, backgroundedRunSessionIds };
}

function unreadAfterLiveSetChange(
  state: State,
  nextForeground: Set<string>,
  nextBackgrounded: Set<string> = new Set(),
): Set<string> {
  let unread = state.unreadDoneSessionIds;
  const previousLive = unionSets(state.activeRunSessionIds, state.backgroundedRunSessionIds);
  const nextLive = unionSets(nextForeground, nextBackgrounded);
  for (const prev of previousLive) {
    if (!nextLive.has(prev) && prev !== state.currentSessionId) {
      if (unread === state.unreadDoneSessionIds) unread = new Set(unread);
      unread.add(prev);
    }
  }
  return unread;
}

function unionSets(left: Set<string>, right: Set<string>): Set<string> {
  if (right.size === 0) return left;
  const next = new Set(left);
  for (const item of right) next.add(item);
  return next;
}

function backgroundActiveActivity(state: State): Pick<RunLifecyclePatch, "activeActivityId" | "messages"> {
  if (!state.activeActivityId) return { activeActivityId: null };
  const existing = state.messages.get(state.activeActivityId);
  if (!existing?.activity) return { activeActivityId: null };
  const messages = new Map(state.messages);
  messages.set(state.activeActivityId, {
    ...existing,
    activity: {
      ...existing.activity,
      done: true,
      label: "Backgrounded",
      backgrounded: true,
      items: existing.activity.items.map((item) =>
        item.status === "ongoing" || item.result == null
          ? { ...item, status: "backgrounded" as const }
          : item,
      ),
    },
  });
  return { activeActivityId: null, messages };
}

function addTerminalRunId(previous: Set<string>, runId: string): Set<string> {
  const next = new Set(previous);
  next.add(runId);
  while (next.size > 200) {
    const oldest = next.values().next().value;
    if (!oldest) break;
    next.delete(oldest);
  }
  return next;
}

function isTerminalStatus(status: string | null | undefined): boolean {
  return (
    status === "completed" ||
    status === "cancelled" ||
    status === "error" ||
    status === "failed" ||
    status === "interrupted"
  );
}

function matchesCurrentRun(state: State, run: RunStatusSnapshot): boolean {
  if (state.currentSessionId && state.currentSessionId !== run.sessionId) return false;
  if (state.currentRunId) return state.currentRunId === run.runId;
  return state.running && state.activeRunSessionIds.has(run.sessionId);
}

function isBackgroundedStatus(run: RunStatusSnapshot): boolean {
  return run.backgrounded === true || run.status === "backgrounded";
}

function isForegroundStatus(status: string | null | undefined): boolean {
  return status === "pending" || status === "running";
}
