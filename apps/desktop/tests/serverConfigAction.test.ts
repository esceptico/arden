import { afterEach, expect, test } from "bun:test";
import { fetchServerConfig, updateServerConfig } from "@/actions/server";
import type { ServerConfig } from "@/api/types";
import { getState, setState } from "@/stores";

const originalDesktop = window.ardenDesktop;
const originalState = getState();

const CONFIG: ServerConfig = {
  chat_model: "openai/gpt",
  chat_model_max_context: 128_000,
  compaction_token_limit: 100_000,
  compaction_token_trigger: 90_000,
  research_model: "openai/gpt",
  workflow_model: "openai/gpt",
  memory_model: "openai/gpt",
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

const MODELS = {
  models: ["openai/gpt"],
  groups: [{ provider: "openai", label: "OpenAI", models: ["openai/gpt"] }],
  reasoning_efforts: {},
  chat_model: "openai/gpt",
  research_model: "openai/gpt",
  workflow_model: "openai/gpt",
  memory_model: "openai/gpt",
};

function response(data: unknown, ok = true) {
  return {
    ok,
    status: ok ? 200 : 500,
    statusText: ok ? "OK" : "Internal Server Error",
    contentType: "application/json",
    data,
    text: "",
  };
}

function installServer(configResponse: ReturnType<typeof response>, modelsResponse: ReturnType<typeof response>) {
  window.ardenDesktop = {
    api: {
      request: async (_config, request) =>
        request.path === "/config" ? configResponse : modelsResponse,
    },
  } as Window["ardenDesktop"];
}

afterEach(() => {
  window.ardenDesktop = originalDesktop;
  setState({
    config: originalState.config,
    serverConfig: originalState.serverConfig,
    serverModels: originalState.serverModels,
    serverConfigError: originalState.serverConfigError,
  });
});

test("config failure clears every stale server-owned model limit", async () => {
  setState({ serverConfig: CONFIG, serverModels: MODELS, serverConfigError: null });
  installServer(response({ detail: "catalog corrupt" }, false), response(MODELS));

  await fetchServerConfig();

  expect(getState().serverConfig).toBeNull();
  expect(getState().serverModels).toBeNull();
  expect(getState().serverConfigError).toBe("catalog corrupt");
});

test("models failure keeps fresh config but clears the stale catalog", async () => {
  setState({ serverConfig: CONFIG, serverModels: MODELS, serverConfigError: null });
  installServer(response(CONFIG), response({ detail: "models unavailable" }, false));

  await fetchServerConfig();

  expect(getState().serverConfig?.chat_model_max_context).toBe(128_000);
  expect(getState().serverModels).toBeNull();
  expect(getState().serverConfigError).toBe("models unavailable");
});

test("successful reload replaces both slices and clears the load error", async () => {
  setState({ serverConfig: null, serverModels: null, serverConfigError: "old failure" });
  installServer(response(CONFIG), response(MODELS));

  await fetchServerConfig();

  expect(getState().serverConfig?.chat_model).toBe("openai/gpt");
  expect(getState().serverModels?.models).toEqual(["openai/gpt"]);
  expect(getState().serverConfigError).toBeNull();
});

test("an older failed load cannot clear a newer successful load", async () => {
  let requestCount = 0;
  const oldResponses: Array<(value: ReturnType<typeof response>) => void> = [];
  window.ardenDesktop = {
    api: {
      request: async (_config, request) => {
        requestCount += 1;
        if (requestCount <= 2) {
          return new Promise<ReturnType<typeof response>>((resolve) => oldResponses.push(resolve));
        }
        return request.path === "/config" ? response(CONFIG) : response(MODELS);
      },
    },
  } as Window["ardenDesktop"];

  const older = fetchServerConfig();
  const newer = fetchServerConfig();
  await newer;
  for (const resolve of oldResponses) resolve(response({ detail: "old failure" }, false));
  await older;

  expect(getState().serverConfig?.chat_model).toBe("openai/gpt");
  expect(getState().serverModels?.models).toEqual(["openai/gpt"]);
  expect(getState().serverConfigError).toBeNull();
});

test("an older load cannot overwrite an authoritative config patch", async () => {
  const patched = { ...CONFIG, chat_model_max_context: 256_000 };
  let resolveModels: ((value: ReturnType<typeof response>) => void) | null = null;
  window.ardenDesktop = {
    api: {
      request: async (_config, request) => {
        if (request.path === "/models") {
          return new Promise<ReturnType<typeof response>>((resolve) => {
            resolveModels = resolve;
          });
        }
        if (request.method === "PATCH") return response(patched);
        return response(CONFIG);
      },
    },
  } as Window["ardenDesktop"];

  const older = fetchServerConfig();
  await updateServerConfig({ compression_threshold: 0.7 });
  if (!resolveModels) throw new Error("models request did not start");
  resolveModels(response(MODELS));
  await older;

  expect(getState().serverConfig?.chat_model_max_context).toBe(256_000);
  expect(getState().serverConfigError).toBeNull();
});
