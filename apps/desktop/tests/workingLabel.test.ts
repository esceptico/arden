import { expect, test } from "bun:test";
import { workingLabel } from "@/features/chat/lib/workingLabel";
import type { ActivityItem, UiMessage } from "@/stores";

const message = (items: ActivityItem[]): UiMessage => ({
  id: "m1",
  role: "assistant",
  content: "",
  activity: { items, label: "activity", done: false } as UiMessage["activity"],
});

const call = (over: Partial<ActivityItem>): ActivityItem => ({
  id: "t1",
  kind: "read_file",
  target: "wiki/pages/areas.md",
  ...over,
});

test("no activity yet reads as the generic label", () => {
  expect(workingLabel(null)).toEqual({ verb: "Working", target: "" });
  expect(workingLabel(message([]))).toEqual({ verb: "Working", target: "" });
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
  expect(label).toEqual({ verb: "Working", target: "" });
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

test("with only nested calls running, the strip stays generic", () => {
  expect(workingLabel(message([
    call({ status: "ongoing", depth: 2, displayTitle: "Reading areas.md" }),
  ]))).toEqual({ verb: "Working", target: "" });
});
