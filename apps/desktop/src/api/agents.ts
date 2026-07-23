import { apiWithConfig, type AppConfig } from "@/api/core";
import type { BackgroundTaskSummary } from "@/api/types";

export interface ChildAgentResult {
  task_id: string;
  child_run_id: string;
  session_id: string;
  status: "running" | "activity" | "completed" | "failed" | "cancelled" | "interrupted" | "cancel_requested" | string;
  terminal: boolean;
  result?: string | null;
  result_ref?: string | null;
}

export async function listChildAgentsApi(
  config: AppConfig,
  sessionId: string,
): Promise<BackgroundTaskSummary[]> {
  const r = await apiWithConfig<{ tasks: BackgroundTaskSummary[] }>(
    config,
    `/chat/child-agents?session_id=${encodeURIComponent(sessionId)}`,
  );
  return r.tasks;
}

export async function getChildAgentResultApi(
  config: AppConfig,
  sessionId: string,
  childRunId: string,
  options: { wait?: boolean; timeoutSeconds?: number } = {},
): Promise<ChildAgentResult> {
  const query = new URLSearchParams({ session_id: sessionId });
  if (options.wait) query.set("wait", "true");
  if (options.timeoutSeconds != null) query.set("timeout_seconds", String(options.timeoutSeconds));
  return apiWithConfig<ChildAgentResult>(
    config,
    `/chat/child-agents/${encodeURIComponent(childRunId)}/result?${query.toString()}`,
  );
}

export async function cancelChildAgentApi(
  config: AppConfig,
  sessionId: string,
  childRunId: string,
): Promise<void> {
  await apiWithConfig(
    config,
    `/chat/child-agents/${encodeURIComponent(childRunId)}/cancel?session_id=${encodeURIComponent(sessionId)}`,
    { method: "POST" },
  );
}
