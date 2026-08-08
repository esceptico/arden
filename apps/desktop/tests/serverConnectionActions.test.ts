import { afterEach, expect, test } from "bun:test";
import { saveAndReconnect } from "@/actions/server";
import type { ServerConfig } from "@/api/types";
import { getState, setState } from "@/stores/index";

const originalFetch = globalThis.fetch;
const originalState = getState();

const SERVER_CONFIG: ServerConfig = {
  chat_model: "new/model",
  chat_model_max_context: 256_000,
  compaction_token_limit: 220_000,
  compaction_token_trigger: 200_000,
  research_model: "new/model",
  workflow_model: "new/model",
  memory_model: "new/model",
  embedding_model: null,
  web_search: "none",
  web_search_provider: "none",
  max_depth: 3,
  reasoning_effort: null,
  reasoning_efforts: [],
  model_reasoning_efforts: {},
  compression_threshold: 0.8,
  max_messages: 100,
  compression_keep_ratio: 0.5,
  summary_max_tokens: 2_000,
  max_space_gb: null,
  storage_backup_retention_days: 7,
  storage_allow_archived_cleanup: false,
  storage_allow_delete_cold_chats: false,
  storage_allow_current_cleanup: false,
  storage_current_inactive_days: 30,
  storage_current_minimum: 10,
  memory_enabled: true,
  integrations: {},
  tool_overrides: {},
};

const SERVER_MODELS = {
  models: ["new/model"],
  groups: [{ provider: "new", label: "New", models: ["new/model"] }],
  reasoning_efforts: {},
  chat_model: "new/model",
  research_model: "new/model",
  workflow_model: "new/model",
  memory_model: "new/model",
};

afterEach(() => {
  globalThis.fetch = originalFetch;
  setState({
    config: originalState.config,
    settingsOpen: originalState.settingsOpen,
    connectionSaving: originalState.connectionSaving,
    connectionError: originalState.connectionError,
    connected: originalState.connected,
    currentSessionId: originalState.currentSessionId,
    serverConfig: originalState.serverConfig,
    serverModels: originalState.serverModels,
    serverConfigError: originalState.serverConfigError,
  });
});

test("successful reconnect clears the saving guard before closing Settings", async () => {
  globalThis.fetch = async (input) => {
    const path = new URL(String(input)).pathname;
    if (path === "/config" || path === "/models") {
      expect(getState().serverConfig).toBeNull();
      expect(getState().serverModels).toBeNull();
    }
    const body =
      path === "/health"
        ? { auth: true, version: "test", has_providers: true }
        : path === "/areas"
          ? { areas: [] }
        : path === "/sessions"
            ? { sessions: [] }
            : path === "/config"
              ? SERVER_CONFIG
              : path === "/models"
                ? SERVER_MODELS
                : {};
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };

  setState({
    settingsOpen: true,
    connectionSaving: false,
    connectionError: null,
    connected: false,
    currentSessionId: null,
    serverConfig: { ...SERVER_CONFIG, chat_model: "old/model", chat_model_max_context: 999_999 },
    serverModels: { ...SERVER_MODELS, models: ["old/model"] },
  });

  await saveAndReconnect({ serverUrl: "http://127.0.0.1:6878", apiKey: "qa-key" });

  expect(getState().connected).toBe(true);
  expect(getState().connectionSaving).toBe(false);
  expect(getState().settingsOpen).toBe(false);
  expect(getState().connectionError).toBeNull();
  expect(getState().serverConfig?.chat_model_max_context).toBe(256_000);
  expect(getState().serverModels?.models).toEqual(["new/model"]);
});
