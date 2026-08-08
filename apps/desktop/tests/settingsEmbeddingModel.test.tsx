import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ModelsTab } from "@/features/settings/components/ModelsTab";
import { getState, setState } from "@/stores";

const CONFIG = {
  chat_model: "openai/gpt",
  chat_model_max_context: 128_000,
  compaction_token_limit: 100_000,
  compaction_token_trigger: 90_000,
  research_model: "openai/gpt",
  workflow_model: "openai/gpt",
  memory_model: "openai/gpt",
  embedding_model: "text-embedding-3-small",
  web_search: "none" as const,
  web_search_provider: "none",
  max_depth: 1,
  reasoning_effort: null,
  reasoning_efforts: [],
  model_reasoning_efforts: {},
  model_roles: {
    research: { model: "openai/gpt", reasoning_effort: null },
    workflow: { model: "openai/gpt", reasoning_effort: null },
    auxiliary: { model: "openai/gpt", reasoning_effort: null },
    memory: { model: "openai/gpt", reasoning_effort: null },
  },
  compression_threshold: 0.8,
  max_messages: 100,
  compression_keep_ratio: 0.5,
  summary_max_tokens: 1_000,
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

let root: Root | null = null;
let restore: (() => void) | null = null;

function response(data: unknown) {
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    contentType: "application/json",
    data,
    text: "",
  };
}

function mount(status: Record<string, unknown>) {
  const previous = getState();
  const previousDesktop = window.ardenDesktop;
  const requests: Array<{ path: string; method?: string; body?: string }> = [];
  const app = document.createElement("div");
  app.id = "app";
  document.body.append(app);
  root = createRoot(app);
  setState({
    connected: true,
    config: { serverUrl: "http://localhost:6877", apiKey: "" },
    serverConfig: CONFIG,
    serverModels: MODELS,
  });
  window.ardenDesktop = {
    api: {
      request: async (_config, request) => {
        requests.push(request);
        if (request.path === "/models/embedding") {
          return response({
            models: ["text-embedding-3-small", "text-embedding-3-large"],
            groups: [{ provider: "openai", models: ["text-embedding-3-small", "text-embedding-3-large"] }],
            current: "text-embedding-3-small",
          });
        }
        if (request.path === "/config") return response(CONFIG);
        return response(status);
      },
    },
  } as Window["ardenDesktop"];
  restore = () => {
    window.ardenDesktop = previousDesktop;
    setState({
      connected: previous.connected,
      config: previous.config,
      serverConfig: previous.serverConfig,
      serverModels: previous.serverModels,
    });
    app.remove();
  };
  return { app, requests };
}

function mountWithUnavailableCatalog() {
  const previous = getState();
  const previousDesktop = window.ardenDesktop;
  const app = document.createElement("div");
  document.body.append(app);
  root = createRoot(app);
  setState({
    connected: true,
    config: { serverUrl: "http://localhost:6877", apiKey: "" },
    serverConfig: CONFIG,
    serverModels: MODELS,
  });
  window.ardenDesktop = {
    api: {
      request: async () => {
        throw new Error("server unavailable");
      },
    },
  } as Window["ardenDesktop"];
  restore = () => {
    window.ardenDesktop = previousDesktop;
    setState({
      connected: previous.connected,
      config: previous.config,
      serverConfig: previous.serverConfig,
      serverModels: previous.serverModels,
    });
    app.remove();
  };
  return { app };
}

async function openPicker(app: HTMLElement) {
  const trigger = app.querySelector<HTMLElement>('[aria-label="Choose embedding model"]');
  expect(trigger).not.toBeNull();
  await act(async () => trigger?.click());
}

async function choose(label: string) {
  const option = Array.from(document.querySelectorAll<HTMLElement>('[role="option"]'))
    .find((element) => element.textContent?.includes(label));
  expect(option).not.toBeUndefined();
  await act(async () => option?.click());
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = null;
  restore?.();
  restore = null;
  document.body.replaceChildren();
});

test("embedding selection waits for confirmation and serializes no embeddings as null", async () => {
  const { app, requests } = mount({
    state: "disabled",
    model: null,
    phase: null,
    done: 0,
    total: 0,
    retry_at: null,
    error: null,
  });

  await act(async () => root?.render(<ModelsTab />));
  await act(async () => { await Bun.sleep(0); });
  await openPicker(app);
  await choose("No embeddings");

  expect(app.textContent).toContain("Turn off embeddings?");
  expect(app.querySelector('[role="alertdialog"]')?.getAttribute("aria-describedby"))
    .toBe("embedding-confirmation-description");
  expect(requests.some((request) => request.path === "/config/embedding")).toBe(false);

  const cancel = Array.from(app.querySelectorAll<HTMLButtonElement>("button"))
    .find((button) => button.textContent === "Cancel");
  expect(document.activeElement).toBe(cancel);
  await act(async () => cancel?.click());
  expect(requests.some((request) => request.path === "/config/embedding")).toBe(false);

  await openPicker(app);
  await choose("No embeddings");
  const confirm = Array.from(app.querySelectorAll<HTMLButtonElement>("button"))
    .find((button) => button.textContent === "Turn off embeddings");
  await act(async () => confirm?.click());

  const update = requests.find((request) => request.path === "/config/embedding");
  expect(JSON.parse(update?.body ?? "{}")).toEqual({ embedding_model: null, confirmed: true });
});

test("changing models names the full rebuild before committing", async () => {
  const { app, requests } = mount({
    state: "ready",
    model: "text-embedding-3-small",
    phase: null,
    done: 0,
    total: 0,
    retry_at: null,
    error: null,
  });

  await act(async () => root?.render(<ModelsTab />));
  await act(async () => { await Bun.sleep(0); });
  await openPicker(app);
  await choose("text-embedding-3-large");

  expect(app.textContent).toContain("Rebuild semantic index?");
  expect(app.textContent).toContain("re-embeds facts and wiki pages");
  expect(requests.some((request) => request.path === "/config/embedding")).toBe(false);

  const confirm = Array.from(app.querySelectorAll<HTMLButtonElement>("button"))
    .find((button) => button.textContent === "Rebuild index");
  await act(async () => confirm?.click());
  const update = requests.find((request) => request.path === "/config/embedding");
  expect(JSON.parse(update?.body ?? "{}")).toEqual({
    embedding_model: "text-embedding-3-large",
    confirmed: true,
  });
});

test("rebuild progress is accessible and disables the picker", async () => {
  const { app, requests } = mount({
    state: "reindexing",
    model: "text-embedding-3-small",
    phase: "facts",
    done: 4,
    total: 12,
    retry_at: null,
    error: null,
  });

  await act(async () => root?.render(<ModelsTab />));
  await act(async () => { await Bun.sleep(0); });

  const progress = app.querySelector<HTMLProgressElement>("progress");
  expect(progress?.getAttribute("aria-label")).toBe("Rebuilding facts");
  expect(progress?.value).toBe(4);
  expect(progress?.max).toBe(12);
  expect(app.querySelector<HTMLElement>('[aria-label="Choose embedding model"]')?.getAttribute("disabled")).not.toBeNull();
});

test("rebuild progress reports provider rate-limit waits", async () => {
  const { app } = mount({
    state: "reindexing",
    model: "gemini-embedding-001",
    phase: "wiki",
    done: 0,
    total: 47,
    retry_at: new Date(Date.now() + 46_000).toISOString(),
    error: null,
  });

  await act(async () => root?.render(<ModelsTab />));
  await act(async () => { await Bun.sleep(0); });

  expect(app.textContent).toContain("Rate limited — resuming in");
  expect(app.querySelector<HTMLProgressElement>("progress")?.getAttribute("aria-label"))
    .toContain("Rate limited — resuming in");
});

test("a rebuild error exposes a direct retry", async () => {
  const { app, requests } = mount({
    state: "error",
    model: "text-embedding-3-small",
    phase: null,
    done: 4,
    total: 12,
    retry_at: null,
    error: "provider unavailable",
  });

  await act(async () => root?.render(<ModelsTab />));
  await act(async () => { await Bun.sleep(0); });
  const retry = Array.from(app.querySelectorAll<HTMLButtonElement>("button"))
    .find((button) => button.textContent === "Retry");
  expect(retry).not.toBeUndefined();
  await act(async () => retry?.click());
  expect(requests.some((request) => request.path === "/index/start")).toBe(true);
});

test("a failed initial embedding load leaves a direct retry", async () => {
  const { app } = mountWithUnavailableCatalog();

  await act(async () => root?.render(<ModelsTab />));
  await act(async () => { await Bun.sleep(0); });

  expect(app.textContent).toContain("Couldn't load embedding settings");
  expect(Array.from(app.querySelectorAll("button")).some((button) => button.textContent === "Retry")).toBe(true);
  expect(app.querySelector<HTMLElement>('[aria-label="Choose embedding model"]')?.getAttribute("disabled")).not.toBeNull();
});

test("a fresh no-provider config shows setup instead of model controls", async () => {
  const { app } = mount({});
  setState({
    serverConfig: {
      ...CONFIG,
      chat_model: null,
      research_model: null,
      workflow_model: null,
      memory_model: null,
    },
    serverModels: {
      models: [],
      groups: [],
      reasoning_efforts: {},
      chat_model: null,
      research_model: null,
      workflow_model: null,
      memory_model: null,
    },
  });

  await act(async () => root?.render(<ModelsTab />));

  expect(app.textContent).toContain("Connect a model provider");
  expect(app.querySelector('[aria-label="Choose model"]')).toBeNull();
});

test("a custom-model-only setup can select its first chat model", async () => {
  const { app, requests } = mount({});
  setState({
    serverConfig: {
      ...CONFIG,
      chat_model: null,
      research_model: null,
      workflow_model: null,
      memory_model: null,
    },
    serverModels: {
      models: ["custom/local"],
      groups: [{ provider: "custom", label: "Custom", models: ["custom/local"] }],
      reasoning_efforts: {},
      chat_model: null,
      research_model: null,
      workflow_model: null,
      memory_model: null,
    },
  });

  await act(async () => root?.render(<ModelsTab />));
  const chooser = app.querySelector<HTMLElement>('[aria-label="Choose model"]');
  expect(chooser).not.toBeNull();
  await act(async () => chooser?.click());
  const option = document.querySelector<HTMLElement>('[role="option"]');
  expect(option?.textContent).toContain("custom/local");
  await act(async () => {
    option?.click();
    await Promise.resolve();
  });

  const patch = requests.find((request) => request.path === "/config" && request.method === "PATCH");
  expect(JSON.parse(patch?.body ?? "null")).toEqual({ chat_model: "custom/local" });
});
