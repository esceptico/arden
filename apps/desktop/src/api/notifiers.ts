import { apiWithConfig, type AppConfig } from "@/api/core";

export interface NotifierConfigItem {
  name: string;
  type: string;
  config: Record<string, string>;
  created_at: string;
}

/** Per-type field lists from the server (the source of truth for the
 *  editor form), plus connected gmail accounts for the email type. */
export interface NotifierTypes {
  [type: string]: { fields: string[]; accounts?: string[] };
}

export async function listNotifierConfigsApi(config: AppConfig): Promise<NotifierConfigItem[]> {
  const r = await apiWithConfig<{ configs: NotifierConfigItem[] }>(config, "/notifiers/configs");
  return r.configs;
}

export async function listNotifierTypesApi(config: AppConfig): Promise<NotifierTypes> {
  const r = await apiWithConfig<{ types: NotifierTypes }>(config, "/notifiers/types");
  return r.types;
}

export async function createNotifierApi(
  config: AppConfig,
  payload: { name: string; type: string; config: Record<string, string> },
): Promise<NotifierConfigItem> {
  return apiWithConfig<NotifierConfigItem>(config, "/notifiers/configs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateNotifierApi(
  config: AppConfig,
  name: string,
  payload: { name?: string; config: Record<string, string> },
): Promise<NotifierConfigItem> {
  return apiWithConfig<NotifierConfigItem>(config, `/notifiers/configs/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function deleteNotifierApi(config: AppConfig, name: string): Promise<void> {
  await apiWithConfig(config, `/notifiers/configs/${encodeURIComponent(name)}`, { method: "DELETE" });
}

export async function testNotifierApi(config: AppConfig, name: string): Promise<void> {
  await apiWithConfig(config, `/notifiers/configs/${encodeURIComponent(name)}/test`, { method: "POST" });
}
