import { expect, test } from "bun:test";
import {
  foregroundRunLabel,
  selectForegroundApprovalCount,
  selectForegroundConnectionLabel,
} from "@/features/chat/lib/foregroundRunStatus";
import { workingLabel } from "@/features/chat/lib/workingLabel";
import type { ActivityItem, State, UiMessage } from "@/stores";

const message = (items: ActivityItem[]): UiMessage => ({
  id: "m1",
  role: "assistant",
  content: "",
  activity: { items, label: "activity", done: false } as UiMessage["activity"],
});

const call = (over: Partial<ActivityItem>): ActivityItem => ({
  id: "t1",
  kind: "file_read",
  target: "wiki/pages/areas.md",
  ...over,
});

test("no activity yet reports the model phase", () => {
  expect(workingLabel(null)).toEqual({ verb: "Thinking", target: "" });
  expect(workingLabel(message([]))).toEqual({ verb: "Thinking", target: "" });
});

test("an ongoing call is named by the model's own display title", () => {
  const label = workingLabel(message([
    call({ status: "ongoing", displayTitle: "Reading areas.md" }),
  ]));
  expect(label).toEqual({ verb: "Reading", target: "areas.md" });
});

test("a finished call is never reported as current", () => {
  // Between two tools the agent is thinking. Naming the read that already
  // completed would state something untrue, so the strip falls back.
  const label = workingLabel(message([
    call({ id: "a", status: "executed", displayTitle: "Reading areas.md", result: "ok" }),
  ]));
  expect(label).toEqual({ verb: "Thinking", target: "" });
});

test("the newest ongoing call wins over an earlier one", () => {
  const label = workingLabel(message([
    call({ id: "a", status: "ongoing", displayTitle: "Reading areas.md" }),
    call({ id: "b", status: "ongoing", displayTitle: "Editing chat.css" }),
  ]));
  expect(label).toEqual({ verb: "Editing", target: "chat.css" });
});

test("without a display title it falls back to the noun, then the tool name", () => {
  expect(workingLabel(message([
    call({ status: "ongoing", noun: "Search", target: "memory" }),
  ]))).toEqual({ verb: "Search", target: "memory" });

  // displayTitle and noun are both absent on history reload and for
  // uncategorized tools, so the bare kind is a reachable case.
  expect(workingLabel(message([
    call({ status: "ongoing", kind: "bash", target: "bun test" }),
  ]))).toEqual({ verb: "bash", target: "bun test" });
});

test("a target that restates the tool name renders once", () => {
  // Uncategorized tools (no display title, no noun) project their target as
  // the display name again — bare while args stream, with args once parsed.
  // Either way the strip must not stutter "ReadSession ReadSession".
  expect(workingLabel(message([
    call({ status: "ongoing", kind: "session_read", displayName: "ReadSession", target: "ReadSession" }),
  ]))).toEqual({ verb: "ReadSession", target: "" });

  expect(workingLabel(message([
    call({
      status: "ongoing",
      kind: "session_read",
      displayName: "ReadSession",
      target: 'ReadSession(session_id="abc")',
    }),
  ]))).toEqual({ verb: "ReadSession", target: "" });
});

test("status is inferred when the server omitted it", () => {
  // activityItemStatus treats a result-less call as still running.
  expect(workingLabel(message([
    call({ displayTitle: "Running tests" }),
  ]))).toEqual({ verb: "Running", target: "tests" });
});

test("a workflow's nested calls never reach the strip", () => {
  // A workflow runs its own agents' tools at depth >= 1. Surfacing those puts
  // a private step from inside the workflow on the composer instead of the
  // workflow itself; the trace panel is where that detail belongs.
  const label = workingLabel(message([
    call({ id: "wf", status: "ongoing", displayTitle: "Inspect updated automation harness" }),
    call({ id: "nested", status: "ongoing", depth: 1, displayTitle: "Tracing harness architecture" }),
  ]));
  expect(label).toEqual({ verb: "Inspect", target: "updated automation harness" });
});

test("with only nested calls running, the strip reports the parent model phase", () => {
  expect(workingLabel(message([
    call({ status: "ongoing", depth: 2, displayTitle: "Reading areas.md" }),
  ]))).toEqual({ verb: "Thinking", target: "" });
});

test("actionable waits override tool and model phases", () => {
  const activityMessage = message([
    call({ status: "ongoing", displayTitle: "Editing chat.css" }),
  ]);

  expect(foregroundRunLabel({ activityMessage, approvalPending: true })).toEqual({
    verb: "Awaiting",
    target: "approval",
  });
  expect(foregroundRunLabel({ activityMessage, connectionLabel: "Google Calendar" })).toEqual({
    verb: "Connect",
    target: "Google Calendar",
  });
});

test("wait phases belong only to the active run", () => {
  const state = {
    running: true,
    currentRunId: "run-new",
    currentSessionId: "session-1",
    pendingApprovals: [
      { toolId: "old", toolName: "write", status: "pending", runId: "run-old", sessionId: "session-1" },
      { toolId: "new", toolName: "send", status: "pending", runId: "run-new", sessionId: "session-1" },
      { toolId: "other", toolName: "edit", status: "pending", runId: "run-new", sessionId: "session-2" },
      { toolId: "legacy", toolName: "edit", status: "pending", sessionId: "session-1" },
    ],
    pendingConnections: [
      { runId: "run-old", label: "Old service" },
      { runId: "run-new", label: "Current service" },
    ],
  } as State;

  expect(selectForegroundApprovalCount(state)).toBe(1);
  expect(selectForegroundConnectionLabel(state)).toBe("Current service");

  expect(selectForegroundApprovalCount({ ...state, currentRunId: null })).toBe(0);
});
