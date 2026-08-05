import { expect, test } from "bun:test";
import { getState, setState, useStore } from "@/stores";

const conversationActions = [
  "setSessions",
  "prependSession",
  "patchSession",
  "syncActiveRuns",
  "markRunStarted",
  "markRunCompleted",
  "clearForegroundRun",
  "setCurrentSession",
  "beginNewChatDraft",
  "setHistory",
  "prependHistory",
  "appendHistoryPage",
  "setHistoryLoading",
  "appendMessage",
  "insertMessageBefore",
  "mutateMessage",
  "upsertTodoList",
  "truncateFrom",
  "setConnected",
  "setServerWarmup",
  "setError",
  "setDraft",
  "setEditingId",
  "resetUsage",
  "accumulateUsage",
  "updateLiveUsage",
  "hydrateUsageSnapshot",
] as const;

test("conversation actions remain on the single public store", () => {
  const state = getState();
  for (const action of conversationActions) {
    expect(typeof state[action]).toBe("function");
  }
  expect(getState).toBe(useStore.getState);
  expect(setState).toBe(useStore.setState);
});
