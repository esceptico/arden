import { apiWithConfig, type AppConfig } from "@/api/core";
import type { SettingsTabId } from "@/stores/types";

export type AppDestination =
  | { kind: "home" }
  | { kind: "session"; session_id: string }
  | { kind: "settings"; tab?: SettingsTabId | null }
  | { kind: "automation"; task_id?: string | null }
  | { kind: "memory"; path?: string | null }
  | { kind: "area"; area_id: string };

export interface CommandChoice {
  label: string;
  query: string;
}

export interface CommandOutcome {
  status: "completed" | "needs_input" | "unsupported" | "failed";
  summary: string;
  destination?: AppDestination | null;
  prompt?: string | null;
  choices?: CommandChoice[];
}

export interface CommandRunResponse {
  run_id: string;
  session_id: string;
  status: string;
}

export async function startCommandRunApi(
  config: AppConfig,
  payload: { query: string; client_id: string; current_destination?: AppDestination | null },
): Promise<CommandRunResponse> {
  return apiWithConfig<CommandRunResponse>(config, "/command/runs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
