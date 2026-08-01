import { describe, expect, it } from "bun:test";
import {
  agentRunFromActivityItem,
  agentRunStatusLabel,
  childAgentTaskToBackgroundSnapshot,
  agentRunFromAutomation,
  agentRunFromBackgroundAgent,
  humanizeAgentType,
  isActiveAgentStatus,
  isAgentSessionId,
  parentSessionIdOf,
  resolveAutomationStatus,
  resultSnippet,
} from "@/lib/agentRun";
import type { Automation } from "@/api/types";
import type { ActivityItem, BackgroundAgent } from "@/stores/types";

function automation(overrides: Partial<Automation> = {}): Automation {
  return {
    task_id: "auto-1",
    name: "Morning briefing",
    description: "Check recent emails",
    triggers: [{ type: "time", at: "10:00", days: "daily" }],
    enabled: true,
    last_status: null,
    recent_statuses: [],
    last_run_at: null,
    next_run_at: null,
    running_since: null,
    builtin: false,
    ...overrides,
  } as Automation;
}

describe("agentRunFromAutomation", () => {
  it("a running automation has NO progress line — the pulsing dot + 'running' badge convey it, so there is no duplicate 'running'", () => {
    const run = agentRunFromAutomation(automation({ running_since: new Date().toISOString() }));
    expect(run.status).toBe("running");
    expect(run.progress).toBeUndefined();
  });

  it("never sets childSessionId (an automation run is not an openable session)", () => {
    expect(agentRunFromAutomation(automation()).childSessionId).toBeUndefined();
  });

  it("carries the enabled facet so a paused automation can read muted, not green", () => {
    expect(agentRunFromAutomation(automation({ enabled: false })).enabled).toBe(false);
    expect(agentRunFromAutomation(automation({ enabled: true })).enabled).toBe(true);
  });

  it("maps schedule + result preview for a finished run", () => {
    const run = agentRunFromAutomation(automation({ last_status: "completed", last_result: "Sent 3 nudges." }));
    expect(run.status).toBe("completed");
    expect(run.schedule).toContain("at 10:00");
    expect(run.resultPreview).toBe("Sent 3 nudges.");
  });
});

describe("resolveAutomationStatus", () => {
  it("running beats last_status; failed/completed map through; never-run is idle", () => {
    expect(resolveAutomationStatus(automation({ running_since: "now", last_status: "completed" }))).toBe("running");
    expect(resolveAutomationStatus(automation({ last_status: "failed" }))).toBe("failed");
    expect(resolveAutomationStatus(automation({ last_status: "completed" }))).toBe("completed");
    expect(resolveAutomationStatus(automation({ last_status: null }))).toBe("interrupted");
  });
});

describe("humanizeAgentType", () => {
  it("maps known types", () => {
    expect(humanizeAgentType("background_research")).toBe("Research");
    expect(humanizeAgentType("research")).toBe("Research");
  });

  it("titlecases and strips the agent suffix", () => {
    expect(humanizeAgentType("code_review_agent")).toBe("Code review");
  });

  it("falls back to Agent (incl. the generic sub_agent type)", () => {
    expect(humanizeAgentType(undefined)).toBe("Agent");
    expect(humanizeAgentType("agent")).toBe("Agent");
    expect(humanizeAgentType("sub_agent")).toBe("Agent");
    expect(humanizeAgentType("sub-agent")).toBe("Agent");
  });
});

describe("resultSnippet", () => {
  it("returns undefined for empty input", () => {
    expect(resultSnippet(undefined)).toBeUndefined();
    expect(resultSnippet(null)).toBeUndefined();
    expect(resultSnippet("")).toBeUndefined();
    expect(resultSnippet("   \n  \n")).toBeUndefined();
  });

  it("takes the first non-empty line and strips markdown chrome", () => {
    expect(resultSnippet("# Heading")).toBe("Heading");
    expect(resultSnippet("- bullet")).toBe("bullet");
    expect(resultSnippet("> quote")).toBe("quote");
    expect(resultSnippet("1. numbered")).toBe("numbered");
    expect(resultSnippet("**bold** text")).toBe("bold text");
  });

  it("skips blank lines and entire fenced blocks, not just their markers", () => {
    expect(resultSnippet("\n```ts\ncode line\n```\nReal summary")).toBe("Real summary");
    // An area agent's ask nomination: prose first, fenced json tail — the
    // json must never become the one-line preview.
    expect(resultSnippet('```json\n{"ask": {"text": "hi"}}\n```\nQuiet day.')).toBe("Quiet day.");
    expect(resultSnippet('```json\n{"ask": {"text": "hi"}}\n```')).toBeUndefined();
  });

  it("truncates to max with an ellipsis", () => {
    const long = "x".repeat(200);
    const out = resultSnippet(long, 10);
    expect(out).toBe(`${"x".repeat(9)}…`);
    expect(out).toHaveLength(10);
  });
});

describe("parentSessionIdOf / isAgentSessionId", () => {
  it("recovers the immediate parent from a child session id (incl. nesting)", () => {
    expect(parentSessionIdOf("abc::d0021162")).toBe("abc");
    // Nested agents stack suffixes — return the IMMEDIATE parent, not the root.
    expect(parentSessionIdOf("root::a1b2::c3d4")).toBe("root::a1b2");
    expect(parentSessionIdOf("plain")).toBeNull();
    expect(parentSessionIdOf(null)).toBeNull();
    expect(parentSessionIdOf(undefined)).toBeNull();
  });

  it("detects agent session ids", () => {
    expect(isAgentSessionId("abc::d0021162")).toBe(true);
    expect(isAgentSessionId("plain")).toBe(false);
    expect(isAgentSessionId(null)).toBe(false);
    expect(isAgentSessionId(undefined)).toBe(false);
  });
});

describe("isActiveAgentStatus", () => {
  it("is true only for in-flight statuses", () => {
    expect(isActiveAgentStatus("running")).toBe(true);
    expect(isActiveAgentStatus("cancel_requested")).toBe(true);
    expect(isActiveAgentStatus("completed")).toBe(false);
    expect(isActiveAgentStatus("failed")).toBe(false);
    expect(isActiveAgentStatus("cancelled")).toBe(false);
    expect(isActiveAgentStatus("interrupted")).toBe(false);
  });
});

function backgroundAgent(overrides: Partial<BackgroundAgent> = {}): BackgroundAgent {
  return {
    taskId: "task-1",
    sessionId: "session-1",
    childSessionId: "session-1::d0021162",
    command: "Audit the auth flow",
    status: "running",
    agentType: "background_research",
    wait: false,
    createdAt: Date.now(),
    updatedAt: Date.now(),
    ...overrides,
  };
}

describe("agentRunFromBackgroundAgent", () => {
  it("maps fields and detaches awaited runs", () => {
    const view = agentRunFromBackgroundAgent(backgroundAgent());
    expect(view.key).toBe("task-1");
    expect(view.name).toBe("Audit the auth flow");
    expect(view.type).toBe("Research");
    expect(view.status).toBe("running");
    expect(view.childSessionId).toBe("session-1::d0021162");
    expect(view.runId).toBe("task-1");
    expect(view.detached).toBe(true);
  });

  it("treats wait !== false as awaited", () => {
    expect(agentRunFromBackgroundAgent(backgroundAgent({ wait: true })).detached).toBe(false);
  });

  it("hides the result preview while active and surfaces detail as progress", () => {
    const view = agentRunFromBackgroundAgent(
      backgroundAgent({ status: "running", detail: "reading files" }),
      "ignored preview",
    );
    expect(view.resultPreview).toBeUndefined();
    expect(view.progress).toBe("reading files");
  });

  it("prefers the passed preview for terminal runs", () => {
    const view = agentRunFromBackgroundAgent(
      backgroundAgent({ status: "completed", detail: "raw detail" }),
      "explicit preview",
    );
    expect(view.resultPreview).toBe("explicit preview");
    expect(view.progress).toBeUndefined();
  });

  it("falls back to a snippet of detail for terminal runs", () => {
    const view = agentRunFromBackgroundAgent(
      backgroundAgent({ status: "completed", detail: "# Final answer" }),
    );
    expect(view.resultPreview).toBe("Final answer");
  });

  it("falls back to the humanized type (never a raw id) when command is blank", () => {
    expect(agentRunFromBackgroundAgent(backgroundAgent({ command: "  " })).name).toBe("Research");
    expect(agentRunFromBackgroundAgent(backgroundAgent({ command: "  ", agentType: "sub_agent" })).name).toBe(
      "Agent",
    );
  });
});

function activityItem(overrides: Partial<ActivityItem> = {}): ActivityItem {
  return {
    id: "call-1",
    kind: "spawn_agent",
    semanticKind: "agent",
    target: "Research",
    args: JSON.stringify({ task: "Summarize the codebase" }),
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

describe("agentRunFromActivityItem", () => {
  it("uses the display name (not the verbose task) and humanizes the child agent type", () => {
    const view = agentRunFromActivityItem(activityItem({ displayName: "Codebase summary" }));
    expect(view.key).toBe("call-1");
    expect(view.name).toBe("Codebase summary");
    expect(view.name).not.toBe("Summarize the codebase");
    expect(view.type).toBe("Research");
    expect(view.status).toBe("running");
    expect(view.childSessionId).toBe("session-1::d0021162");
  });

  it("falls back to the task, then the target, when no display name is set", () => {
    expect(agentRunFromActivityItem(activityItem()).name).toBe("Summarize the codebase");
    expect(agentRunFromActivityItem(activityItem({ args: undefined })).name).toBe("Research");
  });

  it("hides the result preview while running", () => {
    const view = agentRunFromActivityItem(activityItem({ result: "# Done summary" }));
    expect(view.resultPreview).toBeUndefined();
  });

  it("surfaces a result snippet once an awaited agent is terminal", () => {
    const view = agentRunFromActivityItem(
      activityItem({
        taskStatus: "completed",
        result: "# Done summary",
        childAgent: {
          childRunId: "child-run-1",
          childSessionId: "session-1::d0021162",
          parentToolCallId: "call-1",
          agentType: "background_research",
          wait: true,
          status: "completed",
        },
      }),
    );
    expect(view.status).toBe("completed");
    expect(view.detached).toBe(false);
    expect(view.resultPreview).toBe("Done summary");
  });

  it("suppresses the inline result preview for a detached agent (result lives in the child session)", () => {
    const view = agentRunFromActivityItem(
      activityItem({ taskStatus: "completed", result: "# Done summary" }),
    );
    expect(view.detached).toBe(true);
    expect(view.resultPreview).toBeUndefined();
  });

  it("shows the fetched durable snippet for a terminal detached agent", () => {
    const view = agentRunFromActivityItem(
      activityItem({ taskStatus: "completed", result: "spawn ack" }),
      { resultSnippet: "Real durable answer" },
    );
    expect(view.resultPreview).toBe("Real durable answer");
  });

  it("a failed awaited agent's result text becomes the preview (never just the word failed)", () => {
    const view = agentRunFromActivityItem(
      activityItem({
        taskStatus: "failed",
        result: "# Salvage: the child run hit a tool error",
        childAgent: { ...activityItem().childAgent!, wait: true, status: "failed" },
      }),
    );
    expect(view.status).toBe("failed");
    expect(view.resultPreview).toBe("Salvage: the child run hit a tool error");
  });

  it("a failed detached agent shows the fetched failure snippet", () => {
    const view = agentRunFromActivityItem(
      activityItem({ taskStatus: "failed", result: "spawn ack" }),
      { resultSnippet: "Tool error: bash exited 1" },
    );
    expect(view.status).toBe("failed");
    expect(view.resultPreview).toBe("Tool error: bash exited 1");
  });

  it("settles an unknown child status as interrupted, never running", () => {
    const view = agentRunFromActivityItem(
      activityItem({
        taskStatus: undefined,
        childAgent: { ...activityItem().childAgent!, status: "vanished" },
      }),
    );
    expect(view.status).toBe("interrupted");
    expect(agentRunStatusLabel(view.status)).toBe("interrupted");
  });

  it("maps a taskStatus of interrupted through", () => {
    expect(agentRunFromActivityItem(activityItem({ taskStatus: "interrupted" })).status).toBe(
      "interrupted",
    );
  });

  it("derives running-ness from the roster row when the transcript has no terminal evidence", () => {
    // A detached spawn ack: item.result is set (reads "executed"), but the
    // durable roster row still says running — chat must agree with the sidebar.
    const item = activityItem({
      taskStatus: undefined,
      result: "spawn ack",
      childAgent: { ...activityItem().childAgent!, status: "running" },
    });
    expect(agentRunFromActivityItem(item).status).toBe("completed");
    const view = agentRunFromActivityItem(item, {
      roster: backgroundAgent({ status: "running" }),
    });
    expect(view.status).toBe("running");
    expect(view.resultPreview).toBeUndefined();
  });

  it("transcript terminal evidence outranks a stale running roster row", () => {
    const view = agentRunFromActivityItem(activityItem({ taskStatus: "completed" }), {
      roster: backgroundAgent({ status: "running" }),
    });
    expect(view.status).toBe("completed");
  });

  it("takes a terminal roster status immediately, ahead of the transcript patch", () => {
    const view = agentRunFromActivityItem(activityItem({ taskStatus: "running" }), {
      roster: backgroundAgent({ status: "failed" }),
    });
    expect(view.status).toBe("failed");
  });
});

describe("childAgentTaskToBackgroundSnapshot", () => {
  it("keeps live statuses running and settles unknown statuses as interrupted", () => {
    const base = { task_id: "t1", command: "research" };
    expect(childAgentTaskToBackgroundSnapshot({ ...base, status: "running" }).status).toBe("running");
    expect(childAgentTaskToBackgroundSnapshot({ ...base, status: "activity" }).status).toBe("running");
    expect(childAgentTaskToBackgroundSnapshot({ ...base, status: "interrupted" }).status).toBe(
      "interrupted",
    );
    expect(childAgentTaskToBackgroundSnapshot({ ...base, status: "weird" }).status).toBe(
      "interrupted",
    );
  });
});
