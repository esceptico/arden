import { afterEach, expect, test } from "bun:test";
import {
  createCustomEmbeddingModelApi,
  deleteCustomEmbeddingModelApi,
  getEmbeddingModelsApi,
  getIndexStatusApi,
  parseEmbeddingModelsResponse,
  parseIndexStatus,
  parseModelsResponse,
  parseServerConfig,
  startIndexingApi,
  updateEmbeddingModelApi,
} from "@/api/settings";

const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window;

afterEach(() => {
  (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
});

function installRequestRecorder(data: unknown = {}) {
  const requests: { path: string; method?: string; body?: string; timeout?: number }[] = [];
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async (_config: unknown, req: { path: string; method?: string; body?: string; timeout?: number }) => {
          requests.push(req);
          return {
            ok: true,
            status: 200,
            statusText: "OK",
            contentType: "application/json",
            data,
            text: "",
          };
        },
      },
    },
  };
  return requests;
}

const config = {
  chat_model: "gpt-5.2",
  research_model: "claude-opus-4-7",
  workflow_model: "claude-opus-4-7",
  memory_model: "gpt-5.2",
  embedding_model: "text-embedding-3-small",
  web_search: "auto",
  web_search_provider: "none",
  max_depth: 8,
  reasoning_effort: null,
  reasoning_efforts: ["low", "medium"],
  model_reasoning_efforts: {},
  compression_threshold: 0.8,
  compaction_token_limit: 218000,
  compaction_token_trigger: 207100,
  max_messages: 20,
  compression_keep_ratio: 0.5,
  summary_max_tokens: 2500,
  memory_enabled: true,
  integrations: {},
};

test("rejects stale config responses before they enter state", () => {
  const stale = { ...config };
  Reflect.deleteProperty(stale, "model_reasoning_efforts");

  expect(() => parseServerConfig(stale)).toThrow("model_reasoning_efforts");
});

test("rejects config responses without server-owned compaction token triggers", () => {
  const stale = { ...config };
  Reflect.deleteProperty(stale, "compaction_token_trigger");

  expect(() => parseServerConfig(stale)).toThrow("compaction_token_trigger");
});

test("accepts disabling embeddings but rejects malformed config values", () => {
  expect(parseServerConfig({ ...config, embedding_model: null }).embedding_model).toBeNull();
  expect(() => parseServerConfig({ ...config, embedding_model: 42 })).toThrow("embedding_model");
});

test("rejects stale model metadata before it enters state", () => {
  expect(() => parseModelsResponse({
    models: ["gpt-5.2"],
    groups: [{ provider: "openai", label: "OpenAI", models: ["gpt-5.2"] }],
  })).toThrow("reasoning_efforts");
});

test("requires server-owned provider labels in model groups", () => {
  expect(() => parseModelsResponse({
    models: ["gpt-5.2"],
    groups: [{ provider: "openai", models: ["gpt-5.2"] }],
    reasoning_efforts: {},
    chat_model: "gpt-5.2",
    research_model: "gpt-5.2",
    workflow_model: "gpt-5.2",
    memory_model: "gpt-5.2",
  })).toThrow("invalid groups");
});

test("accepts current config and model metadata contracts", () => {
  expect(parseServerConfig(config)).toEqual(config);
  expect(parseModelsResponse({
    models: ["gpt-5.2"],
    groups: [{ provider: "openai", label: "OpenAI", models: ["gpt-5.2"] }],
    reasoning_efforts: { "gpt-5.2": ["low", "medium"] },
    chat_model: "gpt-5.2",
    research_model: "gpt-5.2",
    workflow_model: "gpt-5.2",
    memory_model: "gpt-5.2",
  })).toMatchObject({ reasoning_efforts: { "gpt-5.2": ["low", "medium"] } });
});

test("accepts the nullable embedding catalog and rebuild status contracts", () => {
  expect(parseEmbeddingModelsResponse({
    models: ["text-embedding-3-small"],
    groups: [{ provider: "openai", models: ["text-embedding-3-small"] }],
    current: null,
  })).toMatchObject({ current: null });
  expect(parseIndexStatus({
    state: "reindexing",
    model: "text-embedding-3-small",
    phase: "facts",
    done: 4,
    total: 12,
    retry_at: null,
    error: null,
  })).toMatchObject({ state: "reindexing", phase: "facts" });
});

test("embedding API wrappers preserve explicit confirmation and nullable disable", async () => {
  const status = {
    state: "ready",
    model: null,
    phase: null,
    done: 0,
    total: 0,
    retry_at: null,
    error: null,
  };
  const requests: { path: string; method?: string; body?: string; timeout?: number }[] = [];
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async (_config: unknown, request: { path: string; method?: string; body?: string; timeout?: number }) => {
          requests.push(request);
          return {
            ok: true,
            status: 200,
            statusText: "OK",
            contentType: "application/json",
            data: request.path === "/models/embedding"
              ? {
                  models: ["text-embedding-3-small"],
                  groups: [{ provider: "openai", models: ["text-embedding-3-small"] }],
                  current: "text-embedding-3-small",
                }
              : status,
            text: "",
          };
        },
      },
    },
  };
  const appConfig = { serverUrl: "http://localhost:6877", apiKey: "" };

  await getEmbeddingModelsApi(appConfig);
  await updateEmbeddingModelApi(appConfig, null);
  await getIndexStatusApi(appConfig);
  await startIndexingApi(appConfig);

  expect(requests.map((request) => request.path)).toEqual([
    "/models/embedding",
    "/config/embedding",
    "/index/status",
    "/index/start",
  ]);
  expect(JSON.parse(requests[1].body ?? "{}")).toEqual({ embedding_model: null, confirmed: true });
  expect(requests[1].method).toBe("POST");
  expect(requests[3].method).toBe("POST");
});

test("custom embedding API wrappers preserve endpoint, dimensions, and encoded model ids", async () => {
  const requests = installRequestRecorder({});
  const appConfig = { serverUrl: "http://localhost:6877", apiKey: "" };

  await createCustomEmbeddingModelApi(appConfig, {
    model_id: "local/qwen embed",
    base_url: "http://127.0.0.1:8081/v1",
    dimensions: 1024,
    api_key: null,
  });
  await deleteCustomEmbeddingModelApi(appConfig, "local/qwen embed");

  expect(requests.map((request) => request.path)).toEqual([
    "/models/custom/embedding",
    "/models/custom/embedding/local%2Fqwen%20embed",
  ]);
  expect(JSON.parse(requests[0].body ?? "{}")).toEqual({
    model_id: "local/qwen embed",
    base_url: "http://127.0.0.1:8081/v1",
    dimensions: 1024,
    api_key: null,
  });
  expect(requests.map((request) => request.method)).toEqual(["POST", "DELETE"]);
});
