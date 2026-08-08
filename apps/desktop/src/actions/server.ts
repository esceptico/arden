import { type AppConfig, saveConfig, validateConnection } from "@/api/core";
import { type ServerConfigPatch, getServerConfig, getServerModels, patchServerConfig } from "@/api/settings";
import { getState } from "@/stores";
import { refresh } from "@/actions/bootstrap";

let serverConfigRequest = 0;

export async function fetchServerConfig(): Promise<void> {
  const request = ++serverConfigRequest;
  const config = getState().config;
  const [configResult, modelsResult] = await Promise.allSettled([
    getServerConfig(config),
    getServerModels(config),
  ]);
  if (request !== serverConfigRequest) return;
  const state = getState();

  if (configResult.status === "rejected") {
    state.setServerConfig(null);
    state.setServerModels(null);
    state.setServerConfigError(errorMessage(configResult.reason));
    return;
  }

  state.setServerConfig(configResult.value);
  if (modelsResult.status === "rejected") {
    state.setServerModels(null);
    state.setServerConfigError(errorMessage(modelsResult.reason));
    return;
  }
  state.setServerModels(modelsResult.value);
  state.setServerConfigError(null);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export async function updateServerConfig(patch: ServerConfigPatch): Promise<void> {
  const s = getState();
  const next = await patchServerConfig(s.config, patch);
  serverConfigRequest += 1;
  s.setServerConfig(next);
  s.setServerConfigError(null);
}

export async function saveAndReconnect(next: AppConfig): Promise<void> {
  const s = getState();
  let connected = false;
  s.setConnectionSaving(true);
  s.setConnectionError(null);
  try {
    await validateConnection(next);
    const saved = await saveConfig(next);
    serverConfigRequest += 1;
    s.setConfig(saved);
    s.setServerConfig(null);
    s.setServerModels(null);
    s.setServerConfigError(null);
    await Promise.all([refresh(), fetchServerConfig()]);
    connected = getState().connected;
  } catch (error) {
    s.setConnectionError(error instanceof Error ? error.message : String(error));
  } finally {
    const current = getState();
    current.setConnectionSaving(false);
    if (connected) current.closeSettings();
  }
}
