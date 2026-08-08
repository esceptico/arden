import { expect, test } from "bun:test";
import { messageDisplayPolicy, visibleMessageIds } from "@/lib/messageVisibility";

test("hides reasoning without hiding tool activity", () => {
  expect(visibleMessageIds({
    ids: ["user-1", "reasoning-1", "activity-1", "assistant-1"],
    roles: ["user", "reasoning", "activity", "assistant"],
    showReasoning: false,
  })).toEqual(["user-1", "activity-1", "assistant-1"]);
});

test("shows reasoning when opted in and keeps later tools below it", () => {
  expect(visibleMessageIds({
    ids: ["user-1", "reasoning-1", "activity-1", "assistant-1"],
    roles: ["user", "reasoning", "activity", "assistant"],
    showReasoning: true,
  })).toEqual(["user-1", "reasoning-1", "activity-1", "assistant-1"]);

  expect(messageDisplayPolicy({ role: "reasoning", showReasoning: true })).toEqual({
    hiddenInTranscript: false,
    breaksTurn: false,
    breaksActivity: true,
  });
});

test("hides meta user messages instead of using them as visible separators", () => {
  expect(visibleMessageIds({
    ids: ["user-1", "activity-1", "meta-user-1", "assistant-1"],
    roles: ["user", "activity", "user", "assistant"],
    metaFlags: [false, false, true, false],
  })).toEqual(["user-1", "activity-1", "assistant-1"]);
});

test("hidden meta users break both turn and activity ownership", () => {
  expect(messageDisplayPolicy({ role: "user", isMeta: true })).toEqual({
    hiddenInTranscript: true,
    breaksTurn: true,
    breaksActivity: true,
  });
});

test("hides empty assistant placeholders", () => {
  expect(visibleMessageIds({
    ids: ["activity-1", "assistant-empty", "assistant-visible"],
    roles: ["activity", "assistant", "assistant"],
    contents: ["", "", "done"],
  })).toEqual(["activity-1", "assistant-visible"]);
});

test("hides todo state from transcript", () => {
  expect(visibleMessageIds({
    ids: ["user-1", "todo-1", "assistant-1"],
    roles: ["user", "todo", "assistant"],
    contents: ["work", "", "done"],
  })).toEqual(["user-1", "assistant-1"]);
});
