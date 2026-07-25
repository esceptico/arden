import { afterEach, describe, expect, test } from "bun:test";
import { applyAppDestination } from "@/actions/navigation";
import { getState, setState } from "@/stores";
import type { Automation } from "@/api/types";

const automation: Automation = {
  task_id: "email-digest",
  name: "Email digest",
  description: "Daily email summary",
  model: null,
  triggers: [],
  enabled: true,
  created_at: "2026-07-21T00:00:00Z",
  last_run_at: null,
  next_run_at: null,
  last_result: null,
  auto_approve: false,
  running_since: null,
  handler: null,
  builtin: false,
  cooldown_minutes: null,
};

afterEach(() => {
  setState({ automations: null, automationsOpen: false, automationTargetId: null });
});

describe("typed app navigation", () => {
  test("opens a specific known automation", () => {
    setState({ automations: [automation] });

    const result = applyAppDestination({ kind: "automation", task_id: "email-digest" });

    expect(result).toEqual({ ok: true });
    expect(getState().automationsOpen).toBe(true);
    expect(getState().automationTargetId).toBe("email-digest");
  });

  test("does not open an unknown automation", () => {
    setState({ automations: [automation] });

    const result = applyAppDestination({ kind: "automation", task_id: "missing" });

    expect(result.ok).toBe(false);
    expect(getState().automationsOpen).toBe(false);
  });

  test("opens a known area and rejects an unknown one", () => {
    getState().setAreaRecords([
      {
        area_id: "ops",
        name: "Ops",
        created_at: "2026-07-21T00:00:00Z",
        updated_at: "2026-07-21T00:00:00Z",
      } as never,
    ]);

    expect(applyAppDestination({ kind: "area", area_id: "ops" })).toEqual({ ok: true });
    expect(getState().areas.openAreaKey).toBe("ops");
    expect(applyAppDestination({ kind: "area", area_id: "missing" }).ok).toBe(false);
  });
});
