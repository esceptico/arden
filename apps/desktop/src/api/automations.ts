import { apiWithConfig, type AppConfig } from "@/api/core";
import type {
  Automation,
  AutomationRun,
  AutomationSuggestion,
  CreateAutomationPayload,
  UpdateAutomationPayload,
} from "@/api/types";

export async function listAutomationsApi(config: AppConfig): Promise<Automation[]> {
  const r = await apiWithConfig<{ automations: Automation[] }>(config, "/automations");
  return r.automations;
}

export async function listAutomationRunsApi(
  config: AppConfig,
  taskId: string,
  limit = 30,
): Promise<AutomationRun[]> {
  const r = await apiWithConfig<{ runs: AutomationRun[] }>(
    config,
    `/automations/${encodeURIComponent(taskId)}/runs?limit=${limit}`,
  );
  return r.runs;
}

export async function createAutomationApi(
  config: AppConfig,
  payload: CreateAutomationPayload,
): Promise<Automation> {
  return apiWithConfig<Automation>(config, "/automations", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateAutomationApi(
  config: AppConfig,
  taskId: string,
  patch: UpdateAutomationPayload,
): Promise<Automation> {
  return apiWithConfig<Automation>(config, `/automations/${encodeURIComponent(taskId)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export async function generateAutomationDescriptionApi(
  config: AppConfig,
  taskId: string,
): Promise<Automation> {
  return apiWithConfig<Automation>(
    config,
    `/automations/${encodeURIComponent(taskId)}/description/generate`,
    { method: "POST" },
  );
}

export async function toggleAutomationApi(
  config: AppConfig,
  taskId: string,
): Promise<Automation> {
  return apiWithConfig<Automation>(config, `/automations/${encodeURIComponent(taskId)}/toggle`, {
    method: "POST",
  });
}

export async function runAutomationApi(config: AppConfig, taskId: string): Promise<void> {
  await apiWithConfig(config, `/automations/${encodeURIComponent(taskId)}/run`, { method: "POST" });
}

export async function deleteAutomationApi(config: AppConfig, taskId: string): Promise<void> {
  await apiWithConfig(config, `/automations/${encodeURIComponent(taskId)}`, { method: "DELETE" });
}

/** Active suggestions are server-authored candidates, never client-side
 * guesses. The New menu intentionally uses only the most relevant one. */
export async function listAutomationSuggestionsApi(config: AppConfig): Promise<AutomationSuggestion[]> {
  const r = await apiWithConfig<{ suggestions: AutomationSuggestion[] }>(config, "/automations/suggestions");
  return r.suggestions;
}

/** Flatten the validated first trigger into the existing draft-editor seed,
 * retaining the server id so a successful Create consumes the suggestion. */
export function suggestionToPayload(suggestion: AutomationSuggestion): CreateAutomationPayload {
  const [trigger] = suggestion.triggers;
  const schedule = trigger.type === "event"
    ? {
      trigger_type: "event" as const,
      event_type: trigger.event_type,
      lead_minutes: trigger.lead_minutes,
    }
    : {
      trigger_type: "time" as const,
      at: trigger.at,
      days: trigger.days,
      every: trigger.every,
      start: trigger.start,
      end: trigger.end,
    };
  return {
    name: suggestion.name,
    prompt: suggestion.prompt,
    ...(suggestion.description ? { description: suggestion.description } : {}),
    from_suggestion_id: suggestion.id,
    ...schedule,
  };
}
