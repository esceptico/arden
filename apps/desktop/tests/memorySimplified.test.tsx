import { expect, test } from "bun:test";
import { isNotebookResourcePath } from "@/features/memory/lib/notebookIndex";
import {
  bucketOf,
  buildWorkspaceTree,
  collectNotes,
  countNotes,
  findDir,
  prettyDate,
  sortNotes,
  stem,
} from "@/features/memory/lib/workspaceTree";
import type { MemoryArtifactSummary } from "@/features/memory/lib/notebookTypes";

function summary(path: string, extra: Partial<MemoryArtifactSummary> = {}): MemoryArtifactSummary {
  return {
    path,
    title: stem(path),
    kind: "topic",
    type: "file",
    directory: path.split("/").slice(0, -1).join("/"),
    scope: { kind: "user", key: null },
    snippet: null,
    summary: null,
    revision: "sha256:1",
    recordCount: 0,
    generated: false,
    editable: true,
    readonlyReason: null,
    updatedAt: "2026-07-10T08:00:00Z",
    createdAt: "2026-03-01T08:00:00Z",
    labels: [],
    source: null,
    ...extra,
  };
}

test("machine-only paths never reach the workspace tree", () => {
  const artifacts = [
    summary("index.md"),
    summary("topics/dex.md"),
    summary("raw/topics/dex.md"),
    summary("raw/events/2026-07-01.md"),
    summary("changelog/2026-07.md"),
    summary("health.md"),
  ];
  const tree = buildWorkspaceTree(artifacts);
  expect(collectNotes(tree).map((artifact) => artifact.path)).toEqual(["index.md", "topics/dex.md"]);
  expect(isNotebookResourcePath("raw/topics/dex.md")).toBe(false);
  expect(isNotebookResourcePath("health.md")).toBe(false);
});

test("tree mirrors the directory structure with empty directories included", () => {
  const artifacts = [summary("index.md"), summary("topics/dex.md"), summary("topics/nested/deep.md")];
  const tree = buildWorkspaceTree(artifacts, ["topics/archive/", "inbox/"]);
  expect(tree.dirs.map((dir) => dir.name)).toEqual(["inbox", "topics"]);
  const topics = findDir(tree, "topics/");
  expect(topics?.dirs.map((dir) => dir.name)).toEqual(["archive", "nested"]);
  expect(topics?.files.map((artifact) => artifact.path)).toEqual(["topics/dex.md"]);
  expect(countNotes(tree)).toBe(3);
  expect(findDir(tree, "topics/archive/")?.files).toEqual([]);
});

test("sortNotes pins index.md first and honors key + direction", () => {
  const a = summary("topics/alpha.md", { updatedAt: "2026-07-01T00:00:00Z" });
  const b = summary("topics/beta.md", { updatedAt: "2026-07-12T00:00:00Z" });
  const index = summary("index.md", { updatedAt: "2026-01-01T00:00:00Z" });
  const created = (artifact: MemoryArtifactSummary) => artifact.createdAt ?? "";
  expect(sortNotes([b, index, a], { key: "name", asc: true }, created).map((x) => x.path))
    .toEqual(["index.md", "topics/alpha.md", "topics/beta.md"]);
  expect(sortNotes([a, index, b], { key: "modified", asc: false }, created).map((x) => x.path))
    .toEqual(["index.md", "topics/beta.md", "topics/alpha.md"]);
});

test("stems, buckets, and pretty dates match the draft semantics", () => {
  expect(stem("topics/nexus.md")).toBe("nexus");
  expect(stem("me.md")).toBe("me");
  const now = new Date("2026-07-14T12:00:00Z");
  expect(bucketOf("2026-07-14T09:00:00Z", now)).toBe("Today");
  expect(bucketOf("2026-07-13T09:00:00Z", now)).toBe("Yesterday");
  expect(bucketOf("2026-07-08T09:00:00Z", now)).toBe("This week");
  expect(bucketOf("2026-06-20T09:00:00Z", now)).toBe("This month");
  expect(bucketOf("2026-01-01T09:00:00Z", now)).toBe("Earlier");
  expect(bucketOf(null, now)).toBe("Undated");
  expect(prettyDate("2026-07-13T08:00:00Z")).toBe("Jul 13, 2026");
});
