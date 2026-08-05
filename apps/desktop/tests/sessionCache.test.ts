import { beforeEach, expect, test } from "bun:test";
import { refresh } from "@/actions/bootstrap";
import { loadHistory } from "@/actions/history";
import { goToNewSessionHome, switchSession } from "@/actions/sessions";
import { getState, setState, useStore } from "@/stores/index";
import type { UiMessage } from "@/stores/index";
import { createInitialSessionViewState } from "@/stores/session-view";
import { snapshotSession } from "@/stores/session-cache";

function blank() {
  setState({
    sessionView: createInitialSessionViewState(),
    currentSessionId: null,
    pendingNewChatAreaId: null,
    pendingNewChatDraftId: 0,
    messages: new Map(),
    order: [],
    running: false,
    currentRunId: null,
    activeRunSessionIds: new Set(),
    backgroundedRunSessionIds: new Set(),
    unreadDoneSessionIds: new Set(),
    terminalRunIds: new Set(),
    activeActivityId: null,
    sessionCache: new Map(),
    pendingApprovals: [],
    reviewingApprovalToolId: null,
    compacting: false,
    sourceFocus: null,
    editingId: null,
    queuedMessages: [],
    goals: {},
  });
}

function userMessage(id: string, content: string): UiMessage {
  return { id, role: "user", content };
}

beforeEach(blank);

test("switching sessions snapshots outgoing state into cache", () => {
  const s = getState();
  s.setCurrentSession("A");
  s.setHistory([userMessage("a-1", "hello A")]);
  s.markRunStarted("run-A", "A");

  s.setCurrentSession("B");

  const cached = getState().sessionCache.get("A");
  expect(cached).toBeDefined();
  expect(cached!.order).toEqual(["a-1"]);
  expect(cached!.currentRunId).toBe("run-A");
  expect(cached!.running).toBe(true);
  expect(cached!.sessionView.historyLoadedFor).toBe("A");
});

test("session cache does not replay compaction UI state", () => {
  setState({
    currentSessionId: "session-1",
    compacting: true,
  });

  const cached = snapshotSession(getState());

  expect(cached.compacting).toBe(false);
});

test("session list refresh does not treat backgrounded runs as foreground running", () => {
  const s = getState();
  s.setCurrentSession("A");
  s.setSessions([
    {
      session_id: "A",
      started_at: "",
      last_activity: "",
      name: "A",
      message_count: 0,
      active_run_id: "run-bg",
      run_status: "backgrounded",
    },
  ]);

  expect(getState().running).toBe(false);
  expect(getState().currentRunId).toBeNull();
  expect(getState().activeRunSessionIds.has("A")).toBe(false);
  expect(getState().backgroundedRunSessionIds.has("A")).toBe(true);
});

test("switching back hydrates cached state", () => {
  const s = getState();
  s.setCurrentSession("A");
  s.setHistory([userMessage("a-1", "hello A")]);

  s.setCurrentSession("B");
  // B is fresh and has no cache yet — global slots are blank.
  expect(getState().messages.size).toBe(0);
  expect(getState().sessionView.historyLoadedFor).toBeNull();

  s.setCurrentSession("A");
  // Restored canonical cache is render-ready while the caller reconciles
  // with fresh server history.
  expect(getState().messages.get("a-1")?.content).toBe("hello A");
  expect(getState().sessionView.historyLoadedFor).toBe("A");
  expect(getState().sessionView.historyPhase).toBe("cached-preview");
  expect(getState().sessionView.canonicalHistoryRequired).toBe(true);
});

test("switching back suppresses cached preview entry motion", () => {
  const s = getState();
  s.setCurrentSession("A");
  s.appendMessage(userMessage("a-1", "live A"));
  s.setCurrentSession("B");

  s.setCurrentSession("A");

  expect(getState().messages.get("a-1")?.suppressEntryMotion).toBe(true);
});

test("switching back merges cached activity groups split only by hidden rows", () => {
  const s = getState();
  s.setCurrentSession("A");
  s.appendMessage(userMessage("a-1", "run"));
  s.appendMessage({
    id: "activity-1",
    role: "activity",
    content: "",
    activity: {
      label: "Calling",
      done: false,
      items: [{ id: "tool-1", kind: "Bash", target: "Bash(command='sleep 90')" }],
    },
  });
  s.appendMessage({ id: "reasoning-1", role: "reasoning", title: "Reasoning", content: "thinking" });
  s.appendMessage({ id: "assistant-empty", role: "assistant", content: "" });
  s.appendMessage({
    id: "activity-2",
    role: "activity",
    content: "",
    activity: {
      label: "Calling",
      done: false,
      items: [{ id: "tool-2", kind: "Bash", target: "Bash(command='sleep 120')" }],
    },
  });
  s.setActiveActivityId("activity-2");

  s.setCurrentSession("B");
  s.setCurrentSession("A");

  const state = getState();
  const activityIds = state.order.filter((id) => state.messages.get(id)?.role === "activity");
  expect(activityIds).toEqual(["activity-1"]);
  expect(state.activeActivityId).toBe("activity-1");
  expect(state.messages.get("activity-1")?.activity).toMatchObject({
    label: "Calling",
    done: false,
  });
  expect(state.messages.get("activity-1")?.activity?.items.map((item) => item.id)).toEqual([
    "tool-1",
    "tool-2",
  ]);
});

test("canonical history reload merges activity groups split only by hidden rows", () => {
  const s = getState();
  s.setCurrentSession("A");
  s.setHistory([
    userMessage("a-1", "run"),
    {
      id: "activity-1",
      role: "activity",
      content: "",
      activity: {
        label: "Called",
        done: true,
        items: [{ id: "tool-1", kind: "Bash", target: "Bash(command='sleep 90')" }],
      },
    },
    { id: "reasoning-1", role: "reasoning", title: "Reasoning", content: "thinking" },
    { id: "assistant-empty", role: "assistant", content: "" },
    {
      id: "activity-2",
      role: "activity",
      content: "",
      activity: {
        label: "Calling",
        done: false,
        items: [{ id: "tool-2", kind: "Bash", target: "Bash(command='sleep 120')" }],
      },
    },
  ]);

  const state = getState();
  const activityIds = state.order.filter((id) => state.messages.get(id)?.role === "activity");
  expect(activityIds).toEqual(["activity-1"]);
  expect(state.activeActivityId).toBe("activity-1");
  expect(state.messages.get("activity-1")?.activity).toMatchObject({
    label: "Calling",
    done: false,
  });
  expect(state.messages.get("activity-1")?.activity?.items.map((item) => item.id)).toEqual([
    "tool-1",
    "tool-2",
  ]);
});

test("canonical reload keeps active activity before trailing empty assistant placeholder", () => {
  const s = getState();
  s.setCurrentSession("A");
  s.setHistory([
    userMessage("a-1", "run"),
    {
      id: "activity-1",
      role: "activity",
      content: "",
      activity: {
        label: "Calling",
        done: false,
        items: [{ id: "tool-1", kind: "Bash", target: "Bash(command='sleep 90')" }],
      },
    },
    { id: "assistant-empty", role: "assistant", content: "" },
  ]);

  expect(getState().activeActivityId).toBe("activity-1");
});

test("switchSession preserves cached preview until canonical history replaces it", async () => {
  const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window;
  const requests: string[] = [];
  let resolveRequest: ((value: unknown) => void) | null = null;
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async (_config: unknown, request: { path: string }) => {
          requests.push(request.path);
          if (request.path === "/sessions/A/goal") {
            return {
              ok: true,
              status: 200,
              statusText: "OK",
              contentType: "application/json",
              data: null,
              text: "",
            };
          }
          return await new Promise((resolve) => {
            resolveRequest = resolve;
          });
        },
      },
    },
    setTimeout,
    clearTimeout,
  };

  try {
    const s = getState();
    s.setConfig({ serverUrl: "http://localhost:6877", apiKey: "" });
    s.setCurrentSession("A");
    s.setHistory([userMessage("a-1", "cached A")]);
    s.markRunStarted("run-A", "A");
    s.setCurrentSession("B");

    const switching = switchSession("A");

    expect(requests).toEqual(["/session/history?session_id=A"]);
    expect(getState().messages.get("a-1")?.content).toBe("cached A");
    expect(getState().running).toBe(true);
    expect(getState().currentRunId).toBe("run-A");
    expect(getState().sessionView.historyLoadedFor).toBe("A");
    expect(getState().sessionView.historyReloadingFor).toBe("A");
    expect(getState().sessionView.historyPhase).toBe("loading-history");

    resolveRequest?.({
      ok: true,
      status: 200,
      statusText: "OK",
      contentType: "application/json",
      data: {
        messages: [
          {
            id: "server-a-1",
            role: "user",
            content: "canonical A",
          },
        ],
        active_run_id: null,
      },
      text: "",
    });
    await switching;
    // loadHistory rehydrates the workflows + subagent-roster domains (in-memory,
    // otherwise empty after a fresh load) right after setting the transcript.
    expect(requests).toEqual([
      "/session/history?session_id=A",
      "/chat/A/workflows",
      "/chat/child-agents?session_id=A",
      "/sessions/A/goal",
      "/sessions/A/todo",
    ]);

    expect(getState().messages.get("server-a-1")?.content).toBe("canonical A");
    expect(getState().messages.has("a-1")).toBe(false);
    expect(getState().sessionView.historyLoadedFor).toBe("A");
    expect(getState().sessionView.historyReloadingFor).toBeNull();
    expect(getState().sessionView.canonicalHistoryRequired).toBe(false);
    expect(getState().running).toBe(false);
    expect(getState().currentRunId).toBeNull();
  } finally {
    (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
  }
});

test("refresh loads areas/sessions but does not auto-select a current session", async () => {
  // Home is the app's entrypoint (Areas spec, "Placement") — refresh() must
  // not resurrect the old GET /session "load the latest session" behavior,
  // or every boot would land back in Chat instead of Home.
  const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window;
  const requests: string[] = [];
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async (_config: unknown, request: { path: string }) => {
          requests.push(request.path);
          if (request.path === "/health") {
            return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data: { auth: true }, text: "" };
          }
          if (request.path === "/areas") {
            return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data: { areas: [] }, text: "" };
          }
          if (request.path === "/sessions" || request.path.startsWith("/sessions?limit=500&offset=")) {
            return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data: { sessions: [{ session_id: "A", name: "A" }] }, text: "" };
          }
          throw new Error(`unexpected request: ${request.path}`);
        },
      },
    },
    setTimeout,
    clearTimeout,
  };

  try {
    setState({ config: { serverUrl: "http://localhost:6877", apiKey: "" }, currentSessionId: null });
    await refresh();

    expect(requests).toEqual(["/health", "/areas", "/sessions?limit=500&offset=0&include_agents=false"]);
    expect(getState().connected).toBe(true);
    expect(getState().error).toBeNull();
    expect(getState().sessions.map((s) => s.session_id)).toEqual(["A"]);
    expect(getState().currentSessionId).toBeNull();
  } finally {
    (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
  }
});

test("older replace history response cannot override newer response", async () => {
  const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window;
  const requests: string[] = [];
  const resolvers: Array<(value: unknown) => void> = [];
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async (_config: unknown, request: { path: string }) => {
          requests.push(request.path);
          return await new Promise((resolve) => {
            resolvers.push(resolve);
          });
        },
      },
    },
    setTimeout,
    clearTimeout,
  };

  try {
    const s = getState();
    s.setConfig({ serverUrl: "http://localhost:6877", apiKey: "" });
    s.setCurrentSession("A");

    const older = loadHistory("A");
    const newer = loadHistory("A");

    expect(requests).toEqual([
      "/session/history?session_id=A",
      "/session/history?session_id=A",
    ]);
    expect(getState().sessionView.historyReloadingFor).toBe("A");

    resolvers[1]?.({
      ok: true,
      status: 200,
      statusText: "OK",
      contentType: "application/json",
      data: {
        messages: [
          {
            id: "server-a-new",
            role: "user",
            content: "new canonical A",
          },
        ],
        active_run_id: null,
      },
      text: "",
    });
    await newer;

    resolvers[0]?.({
      ok: true,
      status: 200,
      statusText: "OK",
      contentType: "application/json",
      data: {
        messages: [
          {
            id: "server-a-old",
            role: "user",
            content: "old canonical A",
          },
        ],
        active_run_id: "old-run",
      },
      text: "",
    });
    await older;

    expect(getState().messages.get("server-a-new")?.content).toBe("new canonical A");
    expect(getState().messages.has("server-a-old")).toBe(false);
    expect(getState().sessionView.historyLoadedFor).toBe("A");
    expect(getState().sessionView.historyReloadingFor).toBeNull();
    expect(getState().running).toBe(false);
    expect(getState().currentRunId).toBeNull();
  } finally {
    (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
  }
});

test("newer history tool-only page updates result and advances cursor", async () => {
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
                {
                  id: "tool-result-1",
                  message_id: "tool-result-1",
                  role: "tool",
                  content: "done",
                  tool_call_id: "tool-1",
                  seq: 2,
                },
              ],
              active_run_id: null,
              page: {
                has_more_before: true,
                has_more_after: true,
                before: "tool-result-1",
                after: "tool-result-1",
              },
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
    s.setCurrentSession("A");
    s.setHistory(
      [
        {
          id: "assistant-activity",
          role: "activity",
          sourceMessageId: "assistant-1",
          content: "",
          activity: {
            label: "Called",
            done: true,
            items: [{ id: "tool-1", kind: "ReadFile", target: "ReadFile()" }],
          },
        },
      ],
      {
        has_more_before: false,
        has_more_after: true,
        before: "assistant-1",
        after: "assistant-1",
      },
    );

    await loadHistory("A", { mode: "append", after: getState().sessionView.historyAfterCursor ?? undefined });

    expect(requests).toEqual(["/session/history?session_id=A&after=assistant-1"]);
    expect(getState().messages.get("assistant-activity")?.activity?.items[0]?.result).toBe("done");
    expect(getState().sessionView.historyAfterCursor).toBe("tool-result-1");
  } finally {
    (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
  }
});

test("history tool result page can load before the tool call page", async () => {
  const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window;
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async (_config: unknown, request: { path: string }) => ({
          ok: true,
          status: 200,
          statusText: "OK",
          contentType: "application/json",
          data: request.path.includes("after=")
            ? {
                messages: [
                  {
                    id: "tool-result-1",
                    message_id: "tool-result-1",
                    role: "tool",
                    content: "done",
                    tool_call_id: "tool-1",
                    seq: 3,
                  },
                ],
                active_run_id: null,
                page: {
                  has_more_before: true,
                  has_more_after: false,
                  before: "tool-result-1",
                  after: "tool-result-1",
                },
              }
            : {
                messages: [
                  {
                    id: "assistant-tool-1",
                    message_id: "assistant-tool-1",
                    role: "assistant",
                    content: "",
                    seq: 2,
                    tool_calls: [{ id: "tool-1", name: "Bash", arguments: '{"command":"date"}' }],
                  },
                ],
                active_run_id: null,
                page: {
                  has_more_before: true,
                  has_more_after: true,
                  before: "assistant-tool-1",
                  after: "assistant-tool-1",
                },
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

    await loadHistory("A", { mode: "append", after: "assistant-tool-1" });
    expect([...getState().messages.values()].find((message) => message.role === "activity")).toBeUndefined();

    await loadHistory("A", { mode: "prepend", before: "tool-result-1" });

    const activity = [...getState().messages.values()].find((message) => message.role === "activity");
    expect(activity?.activity?.items).toMatchObject([{ id: "tool-1", result: "done" }]);
  } finally {
    (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
  }
});

test("canonical history replace drops stale pending tool result patches", async () => {
  const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window;
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async (_config: unknown, request: { path: string }) => ({
          ok: true,
          status: 200,
          statusText: "OK",
          contentType: "application/json",
          data: request.path.includes("after=")
            ? {
                messages: [
                  {
                    id: "stale-tool-result",
                    message_id: "stale-tool-result",
                    role: "tool",
                    content: "stale done",
                    tool_call_id: "tool-1",
                    seq: 3,
                  },
                ],
                active_run_id: null,
                page: {
                  has_more_before: true,
                  has_more_after: false,
                  before: "stale-tool-result",
                  after: "stale-tool-result",
                },
              }
            : {
                messages: [
                  {
                    id: "assistant-tool-1",
                    message_id: "assistant-tool-1",
                    role: "assistant",
                    content: "",
                    seq: 10,
                    tool_calls: [{ id: "tool-1", name: "Bash", arguments: '{"command":"date"}' }],
                  },
                ],
                active_run_id: null,
                page: {
                  has_more_before: false,
                  has_more_after: false,
                  before: "assistant-tool-1",
                  after: "assistant-tool-1",
                },
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

    await loadHistory("A", { mode: "append", after: "cursor" });
    await loadHistory("A");

    const activity = [...getState().messages.values()].find((message) => message.role === "activity");
    expect(activity?.activity?.items).toMatchObject([{ id: "tool-1" }]);
    expect(activity?.activity?.items[0]?.result).toBeUndefined();
  } finally {
    (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
  }
});

test("newer non-tail active history page does not steal live activity target", async () => {
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
              {
                id: "assistant-older",
                role: "assistant",
                content: "",
                tool_calls: [{ id: "old-tool", name: "Bash", arguments: "{}" }],
              },
            ],
            active_run_id: "run-active",
            page: {
              has_more_before: true,
              has_more_after: true,
              before: "assistant-older",
              after: "assistant-older",
            },
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

    await loadHistory("A", { mode: "append", after: "cursor" });

    const activity = [...getState().messages.values()].find((message) => message.role === "activity");
    expect(activity?.activity).toMatchObject({ done: true, label: "Called" });
    expect(getState().activeActivityId).toBeNull();
  } finally {
    (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
  }
});

test("paged history merges activity groups split by hidden rows across page boundary", () => {
  const s = getState();
  s.setCurrentSession("A");
  s.setHistory([
    { id: "reasoning-1", role: "reasoning", title: "Reasoning", content: "thinking" },
    { id: "assistant-empty", role: "assistant", content: "" },
    {
      id: "activity-2",
      role: "activity",
      content: "",
      activity: {
        label: "Calling",
        done: false,
        items: [{ id: "tool-2", kind: "Bash", target: "Bash(command='sleep 120')" }],
      },
    },
  ]);
  s.setActiveActivityId("activity-2");

  s.prependHistory([
    {
      id: "activity-1",
      role: "activity",
      content: "",
      activity: {
        label: "Called",
        done: true,
        items: [{ id: "tool-1", kind: "Bash", target: "Bash(command='sleep 90')" }],
      },
    },
  ]);

  const state = getState();
  const activityIds = state.order.filter((id) => state.messages.get(id)?.role === "activity");
  expect(activityIds).toEqual(["activity-1"]);
  expect(state.activeActivityId).toBe("activity-1");
  expect(state.messages.get("activity-1")?.activity).toMatchObject({
    label: "Calling",
    done: false,
  });
  expect(state.messages.get("activity-1")?.activity?.items.map((item) => item.id)).toEqual([
    "tool-1",
    "tool-2",
  ]);
});

test("re-selecting current session is a no-op for view state", () => {
  const s = getState();
  s.setCurrentSession("A");
  s.appendMessage(userMessage("a-1", "live"));
  const liveMessages = getState().messages;

  s.setCurrentSession("A");
  // Same reference — no clobber from a stale cache snapshot.
  expect(getState().messages).toBe(liveMessages);
});

test("running state persists across switches", () => {
  const s = getState();
  s.setCurrentSession("A");
  s.markRunStarted("run-A", "A");

  s.setCurrentSession("B");
  expect(getState().running).toBe(false);
  expect(getState().currentRunId).toBeNull();

  s.setCurrentSession("A");
  expect(getState().running).toBe(true);
  expect(getState().currentRunId).toBe("run-A");
});

test("queued messages persist across switches", () => {
  const s = getState();
  s.setCurrentSession("A");
  s.addQueuedMessage({
    clientId: "cid-1",
    text: "follow-up",
    status: "pending",
    enqueuedAt: 0,
  });
  s.setCurrentSession("B");
  expect(getState().queuedMessages).toEqual([]);
  s.setCurrentSession("A");
  expect(getState().queuedMessages).toHaveLength(1);
  expect(getState().queuedMessages[0].clientId).toBe("cid-1");
});

test("history reload clears queued messages that are already persisted", () => {
  const s = getState();
  s.setCurrentSession("A");
  s.addQueuedMessage({
    clientId: "cid-1",
    text: "follow-up",
    status: "pending",
    enqueuedAt: 0,
  });

  s.setHistory([userMessage("cid-1", "follow-up")]);

  expect(getState().queuedMessages).toEqual([]);
});

test("resetCancellingQueuedMessages releases an interrupted send back to pending", () => {
  const s = getState();
  s.setCurrentSession("A");
  s.addQueuedMessage({ clientId: "cid-1", text: "a", status: "sending", enqueuedAt: 0 });
  s.addQueuedMessage({ clientId: "cid-2", text: "b", status: "pending", enqueuedAt: 0 });
  s.resetCancellingQueuedMessages();
  expect(getState().queuedMessages.map((q) => q.status)).toEqual(["pending", "pending"]);
});

test("goToNewSessionHome keeps only an unpersisted Area intent", () => {
  // "New session" (sidebar nav row / ⌘N / command palette) must land on
  // Home, not eagerly provision an empty session — Home's hero input
  // creates the session lazily on first send.
  const s = getState();
  s.setCurrentSession("A");
  s.openArea("o-1a");
  s.openMemory();
  s.openAutomations();
  s.openSettings();
  expect(getState().areas.openAreaKey).toBe("o-1a");

  goToNewSessionHome("o-1a");

  expect(getState().currentSessionId).toBeNull();
  expect(getState().pendingNewChatAreaId).toBe("o-1a");
  expect(getState().areas.openAreaKey).toBeNull();
  expect(getState().memoryOpen).toBe(false);
  expect(getState().automationsOpen).toBe(false);
  expect(getState().settingsOpen).toBe(false);
  expect(getState().order).toEqual([]);
});

test("opening a real session clears pending New Chat Area intent", () => {
  goToNewSessionHome("o-1a");

  getState().setCurrentSession("A");

  expect(getState().pendingNewChatAreaId).toBeNull();
});
