import { afterEach, expect, test } from "bun:test";
import { addGmailAccountApi, getSetupStatusApi, parseModelsResponse, parseServerConfig, preflightGoogleSetupApi, saveGoogleCredentialsApi, verifySlackTokenApi } from "@/api/settings";

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
  memory_model: "gpt-5.2",
  embedding_model: "text-embedding-3-small",
  web_search: "auto",
  web_search_provider: "none",
  google_enabled: false,
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
  consolidation_interval: 60,
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

test("rejects stale model metadata before it enters state", () => {
  expect(() => parseModelsResponse({
    models: ["gpt-5.2"],
    groups: [{ provider: "openai", models: ["gpt-5.2"] }],
  })).toThrow("reasoning_efforts");
});

test("accepts current config and model metadata contracts", () => {
  expect(parseServerConfig(config)).toEqual(config);
  expect(parseModelsResponse({
    models: ["gpt-5.2"],
    groups: [{ provider: "openai", models: ["gpt-5.2"] }],
    reasoning_efforts: { "gpt-5.2": ["low", "medium"] },
    chat_model: "gpt-5.2",
    research_model: "gpt-5.2",
    memory_model: "gpt-5.2",
  })).toMatchObject({ reasoning_efforts: { "gpt-5.2": ["low", "medium"] } });
});

test("setup API wrappers preserve endpoint contracts", async () => {
  const requests = installRequestRecorder({});
  const appConfig = { serverUrl: "http://localhost:6877", apiKey: "" };

  await getSetupStatusApi(appConfig);
  await saveGoogleCredentialsApi(appConfig, { path: "/tmp/client_secret.json" });
  await preflightGoogleSetupApi(appConfig, "google_drive");
  await verifySlackTokenApi(appConfig, "slack_bot_token", "xoxb-token");

  expect(requests.map((request) => request.path)).toEqual([
    "/setup/status",
    "/setup/google/credentials",
    "/setup/google/preflight",
    "/setup/slack/verify",
  ]);
  expect(JSON.parse(requests[1].body ?? "{}")).toEqual({ path: "/tmp/client_secret.json" });
  expect(JSON.parse(requests[2].body ?? "{}")).toEqual({ integration_id: "google_drive" });
  expect(JSON.parse(requests[3].body ?? "{}")).toEqual({ service_id: "slack_bot_token", api_key: "xoxb-token" });
});

test("addGmailAccountApi sends backward-compatible service_choice body", async () => {
  const requests = installRequestRecorder({ email: "user@example.com", status: "connected" });
  const appConfig = { serverUrl: "http://localhost:6877", apiKey: "" };

  await addGmailAccountApi(appConfig);
  await addGmailAccountApi(appConfig, "calendar");

  expect(requests).toMatchObject([
    { path: "/gmail/add", method: "POST" },
    { path: "/gmail/add", method: "POST" },
  ]);
  expect(JSON.parse(requests[0].body ?? "{}")).toEqual({ service_choice: "all" });
  expect(JSON.parse(requests[1].body ?? "{}")).toEqual({ service_choice: "calendar" });
});
