import { afterEach, expect, test } from "bun:test";
import { respondToHtmlInput } from "@/actions/htmlInput";
import { getState, setState } from "@/stores/index";

afterEach(() => {
  delete (globalThis.window as unknown as { ardenDesktop?: unknown }).ardenDesktop;
});

function stubApi(calls: { path: string; body: unknown }[], options: { fail?: boolean } = {}) {
  (globalThis.window as unknown as { ardenDesktop: unknown }).ardenDesktop = {
    api: {
      request: async (_cfg: unknown, req: { path: string; body?: string }) => {
        calls.push({ path: req.path, body: req.body ? JSON.parse(req.body) : null });
        if (options.fail) throw new Error("server unreachable");
        return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data: {}, text: "" };
      },
    },
  };
}

test("respondToHtmlInput POSTs the action envelope to /tools/result", async () => {
  const calls: { path: string; body: unknown }[] = [];
  stubApi(calls);
  setState({
    config: { serverUrl: "http://localhost:6877", apiKey: "" },
    currentRunId: "run-1",
    messages: new Map(),
    order: [],
  });

  const ok = await respondToHtmlInput("t1", "accept", { a: 1 });

  expect(ok).toBe(true);
  const toolResult = calls.find((c) => c.path === "/tools/result");
  expect(toolResult).toBeTruthy();
  expect(toolResult!.body).toEqual({
    run_id: "run-1",
    tool_id: "t1",
    result: '{"action":"accept","values":{"a":1}}',
    approved: true,
  });
});

test("respondToHtmlInput returns false and surfaces an error when the POST fails", async () => {
  const calls: { path: string; body: unknown }[] = [];
  stubApi(calls, { fail: true });
  setState({
    config: { serverUrl: "http://localhost:6877", apiKey: "" },
    currentRunId: "run-1",
    messages: new Map(),
    order: [],
  });

  const ok = await respondToHtmlInput("t1", "cancel", {});

  expect(ok).toBe(false);
  const state = getState();
  const errorMessage = state.order
    .map((id) => state.messages.get(id))
    .find((message) => message?.role === "error");
  expect(errorMessage?.content).toBe("server unreachable");
});

test("respondToHtmlInput is a no-op without an active run", async () => {
  const calls: { path: string; body: unknown }[] = [];
  stubApi(calls);
  setState({
    config: { serverUrl: "http://localhost:6877", apiKey: "" },
    currentRunId: null,
    messages: new Map(),
    order: [],
  });

  const ok = await respondToHtmlInput("t1", "decline", {});

  expect(ok).toBe(false);
  expect(calls).toHaveLength(0);
});
