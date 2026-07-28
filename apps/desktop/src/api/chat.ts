import { apiWithConfig, type AppConfig } from "@/api/core";
import type { ToolOutcome } from "@/api/events";
import type { TodoListItem } from "@/api/types";

export interface HistoryToolCall {
  id: string;
  name: string;
  arguments: string;
  display_name?: string;
  result?: string;
  /** Semantic kind ("tool" | "agent") for the row renderer. Server fills
   *  this from the tool registry at history-read time. */
  kind?: string;
  outcome?: ToolOutcome;
}

export interface HistoryImage {
  media_type: string;
  data: string;
}

export interface HistoryMessage {
  role: "user" | "assistant" | "tool";
  content: string;
  reasoning_content?: string;
  tool_calls?: HistoryToolCall[];
  tool_call_id?: string;
  /** Untrusted persisted tool-result metadata. Validate fields at projection boundaries. */
  data?: unknown;
  images?: HistoryImage[];
  /** Stable client-side id (the same one we streamed for assistant turns).
   *  Available for messages saved after id-based persistence landed; older
   *  sessions may not have it. */
  id?: string;
  /** Stable durable transcript id. */
  message_id?: string;
  /** Durable transcript order within the session. */
  seq?: number;
  /** ISO-8601 UTC timestamp stamped at first save. */
  created_at?: string;
  /** True for system-generated user messages that should stay in the
   *  model's conversation history but be hidden from the transcript UI
   *  (e.g. loop tick prompts). */
  is_meta?: boolean;
}

export interface HistoryPage {
  has_more_before: boolean;
  has_more_after: boolean;
  before?: string | null;
  after?: string | null;
}

export async function submitToolResult(
  config: AppConfig,
  payload: { run_id: string; tool_id: string; result: string; approved: boolean },
): Promise<void> {
  await apiWithConfig(config, "/tools/result", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function cancelRun(
  config: AppConfig,
  runId: string | null,
  sessionId?: string | null,
): Promise<void> {
  // Prefer run_id; fall back to session_id so the server resolves the active
  // run when the client has no reliable run_id (backgrounded/automation runs).
  const body = runId ? { run_id: runId } : { session_id: sessionId ?? null };
  await apiWithConfig(config, "/cancel", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function cancelSubagentApi(
  config: AppConfig,
  runId: string,
  toolCallId: string,
): Promise<void> {
  await apiWithConfig(
    config,
    `/chat/subagents/${encodeURIComponent(toolCallId)}/cancel?run_id=${encodeURIComponent(runId)}`,
    { method: "POST" },
  );
}

export interface TodoOverride {
  items: TodoListItem[];
  explanation: string | null;
  updated_at: string;
}

// Manual todo edits, persisted server-side so the agent sees them on its next
// run. The agent's own update_todos clears the override (its list wins).
export async function getTodoOverrideApi(
  config: AppConfig,
  sessionId: string,
): Promise<TodoOverride | null> {
  return apiWithConfig<TodoOverride | null>(config, `/sessions/${encodeURIComponent(sessionId)}/todo`);
}

export async function setTodoOverrideApi(
  config: AppConfig,
  sessionId: string,
  items: TodoListItem[],
): Promise<void> {
  await apiWithConfig(config, `/sessions/${encodeURIComponent(sessionId)}/todo`, {
    method: "POST",
    body: JSON.stringify({ items }),
  });
}

export async function clearTodoOverrideApi(config: AppConfig, sessionId: string): Promise<void> {
  await apiWithConfig(config, `/sessions/${encodeURIComponent(sessionId)}/todo`, { method: "DELETE" });
}

// Steer a running background agent — deliver a message into its loop at its
// next step. sessionId is the PARENT session that owns the agent.
export async function sendToChildAgentApi(
  config: AppConfig,
  sessionId: string,
  childRunId: string,
  message: string,
): Promise<void> {
  await apiWithConfig(
    config,
    `/chat/child-agents/${encodeURIComponent(childRunId)}/inject?session_id=${encodeURIComponent(sessionId)}`,
    { method: "POST", body: JSON.stringify({ message }) },
  );
}
