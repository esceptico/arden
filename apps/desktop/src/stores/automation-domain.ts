export type AutomationStreamPhase =
  | "idle"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "stale"
  | "failed";

export interface AutomationStreamDomainState {
  phase: AutomationStreamPhase;
  statuses: Record<string, string>;
  error: string | null;
  updatedAt: number | null;
}

export function createAutomationStreamDomainState(): AutomationStreamDomainState {
  return {
    phase: "idle",
    statuses: {},
    error: null,
    updatedAt: null,
  };
}

export function reduceAutomationStreamConnecting(
  state: AutomationStreamDomainState,
  now = Date.now(),
): AutomationStreamDomainState {
  const phase = state.phase === "idle" ? "connecting" : "reconnecting";
  return { ...state, phase, error: null, updatedAt: now };
}

export function reduceAutomationStreamConnected(
  state: AutomationStreamDomainState,
  now = Date.now(),
): AutomationStreamDomainState {
  return { ...state, phase: "connected", error: null, updatedAt: now };
}

export function reduceAutomationStreamStale(
  state: AutomationStreamDomainState,
  now = Date.now(),
): AutomationStreamDomainState {
  return { ...state, phase: "stale", updatedAt: now };
}

export function reduceAutomationStreamFailed(
  state: AutomationStreamDomainState,
  error: string,
  now = Date.now(),
): AutomationStreamDomainState {
  return { ...state, phase: "failed", error, updatedAt: now };
}

export function reduceAutomationStreamIdle(
  state: AutomationStreamDomainState,
  now = Date.now(),
): AutomationStreamDomainState {
  return { ...state, phase: "idle", error: null, updatedAt: now };
}

export function reduceAutomationStreamReset(
  state: AutomationStreamDomainState,
  now = Date.now(),
): AutomationStreamDomainState {
  return { ...state, statuses: {}, updatedAt: now };
}

export function reduceAutomationProgress(
  state: AutomationStreamDomainState,
  taskId: string,
  status: string,
  now = Date.now(),
): AutomationStreamDomainState {
  return {
    ...state,
    statuses: { ...state.statuses, [taskId]: status },
    updatedAt: now,
  };
}

export function reduceAutomationFinished(
  state: AutomationStreamDomainState,
  taskId: string,
  now = Date.now(),
): AutomationStreamDomainState {
  if (!(taskId in state.statuses)) return state;
  const statuses = { ...state.statuses };
  delete statuses[taskId];
  return { ...state, statuses, updatedAt: now };
}
