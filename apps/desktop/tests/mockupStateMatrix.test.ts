import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
const matrix = read("../../../docs/mockups/BOARD_STATE_MATRIX.md");

test("the matrix covers every primary Board surface", () => {
  expect(matrix).toContain("is authoritative");
  for (const surface of ["Home", "Chat", "Automations", "Memory", "Settings", "Area Room", "Agent Hub", "System overlays"]) {
    expect(matrix).toMatch(new RegExp(`\\| ${surface.replace(" ", "\\s")}`));
  }
});

test("the matrix names shared failure and pressure states", () => {
  for (const state of ["loading", "empty", "partial data", "error", "offline", "reconnecting", "auth required", "disabled", "destructive confirmation", "long content"]) {
    expect(matrix.toLowerCase()).toContain(state);
  }
  for (const chatState of ["interrupted streaming", "queued messages", "compaction", "approval allowed", "approval denied", "approval expired", "cancelled run", "stale run", "source failure"]) {
    expect(matrix.toLowerCase()).toContain(chatState);
  }
  for (const automationState of ["never run", "running", "completed", "failed", "paused", "unsafe trigger", "unavailable integration"]) {
    expect(matrix.toLowerCase()).toContain(automationState);
  }
});
