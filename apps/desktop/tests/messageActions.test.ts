import { beforeEach, expect, test } from "bun:test";
import { enqueueMessage, sendMessage, stopRun } from "@/actions/messages";
import { getState, setState } from "@/stores/index";

type CapturedRequest = { path: string; method: string; body?: string };

const CONTEXT_BUDGET = {
  model: "openai-codex/gpt-5.6-sol",
  hard_limit: 372_000,
  compaction_trigger: 282_720,
  message_limit: 120,
  input_tokens: 0,
  message_count: 0,
};

function mockBridge(requests: CapturedRequest[]) {
  const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window;
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async (_config: unknown, request: CapturedRequest) => {
          requests.push(request);
          return { ok: true, contentType: "application/json", data: {} };
        },
      },
    },
    setTimeout,
    clearTimeout,
  };
  return () => {
    (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
  };
}

const SESSION_ROW = {
  session_id: "session-1",
  started_at: "2026-05-30T00:00:00Z",
  last_activity: "2026-05-30T00:00:00Z",
  name: "chan",
  message_count: 3,
  session_type: "channel" as const,
  active_run_id: "run-bg",
};

test("stopRun cancels a backgrounded/automation run by session even when currentRunId is null", async () => {
  const requests: CapturedRequest[] = [];
  const restore = mockBridge(requests);
  try {
    setState({
      config: { serverUrl: "http://x", apiKey: "" },
      currentSessionId: "session-1",
      currentRunId: null,
      running: true,
      sessions: [SESSION_ROW],
      activeRunSessionIds: new Set(["session-1"]),
      messages: new Map(),
      order: [],
    });

    await stopRun();

    expect(requests).toHaveLength(1);
    expect(requests[0].path).toBe("/cancel");
    const body = JSON.parse(requests[0].body ?? "{}");
    // Resolved from the session's active_run_id instead of no-op'ing on the
    // null currentRunId — this is the Stop-button fix.
    expect(body.run_id).toBe("run-bg");
  } finally {
    restore();
  }
});

test("stopRun falls back to session_id when no run id is known at all", async () => {
  const requests: CapturedRequest[] = [];
  const restore = mockBridge(requests);
  try {
    setState({
      config: { serverUrl: "http://x", apiKey: "" },
      currentSessionId: "session-1",
      currentRunId: null,
      running: true,
      sessions: [{ ...SESSION_ROW, active_run_id: null }],
      activeRunSessionIds: new Set(["session-1"]),
      messages: new Map(),
      order: [],
    });

    await stopRun();

    expect(requests).toHaveLength(1);
    const body = JSON.parse(requests[0].body ?? "{}");
    expect(body.session_id).toBe("session-1"); // server resolves the active run
    expect(body.run_id).toBeUndefined();
  } finally {
    restore();
  }
});

beforeEach(() => {
  setState({
    config: { serverUrl: "http://localhost:6877", apiKey: "" },
    currentSessionId: "session-1",
    messages: new Map(),
    order: [],
    running: false,
    currentRunId: null,
    activeRunSessionIds: new Set(),
    queuedMessages: [],
    pendingApprovals: [],
  });
});

test("enqueueMessage stages a local FIFO turn without starting another run", async () => {
  const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window;
  const requests: unknown[] = [];
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async (_config: unknown, request: unknown) => {
          requests.push(request);
          return {
            ok: true,
            contentType: "application/json",
            data: { run_id: "run-new", session_id: "session-1" },
          };
        },
      },
    },
    setTimeout,
    clearTimeout,
  };

  try {
    setState({
      running: true,
      currentRunId: "run-stale",
      activeRunSessionIds: new Set(["session-1"]),
    });

    await enqueueMessage("use mcp");

    const state = getState();
    expect(requests).toHaveLength(0);
    expect(state.queuedMessages).toHaveLength(1);
    expect(state.queuedMessages[0]).toMatchObject({ text: "use mcp", status: "pending" });
    expect(state.running).toBe(true);
    expect(state.currentRunId).toBe("run-stale");
    expect(state.order).toHaveLength(0);
  } finally {
    (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
  }
});

test("enqueueMessage stays local until the current run reaches a dispatch point", async () => {
  const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window;
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async () => ({
          ok: true,
          contentType: "application/json",
          data: { run_id: "", session_id: "session-1", status: "cancelled" },
        }),
      },
    },
    setTimeout,
    clearTimeout,
  };

  try {
    setState({
      running: false,
      currentRunId: null,
      activeRunSessionIds: new Set(),
    });

    await enqueueMessage("do not revive this");

    const state = getState();
    expect(state.queuedMessages).toHaveLength(1);
    expect(state.queuedMessages[0]).toMatchObject({ text: "do not revive this", status: "pending" });
    expect(state.order).toEqual([]);
    expect(state.messages.size).toBe(0);
    expect(state.running).toBe(false);
    expect(state.currentRunId).toBeNull();
  } finally {
    (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
  }
});

test("enqueueMessage reserves its client id locally until dispatch", async () => {
  const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window;
  const originalRandomUUID = crypto.randomUUID;
  crypto.randomUUID = () => "cid-ingested";
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async () => ({
          ok: true,
          contentType: "application/json",
          data: { run_id: "run-original", session_id: "session-1", status: "ingested" },
        }),
      },
    },
    setTimeout,
    clearTimeout,
  };

  try {
    setState({
      messages: new Map([
        ["cid-ingested", { id: "cid-ingested", role: "user", content: "already durable" }],
      ]),
      order: ["cid-ingested"],
      running: false,
      currentRunId: null,
      activeRunSessionIds: new Set(),
    });

    await enqueueMessage("already durable");

    const state = getState();
    expect(state.queuedMessages).toEqual([
      expect.objectContaining({ clientId: "cid-ingested", text: "already durable", status: "pending" }),
    ]);
    expect(state.order).toEqual(["cid-ingested"]);
    expect(state.messages.size).toBe(1);
    expect(state.running).toBe(false);
    expect(state.currentRunId).toBeNull();
  } finally {
    crypto.randomUUID = originalRandomUUID;
    (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
  }
});

test("sendMessage with no current session lazily creates one, then sends into it", async () => {
  // Home has no current session — the first message from its hero input
  // must provision one (reusing createSession) rather than silently no-op
  // like sendMessage used to when currentSessionId was null.
  setState({
    config: { serverUrl: "http://localhost:6877", apiKey: "" },
    currentSessionId: null,
    sessions: [],
    messages: new Map(),
    order: [],
    running: false,
    currentRunId: null,
    activeRunSessionIds: new Set(),
    queuedMessages: [],
    pendingApprovals: [],
  });

  const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window;
  const requests: { path: string; method?: string; body?: string }[] = [];
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async (_config: unknown, request: { path: string; method?: string; body?: string }) => {
          requests.push(request);
          if (request.path === "/sessions" && request.method === "POST") {
            return {
              ok: true,
              contentType: "application/json",
              data: { session_id: "new-1", name: null, area_id: null },
            };
          }
          if (request.path === "/session/history?session_id=new-1") {
            return {
              ok: true,
              contentType: "application/json",
              data: {
                messages: [],
                active_run_id: null,
                context_budget: CONTEXT_BUDGET,
                page: { has_more_before: false, has_more_after: false },
              },
            };
          }
          if (request.path === "/sessions/new-1/goal") {
            return { ok: true, contentType: "application/json", data: null };
          }
          if (request.path === "/chat/message") {
            return { ok: true, contentType: "application/json", data: { run_id: "run-1", session_id: "new-1" } };
          }
          throw new Error(`unexpected request: ${request.path}`);
        },
      },
    },
    setTimeout,
    clearTimeout,
  };

  try {
    await sendMessage("hello from home");

    expect(getState().currentSessionId).toBe("new-1");
    const postedSession = requests.find((r) => r.path === "/sessions" && r.method === "POST");
    expect(postedSession).toBeDefined();
    const chatCall = requests.find((r) => r.path === "/chat/message");
    expect(chatCall).toBeDefined();
    const chatBody = JSON.parse(chatCall?.body ?? "{}");
    expect(chatBody.session_id).toBe("new-1");
  } finally {
    (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
  }
});

test("enqueueMessage does not make a request before local dispatch", async () => {
  const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window;
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async () => {
          throw new Error("request failed");
        },
      },
    },
    setTimeout,
    clearTimeout,
  };

  try {
    setState({
      running: true,
      currentRunId: "run-1",
      activeRunSessionIds: new Set(["session-1"]),
    });

    await enqueueMessage("use mcp");

    const state = getState();
    expect(state.queuedMessages).toHaveLength(1);
    expect(state.queuedMessages[0]).toMatchObject({ text: "use mcp", status: "pending" });
    expect(state.order).toHaveLength(0);
  } finally {
    (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
  }
});
