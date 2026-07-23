import { describe, expect, test } from "bun:test";
import {
  createCommandSidecarState,
  reduceCommandEvent,
} from "@/stores/command-sidecar-domain";

describe("command sidecar event projection", () => {
  test("projects tool activity and a validated destination", () => {
    let state = createCommandSidecarState();
    state = reduceCommandEvent(state, {
      type: "TOOL_CALL_START",
      tool_call_id: "t1",
      tool_call_name: "list_automations",
    });
    state = reduceCommandEvent(state, {
      type: "command_completed",
      run_id: "r1",
      outcome: {
        status: "completed",
        summary: "Opened",
        destination: { kind: "automation", task_id: "a1" },
      },
    });

    expect(state.activities[0]?.name).toBe("list_automations");
    expect(state.outcome?.destination).toEqual({ kind: "automation", task_id: "a1" });
    expect(state.status).toBe("completed");
  });

  test("fails closed on malformed destinations", () => {
    const state = reduceCommandEvent(createCommandSidecarState(), {
      type: "command_completed",
      run_id: "r1",
      outcome: {
        status: "completed",
        summary: "Opened",
        destination: { kind: "url", url: "https://example.com" },
      },
    } as never);

    expect(state.outcome).toBeNull();
    expect(state.error).toMatch(/invalid/i);
    expect(state.status).toBe("failed");
  });

  test("keeps approvals local to the command run", () => {
    const state = reduceCommandEvent(createCommandSidecarState(), {
      type: "approval_needed",
      tool_id: "t1",
      name: "update_automation",
      content_preview: "Pause email digest",
    });

    expect(state.approval).toEqual({
      toolId: "t1",
      name: "update_automation",
      preview: "Pause email digest",
      diff: null,
    });
  });
});
