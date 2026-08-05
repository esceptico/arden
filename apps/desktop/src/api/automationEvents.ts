import type { RuntimeRunStatus } from "@/api/events";
import type { AppDestination } from "@/api/navigation";
import type { SessionListItem, SessionType } from "@/api/types";
import type { SettingsTabId } from "@/stores/types";

export type AutomationEvent =
  | { type: "automation_progress"; task_id: string; status: string; seq?: number }
  | { type: "automation_finished"; task_id: string; result: string | null; seq?: number }
  | { type: "session_created"; session: SessionListItem; seq?: number }
  | { type: "session_activity"; session: SessionListItem; seq?: number }
  | { type: "memory_changed"; paths: string[]; revision?: string | null; seq?: number }
  | { type: "areas_changed"; keys: string[]; seq?: number }
  | { type: "navigation_requested"; origin_session_id: string; destination: AppDestination; label: string; seq?: number }
  | { type: "stream_keepalive"; latest_seq: number; seq?: number }
  | { type: "stream_reset"; reason: string; seq?: number };

type JsonObject = Record<string, unknown>;

const SESSION_TYPES: readonly SessionType[] = ["chat", "channel", "agent"];
const RUN_STATUSES: readonly RuntimeRunStatus[] = [
  "pending",
  "running",
  "backgrounded",
  "interrupted",
  "error",
  "failed",
  "cancelled",
  "completed",
];
const SETTINGS_TABS: readonly SettingsTabId[] = [
  "connection",
  "providers",
  "integrations",
  "notifiers",
  "models",
  "agent",
  "context",
  "tools",
  "mcp",
  "appearance",
  "storage",
  "archive",
];

function object(value: unknown, label: string): JsonObject {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  return value as JsonObject;
}

function string(value: JsonObject, field: string): string {
  const result = value[field];
  if (typeof result !== "string") throw new Error(`${field} must be a string`);
  return result;
}

function nullableString(value: JsonObject, field: string): string | null {
  const result = value[field];
  if (result !== null && typeof result !== "string") {
    throw new Error(`${field} must be a string or null`);
  }
  return result;
}

function optionalNullableString(value: JsonObject, field: string): string | null | undefined {
  return value[field] === undefined ? undefined : nullableString(value, field);
}

function integer(value: JsonObject, field: string): number {
  const result = value[field];
  if (!Number.isInteger(result)) throw new Error(`${field} must be an integer`);
  return result as number;
}

function optionalInteger(value: JsonObject, field: string): number | undefined {
  return value[field] === undefined ? undefined : integer(value, field);
}

function optionalBoolean(value: JsonObject, field: string): boolean | undefined {
  const result = value[field];
  if (result !== undefined && typeof result !== "boolean") {
    throw new Error(`${field} must be a boolean`);
  }
  return result;
}

function optionalChoice<T extends string>(
  value: JsonObject,
  field: string,
  choices: readonly T[],
): T | undefined {
  const result = value[field];
  if (result === undefined) return undefined;
  if (typeof result !== "string" || !choices.includes(result as T)) {
    throw new Error(`${field} has an unsupported value`);
  }
  return result as T;
}

function optionalNullableChoice<T extends string>(
  value: JsonObject,
  field: string,
  choices: readonly T[],
): T | null | undefined {
  return value[field] === null ? null : optionalChoice(value, field, choices);
}

function stringArray(value: JsonObject, field: string): string[] {
  const result = value[field];
  if (!Array.isArray(result) || !result.every((item) => typeof item === "string")) {
    throw new Error(`${field} must be an array of strings`);
  }
  return result;
}

function sessionListItem(value: unknown): SessionListItem {
  const session = object(value, "session");
  return {
    session_id: string(session, "session_id"),
    started_at: string(session, "started_at"),
    last_activity: string(session, "last_activity"),
    name: nullableString(session, "name"),
    message_count: integer(session, "message_count"),
    area_id: optionalNullableString(session, "area_id"),
    chat_model: optionalNullableString(session, "chat_model"),
    session_type: optionalChoice(session, "session_type", SESSION_TYPES),
    origin_automation_id: optionalNullableString(session, "origin_automation_id"),
    parent_session_id: optionalNullableString(session, "parent_session_id"),
    parent_tool_call_id: optionalNullableString(session, "parent_tool_call_id"),
    agent_type: optionalNullableString(session, "agent_type"),
    agent_status: optionalNullableString(session, "agent_status"),
    active_run_id: optionalNullableString(session, "active_run_id"),
    run_status: optionalNullableChoice(session, "run_status", RUN_STATUSES),
    checkpoint_seq: optionalInteger(session, "checkpoint_seq"),
    latest_event_seq: optionalInteger(session, "latest_event_seq"),
    is_active: optionalBoolean(session, "is_active"),
    pending_approvals_count: optionalInteger(session, "pending_approvals_count"),
    run_error_code: optionalNullableString(session, "run_error_code"),
    run_stop_reason: optionalNullableString(session, "run_stop_reason"),
  };
}

function destination(value: unknown): AppDestination {
  const target = object(value, "destination");
  const kind = string(target, "kind");
  switch (kind) {
    case "home":
      return { kind };
    case "session":
      return { kind, session_id: string(target, "session_id") };
    case "settings":
      return { kind, tab: optionalNullableChoice(target, "tab", SETTINGS_TABS) };
    case "automation":
      return { kind, task_id: optionalNullableString(target, "task_id") };
    case "memory":
      return { kind, path: optionalNullableString(target, "path") };
    case "area":
      return { kind, area_id: string(target, "area_id") };
    default:
      throw new Error(`unsupported destination kind: ${kind}`);
  }
}

export function parseAutomationEvent(data: string): AutomationEvent {
  const event = object(JSON.parse(data), "automation event");
  const type = string(event, "type");
  const seq = optionalInteger(event, "seq");

  switch (type) {
    case "automation_progress":
      return { type, task_id: string(event, "task_id"), status: string(event, "status"), seq };
    case "automation_finished":
      return { type, task_id: string(event, "task_id"), result: nullableString(event, "result"), seq };
    case "session_created":
    case "session_activity":
      return { type, session: sessionListItem(event.session), seq };
    case "memory_changed":
      return {
        type,
        paths: stringArray(event, "paths"),
        revision: optionalNullableString(event, "revision"),
        seq,
      };
    case "areas_changed":
      return { type, keys: stringArray(event, "keys"), seq };
    case "navigation_requested":
      return {
        type,
        origin_session_id: string(event, "origin_session_id"),
        destination: destination(event.destination),
        label: string(event, "label"),
        seq,
      };
    case "stream_keepalive":
      return { type, latest_seq: integer(event, "latest_seq"), seq };
    case "stream_reset":
      return { type, reason: string(event, "reason"), seq };
    default:
      throw new Error(`unsupported automation event type: ${type}`);
  }
}
