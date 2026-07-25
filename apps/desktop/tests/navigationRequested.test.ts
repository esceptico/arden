import { afterEach, beforeEach, expect, test } from "bun:test";
import { applyNavigationRequest } from "@/features/automations/hooks/useAutomationEvents";
import { getState, setState } from "@/stores";
function seedArea() {
  getState().setAreaRecords([
    {
      area_id: "ops",
      name: "Ops",
      created_at: "2026-07-25T00:00:00Z",
      updated_at: "2026-07-25T00:00:00Z",
    } as never,
  ]);
}

beforeEach(() => {
  // Other suites share this store instance; start from a clean toast stack.
  setState({ toasts: [] });
});

afterEach(() => {
  getState().setAreaRecords([]);
  getState().openArea(null);
  setState({ toasts: [], currentSessionId: null, memoryOpen: false });
});

test("the viewed session's request navigates immediately and raises no toast", () => {
  seedArea();
  setState({ currentSessionId: "s-1" });

  applyNavigationRequest({
    type: "navigation_requested",
    origin_session_id: "s-1",
    destination: { kind: "area", area_id: "ops" },
    label: "Open the Ops area",
    seq: 7,
  });

  expect(getState().areas.openAreaKey).toBe("ops");
  expect(getState().toasts).toEqual([]);
});

test("a request from another session offers the move as an info toast", () => {
  seedArea();
  setState({ currentSessionId: "s-other" });

  applyNavigationRequest({
    type: "navigation_requested",
    origin_session_id: "s-1",
    destination: { kind: "area", area_id: "ops" },
    label: "Open the Ops area",
    seq: 7,
  });

  expect(getState().areas.openAreaKey).toBeNull();
  expect(getState().toasts).toEqual([
    {
      id: "nav:s-1:7",
      title: "Open the Ops area",
      detail: undefined,
      status: "info",
      target: { kind: "destination", destination: { kind: "area", area_id: "ops" } },
    },
  ]);
});

test("clicking the offered toast applies its destination", async () => {
  seedArea();
  setState({ currentSessionId: "s-other" });

  applyNavigationRequest({
    type: "navigation_requested",
    origin_session_id: "s-1",
    destination: { kind: "area", area_id: "ops" },
    label: "Open the Ops area",
  });

  const target = getState().toasts[0].target;
  expect(target.kind).toBe("destination");
  const { applyAppDestination } = await import("@/actions/navigation");
  if (target.kind !== "destination") throw new Error("unreachable");
  expect(applyAppDestination(target.destination)).toEqual({ ok: true });
  expect(getState().areas.openAreaKey).toBe("ops");
});

test("a destination that no longer resolves degrades to a failed toast", () => {
  setState({ currentSessionId: "s-1" });

  applyNavigationRequest({
    type: "navigation_requested",
    origin_session_id: "s-1",
    destination: { kind: "area", area_id: "gone" },
    label: "Open the Gone area",
    seq: 9,
  });

  expect(getState().areas.openAreaKey).toBeNull();
  expect(getState().toasts).toEqual([
    {
      id: "nav:s-1:9",
      title: "Open the Gone area",
      detail: "That area is no longer available.",
      status: "failed",
      target: { kind: "destination", destination: { kind: "area", area_id: "gone" } },
    },
  ]);
});
