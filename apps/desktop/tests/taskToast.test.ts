import { expect, test } from "bun:test";
import {
  automationToast,
  backgroundAgentToast,
  isTerminalStatus,
  toastDismissDelay,
  TOAST_CEILING_MS,
  TOAST_LIFETIME_MS,
} from "@/lib/taskToast";
import type { BackgroundAgent } from "@/stores/background-agent-domain";

const agent = (over: Partial<BackgroundAgent> = {}): BackgroundAgent => ({
  taskId: "t1",
  sessionId: "s1",
  command: "build the thing",
  status: "completed",
  createdAt: 0,
  updatedAt: 1,
  ...over,
});

test("isTerminalStatus only matches completed/failed/cancelled", () => {
  expect(isTerminalStatus("completed")).toBe(true);
  expect(isTerminalStatus("failed")).toBe(true);
  expect(isTerminalStatus("cancelled")).toBe(true);
  expect(isTerminalStatus("running")).toBe(false);
  expect(isTerminalStatus("interrupted")).toBe(false);
  // "info" is a toast tone, not a run state — it must never narrow.
  expect(isTerminalStatus("info")).toBe(false);
});

test("backgroundAgentToast: terminal + not focused → session-targeted toast", () => {
  const toast = backgroundAgentToast(agent(), "other-session");
  expect(toast).not.toBeNull();
  expect(toast?.id).toBe("bg:s1:t1");
  expect(toast?.title).toBe("build the thing");
  expect(toast?.status).toBe("completed");
  expect(toast?.target).toEqual({ kind: "session", sessionId: "s1" });
});

test("backgroundAgentToast: suppressed when its session is focused", () => {
  expect(backgroundAgentToast(agent({ sessionId: "s1" }), "s1")).toBeNull();
});

test("backgroundAgentToast: non-terminal → null", () => {
  expect(backgroundAgentToast(agent({ status: "running" }), "x")).toBeNull();
});

test("automationToast: builds an automation-targeted toast", () => {
  const toast = automationToast({
    taskId: "a1",
    name: "Daily digest",
    result: "3 items",
    automationsOpen: false,
  });
  expect(toast?.id).toBe("auto:a1");
  expect(toast?.title).toBe("Daily digest");
  expect(toast?.detail).toBe("3 items");
  expect(toast?.target).toEqual({ kind: "automation", taskId: "a1" });
});

test("automationToast: falls back to a generic title; suppressed when modal open", () => {
  expect(
    automationToast({ taskId: "a1", name: null, result: null, automationsOpen: false })?.title,
  ).toBe("Scheduled task");
  expect(
    automationToast({ taskId: "a1", name: "X", result: null, automationsOpen: true }),
  ).toBeNull();
});

test("toastDismissDelay: a resting card counts down its lifetime", () => {
  expect(toastDismissDelay({ hovered: false, expired: false, blocked: false })).toBe(
    TOAST_LIFETIME_MS,
  );
});

test("toastDismissDelay: hover and blocking overlays hold the card", () => {
  expect(toastDismissDelay({ hovered: true, expired: false, blocked: false })).toBeNull();
  expect(toastDismissDelay({ hovered: false, expired: false, blocked: true })).toBeNull();
});

test("toastDismissDelay: past the ceiling only hover still holds", () => {
  // The card outlived the ceiling behind an overlay — it goes the moment the
  // overlay closes, without another full lifetime.
  expect(toastDismissDelay({ hovered: false, expired: true, blocked: true })).toBe(0);
  expect(toastDismissDelay({ hovered: false, expired: true, blocked: false })).toBe(0);
  expect(toastDismissDelay({ hovered: true, expired: true, blocked: false })).toBeNull();
});

test("the ceiling bounds a held card at a small multiple of its lifetime", () => {
  expect(TOAST_CEILING_MS).toBe(TOAST_LIFETIME_MS * 3);
});
