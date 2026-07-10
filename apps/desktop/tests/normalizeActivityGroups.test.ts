import { expect, test } from "bun:test";
import { normalizeActivityGroups } from "@/stores/session-cache";
import type { ActivityItem, UiMessage } from "@/stores/types";

function activityItem(id: string): ActivityItem {
  return { id, kind: "bash", target: id, status: "executed" };
}

function activity(id: string, itemIds: string[]): UiMessage {
  return {
    id,
    role: "activity",
    content: "",
    activity: { items: itemIds.map(activityItem), label: "Called", done: true },
  };
}

function user(id: string): UiMessage {
  return { id, role: "user", content: "hi" };
}

function metaUser(id: string): UiMessage {
  return { id, role: "user", content: "hidden", isMeta: true };
}

// Regression: during an active run the projection could list one activity id
// twice in `order` (a stale cached copy beside the freshly projected one),
// separated by a visible boundary so the positional collapse missed it. That
// surfaced two children with the same React key. The id must appear once and
// the group's items must not be doubled.
test("normalizeActivityGroups collapses a duplicated activity id into one render key", () => {
  const messages = new Map<string, UiMessage>([
    ["msg-x-activity", activity("msg-x-activity", ["t1", "t2"])],
    ["user-1", user("user-1")],
  ]);
  const order = ["msg-x-activity", "user-1", "msg-x-activity"];

  const out = normalizeActivityGroups(messages, order, "msg-x-activity");

  expect(out.order.filter((id) => id === "msg-x-activity")).toHaveLength(1);
  expect(new Set(out.order).size).toBe(out.order.length);
  expect(out.messages.get("msg-x-activity")?.activity?.items.map((i) => i.id)).toEqual([
    "t1",
    "t2",
  ]);
});

// Distinct, consecutive activity groups still collapse into one (existing
// behavior — a run's tool calls span several assistant messages).
test("normalizeActivityGroups still merges distinct consecutive activities", () => {
  const messages = new Map<string, UiMessage>([
    ["a1", activity("a1", ["t1"])],
    ["a2", activity("a2", ["t2"])],
  ]);
  const order = ["a1", "a2"];

  const out = normalizeActivityGroups(messages, order, "a1");

  expect(out.order).toEqual(["a1"]);
  expect(out.messages.get("a1")?.activity?.items.map((i) => i.id)).toEqual(["t1", "t2"]);
  expect(out.activeActivityId).toBe("a1");
});

test("normalizeActivityGroups keeps activities separate across a hidden meta boundary", () => {
  const messages = new Map<string, UiMessage>([
    ["a1", activity("a1", ["t1"])],
    ["meta-1", metaUser("meta-1")],
    ["a2", activity("a2", ["t2"])],
  ]);

  const out = normalizeActivityGroups(messages, ["a1", "meta-1", "a2"], "a2");

  expect(out.order).toEqual(["a1", "meta-1", "a2"]);
  expect(out.messages.get("a1")?.activity?.items.map((item) => item.id)).toEqual(["t1"]);
  expect(out.messages.get("a2")?.activity?.items.map((item) => item.id)).toEqual(["t2"]);
});

// Regression: a live-built activity (uuid id) and a history rebuild of the SAME
// turn (`msg-…-activity` id) can sit adjacent after a cached-history refresh.
// Their items share tool-call ids — merging must dedupe by item id, not concat
// (the concat rendered every row twice and the workflow card twice).
test("normalizeActivityGroups dedupes same-id items when merging two builds of one turn", () => {
  const live: UiMessage = {
    id: "uuid-live",
    role: "activity",
    content: "",
    activity: {
      label: "Calling",
      done: false,
      items: [
        { id: "call_lt1", kind: "load_tools", target: 'Load Tools(group="slack")', status: "ongoing" },
        { id: "call_wf", kind: "workflow", semanticKind: "workflow", target: "workflow(...)", status: "ongoing" },
      ],
    },
  };
  const history: UiMessage = {
    id: "msg-1-activity",
    role: "activity",
    content: "",
    activity: {
      label: "Called",
      done: true,
      items: [
        { id: "call_lt1", kind: "load_tools", target: 'Load Tools(group="slack")', status: "executed", result: "ok" },
        { id: "call_wf", kind: "workflow", semanticKind: "workflow", target: "workflow(...)", status: "executed" },
        { id: "call_new", kind: "read_file", target: "read_file", status: "executed" },
      ],
    },
  };
  const messages = new Map<string, UiMessage>([
    ["uuid-live", live],
    ["msg-1-activity", history],
  ]);

  const out = normalizeActivityGroups(messages, ["uuid-live", "msg-1-activity"], "uuid-live");

  const items = out.messages.get("uuid-live")?.activity?.items ?? [];
  expect(items.map((i) => i.id)).toEqual(["call_lt1", "call_wf", "call_new"]);
  // First occurrence (live) wins on conflicts; the duplicate only fills gaps.
  expect(items[0].status).toBe("ongoing");
  expect(items[0].result).toBe("ok");
  expect(items[1].semanticKind).toBe("workflow");
});
