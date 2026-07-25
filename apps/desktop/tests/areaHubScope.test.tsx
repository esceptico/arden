import { expect, test } from "bun:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import {
  areaHubApprovals,
  areaHubSources,
  areaHubSessionIds,
} from "@/features/background-agents/components/AgentRightSidebar";
import { ApprovalsRow } from "@/features/background-agents/components/ApprovalsRow";

const areaScope = {
  sessions: [
    { session_id: "area-session-a", name: "Research" },
    { session_id: "area-session-b", name: "Draft" },
  ],
  automations: [],
  work: {
    outcomes: [],
    work_items: [],
    events: [
      {
        event_id: 1,
        area_id: "area",
        outcome_id: null,
        item_id: null,
        run_ref: null,
        event_type: "evidence",
        summary: "Saved launch interviews",
        source_refs: ["memory:launch-interviews", "drive:retention-cohorts"],
        created_at: "2026-07-23T10:00:00Z",
      },
      {
        event_id: 2,
        area_id: "area",
        outcome_id: null,
        item_id: null,
        run_ref: null,
        event_type: "evidence",
        summary: "Reused launch interviews",
        source_refs: ["memory:launch-interviews"],
        created_at: "2026-07-23T09:00:00Z",
      },
    ],
  },
};

test("Area Hub derives its session boundary from AreaDetail", () => {
  expect(areaHubSessionIds(areaScope)).toEqual(["area-session-a", "area-session-b"]);
  expect(areaHubSessionIds(null)).toEqual([]);
});

test("Area Hub never projects an approval from an unrelated current chat", () => {
  const approvals = [{ toolId: "tool-1", toolName: "write_file" }];

  expect(areaHubApprovals(areaScope, "area-session-b", approvals)).toEqual(approvals);
  expect(areaHubApprovals(areaScope, "outside-area", approvals)).toEqual([]);
  expect(areaHubApprovals(null, "area-session-a", approvals)).toEqual([]);
});

test("Area Hub renders only retained Area source refs, newest provenance first", () => {
  expect(areaHubSources(areaScope)).toEqual([
    expect.objectContaining({ ref: "memory:launch-interviews", event: expect.objectContaining({ summary: "Saved launch interviews" }) }),
    expect.objectContaining({ ref: "drive:retention-cohorts", event: expect.objectContaining({ summary: "Saved launch interviews" }) }),
  ]);
  expect(areaHubSources(null)).toEqual([]);
});

test("Area Hub renders complete actionable approval cards", async () => {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => {
      root.render(
        <ApprovalsRow
          variant="hub"
          approvals={[
            {
              toolId: "tool-write",
              toolName: "Update launch brief",
              preview: "Writes one file. Review the exact diff.",
              status: "pending",
            },
            {
              toolId: "tool-publish",
              toolName: "Publish evidence brief",
              status: "pending",
            },
          ]}
        />,
      );
    });

    expect(host.querySelectorAll(".board-hub-approval-card")).toHaveLength(2);
    expect(host.textContent).toContain("Awaiting approval");
    expect(host.textContent).toContain("Deny");
    expect(host.textContent).toContain("Review");
  } finally {
    await act(async () => root.unmount());
    host.remove();
  }
});
