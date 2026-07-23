import type { CommandOutcome } from "@/api/commands";
import type { ConnectionAction, ConnectionState, ServerEvent } from "@/api/events";

export interface CommandActivity {
  id: string;
  name: string;
  status: "running" | "completed" | "failed";
  preview?: string | null;
}

export interface CommandApproval {
  toolId: string;
  name: string;
  preview: string | null;
  diff: string | null;
}

export interface CommandConnection {
  runId: string;
  toolId: string;
  integrationId: string;
  connectionId: string;
  label: string;
  reason: ConnectionState;
  detail: string;
  capability: string;
  action: ConnectionAction;
  settingsTab: string;
  requiredScopes: string[];
  source: "recovery" | "suggestion";
}

export interface CommandSidecarState {
  open: boolean;
  clientId: string | null;
  query: string;
  runId: string | null;
  sessionId: string | null;
  status: "idle" | "starting" | "running" | "completed" | "failed" | "cancelled";
  activities: CommandActivity[];
  approval: CommandApproval | null;
  connection: CommandConnection | null;
  outcome: CommandOutcome | null;
  error: string | null;
}

export function createCommandSidecarState(): CommandSidecarState {
  return {
    open: false,
    clientId: null,
    query: "",
    runId: null,
    sessionId: null,
    status: "idle",
    activities: [],
    approval: null,
    connection: null,
    outcome: null,
    error: null,
  };
}

function isString(value: unknown, max = 2000): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= max;
}

function isDestination(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  const destination = value as Record<string, unknown>;
  switch (destination.kind) {
    case "home":
      return Object.keys(destination).every((key) => key === "kind");
    case "session":
      return isString(destination.session_id, 200);
    case "settings":
      return destination.tab === undefined || destination.tab === null || isString(destination.tab, 40);
    case "automation":
      return destination.task_id === undefined || destination.task_id === null || isString(destination.task_id, 200);
    case "memory":
      return destination.path === undefined || destination.path === null || isString(destination.path, 1000);
    case "area":
      return isString(destination.area_id, 200);
    default:
      return false;
  }
}

export function parseCommandOutcome(value: unknown): CommandOutcome | null {
  if (!value || typeof value !== "object") return null;
  const outcome = value as Record<string, unknown>;
  if (!["completed", "needs_input", "unsupported", "failed"].includes(String(outcome.status))) return null;
  if (!isString(outcome.summary, 500)) return null;
  if (outcome.destination !== undefined && outcome.destination !== null && !isDestination(outcome.destination)) return null;
  if (outcome.prompt !== undefined && outcome.prompt !== null && !isString(outcome.prompt, 500)) return null;
  if (outcome.choices !== undefined) {
    if (!Array.isArray(outcome.choices) || outcome.choices.length > 5) return null;
    if (!outcome.choices.every((choice) => {
      if (!choice || typeof choice !== "object") return false;
      const row = choice as Record<string, unknown>;
      return isString(row.label, 120) && isString(row.query, 500);
    })) return null;
  }
  return outcome as unknown as CommandOutcome;
}

export function reduceCommandEvent(
  state: CommandSidecarState,
  event: ServerEvent,
): CommandSidecarState {
  switch (event.type) {
    case "RUN_STARTED":
      return { ...state, runId: event.run_id, status: "running", error: null };
    case "TOOL_CALL_START":
      return {
        ...state,
        activities: [
          ...state.activities.filter((item) => item.id !== event.tool_call_id),
          {
            id: event.tool_call_id,
            name: event.display_name || event.tool_call_name,
            status: "running" as const,
          },
        ].slice(-8),
      };
    case "TOOL_CALL_RESULT":
      return {
        ...state,
        activities: state.activities.map((item) =>
          item.id === event.tool_call_id
            ? { ...item, status: event.is_error ? "failed" : "completed", preview: event.preview ?? null }
            : item,
        ),
      };
    case "approval_needed":
      return {
        ...state,
        approval: {
          toolId: event.tool_id,
          name: event.name,
          preview: event.content_preview ?? null,
          diff: event.diff ?? null,
        },
      };
    case "connection_needed":
      return {
        ...state,
        connection: {
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
        },
      };
    case "command_completed": {
      const outcome = parseCommandOutcome(event.outcome);
      if (!outcome) {
        return { ...state, status: "failed", outcome: null, error: "Invalid command result." };
      }
      return {
        ...state,
        status: outcome.status === "failed" ? "failed" : "completed",
        outcome,
        error: null,
      };
    }
    case "run_cancelled":
      return { ...state, status: "cancelled" };
    case "RUN_ERROR":
      return { ...state, status: "failed", error: event.message };
    default:
      return state;
  }
}
