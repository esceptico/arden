import { afterEach, beforeEach, expect, test } from "bun:test";
import { runBuiltinCommand } from "@/actions/builtins";
import { acceptGoalProposal, cancelGoalProposal, editGoalProposal } from "@/actions/goals";
import { getState, setState } from "@/stores/index";

const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window;

beforeEach(() => {
  setState({
    config: { serverUrl: "http://localhost:6877", apiKey: "" },
    currentSessionId: "sess-1",
    messages: new Map(),
    order: [],
    running: false,
    goals: {},
    draft: "",
  });
});

afterEach(() => {
  (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
});

test("empty goal command stores proposed goal draft without persisting", async () => {
  const requests: { path: string; method: string }[] = [];
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async (_config: unknown, req: { path: string; method?: string }) => {
          requests.push({ path: req.path, method: req.method ?? "GET" });
          return {
            ok: true,
            status: 200,
            statusText: "OK",
            contentType: "application/json",
            data: { objective: "Fix the checkout retry bug." },
            text: "",
          };
        },
      },
    },
    setTimeout,
    clearTimeout,
  };

  await runBuiltinCommand("goal", "");

  expect(requests).toEqual([{ path: "/sessions/sess-1/goal/propose", method: "POST" }]);
  expect((getState() as any).pendingGoalProposal).toEqual({
    sessionId: "sess-1",
    objective: "Fix the checkout retry bug.",
  });
  expect(getState().goals["sess-1"]).toBeUndefined();
});

test("accept goal proposal persists objective and clears draft", async () => {
  const requests: { path: string; method: string; body?: string }[] = [];
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ardenDesktop: {
      api: {
        request: async (_config: unknown, req: { path: string; method?: string; body?: string }) => {
          requests.push({ path: req.path, method: req.method ?? "GET", body: req.body });
          return {
            ok: true,
            status: 200,
            statusText: "OK",
            contentType: "application/json",
            data: {
              session_id: "sess-1",
              goal_id: "goal-1",
              objective: "Fix the checkout retry bug.",
              status: "active",
              evidence: [],
              tokens_used: 0,
              time_used_seconds: 0,
              created_at: "now",
              updated_at: "now",
            },
            text: "",
          };
        },
      },
    },
    setTimeout,
    clearTimeout,
  };
  getState().setPendingGoalProposal({
    sessionId: "sess-1",
    objective: "Fix the checkout retry bug.",
  });

  await acceptGoalProposal();

  expect(requests[0]).toEqual({
    path: "/sessions/sess-1/goal",
    method: "POST",
    body: JSON.stringify({ objective: "Fix the checkout retry bug." }),
  });
  expect(requests[1]?.path).toBe("/chat/message");
  expect(requests[1]?.method).toBe("POST");
  expect(JSON.parse(requests[1]?.body ?? "{}")).toMatchObject({
    message: "/goal Fix the checkout retry bug.",
    session_id: "sess-1",
  });
  expect(getState().goals["sess-1"]?.objective).toBe("Fix the checkout retry bug.");
  expect(getState().pendingGoalProposal).toBeNull();
});

test("edit and cancel goal proposal keep persistence untouched", () => {
  getState().setPendingGoalProposal({
    sessionId: "sess-1",
    objective: "Fix the checkout retry bug.",
  });

  editGoalProposal();

  expect(getState().draft).toBe("/goal Fix the checkout retry bug.");
  expect(getState().pendingGoalProposal).toBeNull();

  getState().setPendingGoalProposal({
    sessionId: "sess-1",
    objective: "Fix the checkout retry bug.",
  });
  cancelGoalProposal();

  expect(getState().pendingGoalProposal).toBeNull();
});
