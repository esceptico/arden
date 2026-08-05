import { expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import type { AreaAgentStatus, AreaOutcome, AreaWorkItem } from "@/api/areas";
import { AreaManagement } from "@/features/areas/components/AreaManagement";

const outcome: AreaOutcome = {
  outcome_id: "outcome-1",
  area_id: "area-1",
  stable_key: "filed",
  title: "Petition filed",
  success_criteria: "Receipt exists",
  status: "active",
  priority: 5,
  source: "inferred",
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
  completed_at: null,
};

function work(
  item_id: string,
  status: AreaWorkItem["status"],
  overrides: Partial<AreaWorkItem> = {},
): AreaWorkItem {
  return {
    item_id,
    area_id: "area-1",
    stable_key: item_id,
    outcome_id: outcome.outcome_id,
    kind: "action",
    text: `Work ${item_id}`,
    status,
    owner: "custodian",
    due_at: null,
    next_attempt_at: null,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    completed_at: null,
    ...overrides,
  };
}

const agent: AreaAgentStatus = {
  last_checked: "2026-08-01T00:00:00Z",
  next_check: "2099-08-10T00:00:00Z",
  next_check_reason: "Counsel response is due.",
  last_report: "Filed the final supporting exhibits.",
  woken_by: [],
  runs_today: 1,
  runs_cap: 8,
  running_since: null,
  availability: "ready",
  last_error: null,
};

test("Area management renders report, next check, and only live typed work", () => {
  const html = renderToStaticMarkup(
    <AreaManagement
      agent={agent}
      paused={false}
      outcomes={[outcome]}
      workItems={[
        work("doing", "in_progress", { owner: "user" }),
        work("blocked", "active", { kind: "blocker", outcome_id: null, next_attempt_at: "not-a-date" }),
        work("done", "completed"),
        work("cancelled", "cancelled"),
      ]}
    />,
  );

  expect(html).toContain("Filed the final supporting exhibits.");
  expect(html).toContain("Counsel response is due.");
  expect(html).toContain("Work doing");
  expect(html).toContain("In progress");
  expect(html).toContain("You");
  expect(html).toContain("Petition filed");
  expect(html).toContain("Work blocked");
  expect(html).toContain("Blocked");
  expect(html).toContain("Unfiled");
  expect(html).not.toContain("Work done");
  expect(html).not.toContain("Work cancelled");
  expect(html).not.toContain("NaN");
});

test("Area management states empty and paused conditions without implying activity", () => {
  const empty = renderToStaticMarkup(
    <AreaManagement agent={null} paused={false} outcomes={[]} workItems={[]} />,
  );
  expect(empty).toContain("No report yet.");
  expect(empty).toContain("This Area has no custodian.");
  expect(empty).toContain("No open work.");

  const paused = renderToStaticMarkup(
    <AreaManagement agent={{ ...agent, next_check: "not-a-date" }} paused outcomes={[]} workItems={[]} />,
  );
  expect(paused).toContain("Paused");
  expect(paused).toContain("Resume to schedule the next check.");
  expect(paused).not.toContain("NaN");
});

test("Area management surfaces a bounded latest-check failure", () => {
  const failed = renderToStaticMarkup(
    <AreaManagement
      agent={{ ...agent, availability: "error", last_error: `provider unavailable\n${"x".repeat(400)}` }}
      paused={false}
      outcomes={[]}
      workItems={[]}
    />,
  );

  expect(failed).toContain("Failed");
  expect(failed).toContain("provider unavailable");
  expect(failed).not.toContain("x".repeat(20));
});
