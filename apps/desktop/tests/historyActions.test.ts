import { beforeEach, expect, test } from "bun:test";

import {
  cachedHistoryRefreshSessionIds,
  loadHistory,
  refreshCachedActiveSessionHistories,
} from "@/actions/history";
import { getState, setState } from "@/stores/index";
import {
  handleIncomingServerEvent,
  handleServerEvent,
  lastEventSeqForSession,
  resetEventSeqStateForTest,
  resetStreamStateForTest,
} from "@/hooks/useEvents";

beforeEach(() => {
  resetStreamStateForTest();
  resetEventSeqStateForTest();
  setState({
    messages: new Map(),
    order: [],
    activeActivityId: null,
    running: false,
    currentRunId: null,
    currentSessionId: null,
    error: null,
    skipApprovals: false,
    pendingApprovals: [],
    queuedMessages: [],
    activeRunSessionIds: new Set(),
    backgroundedRunSessionIds: new Set(),
    unreadDoneSessionIds: new Set(),
    sessionCache: new Map(),
    stoppingRunId: null,
    terminalRunIds: new Set(),
  });
});

test("loadHistory does not rewind past a stream_reset cursor", async () => {
  const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window;
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async () => ({
          ok: true,
          status: 200,
          statusText: "OK",
          contentType: "application/json",
          data: {
            messages: [],
            active_run_id: "run-active",
            runtime: {
              session_id: "cursor-session",
              latest_event_seq: 8,
              checkpoint_seq: 7,
              active_run: { run_id: "run-active", status: "running" },
              pending_approvals: [],
              queued_messages: [],
            },
            page: { has_more_before: false, has_more_after: false },
          },
          text: "",
        }),
      },
    },
    setTimeout,
    clearTimeout,
  };

  try {
    setState({
      config: { serverUrl: "http://localhost:6877", apiKey: "" },
      currentSessionId: "cursor-session",
    });
    handleServerEvent({
      type: "TOOL_CALL_START",
      tool_call_id: "tool-cursor",
      tool_call_name: "ReadFile",
      session_id: "cursor-session",
      seq: 10,
    });
    await handleIncomingServerEvent(
      {
        type: "stream_reset",
        reason: "replay_gap",
        session_id: "cursor-session",
        seq: 20,
      },
      async () => undefined,
    );

    await loadHistory("cursor-session");

    expect(lastEventSeqForSession("cursor-session")).toBe(20);
  } finally {
    (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
  }
});

test("active-session cache refresh hydrates inactive running sessions with current activity", async () => {
  const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window;
  const requests: string[] = [];
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async (_config: unknown, request: { path: string }) => {
          requests.push(request.path);
          return {
            ok: true,
            status: 200,
            statusText: "OK",
            contentType: "application/json",
            data: {
              messages: [
                { role: "user", content: "fresh A", id: "a-1" },
                {
                  role: "assistant",
                  content: "",
                  id: "assistant-research-1",
                  tool_calls: [
                    {
                      id: "research-1",
                      name: "research",
                      arguments: '{"task":"history state"}',
                    },
                  ],
                },
              ],
              active_run_id: "run-A",
              runtime: {
                session_id: "A",
                latest_event_seq: 4,
                checkpoint_seq: 4,
                active_run: { run_id: "run-A", status: "running" },
                pending_approvals: [],
                queued_messages: [],
              },
              page: { has_more_before: false, has_more_after: false },
            },
            text: "",
          };
        },
      },
    },
    setTimeout,
    clearTimeout,
  };

  try {
    const s = getState();
    s.setConfig({ serverUrl: "http://localhost:6877", apiKey: "" });
    s.setCurrentSession("B");

    await refreshCachedActiveSessionHistories(
      [{ sessionId: "A", runId: "run-A", status: "running" }],
      { force: true },
    );

    expect(requests).toEqual(["/session/history?session_id=A"]);
    expect(getState().sessionCache.get("A")?.messages.get("a-1")?.content).toBe("fresh A");
    const cachedActivityId = getState()
      .sessionCache.get("A")
      ?.order.find((id) => getState().sessionCache.get("A")?.messages.get(id)?.role === "activity");
    expect(getState().sessionCache.get("A")?.activeActivityId).toBe(cachedActivityId);
    expect(getState().sessionCache.get("A")?.messages.get(cachedActivityId!)?.activity).toMatchObject({
      label: "Calling",
      done: false,
      items: [{ id: "research-1", kind: "research", status: "ongoing" }],
    });
    expect(lastEventSeqForSession("A")).toBe(4);

    s.setCurrentSession("A");
    expect(getState().messages.get("a-1")?.content).toBe("fresh A");
    expect(getState().running).toBe(true);
    expect(getState().currentRunId).toBe("run-A");
    expect(getState().sessionView.historyLoadedFor).toBe("A");
    const activityId = getState().order.find((id) => getState().messages.get(id)?.role === "activity");
    expect(getState().activeActivityId).toBe(activityId);
    expect(getState().messages.get(activityId!)?.suppressEntryMotion).toBe(true);
    expect(getState().messages.get(activityId!)?.activity?.items[0]).toMatchObject({
      id: "research-1",
      status: "ongoing",
    });
  } finally {
    (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
  }
});

test("active-session cache refresh preserves live agent trace not represented in history", async () => {
  const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window;
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async () => ({
          ok: true,
          status: 200,
          statusText: "OK",
          contentType: "application/json",
          data: {
            messages: [
              { role: "user", content: "research", id: "user-1" },
              {
                role: "assistant",
                content: "",
                id: "assistant-load-tools",
                tool_calls: [
                  {
                    id: "load-tools",
                    name: "load_tools",
                    arguments: '{"group":"mcp:obsidian"}',
                  },
                ],
              },
            ],
            active_run_id: "run-A",
            runtime: {
              session_id: "A",
              latest_event_seq: 20,
              checkpoint_seq: 10,
              active_run: { run_id: "run-A", status: "running" },
              pending_approvals: [],
              queued_messages: [],
            },
            page: { has_more_before: false, has_more_after: false },
          },
          text: "",
        }),
      },
    },
    setTimeout,
    clearTimeout,
  };

  try {
    const s = getState();
    s.setConfig({ serverUrl: "http://localhost:6877", apiKey: "" });
    s.setCurrentSession("A");
    s.appendMessage({ id: "user-1", role: "user", content: "research" });
    s.appendMessage({
      id: "live-activity",
      role: "activity",
      content: "",
      suppressEntryMotion: true,
      activity: {
        label: "Calling",
        done: false,
        items: [
          {
            id: "research-parent",
            kind: "research",
            semanticKind: "agent",
            displayName: "Continual LLM memory",
            target: "Research(task='Find recent areas')",
            status: "ongoing",
            taskStatus: "running",
            runId: "run-A",
          },
          {
            id: "child-search",
            kind: "WebSearch",
            target: "WebSearch(query='continual learning benchmark')",
            status: "ongoing",
            depth: 1,
            parentToolId: "research-parent",
          },
        ],
      },
    });
    s.setActiveActivityId("live-activity");
    s.markRunStarted("run-A", "A");
    s.setCurrentSession("B");

    await refreshCachedActiveSessionHistories(
      [{ sessionId: "A", runId: "run-A", status: "running" }],
      { force: true },
    );

    const cached = getState().sessionCache.get("A");
    const activityId = cached?.activeActivityId;
    const activity = activityId ? cached?.messages.get(activityId)?.activity : null;
    expect(activity?.items.map((item) => item.id)).toEqual([
      "load-tools",
      "research-parent",
      "child-search",
    ]);
    expect(activity?.items.find((item) => item.id === "child-search")).toMatchObject({
      parentToolId: "research-parent",
      depth: 1,
    });
    expect(activity?.items.find((item) => item.id === "research-parent")).toMatchObject({
      semanticKind: "agent",
      taskStatus: "running",
    });

    s.setCurrentSession("A");
    const restoredActivity = getState().messages.get(getState().activeActivityId!)?.activity;
    expect(restoredActivity?.items.map((item) => item.id)).toEqual([
      "load-tools",
      "research-parent",
      "child-search",
    ]);
  } finally {
    (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
  }
});

test("loadHistory preserves visible live agent trace not represented in history", async () => {
  const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window;
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async () => ({
          ok: true,
          status: 200,
          statusText: "OK",
          contentType: "application/json",
          data: {
            messages: [
              { role: "user", content: "research", id: "user-1" },
              {
                role: "assistant",
                content: "",
                id: "assistant-load-tools",
                tool_calls: [
                  {
                    id: "load-tools",
                    name: "load_tools",
                    arguments: '{"group":"mcp:obsidian"}',
                  },
                ],
              },
            ],
            active_run_id: "run-A",
            runtime: {
              session_id: "A",
              latest_event_seq: 20,
              checkpoint_seq: 10,
              active_run: { run_id: "run-A", status: "running" },
              pending_approvals: [],
              queued_messages: [],
            },
            page: { has_more_before: false, has_more_after: false },
          },
          text: "",
        }),
      },
    },
    setTimeout,
    clearTimeout,
  };

  try {
    const s = getState();
    s.setConfig({ serverUrl: "http://localhost:6877", apiKey: "" });
    s.setCurrentSession("A");
    s.appendMessage({
      id: "live-activity",
      role: "activity",
      content: "",
      suppressEntryMotion: true,
      activity: {
        label: "Calling",
        done: false,
        items: [
          {
            id: "research-parent",
            kind: "research",
            semanticKind: "agent",
            displayName: "Continual LLM memory",
            target: "Research(task='Find recent areas')",
            status: "ongoing",
            taskStatus: "running",
            runId: "run-A",
          },
          {
            id: "child-search",
            kind: "WebSearch",
            target: "WebSearch(query='continual learning benchmark')",
            status: "ongoing",
            depth: 1,
            parentToolId: "research-parent",
          },
        ],
      },
    });
    s.setActiveActivityId("live-activity");
    s.markRunStarted("run-A", "A");

    await loadHistory("A");

    const activity = getState().messages.get(getState().activeActivityId!)?.activity;
    expect(activity?.items.map((item) => item.id)).toEqual([
      "load-tools",
      "research-parent",
      "child-search",
    ]);
    expect(activity?.items.find((item) => item.id === "child-search")).toMatchObject({
      parentToolId: "research-parent",
      depth: 1,
    });
  } finally {
    (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
  }
});

test("active-session cache refresh skips the visible session", () => {
  const s = getState();
  s.setCurrentSession("A");

  expect(cachedHistoryRefreshSessionIds([
    { sessionId: "A", runId: "run-A", status: "running" },
    { sessionId: "B", runId: "run-B", status: "backgrounded", backgrounded: true },
    { sessionId: "C", runId: "run-C", status: "completed" },
  ])).toEqual(["B"]);
});

test("loadHistory reapplies local Auto to active runtime and hides stale approvals", async () => {
  const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window;
  const requests: { path: string; method?: string; body?: string }[] = [];
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async (_config: unknown, request: { path: string; method?: string; body?: string }) => {
          requests.push(request);
          const sessionId = request.path.startsWith("/session/history")
            ? new URLSearchParams(request.path.split("?")[1] ?? "").get("session_id") ?? "auto-session"
            : "auto-session";
          return {
            ok: true,
            status: 200,
            statusText: "OK",
            contentType: "application/json",
            data: request.path.startsWith("/session/history")
              ? {
                  messages: [],
                  active_run_id: "run-auto",
                  runtime: {
                    session_id: sessionId,
                    latest_event_seq: 7,
                    checkpoint_seq: 7,
                    active_run: { run_id: "run-auto", status: "running" },
                    pending_approvals: [
                      {
                        tool_id: "tool-1",
                        tool_name: "Bash",
                        preview: "date",
                        diff: null,
                        status: "pending",
                      },
                    ],
                    queued_messages: [],
                  },
                }
              : { status: "ok", skip_approvals: true, auto_resolved: 1 },
            text: "",
          };
        },
      },
    },
    setTimeout,
    clearTimeout,
  };

  try {
    setState({
      config: { serverUrl: "http://localhost:6877", apiKey: "" },
      currentSessionId: "auto-session",
      skipApprovals: true,
    });

    await loadHistory("auto-session");

    expect(getState().pendingApprovals).toEqual([]);
    expect(
      requests.some(
        (request) =>
          request.path === "/sessions/auto-session/auto" &&
          request.method === "POST" &&
          request.body === JSON.stringify({ value: true }),
      ),
    ).toBe(true);

    requests.length = 0;
    setState({
      currentSessionId: "manual-session",
      skipApprovals: false,
      pendingApprovals: [],
    });

    await loadHistory("manual-session");

    expect(getState().pendingApprovals).toMatchObject([{ toolId: "tool-1", status: "pending" }]);
    expect(requests.some((request) => request.path === "/sessions/manual-session/auto")).toBe(false);
  } finally {
    (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
  }
});

test("loadHistory keeps active run tail activity open while a tool is being called", async () => {
  const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window;
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async () => ({
          ok: true,
          status: 200,
          statusText: "OK",
          contentType: "application/json",
          data: {
            messages: [
              { role: "user", content: "inspect", id: "user-1" },
              {
                role: "assistant",
                content: "",
                id: "assistant-tool-1",
                tool_calls: [{ id: "tool-1", name: "ReadFile", arguments: '{"path":"a"}' }],
              },
            ],
            active_run_id: "run-active",
            runtime: {
              session_id: "tool-active-session",
              latest_event_seq: 4,
              checkpoint_seq: 4,
              active_run: { run_id: "run-active", status: "running" },
              pending_approvals: [],
              queued_messages: [],
            },
            page: { has_more_before: false, has_more_after: false },
          },
          text: "",
        }),
      },
    },
    setTimeout,
    clearTimeout,
  };

  try {
    setState({
      config: { serverUrl: "http://localhost:6877", apiKey: "" },
      currentSessionId: "tool-active-session",
    });

    await loadHistory("tool-active-session");

    const state = getState();
    const activityId = state.order.find((id) => state.messages.get(id)?.role === "activity");
    expect(state.sessionView.historyLoadedFor).toBe("tool-active-session");
    expect(state.activeActivityId).toBe(activityId);
    expect(state.messages.get(activityId!)?.activity).toMatchObject({
      done: false,
      label: "Calling",
      items: [{ id: "tool-1", status: "ongoing" }],
    });
  } finally {
    (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
  }
});

test("loadHistory treats backgrounded runtime as non-foreground", async () => {
  const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window;
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async () => ({
          ok: true,
          status: 200,
          statusText: "OK",
          contentType: "application/json",
          data: {
            messages: [
              { role: "user", content: "inspect", id: "user-1" },
              {
                role: "assistant",
                content: "",
                id: "assistant-tool-1",
                tool_calls: [{ id: "tool-1", name: "ReadFile", arguments: '{"path":"a"}' }],
              },
            ],
            active_run_id: "run-bg",
            runtime: {
              session_id: "bg-session",
              latest_event_seq: 4,
              checkpoint_seq: 4,
              active_run: { run_id: "run-bg", status: "backgrounded" },
              pending_approvals: [],
              queued_messages: [],
            },
            page: { has_more_before: false, has_more_after: false },
          },
          text: "",
        }),
      },
    },
    setTimeout,
    clearTimeout,
  };

  try {
    setState({
      config: { serverUrl: "http://localhost:6877", apiKey: "" },
      currentSessionId: "bg-session",
      running: true,
      currentRunId: "run-bg",
      activeRunSessionIds: new Set(["bg-session"]),
    });

    await loadHistory("bg-session");

    const state = getState();
    const activityId = state.order.find((id) => state.messages.get(id)?.role === "activity");
    expect(state.running).toBe(false);
    expect(state.currentRunId).toBeNull();
    expect(state.activeRunSessionIds.has("bg-session")).toBe(false);
    expect(state.backgroundedRunSessionIds.has("bg-session")).toBe(true);
    expect(state.activeActivityId).toBeNull();
    expect(state.messages.get(activityId!)?.activity).toMatchObject({
      done: true,
      label: "Called",
      items: [{ id: "tool-1", status: "executed" }],
    });
  } finally {
    (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
  }
});
