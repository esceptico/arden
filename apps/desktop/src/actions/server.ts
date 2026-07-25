import { type AppConfig, saveConfig, validateConnection } from "@/api/core";
import { type ServerConfigPatch, getServerConfig, getServerModels, patchServerConfig } from "@/api/settings";
import { getState } from "@/stores";
import { refresh } from "@/actions/bootstrap";

export async function fetchServerConfig(): Promise<void> {
  const s = getState();
  try {
    const [cfg, models] = await Promise.all([
      getServerConfig(s.config),
      getServerModels(s.config).catch(() => null),
    ]);
    s.setServerConfig(cfg);
    if (models) s.setServerModels(models);
  } catch {
    /* server config is optional UI surface — don't surface this error */
  }
}

export async function updateServerConfig(patch: ServerConfigPatch): Promise<void> {
  const s = getState();
  const next = await patchServerConfig(s.config, patch);
  s.setServerConfig(next);
}

export async function saveAndReconnect(next: AppConfig): Promise<void> {
  const s = getState();
  let connected = false;
  s.setConnectionSaving(true);
  s.setConnectionError(null);
  try {
    await validateConnection(next);
    const saved = await saveConfig(next);
    s.setConfig(saved);
    await refresh();
    connected = getState().connected;
  } catch (error) {
    s.setConnectionError(error instanceof Error ? error.message : String(error));
  } finally {
    const current = getState();
    current.setConnectionSaving(false);
    if (connected) current.closeSettings();
  }
}
