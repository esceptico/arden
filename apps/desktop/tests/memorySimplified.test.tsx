import { expect, test } from "bun:test";
import {
  buildWorkspaceTree,
  noteDateGroup,
  prettyDate,
  sortNotes,
  stem,
} from "@/features/memory/lib/workspaceTree";
import type { MemoryArtifactSummary } from "@/features/memory/lib/notebookTypes";
import type { WorkspaceDir } from "@/features/memory/lib/workspaceTree";

function findDir(root: WorkspaceDir, key: string): WorkspaceDir | null {
  let node: WorkspaceDir | null = root;
  for (const part of key.replace(/\/$/, "").split("/").filter(Boolean)) {
    node = node?.dirs.find((dir) => dir.name === part) ?? null;
  }
  return node;
}

function collectNotes(dir: WorkspaceDir): MemoryArtifactSummary[] {
  return [...dir.files, ...dir.dirs.flatMap(collectNotes)];
}

function summary(path: string, extra: Partial<MemoryArtifactSummary> = {}): MemoryArtifactSummary {
  return {
    pageId: `page:${path}`,
    path,
    title: stem(path),
    kind: "topic",
    source: "wiki",
    revision: "sha256:1",
    head: "sha256:head",
    aliases: [],
    lifecycle: "active",
    snippet: null,
    summary: null,
    editable: true,
    updatedAt: "2026-07-10T08:00:00Z",
    createdAt: "2026-03-01T08:00:00Z",
    ...extra,
  };
}

test("the canonical page list is authoritative for the workspace tree", () => {
  const artifacts = [
    summary("index.md"),
    summary("topics/dex.md"),
    summary("raw/topics/dex.md"),
    summary("raw/events/2026-07-01.md"),
    summary("changelog/2026-07.md"),
    summary("health.md"),
  ];
  const tree = buildWorkspaceTree(artifacts);
  expect(collectNotes(tree).map((artifact) => artifact.path)).toEqual([
    "index.md",
    "health.md",
    "changelog/2026-07.md",
    "raw/events/2026-07-01.md",
    "raw/topics/dex.md",
    "topics/dex.md",
  ]);
});

test("managed wiki READMEs remain visible as canonical folder overviews", () => {
  const rootReadme = summary("README.md", { source: "wiki" });
  const topicReadme = summary("topics/README.md", { source: "wiki" });
  const topic = summary("topics/dex.md", { source: "wiki" });
  const tree = buildWorkspaceTree([topic, rootReadme, topicReadme]);

  expect(tree.files.map((artifact) => artifact.path)).toEqual(["README.md"]);
  expect(findDir(tree, "topics/")?.files.map((artifact) => artifact.path)).toEqual([
    "topics/dex.md",
    "topics/README.md",
  ]);
  expect(sortNotes([topic, topicReadme], { key: "name", asc: true }, () => ""))
    .toEqual([topicReadme, topic]);
});

test("tree mirrors the canonical page directory structure", () => {
  const artifacts = [summary("index.md"), summary("topics/dex.md"), summary("topics/nested/deep.md")];
  const tree = buildWorkspaceTree(artifacts);
  expect(tree.dirs.map((dir) => dir.name)).toEqual(["topics"]);
  const topics = findDir(tree, "topics/");
  expect(topics?.dirs.map((dir) => dir.name)).toEqual(["nested"]);
  expect(topics?.files.map((artifact) => artifact.path)).toEqual(["topics/dex.md"]);
  expect(collectNotes(tree)).toHaveLength(3);
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

test("stems, date groups, and pretty dates match the draft semantics", () => {
  expect(stem("topics/nexus.md")).toBe("nexus");
  expect(stem("me.md")).toBe("me");
  const now = new Date("2026-07-14T12:00:00Z");
  // Notebook Navigator's rail: relative buckets first, then calendar months,
  // year-qualified once they leave the current one.
  expect(noteDateGroup("2026-07-14T09:00:00Z", now)).toBe("Today");
  expect(noteDateGroup("2026-07-13T09:00:00Z", now)).toBe("Yesterday");
  expect(noteDateGroup("2026-07-08T09:00:00Z", now)).toBe("Previous 7 days");
  expect(noteDateGroup("2026-06-20T09:00:00Z", now)).toBe("Previous 30 days");
  expect(noteDateGroup("2026-05-01T09:00:00Z", now)).toBe("May");
  expect(noteDateGroup("2025-01-01T09:00:00Z", now)).toBe("January 2025");
  expect(noteDateGroup(null, now)).toBe("Undated");
  expect(prettyDate("2026-07-13T08:00:00Z")).toBe("Jul 13, 2026");
});
