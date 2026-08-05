import { afterEach, describe, expect, test } from "bun:test";

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { createExecutorClient } = require("../electron/executor.cjs");

type FetchCall = { url: string; init: RequestInit | undefined };

function sse(kind: string, data: Record<string, unknown>): string {
  return `event: ${kind}\ndata: ${JSON.stringify({ kind, ...data })}\n\n`;
}

function streamResponse(frames: string[], holdOpen: { promise: Promise<void> } | null = null): Response {
  const body = new ReadableStream<Uint8Array>({
    async start(controller) {
      const encoder = new TextEncoder();
      for (const frame of frames) controller.enqueue(encoder.encode(frame));
      if (holdOpen) await holdOpen.promise;
      controller.close();
    },
  });
  return new Response(body, { status: 200 });
}

function jsonResponse(status: number, body: Record<string, unknown> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function makeStateStore(initial: { executorId: string; token: string; cursor: number } | null = null) {
  let state = initial;
  return {
    deviceName: "test-mac",
    saves: [] as Array<unknown>,
    async load() {
      return state;
    },
    async save(next: { executorId: string; token: string; cursor: number } | null) {
      state = next;
      this.saves.push(next);
    },
  };
}

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>(r => {
    resolve = r;
  });
  return { promise, resolve };
}

const clients: Array<{ dispose: () => void }> = [];

function makeClient(options: Parameters<typeof createExecutorClient>[0]) {
  const client = createExecutorClient({ heartbeatIntervalMs: 3_600_000, ...options });
  clients.push(client);
  return client;
}

afterEach(() => {
  for (const client of clients.splice(0)) client.dispose();
});

async function until(predicate: () => boolean, timeoutMs = 2000): Promise<void> {
  const start = Date.now();
  while (!predicate()) {
    if (Date.now() - start > timeoutMs) throw new Error("condition never became true");
    await new Promise(resolve => setTimeout(resolve, 5));
  }
}

describe("executor client", () => {
  test("enrolls with the API key when no credential is stored", async () => {
    const store = makeStateStore();
    const calls: FetchCall[] = [];
    const hold = deferred();
    const client = makeClient({
      getConfig: () => ({ serverUrl: "http://server", apiKey: "api-key" }),
      stateStore: store,
      fetchImpl: async (url: URL, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        if (String(url).includes("/executor/enroll")) {
          return jsonResponse(200, { executor_id: "exec_1", token: "device-token" });
        }
        return streamResponse([sse("lease", { lease_id: "lease_1" })], hold);
      },
    });
    client.start();

    await until(() => client.getStatus().connected);
    expect(calls[0].url).toBe("http://server/executor/enroll");
    expect((calls[0].init?.headers as Record<string, string>).Authorization).toBe("Bearer api-key");
    expect(calls[1].url).toBe("http://server/executor/stream?cursor=0");
    expect((calls[1].init?.headers as Record<string, string>).Authorization).toBe("Bearer device-token");
    expect(store.saves[0]).toEqual({ executorId: "exec_1", token: "device-token", cursor: 0 });
    hold.resolve();
  });

  test("answers an unimplemented tool with a failed result and acks the cursor", async () => {
    const store = makeStateStore({ executorId: "exec_1", token: "device-token", cursor: 0 });
    const results: Array<Record<string, unknown>> = [];
    const hold = deferred();
    const client = makeClient({
      getConfig: () => ({ serverUrl: "http://server", apiKey: "" }),
      stateStore: store,
      fetchImpl: async (url: URL, init?: RequestInit) => {
        const target = String(url);
        if (target.includes("/executor/results")) {
          results.push(JSON.parse(String(init?.body)));
          return jsonResponse(200, { status: "failed" });
        }
        return streamResponse(
          [
            sse("lease", { lease_id: "lease_1" }),
            sse("command", {
              seq: 7,
              type: "execute_tool",
              invocation_id: "inv-1",
              payload: { invocation_id: "inv-1", tool_name: "file_read", arguments: { path: "/x" } },
            }),
          ],
          hold,
        );
      },
    });
    client.start();

    await until(() => results.length === 1);
    expect(results[0].invocation_id).toBe("inv-1");
    expect(results[0].status).toBe("failed");
    expect(results[0].error_code).toBe("unsupported_tool");
    expect(results[0].lease_id).toBe("lease_1");
    await until(() => store.saves.some(s => (s as { cursor: number } | null)?.cursor === 7));
    hold.resolve();
  });

  test("runs a registered handler and reports success", async () => {
    const store = makeStateStore({ executorId: "exec_1", token: "device-token", cursor: 0 });
    const posts: Array<{ path: string; body: Record<string, unknown> }> = [];
    const hold = deferred();
    const client = makeClient({
      getConfig: () => ({ serverUrl: "http://server", apiKey: "" }),
      stateStore: store,
      fetchImpl: async (url: URL, init?: RequestInit) => {
        const target = String(url);
        if (init?.method === "POST") {
          posts.push({ path: new URL(target).pathname, body: JSON.parse(String(init.body)) });
          return jsonResponse(200, {});
        }
        return streamResponse(
          [
            sse("lease", { lease_id: "lease_1" }),
            sse("command", {
              seq: 3,
              type: "execute_tool",
              invocation_id: "inv-2",
              payload: { invocation_id: "inv-2", tool_name: "file_read", arguments: { path: "/etc/hosts" } },
            }),
          ],
          hold,
        );
      },
    });
    client.registerHandler("file_read", async (args: { path: string }) => ({
      status: "succeeded",
      payload: { content: `contents of ${args.path}`, preview: "read" },
    }));
    client.start();

    await until(() => posts.some(p => p.path === "/executor/results"));
    const started = posts.find(p => p.path === "/executor/started");
    expect(started?.body.invocation_id).toBe("inv-2");
    const result = posts.find(p => p.path === "/executor/results");
    expect(result?.body.status).toBe("succeeded");
    expect((result?.body.result as { content: string }).content).toBe("contents of /etc/hosts");
    hold.resolve();
  });

  test("cancel_tool aborts the running handler and reports cancelled", async () => {
    const store = makeStateStore({ executorId: "exec_1", token: "device-token", cursor: 0 });
    const posts: Array<{ path: string; body: Record<string, unknown> }> = [];
    const hold = deferred();
    const client = makeClient({
      getConfig: () => ({ serverUrl: "http://server", apiKey: "" }),
      stateStore: store,
      fetchImpl: async (url: URL, init?: RequestInit) => {
        const target = String(url);
        if (init?.method === "POST") {
          posts.push({ path: new URL(target).pathname, body: JSON.parse(String(init.body)) });
          return jsonResponse(200, {});
        }
        return streamResponse(
          [
            sse("lease", { lease_id: "lease_1" }),
            sse("command", {
              seq: 1,
              type: "execute_tool",
              invocation_id: "inv-3",
              payload: { invocation_id: "inv-3", tool_name: "slow_tool", arguments: {} },
            }),
            sse("command", { seq: 2, type: "cancel_tool", invocation_id: "inv-3", payload: { invocation_id: "inv-3" } }),
          ],
          hold,
        );
      },
    });
    client.registerHandler("slow_tool", (_args: unknown, { signal }: { signal: AbortSignal }) => {
      return new Promise((_resolve, reject) => {
        signal.addEventListener("abort", () => reject(new Error("aborted")));
      });
    });
    client.start();

    await until(() => posts.some(p => p.path === "/executor/results"));
    const result = posts.find(p => p.path === "/executor/results");
    expect(result?.body.status).toBe("cancelled");
    expect(result?.body.error_code).toBe("cancelled");
    hold.resolve();
  });

  test("a rejected executor token clears the stored credential", async () => {
    const store = makeStateStore({ executorId: "exec_1", token: "revoked-token", cursor: 5 });
    let sawUnauthorized = false;
    const client = makeClient({
      getConfig: () => ({ serverUrl: "http://server", apiKey: "" }),
      stateStore: store,
      fetchImpl: async (url: URL) => {
        if (String(url).includes("/executor/stream")) {
          sawUnauthorized = true;
          return jsonResponse(401, {});
        }
        return jsonResponse(200, {});
      },
    });
    client.start();

    await until(() => sawUnauthorized && store.saves.includes(null));
    expect(await store.load()).toBeNull();
  });
});

  test("heartbeat 401 clears the credential and re-enrolls", async () => {
    const store = makeStateStore({ executorId: "exec_1", token: "revoked-mid-session", cursor: 0 });
    const hold = deferred();
    const client = makeClient({
      heartbeatIntervalMs: 30,
      getConfig: () => ({ serverUrl: "http://server", apiKey: "" }),
      stateStore: store,
      fetchImpl: async (url: URL, init?: RequestInit) => {
        const target = String(url);
        if (target.includes("/executor/heartbeat")) return jsonResponse(401, {});
        if (target.includes("/executor/stream")) {
          return streamResponse([sse("lease", { lease_id: "lease_1" })], hold);
        }
        return jsonResponse(200, {});
      },
    });
    client.start();

    await until(() => store.saves.includes(null));
    expect(await store.load()).toBeNull();
    hold.resolve();
  });

  test("result delivery retries network failures until it lands", async () => {
    const store = makeStateStore({ executorId: "exec_1", token: "device-token", cursor: 0 });
    const results: Array<Record<string, unknown>> = [];
    let resultAttempts = 0;
    const hold = deferred();
    const client = makeClient({
      getConfig: () => ({ serverUrl: "http://server", apiKey: "" }),
      stateStore: store,
      fetchImpl: async (url: URL, init?: RequestInit) => {
        const target = String(url);
        if (target.includes("/executor/results")) {
          resultAttempts += 1;
          if (resultAttempts < 3) throw new Error("network down");
          results.push(JSON.parse(String(init?.body)));
          return jsonResponse(200, {});
        }
        if (init?.method === "POST") return jsonResponse(200, {});
        return streamResponse(
          [
            sse("lease", { lease_id: "lease_1" }),
            sse("command", {
              seq: 1,
              type: "execute_tool",
              invocation_id: "inv-retry",
              payload: { invocation_id: "inv-retry", tool_name: "quick", arguments: {} },
            }),
          ],
          hold,
        );
      },
    });
    client.registerHandler("quick", async () => ({ status: "succeeded", payload: { content: "done", preview: "ok" } }));
    client.start();

    await until(() => results.length === 1, 10_000);
    expect(resultAttempts).toBe(3);
    expect(results[0].invocation_id).toBe("inv-retry");
    hold.resolve();
  });

  test("advertises device skills on lease and uploads needed bodies", async () => {
    const { mkdirSync, mkdtempSync, writeFileSync } = require("node:fs");
    const { tmpdir } = require("node:os");
    const { join } = require("node:path");
    const root = mkdtempSync(join(tmpdir(), "arden-skills-adv-"));
    mkdirSync(join(root, "mac-notes"), { recursive: true });
    writeFileSync(join(root, "mac-notes", "SKILL.md"), "---\nname: mac-notes\ndescription: Notes\n---\n\n# Steps\n");

    const store = makeStateStore({ executorId: "exec_1", token: "device-token", cursor: 0 });
    const posts: Array<{ path: string; body: Record<string, unknown> }> = [];
    const hold = deferred();
    const client = makeClient({
      getConfig: () => ({ serverUrl: "http://server", apiKey: "" }),
      stateStore: store,
      skillsDirs: [root],
      fetchImpl: async (url: URL, init?: RequestInit) => {
        const target = String(url);
        if (init?.method === "POST") {
          const body = JSON.parse(String(init.body));
          posts.push({ path: new URL(target).pathname, body });
          if (target.endsWith("/executor/skills")) return jsonResponse(200, { need: ["mac-notes"] });
          return jsonResponse(200, {});
        }
        return streamResponse([sse("lease", { lease_id: "lease_1" })], hold);
      },
    });
    client.start();

    await until(() => posts.some(p => p.path === "/executor/skills/bodies"));
    const ad = posts.find(p => p.path === "/executor/skills");
    expect((ad?.body.skills as Array<{ name: string }>)[0].name).toBe("mac-notes");
    expect(ad?.body.lease_id).toBe("lease_1");
    const bodies = posts.find(p => p.path === "/executor/skills/bodies");
    expect((bodies?.body.skills as Array<{ body: string }>)[0].body).toContain("# Steps");
    hold.resolve();
  });
