import { describe, expect, it } from "bun:test";
import { syncTranscriptAgentsFromRoster } from "@/stores/transcript-roster-sync";
import type { ActivityItem, BackgroundAgent, UiMessage } from "@/stores/types";

function agentItem(overrides: Partial<ActivityItem> = {}): ActivityItem {
  return {
    id: "call-1",
    kind: "spawn_agent",
    semanticKind: "agent",
    target: "Research",
    status: "ongoing",
    taskStatus: "running",
    childAgent: {
      childRunId: "child-run-1",
      childSessionId: "session-1::d0021162",
      parentToolCallId: "call-1",
      agentType: "background_research",
      wait: false,
      status: "running",
    },
    ...overrides,
  };
}

function activityMessage(items: ActivityItem[]): UiMessage {
  return {
    id: "activity-1",
    role: "activity",
    content: "",
    activity: { items, label: "Called", done: true },
  };
}

function rosterRow(overrides: Partial<BackgroundAgent> = {}): BackgroundAgent {
  return {
    taskId: "child-run-1",
    agentRef: "research-auth-flow~abc123",
    sessionId: "session-1",
    childSessionId: "session-1::d0021162",
    command: "Research",
    status: "completed",
    parentToolCallId: "call-1",
    wait: false,
    createdAt: 1,
    updatedAt: 2,
    ...overrides,
  };
}

describe("syncTranscriptAgentsFromRoster", () => {
  it("settles a still-running transcript item when its roster row is terminal", () => {
    const messages = new Map([["activity-1", activityMessage([agentItem()])]]);
    const next = syncTranscriptAgentsFromRoster(
      messages,
      { "session-1:child-run-1": rosterRow() },
      "session-1",
    );
    expect(next).not.toBeNull();
    const item = next?.get("activity-1")?.activity?.items[0];
    expect(item?.taskStatus).toBe("completed");
    expect(item?.status).toBe("executed");
    expect(item?.cancelRequested).toBe(false);
    // The input map is never mutated.
    expect(messages.get("activity-1")?.activity?.items[0].taskStatus).toBe("running");
  });

  it("matches persisted public metadata to a roster with unrelated internal IDs", () => {
    const publicItem = agentItem({
      id: "history-call",
      childAgent: {
        agentRef: "research-auth-flow~abc123",
        sessionRef: "research-auth-flow~abc123",
        agentType: "background_research",
        wait: false,
        status: "running",
      },
    });
    const messages = new Map([["activity-1", activityMessage([publicItem])]]);
    const next = syncTranscriptAgentsFromRoster(
      messages,
      {
        "session-1:internal-task": rosterRow({
          taskId: "internal-task",
          childSessionId: "session-1::internal",
          parentToolCallId: undefined,
        }),
      },
      "session-1",
    );

    expect(next?.get("activity-1")?.activity?.items[0].taskStatus).toBe("completed");
  });

  it("carries failed and interrupted through, and matches by parent tool call id alone", () => {
    const messages = new Map([
      ["activity-1", activityMessage([agentItem({ childAgent: undefined, runId: undefined })])],
    ]);
    const next = syncTranscriptAgentsFromRoster(
      messages,
      { "session-1:child-run-1": rosterRow({ status: "failed" }) },
      "session-1",
    );
    expect(next?.get("activity-1")?.activity?.items[0].taskStatus).toBe("failed");
  });

  it("is a no-op (null) when items are already settled, roster rows are still running, or the session differs", () => {
    const settled = new Map([["activity-1", activityMessage([agentItem({ taskStatus: "completed" })])]]);
    expect(
      syncTranscriptAgentsFromRoster(settled, { "session-1:child-run-1": rosterRow() }, "session-1"),
    ).toBeNull();

    const running = new Map([["activity-1", activityMessage([agentItem()])]]);
    expect(
      syncTranscriptAgentsFromRoster(
        running,
        { "session-1:child-run-1": rosterRow({ status: "running" }) },
        "session-1",
      ),
    ).toBeNull();
    expect(
      syncTranscriptAgentsFromRoster(
        running,
        { "session-1:child-run-1": rosterRow() },
        "other-session",
      ),
    ).toBeNull();
  });

  it("never regresses a transcript-settled terminal status to interrupted", () => {
    const messages = new Map([
      ["activity-1", activityMessage([agentItem({ taskStatus: "completed" })])],
    ]);
    expect(
      syncTranscriptAgentsFromRoster(
        messages,
        { "session-1:child-run-1": rosterRow({ status: "interrupted" }) },
        "session-1",
      ),
    ).toBeNull();
  });
});
