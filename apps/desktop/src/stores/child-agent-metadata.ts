import type { ActivityItem, ChildAgentRef } from "@/stores/types";

export type ToolResultData = {
  usage?: ActivityItem["usage"];
  cost?: number;
  child_agent?: {
    agent_ref?: unknown;
    session_ref?: unknown;
    agent_type?: unknown;
    wait?: unknown;
    status?: unknown;
  };
};

const PUBLIC_REF = /^[a-z0-9]+(?:-[a-z0-9]+)*~[a-z0-9]{6}$/;
const CHILD_AGENT_FIELDS = new Set(["agent_ref", "session_ref", "agent_type", "wait", "status"]);
const CHILD_AGENT_STATUSES = new Set([
  "running",
  "completed",
  "failed",
  "cancelled",
  "interrupted",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object";
}

export function childAgentFromToolResultData(data: unknown): ChildAgentRef | undefined {
  if (!isRecord(data)) return undefined;
  if (!("child_agent" in data)) return undefined;
  const child = data.child_agent;
  if (!isRecord(child)) {
    throw new Error("Persisted child-agent metadata must be an object");
  }
  const unexpected = Object.keys(child).find((field) => !CHILD_AGENT_FIELDS.has(field));
  if (unexpected) {
    throw new Error(`Persisted child-agent metadata has an unknown field: ${unexpected}`);
  }
  if (typeof child.agent_ref !== "string" || !PUBLIC_REF.test(child.agent_ref)) {
    throw new Error("Persisted child-agent metadata has an invalid agent_ref");
  }
  if (
    child.session_ref !== undefined &&
    (typeof child.session_ref !== "string" || child.session_ref !== child.agent_ref)
  ) {
    throw new Error("Persisted child-agent metadata has an invalid session_ref");
  }
  if (typeof child.agent_type !== "string" || !child.agent_type || child.agent_type.length > 80) {
    throw new Error("Persisted child-agent metadata has an invalid agent_type");
  }
  if (typeof child.wait !== "boolean") {
    throw new Error("Persisted child-agent metadata has an invalid wait value");
  }
  if (typeof child.status !== "string" || !CHILD_AGENT_STATUSES.has(child.status)) {
    throw new Error("Persisted child-agent metadata has an invalid status");
  }
  return {
    agentRef: child.agent_ref,
    sessionRef: child.session_ref,
    agentType: child.agent_type,
    wait: child.wait,
    status: child.status,
  };
}
