import { expect, test } from "bun:test";
import {
  createDomainState,
  reduceDomainState,
  type RunPhase,
} from "@/stores/domains";
import {
  createAutomationStreamDomainState,
  reduceAutomationFinished,
  reduceAutomationProgress,
  reduceAutomationStreamConnected,
  reduceAutomationStreamConnecting,
  reduceAutomationStreamFailed,
  reduceAutomationStreamStale,
} from "@/stores/automation-domain";
import {
  createBackgroundAgentsDomainState,
  reduceBackgroundAgentOpenItems,
  reduceBackgroundAgentUpsert,
  reduceBackgroundAgentsForSession,
  reduceBackgroundAgentsRefreshFailed,
  reduceBackgroundAgentsRefreshStarted,
} from "@/stores/background-agent-domain";

test("cached-preview cannot enable SSE rendering", () => {
  const state = reduceDomainState(createDomainState(), {
    type: "session.cachedPreview",
    sessionId: "session-1",
  });

  const next = reduceDomainState(state, {
    type: "chatStream.liveTailRequested",
    sessionId: "session-1",
  });

  expect(next.sessionView.historyPhase).toBe("cached-preview");
  expect(next.chatStream.sseRenderingEnabled).toBe(false);
});

test("loading-history blocks replayed live tail", () => {
  const state = reduceDomainState(createDomainState(), {
    type: "session.loadingHistory",
    sessionId: "session-1",
  });

  const next = reduceDomainState(state, {
    type: "chatStream.liveTailRequested",
    sessionId: "session-1",
    replayed: true,
  });

  expect(next.sessionView.historyPhase).toBe("loading-history");
  expect(next.chatStream.replayedTailBlocked).toBe(true);
  expect(next.chatStream.sseRenderingEnabled).toBe(false);
});

test("loading-history requires canonical history", () => {
  const state = reduceDomainState(createDomainState(), {
    type: "session.loadingHistory",
    sessionId: "session-1",
  });

  expect(state.sessionView.historyPhase).toBe("loading-history");
  expect(state.sessionView.canonicalHistoryRequired).toBe(true);
});

test("live-tail starts only after server history is loaded", () => {
  const initial = createDomainState();

  const beforeHistory = reduceDomainState(initial, {
    type: "chatStream.liveTailRequested",
    sessionId: "session-1",
  });
  expect(beforeHistory.sessionView.historyPhase).toBe("idle");
  expect(beforeHistory.chatStream.sseRenderingEnabled).toBe(false);

  const loaded = reduceDomainState(beforeHistory, {
    type: "session.serverHistoryLoaded",
    sessionId: "session-1",
  });
  const live = reduceDomainState(loaded, {
    type: "chatStream.liveTailRequested",
    sessionId: "session-1",
  });

  expect(live.sessionView.historyPhase).toBe("live-tail");
  expect(live.chatStream.sseRenderingEnabled).toBe(true);
});

test("replay-gap forces canonical history reload", () => {
  const loaded = reduceDomainState(createDomainState(), {
    type: "session.serverHistoryLoaded",
    sessionId: "session-1",
  });
  const live = reduceDomainState(loaded, {
    type: "chatStream.liveTailRequested",
    sessionId: "session-1",
  });

  const gap = reduceDomainState(live, {
    type: "chatStream.replayGapDetected",
    sessionId: "session-1",
  });

  expect(gap.sessionView.historyPhase).toBe("replay-gap");
  expect(gap.sessionView.canonicalHistoryRequired).toBe(true);
  expect(gap.chatStream.sseRenderingEnabled).toBe(false);
});

test("terminal run states clear active running state", () => {
  const terminalPhases: RunPhase[] = ["completed", "failed", "cancelled"];

  for (const phase of terminalPhases) {
    const running = reduceDomainState(createDomainState(), {
      type: "run.started",
      runId: `run-${phase}`,
      sessionId: "session-1",
    });

    const terminal = reduceDomainState(running, {
      type: "run.terminal",
      runId: `run-${phase}`,
      phase,
    });

    expect(terminal.runLifecycle.phase).toBe(phase);
    expect(terminal.runLifecycle.activeRunId).toBeNull();
    expect(terminal.runLifecycle.activeSessionId).toBeNull();
  }
});

test("automation domain areas stream phase and per-task status", () => {
  const connecting = reduceAutomationStreamConnecting(
    createAutomationStreamDomainState(),
    1,
  );
  expect(connecting.phase).toBe("connecting");

  const connected = reduceAutomationStreamConnected(connecting, 2);
  const progressed = reduceAutomationProgress(connected, "task-1", "starting...", 3);
  expect(progressed.phase).toBe("connected");
  expect(progressed.statuses["task-1"]).toBe("starting...");

  const stale = reduceAutomationStreamStale(progressed, 4);
  expect(stale.phase).toBe("stale");
  expect(reduceAutomationStreamConnecting(stale, 5).phase).toBe("reconnecting");

  const failed = reduceAutomationStreamFailed(stale, "boom", 6);
  expect(failed.phase).toBe("failed");
  expect(failed.error).toBe("boom");

  const finished = reduceAutomationFinished(failed, "task-1", 7);
  expect(finished.statuses["task-1"]).toBeUndefined();
});

test("background agent domain upserts rows and preserves missing running snapshots", () => {
  const initial = reduceBackgroundAgentUpsert(
    createBackgroundAgentsDomainState(),
    {
      taskId: "bg-1",
      sessionId: "session-1",
      command: "research",
      status: "running",
      updatedAt: 1,
    },
    1,
  );

  const refreshed = reduceBackgroundAgentsForSession(initial, "session-1", [], 2);
  expect(refreshed.rows["session-1:bg-1"].status).toBe("running");
  expect(refreshed.refreshStatus).toBe("ready");

  const completed = reduceBackgroundAgentsForSession(
    refreshed,
    "session-1",
    [
      {
        taskId: "bg-1",
        command: "research",
        status: "completed",
        detail: "done",
      },
    ],
    3,
  );

  expect(completed.rows["session-1:bg-1"]).toMatchObject({
    status: "completed",
    detail: "done",
    updatedAt: 3,
  });
});

test("background agent domain keeps child-agent metadata from snapshots", () => {
  const state = reduceBackgroundAgentsForSession(
    createBackgroundAgentsDomainState(),
    "session-1",
    [
      {
        taskId: "child-run-1",
        command: "research auth flow",
        status: "running",
        detail: "working",
        agentType: "research",
        wait: false,
        parentToolCallId: "tool-call-1",
      },
    ],
    1,
  );

  expect(state.rows["session-1:child-run-1"]).toMatchObject({
    agentType: "research",
    wait: false,
    parentToolCallId: "tool-call-1",
  });
});

test("background agent domain tracks refresh status", () => {
  const refreshing = reduceBackgroundAgentsRefreshStarted(
    createBackgroundAgentsDomainState(),
  );
  expect(refreshing.refreshStatus).toBe("refreshing");

  const failed = reduceBackgroundAgentsRefreshFailed(refreshing, "offline");
  expect(failed.refreshStatus).toBe("failed");
  expect(failed.refreshError).toBe("offline");
});

test("background agent domain preserves unchanged snapshot row identity", () => {
  const initial = reduceBackgroundAgentUpsert(
    createBackgroundAgentsDomainState(),
    {
      taskId: "bg-1",
      sessionId: "session-1",
      command: "research",
      status: "running",
      detail: "working",
      resultRef: "ref-1",
      updatedAt: 1,
    },
    1,
  );

  const ready = reduceBackgroundAgentsForSession(
    initial,
    "session-1",
    [
      {
        taskId: "bg-1",
        command: "research",
        status: "running",
        detail: "working",
        resultRef: "ref-1",
      },
    ],
    2,
  );

  expect(ready.rows["session-1:bg-1"]).toBe(initial.rows["session-1:bg-1"]);
  expect(ready.rows["session-1:bg-1"].updatedAt).toBe(1);
  expect(ready.refreshStatus).toBe("ready");

  const unchangedReady = reduceBackgroundAgentsForSession(
    ready,
    "session-1",
    [
      {
        taskId: "bg-1",
        command: "research",
        status: "running",
        detail: "working",
        resultRef: "ref-1",
      },
    ],
    3,
  );

  expect(unchangedReady).toBe(ready);
});

test("background agent open item reducer clones caller Set", () => {
  const openItemIds = new Set(["session-1:bg-1"]);
  const state = reduceBackgroundAgentOpenItems(
    createBackgroundAgentsDomainState(),
    openItemIds,
  );

  openItemIds.add("session-1:bg-2");

  expect(state.openItemIds.has("session-1:bg-1")).toBe(true);
  expect(state.openItemIds.has("session-1:bg-2")).toBe(false);
});
