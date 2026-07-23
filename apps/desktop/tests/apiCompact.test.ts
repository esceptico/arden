import { afterEach, expect, test } from "bun:test";
import { getChildAgentResultApi } from "@/api/agents";
import { apiWithConfig, compactSessionApi } from "@/api/core";
import { archiveAreaApi, listAreasApi } from "@/api/sessions";
import { runBuiltinCommand } from "@/actions/builtins";
import { getState, setState } from "@/stores/index";

const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window;
const originalFetch = globalThis.fetch;

afterEach(() => {
  (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
  globalThis.fetch = originalFetch;
});

test("compactSessionApi uses an extended timeout", async () => {
  let request: { path: string; method?: string; body?: string; timeout?: number } | null = null;
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async (_config: unknown, req: typeof request) => {
          request = req;
          return {
            ok: true,
            status: 200,
            statusText: "OK",
            contentType: "application/json",
            data: { status: "compacted" },
            text: "",
          };
        },
      },
    },
  };

  await compactSessionApi({ serverUrl: "http://localhost:6877", apiKey: "" }, "sess-1");

  expect(request).toEqual({
    path: "/compact",
    method: "POST",
    body: JSON.stringify({ session_id: "sess-1" }),
    timeout: 600_000,
  });
});

test("standard API calls use the default timeout", async () => {
  let request: { path: string; method?: string; body?: string; timeout?: number } | null = null;
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async (_config: unknown, req: typeof request) => {
          request = req;
          return {
            ok: true,
            status: 200,
            statusText: "OK",
            contentType: "application/json",
            data: [],
            text: "",
          };
        },
      },
    },
  };

  await listAreasApi({ serverUrl: "http://localhost:6877", apiKey: "" });

  expect(request).toEqual({
    path: "/areas",
    method: "GET",
    body: undefined,
    timeout: 60_000,
  });
});

test("archiveAreaApi sends DELETE to the area route", async () => {
  let request: { path: string; method?: string; body?: string; timeout?: number } | null = null;
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async (_config: unknown, req: typeof request) => {
          request = req;
          return {
            ok: true,
            status: 200,
            statusText: "OK",
            contentType: "application/json",
            data: { status: "archived", area_id: "proj/1" },
            text: "",
          };
        },
      },
    },
  };

  await archiveAreaApi({ serverUrl: "http://localhost:6877", apiKey: "" }, "proj/1");

  expect(request).toEqual({
    path: "/areas/proj%2F1",
    method: "DELETE",
    body: undefined,
    timeout: 60_000,
  });
});

test("getChildAgentResultApi uses child-agent result route with wait params", async () => {
  let request: { path: string; method?: string; body?: string; timeout?: number } | null = null;
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async (_config: unknown, req: typeof request) => {
          request = req;
          return {
            ok: true,
            status: 200,
            statusText: "OK",
            contentType: "application/json",
            data: {
              task_id: "child-run-1",
              child_run_id: "child-run-1",
              session_id: "sess-1",
              status: "completed",
              terminal: true,
              result: "done",
              result_ref: "bg_results/child-run-1.txt",
            },
            text: "",
          };
        },
      },
    },
  };

  const result = await getChildAgentResultApi(
    { serverUrl: "http://localhost:6877", apiKey: "" },
    "sess-1",
    "child-run-1",
    { wait: true, timeoutSeconds: 2 },
  );

  expect(request).toEqual({
    path: "/chat/child-agents/child-run-1/result?session_id=sess-1&wait=true&timeout_seconds=2",
    method: "GET",
    body: undefined,
    timeout: 60_000,
  });
  expect(result.result).toBe("done");
});

test("desktop API bridge rejects when the renderer timeout elapses", async () => {
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async () => new Promise(() => {}),
      },
    },
  };

  let error: unknown = null;
  try {
    await apiWithConfig({ serverUrl: "http://localhost:6877", apiKey: "" }, "/slow", {
      timeout: 5,
    } as RequestInit & { timeout: number });
  } catch (e) {
    error = e;
  }

  expect(error).toBeInstanceOf(Error);
  expect((error as Error).message).toBe("Request timed out for GET /slow");
});

test("compact command does not reload or claim success when compaction is below threshold", async () => {
  const requests: string[] = [];
  setState({
    config: { serverUrl: "http://localhost:6877", apiKey: "" },
    currentSessionId: "sess-1",
    messages: new Map(),
    order: [],
  });
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async (_config: unknown, req: { path: string }) => {
          requests.push(req.path);
          return {
            ok: true,
            status: 200,
            statusText: "OK",
            contentType: "application/json",
            data: { status: "not_needed", message: "Context below compaction threshold" },
            text: "",
          };
        },
      },
    },
  };

  await runBuiltinCommand("compact", "");

  expect(requests).toEqual(["/compact"]);
  const notices = getState().order.map((id) => getState().messages.get(id)?.content);
  expect(notices).toContain("Context below compaction threshold");
  expect(notices).not.toContain("Context compacted.");
});
