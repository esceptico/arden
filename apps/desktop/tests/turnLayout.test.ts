import { expect, test } from "bun:test";
import { rollingWorkWindow, turnLayout } from "@/features/chat/lib/turnLayout";

test("keeps live assistant text outside the work trace", () => {
  const layout = turnLayout({
    children: [
      { id: "assistant-1", role: "assistant" },
      { id: "activity-1", role: "activity" },
      { id: "assistant-2", role: "assistant" },
      { id: "activity-2", role: "activity" },
    ],
    isDone: false,
  });

  expect(layout).toEqual({
    workIds: ["activity-1", "activity-2"],
    afterWorkIds: ["assistant-1", "assistant-2"],
    finalAssistantId: "assistant-2",
  });
});

test("shares the three-row live cap across reasoning and tool groups", () => {
  expect(rollingWorkWindow([
    { id: "reasoning-old", rowCount: 1 },
    { id: "tools", rowCount: 2 },
    { id: "reasoning-new", rowCount: 2 },
  ], 3)).toEqual([
    { id: "tools", rowLimit: 1 },
    { id: "reasoning-new", rowLimit: 2 },
  ]);
});

test("keeps live reasoning inside the tool trace", () => {
  const layout = turnLayout({
    children: [
      { id: "reasoning-1", role: "reasoning" },
      { id: "activity-1", role: "activity" },
      { id: "reasoning-2", role: "reasoning" },
    ],
    isDone: false,
  });

  expect(layout).toEqual({
    workIds: ["reasoning-1", "activity-1", "reasoning-2"],
    afterWorkIds: [],
    finalAssistantId: null,
  });
});

test("keeps completed turns split into work block plus final assistant", () => {
  const layout = turnLayout({
    children: [
      { id: "activity-1", role: "activity" },
      { id: "assistant-1", role: "assistant" },
    ],
    isDone: true,
  });

  expect(layout).toEqual({
    workIds: ["activity-1"],
    afterWorkIds: ["assistant-1"],
    finalAssistantId: "assistant-1",
  });
});

test("does not treat hidden todo state as completed-turn work", () => {
  const layout = turnLayout({
    children: [
      { id: "todo-1", role: "todo" },
      { id: "assistant-1", role: "assistant" },
    ],
    isDone: true,
  });

  expect(layout).toEqual({
    workIds: [],
    afterWorkIds: ["todo-1", "assistant-1"],
    finalAssistantId: "assistant-1",
  });
});

test("puts pre-tool assistant text inside completed work when there is a final response", () => {
  const layout = turnLayout({
    children: [
      { id: "assistant-1", role: "assistant" },
      { id: "activity-1", role: "activity" },
      { id: "assistant-2", role: "assistant" },
    ],
    isDone: true,
  });

  expect(layout).toEqual({
    workIds: ["assistant-1", "activity-1"],
    afterWorkIds: ["assistant-2"],
    finalAssistantId: "assistant-2",
  });
});

test("puts everything except the final assistant message inside completed work", () => {
  const layout = turnLayout({
    children: [
      { id: "assistant-1", role: "assistant" },
      { id: "activity-1", role: "activity" },
      { id: "assistant-2", role: "assistant" },
      { id: "activity-2", role: "activity" },
      { id: "assistant-3", role: "assistant" },
    ],
    isDone: true,
  });

  expect(layout).toEqual({
    workIds: ["assistant-1", "activity-1", "assistant-2", "activity-2"],
    afterWorkIds: ["assistant-3"],
    finalAssistantId: "assistant-3",
  });
});

test("keeps post-tool nonfinal items inside the completed work block", () => {
  const layout = turnLayout({
    children: [
      { id: "assistant-1", role: "assistant" },
      { id: "activity-1", role: "activity" },
      { id: "reasoning-1", role: "reasoning" },
      { id: "assistant-2", role: "assistant" },
    ],
    isDone: true,
  });

  expect(layout).toEqual({
    workIds: ["assistant-1", "activity-1", "reasoning-1"],
    afterWorkIds: ["assistant-2"],
    finalAssistantId: "assistant-2",
  });
});

test("does not move a pre-tool assistant message when no final assistant exists", () => {
  const layout = turnLayout({
    children: [
      { id: "assistant-1", role: "assistant" },
      { id: "activity-1", role: "activity" },
    ],
    isDone: true,
  });

  expect(layout).toEqual({
    workIds: ["assistant-1", "activity-1"],
    afterWorkIds: [],
    finalAssistantId: null,
  });
});

test("keeps the final assistant visible when replay appends trailing activity", () => {
  const layout = turnLayout({
    children: [
      { id: "activity-1", role: "activity" },
      { id: "assistant-1", role: "assistant" },
      { id: "activity-2", role: "activity" },
    ],
    isDone: true,
  });

  expect(layout).toEqual({
    workIds: ["activity-1"],
    afterWorkIds: ["assistant-1", "activity-2"],
    finalAssistantId: "assistant-1",
  });
});

test("does not reorder a completed reload when activity follows assistant text", () => {
  const layout = turnLayout({
    children: [
      { id: "activity-load-tools", role: "activity" },
      { id: "assistant-progress", role: "assistant" },
      { id: "activity-read-thread", role: "activity" },
    ],
    isDone: true,
  });

  expect(layout).toEqual({
    workIds: ["activity-load-tools"],
    afterWorkIds: ["assistant-progress", "activity-read-thread"],
    finalAssistantId: "assistant-progress",
  });
});

test("keeps only the trailing assistant and later activity inline", () => {
  const layout = turnLayout({
    children: [
      { id: "activity-load-tools", role: "activity" },
      { id: "activity-read-files", role: "activity" },
      { id: "assistant-progress", role: "assistant" },
      { id: "activity-run-tests", role: "activity" },
    ],
    isDone: true,
  });

  expect(layout).toEqual({
    workIds: ["activity-load-tools", "activity-read-files"],
    afterWorkIds: ["assistant-progress", "activity-run-tests"],
    finalAssistantId: "assistant-progress",
  });
});

test("does not treat pre-work assistant text as final just because it is the only assistant", () => {
  const layout = turnLayout({
    children: [
      { id: "assistant-1", role: "assistant" },
      { id: "activity-1", role: "activity" },
    ],
    isDone: true,
  });

  expect(layout).toEqual({
    workIds: ["assistant-1", "activity-1"],
    afterWorkIds: [],
    finalAssistantId: null,
  });
});
