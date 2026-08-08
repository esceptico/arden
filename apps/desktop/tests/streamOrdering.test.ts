import { beforeEach, expect, test } from "bun:test";
import { eventStreamUrl, handleServerEvent, resetStreamStateForTest, setEventCursorForSession } from "@/hooks/useEvents";
import { getState, setState } from "@/stores/index";

beforeEach(() => {
  resetStreamStateForTest();
  setState({
    messages: new Map(),
    order: [],
    activeActivityId: null,
    running: false,
    currentRunId: null,
    error: null,
  });
});

test("appends a new activity group after assistant text that already streamed", () => {
  handleServerEvent({
    type: "RUN_STARTED",
    run_id: "run-1",
    session_id: "session-1",
    timestamp: 1,
  });
  handleServerEvent({
    type: "TEXT_MESSAGE_START",
    message_id: "assistant-1",
    timestamp: 2,
  });
  handleServerEvent({
    type: "TEXT_MESSAGE_CONTENT",
    message_id: "assistant-1",
    delta: "I am streaming",
    timestamp: 3,
  });
  handleServerEvent({
    type: "TOOL_CALL_START",
    tool_call_id: "tool-1",
    tool_call_name: "ReadFile",
    timestamp: 4,
  });
  handleServerEvent({
    type: "TOOL_CALL_ARGS",
    tool_call_id: "tool-1",
    delta: '{"path":"apps/desktop/src/hooks/useEvents.ts"}',
    timestamp: 5,
  });
  handleServerEvent({
    type: "TOOL_CALL_END",
    tool_call_id: "tool-1",
    timestamp: 6,
  });

  const state = getState();
  expect(state.order).toHaveLength(2);
  expect(state.messages.get(state.order[0])?.role).toBe("assistant");
  expect(state.messages.get(state.order[1])?.role).toBe("activity");
});

test("shows calling activity immediately on live tool start", () => {
  handleServerEvent({
    type: "RUN_STARTED",
    run_id: "run-1",
    session_id: "session-1",
    timestamp: 1,
  });
  handleServerEvent({
    type: "TOOL_CALL_START",
    tool_call_id: "tool-1",
    tool_call_name: "Bash",
    timestamp: 2,
  });

  const state = getState();
  const activity = state.messages.get(state.order[0]);
  expect(activity?.role).toBe("activity");
  expect(activity?.activity).toMatchObject({
    label: "Calling",
    done: false,
  });
  expect(activity?.activity?.items.map((item) => item.id)).toEqual(["tool-1"]);
});

test("anchors a new activity group before an empty assistant placeholder", () => {
  handleServerEvent({
    type: "RUN_STARTED",
    run_id: "run-1",
    session_id: "session-1",
    timestamp: 1,
  });
  handleServerEvent({
    type: "TEXT_MESSAGE_START",
    message_id: "assistant-1",
    timestamp: 2,
  });
  handleServerEvent({
    type: "TOOL_CALL_START",
    tool_call_id: "tool-1",
    tool_call_name: "ReadFile",
    timestamp: 3,
  });
  handleServerEvent({
    type: "TOOL_CALL_END",
    tool_call_id: "tool-1",
    timestamp: 4,
  });

  const state = getState();
  expect(state.order).toHaveLength(2);
  expect(state.messages.get(state.order[0])?.role).toBe("activity");
  expect(state.messages.get(state.order[1])?.role).toBe("assistant");
});

test("reconstructs empty assistant anchor after reconnect", () => {
  handleServerEvent({
    type: "RUN_STARTED",
    run_id: "run-1",
    session_id: "session-1",
    timestamp: 1,
  });
  handleServerEvent({
    type: "TEXT_MESSAGE_START",
    message_id: "assistant-1",
    timestamp: 2,
  });

  resetStreamStateForTest();

  handleServerEvent({
    type: "TOOL_CALL_START",
    tool_call_id: "tool-1",
    tool_call_name: "ReadFile",
    timestamp: 3,
  });
  handleServerEvent({
    type: "TOOL_CALL_END",
    tool_call_id: "tool-1",
    timestamp: 4,
  });

  const state = getState();
  expect(state.order).toHaveLength(2);
  expect(state.messages.get(state.order[0])?.role).toBe("activity");
  expect(state.messages.get(state.order[1])?.role).toBe("assistant");
});

test("does not mutate a finalized activity group after a tool burst", async () => {
  handleServerEvent({
    type: "RUN_STARTED",
    run_id: "run-1",
    session_id: "session-1",
    timestamp: 1,
  });
  handleServerEvent({
    type: "TOOL_CALL_START",
    tool_call_id: "tool-1",
    tool_call_name: "ReadFile",
    timestamp: 2,
  });
  handleServerEvent({
    type: "TOOL_CALL_END",
    tool_call_id: "tool-1",
    timestamp: 3,
  });
  handleServerEvent({
    type: "TOOL_CALL_START",
    tool_call_id: "tool-2",
    tool_call_name: "ListFiles",
    timestamp: 4,
  });
  handleServerEvent({
    type: "TOOL_CALL_END",
    tool_call_id: "tool-2",
    timestamp: 5,
  });
  handleServerEvent({
    type: "RUN_FINISHED",
    run_id: "run-1",
    timestamp: 6,
  });

  // Drain the stagger queue — burst items roll in over ITEM_STAGGER_MS each.
  await new Promise((resolve) => setTimeout(resolve, 80));

  const state = getState();
  const activityId = state.order.find((id) => state.messages.get(id)?.role === "activity");
  expect(activityId).toBeTruthy();
  expect(state.messages.get(activityId!)?.activity?.items.map((item) => item.id)).toEqual([
    "tool-1",
    "tool-2",
  ]);
  expect(state.messages.get(activityId!)?.activity?.done).toBe(true);

  await new Promise((resolve) => setTimeout(resolve, 80));

  expect(getState().messages.get(activityId!)?.activity?.items.map((item) => item.id)).toEqual([
    "tool-1",
    "tool-2",
  ]);
});

test("starts a new activity group after top-level reasoning", async () => {
  handleServerEvent({
    type: "RUN_STARTED",
    run_id: "run-1",
    session_id: "session-1",
    timestamp: 1,
  });
  handleServerEvent({
    type: "TOOL_CALL_START",
    tool_call_id: "tool-1",
    tool_call_name: "ReadFile",
    timestamp: 2,
  });
  handleServerEvent({
    type: "TOOL_CALL_END",
    tool_call_id: "tool-1",
    timestamp: 3,
  });
  handleServerEvent({
    type: "REASONING_MESSAGE_START",
    message_id: "reasoning-1",
    timestamp: 4,
  });
  handleServerEvent({
    type: "REASONING_MESSAGE_CONTENT",
    message_id: "reasoning-1",
    delta: "thinking",
    timestamp: 5,
  });
  handleServerEvent({
    type: "TOOL_CALL_START",
    tool_call_id: "tool-2",
    tool_call_name: "ListFiles",
    timestamp: 6,
  });
  handleServerEvent({
    type: "TOOL_CALL_END",
    tool_call_id: "tool-2",
    timestamp: 7,
  });

  await new Promise((resolve) => setTimeout(resolve, 80));

  const state = getState();
  const activityIds = state.order.filter((id) => state.messages.get(id)?.role === "activity");
  expect(state.order.map((id) => state.messages.get(id)?.role)).toEqual([
    "activity",
    "reasoning",
    "activity",
  ]);
  expect(activityIds).toHaveLength(2);
  expect(state.messages.get(activityIds[0])?.activity?.items.map((item) => item.id)).toEqual(["tool-1"]);
  expect(state.messages.get(activityIds[1])?.activity?.items.map((item) => item.id)).toEqual(["tool-2"]);
});

test("keeps one activity group across empty assistant placeholders", async () => {
  handleServerEvent({
    type: "RUN_STARTED",
    run_id: "run-1",
    session_id: "session-1",
    timestamp: 1,
  });
  handleServerEvent({
    type: "TOOL_CALL_START",
    tool_call_id: "tool-1",
    tool_call_name: "ReadFile",
    timestamp: 2,
  });
  handleServerEvent({
    type: "TOOL_CALL_END",
    tool_call_id: "tool-1",
    timestamp: 3,
  });
  handleServerEvent({
    type: "TEXT_MESSAGE_START",
    message_id: "empty-assistant-1",
    timestamp: 4,
  });
  handleServerEvent({
    type: "TOOL_CALL_START",
    tool_call_id: "tool-2",
    tool_call_name: "ListFiles",
    timestamp: 5,
  });
  handleServerEvent({
    type: "TOOL_CALL_END",
    tool_call_id: "tool-2",
    timestamp: 6,
  });

  await new Promise((resolve) => setTimeout(resolve, 80));

  const state = getState();
  const activityIds = state.order.filter((id) => state.messages.get(id)?.role === "activity");
  expect(activityIds).toHaveLength(1);
  expect(state.messages.get(activityIds[0])?.activity?.items.map((item) => item.id)).toEqual([
    "tool-1",
    "tool-2",
  ]);
});

test("recovers the trailing activity group when the active pointer was lost", async () => {
  setState({
    messages: new Map([
      [
        "activity-1",
        {
          id: "activity-1",
          role: "activity",
          content: "",
          activity: {
            label: "Calling",
            done: false,
            items: [{ id: "tool-1", kind: "Bash", target: "Bash(command=\"date\")" }],
          },
        },
      ],
      ["assistant-empty", { id: "assistant-empty", role: "assistant", content: "" }],
    ]),
    order: ["activity-1", "assistant-empty"],
    activeActivityId: null,
  });

  handleServerEvent({
    type: "TOOL_CALL_START",
    tool_call_id: "tool-2",
    tool_call_name: "ListFiles",
    timestamp: 8,
  });

  const state = getState();
  const activityIds = state.order.filter((id) => state.messages.get(id)?.role === "activity");
  expect(activityIds).toEqual(["activity-1"]);
  expect(state.activeActivityId).toBe("activity-1");
  expect(state.messages.get("activity-1")?.activity?.items.map((item) => item.id)).toEqual([
    "tool-1",
    "tool-2",
  ]);
});

test("splits activity when an assistant placeholder receives visible text", async () => {
  handleServerEvent({
    type: "RUN_STARTED",
    run_id: "run-1",
    session_id: "session-1",
    timestamp: 1,
  });
  handleServerEvent({
    type: "TOOL_CALL_START",
    tool_call_id: "tool-1",
    tool_call_name: "ReadFile",
    timestamp: 2,
  });
  handleServerEvent({
    type: "TOOL_CALL_END",
    tool_call_id: "tool-1",
    timestamp: 3,
  });
  handleServerEvent({
    type: "TEXT_MESSAGE_START",
    message_id: "assistant-progress",
    timestamp: 4,
  });
  handleServerEvent({
    type: "TEXT_MESSAGE_CONTENT",
    message_id: "assistant-progress",
    delta: "visible progress",
    timestamp: 5,
  });
  handleServerEvent({
    type: "TOOL_CALL_START",
    tool_call_id: "tool-2",
    tool_call_name: "ListFiles",
    timestamp: 6,
  });
  handleServerEvent({
    type: "TOOL_CALL_END",
    tool_call_id: "tool-2",
    timestamp: 7,
  });

  await new Promise((resolve) => setTimeout(resolve, 80));

  const state = getState();
  const activityIds = state.order.filter((id) => state.messages.get(id)?.role === "activity");
  expect(activityIds).toHaveLength(2);
  expect(state.messages.get(activityIds[0])?.activity?.items.map((item) => item.id)).toEqual([
    "tool-1",
  ]);
  expect(state.messages.get(activityIds[1])?.activity?.items.map((item) => item.id)).toEqual([
    "tool-2",
  ]);
});

test("splits activity when assistant text arrives only at message end", async () => {
  handleServerEvent({
    type: "RUN_STARTED",
    run_id: "run-1",
    session_id: "session-1",
    timestamp: 1,
  });
  handleServerEvent({
    type: "TOOL_CALL_START",
    tool_call_id: "tool-1",
    tool_call_name: "ReadFile",
    timestamp: 2,
  });
  handleServerEvent({
    type: "TOOL_CALL_END",
    tool_call_id: "tool-1",
    timestamp: 3,
  });
  handleServerEvent({
    type: "TEXT_MESSAGE_START",
    message_id: "assistant-progress",
    timestamp: 4,
  });
  handleServerEvent({
    type: "TEXT_MESSAGE_END",
    message_id: "assistant-progress",
    content: "visible progress",
    timestamp: 5,
  });
  handleServerEvent({
    type: "TOOL_CALL_START",
    tool_call_id: "tool-2",
    tool_call_name: "ListFiles",
    timestamp: 6,
  });
  handleServerEvent({
    type: "TOOL_CALL_END",
    tool_call_id: "tool-2",
    timestamp: 7,
  });

  await new Promise((resolve) => setTimeout(resolve, 80));

  const state = getState();
  const activityIds = state.order.filter((id) => state.messages.get(id)?.role === "activity");
  expect(state.order.map((id) => state.messages.get(id)?.role)).toEqual([
    "activity",
    "assistant",
    "activity",
  ]);
  expect(activityIds).toHaveLength(2);
  expect(state.messages.get(activityIds[0])?.activity?.items.map((item) => item.id)).toEqual([
    "tool-1",
  ]);
  expect(state.messages.get(activityIds[1])?.activity?.items.map((item) => item.id)).toEqual([
    "tool-2",
  ]);
});

test("renders parallel tool calls in start order even when results finish out of order", async () => {
  handleServerEvent({
    type: "RUN_STARTED",
    run_id: "run-1",
    session_id: "session-1",
    timestamp: 1,
  });
  handleServerEvent({
    type: "TOOL_CALL_START",
    tool_call_id: "tool-1",
    tool_call_name: "SlowTool",
    timestamp: 2,
  });
  handleServerEvent({
    type: "TOOL_CALL_START",
    tool_call_id: "tool-2",
    tool_call_name: "FastTool",
    timestamp: 3,
  });
  handleServerEvent({
    type: "TOOL_CALL_END",
    tool_call_id: "tool-2",
    timestamp: 4,
  });
  handleServerEvent({
    type: "TOOL_CALL_END",
    tool_call_id: "tool-1",
    timestamp: 5,
  });

  await new Promise((resolve) => setTimeout(resolve, 80));

  const state = getState();
  const activityId = state.order.find((id) => state.messages.get(id)?.role === "activity");
  expect(activityId).toBeTruthy();
  expect(state.messages.get(activityId!)?.activity?.items.map((item) => item.id)).toEqual([
    "tool-1",
    "tool-2",
  ]);
});

test("event stream URL uses explicit checkpoint cursor from runtime snapshot", () => {
  setEventCursorForSession("session-1", 42);
  expect(eventStreamUrl({ serverUrl: "http://localhost:3000", apiKey: "" }, "session-1")).toBe(
    "http://localhost:3000/chat/events/session-1?stream=true&after_seq=42",
  );
});
