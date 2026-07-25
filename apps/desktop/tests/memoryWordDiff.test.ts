import { expect, test } from "bun:test";
import { diffLines, wordDiff } from "@/features/memory/components/MemoryDiffOverlay";

test("word diff keeps shared words quiet and highlights changed words", () => {
  const result = wordDiff(
    "The quick brown fox jumps.",
    "The quick blue fox jumps!",
  );

  expect(result.before.filter((segment) => segment.changed).map((segment) => segment.text).join(""))
    .toBe("brown.");
  expect(result.after.filter((segment) => segment.changed).map((segment) => segment.text).join(""))
    .toBe("blue!");
});

test("unified diff pairs removed and added lines for intraline rendering", () => {
  const lines = diffLines(
    "--- a/note.md\n+++ b/note.md\n@@ -1 +1 @@\n-old durable fact\n+new durable fact",
  );

  expect(lines).toHaveLength(3);
  expect(lines[0]).toMatchObject({ kind: "hunk", oldLine: null, newLine: null });
  expect(lines[1]).toMatchObject({ kind: "del", oldLine: 1, newLine: null });
  expect(lines[2]).toMatchObject({ kind: "add", oldLine: null, newLine: 1 });
  expect(lines[1].segments?.some((segment) => segment.changed && segment.text === "old")).toBe(true);
  expect(lines[2].segments?.some((segment) => segment.changed && segment.text === "new")).toBe(true);
});

test("unrelated removed and added lines remain whole-row changes", () => {
  const lines = diffLines(
    "@@ -7,2 +7,2 @@\n-a completely obsolete payment record\n-an unrelated archived incident\n+a short current summary\n+another compact result",
  );

  expect(lines.slice(1).every((line) => line.segments === undefined)).toBe(true);
  expect(lines.map((line) => [line.kind, line.oldLine, line.newLine])).toEqual([
    ["hunk", null, null],
    ["del", 7, null],
    ["del", 8, null],
    ["add", null, 7],
    ["add", null, 8],
  ]);
});

test("trailing patch newline does not create a phantom context row", () => {
  const lines = diffLines("@@ -3 +3 @@\n-old fact\n+new fact\n");

  expect(lines).toHaveLength(3);
});
