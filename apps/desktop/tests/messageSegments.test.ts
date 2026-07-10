import { expect, test } from "bun:test";
import { messageSegments } from "@/lib/messageSegments";

test("hidden meta user messages split visible turns without rendering", () => {
  expect(messageSegments({
    ids: ["user-1", "activity-1", "meta-user-1", "assistant-1"],
    roles: ["user", "activity", "user", "assistant"],
    metaFlags: [false, false, true, false],
    visibleIds: ["user-1", "activity-1", "assistant-1"],
  })).toEqual([
    { userId: "user-1", childIds: ["activity-1"] },
    { userId: null, childIds: ["assistant-1"] },
  ]);
});

test("trailing hidden meta user messages do not create empty render segments", () => {
  expect(messageSegments({
    ids: ["user-1", "activity-1", "meta-user-1"],
    roles: ["user", "activity", "user"],
    metaFlags: [false, false, true],
    visibleIds: ["user-1", "activity-1"],
  })).toEqual([
    { userId: "user-1", childIds: ["activity-1"] },
  ]);
});

test("status messages stay between turns", () => {
  expect(messageSegments({
    ids: ["user-1", "assistant-1", "status-1", "assistant-2"],
    roles: ["user", "assistant", "status", "assistant"],
    visibleIds: ["user-1", "assistant-1", "status-1", "assistant-2"],
  })).toEqual([
    { userId: "user-1", childIds: ["assistant-1"] },
    { userId: null, childIds: ["status-1"] },
    { userId: null, childIds: ["assistant-2"] },
  ]);
});
