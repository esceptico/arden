import { expect, test } from "bun:test";
import type { Automation } from "@/api/types";
import { isIterationLoop } from "@/lib/automationFilters";
import { groupAutomations } from "@/features/automations/components/AutomationRail";

function automation(patch: Partial<Automation>): Automation {
  return {
    task_id: "task",
    name: "Automation",
    description: "",
    model: null,
    triggers: [],
    enabled: true,
    created_at: "2026-05-07T00:00:00Z",
    last_run_at: null,
    next_run_at: null,
    last_result: null,
    auto_approve: false,
    running_since: null,
    handler: null,
    builtin: false,
    cooldown_minutes: null,
    ...patch,
  };
}

test("keeps user automations separate from internal automations", () => {
  const user = automation({ task_id: "user", name: "Daily brief" });
  const internal = automation({
    task_id: "knowledge-retention",
    name: "Knowledge Retention",
    builtin: true,
  });
  const groups = groupAutomations([internal, user]);
  expect(groups.user).toEqual([user]);
  expect(groups.system).toEqual([internal]);
  expect(groups.area).toEqual([]);
});

test("treats known knowledge handlers as internal even before builtin metadata is set", () => {
  const health = automation({
    task_id: "knowledge-health",
    handler: "knowledge_health",
  });
  expect(groupAutomations([health]).system).toEqual([health]);
});

test("splits seeded area agents from the user's own automations", () => {
  const custodian = automation({ task_id: "area:arden:custodian", name: "arden custodian" });
  const own = automation({ task_id: "own", name: "Daily brief" });
  const groups = groupAutomations([custodian, own]);
  expect(groups.user).toEqual([own]);
  expect(groups.area).toEqual([custodian]);
});

test("keeps post-mode channel automations in the user group", () => {
  const channel = automation({
    task_id: "channel",
    kind: "loop",
    read_history: false,
  });
  expect(groupAutomations([channel]).user).toEqual([channel]);
});

test("iteration loops are identified for the modal to drop", () => {
  const iteration = automation({
    task_id: "loop",
    kind: "loop",
    read_history: true,
  });
  expect(isIterationLoop(iteration)).toBe(true);
  const channel = automation({ task_id: "c", kind: "loop", read_history: false });
  expect(isIterationLoop(channel)).toBe(false);
});
