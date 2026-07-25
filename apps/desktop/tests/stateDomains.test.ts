import { expect, test } from "bun:test";
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
  reduceBackgroundAgentUpsert,
  reduceBackgroundAgentsForSession,
  reduceBackgroundAgentsRefreshFailed,
  reduceBackgroundAgentsRefreshStarted,
} from "@/stores/background-agent-domain";

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
