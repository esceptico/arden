import { expect, test } from "bun:test";
import { primaryActionFor } from "@/lib/askActions";
import type { AreaAsk } from "@/api/areas";
import type { Automation } from "@/api/types";

function ask(verb: string, ref: string): AreaAsk {
  return {
    id: "ask1",
    area_key: "o-1a",
    text: "some ask",
    kind: "review",
    source: "test",
    actions: [{ verb, ref }],
    state: "active",
    created_at: "2026-07-06T00:00:00Z",
    snoozed_until: null,
  };
}

function automation(taskId: string, name: string): Automation {
  return {
    task_id: taskId,
    name,
    description: "",
    model: null,
    triggers: [],
    enabled: true,
    created_at: "2026-07-06T00:00:00Z",
    last_run_at: null,
    next_run_at: null,
  };
}

const handlers = {
  switchSession: () => {},
  runAutomation: () => {},
  openArea: () => {},
};

test("open_session maps to switchSession with Open label", () => {
  let called: string | null = null;
  const action = primaryActionFor(ask("open_session", "sess-1"), null, {
    ...handlers,
    switchSession: (id) => {
      called = id;
    },
  });
  expect(action?.label).toBe("Open");
  action?.run();
  expect(called).toBe("sess-1");
});

test("retry resolves automation name to task_id via Retry label", () => {
  let called: string | null = null;
  const automations = [automation("t1", "morning-digest")];
  const action = primaryActionFor(ask("retry", "morning-digest"), automations, {
    ...handlers,
    runAutomation: (taskId) => {
      called = taskId;
    },
  });
  expect(action?.label).toBe("Retry");
  action?.run();
  expect(called).toBe("t1");
});

test("retry with unresolvable automation name returns null", () => {
  const automations = [automation("t1", "morning-digest")];
  const action = primaryActionFor(ask("retry", "unknown-automation"), automations, handlers);
  expect(action).toBeNull();
});

test("open_page maps to openArea with Review label", () => {
  let called: string | null = null;
  const action = primaryActionFor(ask("open_page", "/some/page"), null, {
    ...handlers,
    openArea: (key) => {
      called = key;
    },
  });
  expect(action?.label).toBe("Review");
  action?.run();
  expect(called).toBe("o-1a");
});

test("unknown verb returns null", () => {
  const action = primaryActionFor(ask("frobnicate", "whatever"), null, handlers);
  expect(action).toBeNull();
});
