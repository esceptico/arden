import type { ActivityItem, BackgroundAgent, BackgroundAgentStatus } from "@/stores/types";

export type { BackgroundAgent } from "@/stores/types";

export type BackgroundAgentRefreshStatus =
  | "idle"
  | "refreshing"
  | "ready"
  | "failed";

export interface BackgroundAgentsDomainState {
  rows: Record<string, BackgroundAgent>;
  openItemIds: Set<string>;
  refreshStatus: BackgroundAgentRefreshStatus;
  refreshError: string | null;
}

export interface BackgroundAgentSnapshot {
  taskId: string;
  agentRef: string;
  childSessionId?: string;
  command: string;
  status?: BackgroundAgentStatus | string | null;
  detail?: string;
  resultRef?: string;
  parentToolCallId?: string;
  agentType?: string;
  wait?: boolean;
  createdAt: number;
}

export type BackgroundAgentUpsert = Omit<BackgroundAgent, "createdAt"> & {
  createdAt?: number;
};

export function createBackgroundAgentsDomainState(): BackgroundAgentsDomainState {
  return {
    rows: {},
    openItemIds: new Set(),
    refreshStatus: "idle",
    refreshError: null,
  };
}

export function backgroundAgentKey(sessionId: string, taskId: string): string {
  return `${sessionId}:${taskId}`;
}

export function isActiveBackgroundAgent(agent: Pick<BackgroundAgent, "status">): boolean {
  return agent.status === "running" || agent.status === "cancel_requested";
}

export function reduceBackgroundAgentsRefreshStarted(
  state: BackgroundAgentsDomainState,
): BackgroundAgentsDomainState {
  return { ...state, refreshStatus: "refreshing", refreshError: null };
}

export function reduceBackgroundAgentsRefreshFailed(
  state: BackgroundAgentsDomainState,
  error: string,
): BackgroundAgentsDomainState {
  return { ...state, refreshStatus: "failed", refreshError: error };
}

export function reduceBackgroundAgentsForSession(
  state: BackgroundAgentsDomainState,
  sessionId: string,
  agents: BackgroundAgentSnapshot[],
  now = Date.now(),
): BackgroundAgentsDomainState {
  let rows = state.rows;
  let rowsChanged = false;
  for (const agent of agents) {
    const key = backgroundAgentKey(sessionId, agent.taskId);
    const prev = rows[key];
    const next: BackgroundAgent = {
      taskId: agent.taskId,
      agentRef: agent.agentRef,
      sessionId,
      childSessionId: agent.childSessionId ?? prev?.childSessionId,
      command: agent.command,
      status: normalizeBackgroundAgentStatus(agent.status ?? prev?.status),
      detail: agent.detail ?? prev?.detail,
      resultRef: agent.resultRef ?? prev?.resultRef,
      parentToolCallId: agent.parentToolCallId ?? prev?.parentToolCallId,
      agentType: agent.agentType ?? prev?.agentType,
      wait: agent.wait ?? prev?.wait,
      createdAt: agent.createdAt,
      updatedAt: now,
    };

    if (prev && isEquivalentBackgroundAgent(prev, next)) {
      continue;
    }

    if (!rowsChanged) {
      rows = { ...state.rows };
      rowsChanged = true;
    }
    rows[key] = next;
  }

  if (!rowsChanged) {
    if (state.refreshStatus === "ready" && state.refreshError === null) {
      return state;
    }
    return { ...state, refreshStatus: "ready", refreshError: null };
  }

  return { ...state, rows, refreshStatus: "ready", refreshError: null };
}

export function reduceBackgroundAgentUpsert(
  state: BackgroundAgentsDomainState,
  agent: BackgroundAgentUpsert,
  now = Date.now(),
): BackgroundAgentsDomainState {
  const key = backgroundAgentKey(agent.sessionId, agent.taskId);
  const prev = state.rows[key];
  return {
    ...state,
    rows: {
      ...state.rows,
      [key]: {
        ...prev,
        ...agent,
        status: normalizeBackgroundAgentStatus(agent.status),
        createdAt: agent.createdAt ?? prev?.createdAt ?? now,
        updatedAt: agent.updatedAt ?? now,
      },
    },
  };
}

function isEquivalentBackgroundAgent(
  prev: BackgroundAgent,
  next: BackgroundAgent,
): boolean {
  return (
    prev.taskId === next.taskId &&
    prev.agentRef === next.agentRef &&
    prev.sessionId === next.sessionId &&
    prev.childSessionId === next.childSessionId &&
    prev.command === next.command &&
    prev.status === next.status &&
    prev.detail === next.detail &&
    prev.resultRef === next.resultRef &&
    prev.parentToolCallId === next.parentToolCallId &&
    prev.agentType === next.agentType &&
    prev.wait === next.wait &&
    prev.createdAt === next.createdAt
  );
}

/** Map a server status onto the client union. Only statuses the server
 *  actually emits for live work count as running — an unrecognized status must
 *  not claim "running" forever, so it settles as "interrupted". */
export function normalizeBackgroundAgentStatus(
  status: BackgroundAgentStatus | string | null | undefined,
): BackgroundAgentStatus {
  if (
    status === "completed" ||
    status === "failed" ||
    status === "cancelled" ||
    status === "interrupted" ||
    status === "cancel_requested"
  ) {
    return status;
  }
  return status === "running" || status === "activity" || status === "started" ? "running" : "interrupted";
}

/** The roster row backing a transcript agent item — matched exactly the way
 *  turnActiveAgents matches (parent tool call id, child run id, child session
 *  id) so chat rows and the sidebar agree on which durable run an item is. */
export function findBackgroundAgentForActivityItem(
  rows: Record<string, BackgroundAgent>,
  item: Pick<ActivityItem, "id" | "runId" | "childAgent">,
  sessionId: string | null,
): BackgroundAgent | undefined {
  if (!sessionId) return undefined;
  for (const agent of Object.values(rows)) {
    if (agent.sessionId !== sessionId) continue;
    if (agent.agentRef && agent.agentRef === item.childAgent?.agentRef) {
      return agent;
    }
    if (
      agent.parentToolCallId &&
      (agent.parentToolCallId === item.id ||
        agent.parentToolCallId === item.childAgent?.parentToolCallId)
    ) {
      return agent;
    }
    if (agent.childSessionId && agent.childSessionId === item.childAgent?.childSessionId) {
      return agent;
    }
    if (agent.taskId === item.childAgent?.childRunId || agent.taskId === item.runId) {
      return agent;
    }
  }
  return undefined;
}
