import { apiWithConfig, type AppConfig } from "@/api/core";
import type {
  Automation,
  AutomationRun,
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
