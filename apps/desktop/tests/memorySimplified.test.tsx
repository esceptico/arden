import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import {
  buildNotebookRailModel,
  isNotebookPage,
  parseManagedIndex,
  selectIndexDocuments,
} from "@/features/memory/lib/notebookIndex";
import type { MemoryArtifactSummary } from "@/features/memory/lib/notebookTypes";

test("memory modal uses the directory-first artifact browser instead of pane tabs", () => {
  const modal = readFileSync(new URL("../src/features/memory/components/MemoryModal.tsx", import.meta.url), "utf8");
  const pane = readFileSync(new URL("../src/features/memory/components/MemoryPane.tsx", import.meta.url), "utf8");

  expect(modal).toContain("<MemoryPane />");
  expect(modal).not.toContain("MEMORY_TABS");
  expect(modal).not.toContain("<Tabs");
  expect(pane).toContain("ArtifactMemoryView");
  expect(pane).not.toContain("GraphView");
  expect(pane).not.toContain("LensesView");
});

test("managed rows use their encoded identity when labels and descriptions contain em dashes", () => {
  const content = [
    "<!-- ntrp:index:start -->",
    "- a — b.md — Decision — with context <!-- ntrp:path=a%20%E2%80%94%20b.md -->",
    "<!-- ntrp:index:end -->",
  ].join("\n");

  expect(parseManagedIndex("index.md", content)).toEqual([{
    path: "a — b.md",
    description: "Decision — with context",
    directory: false,
  }]);
});

test("notebook hierarchy keeps described directories semantic and machine pages hidden", () => {
  const artifact = (path: string, title: string, summary: string): MemoryArtifactSummary => ({
    path, title, summary, kind: "topic", type: "file", directory: path.includes("/") ? path.split("/")[0]! : "",
    scope: { kind: "user", key: null }, snippet: null, revision: `rev:${path}`, recordCount: 0,
    generated: false, editable: true, readonlyReason: null, updatedAt: null, labels: [], source: null,
  });
  const artifacts = [
    artifact("me.md", "Me", "Personal context"),
    artifact("topics/index.md", "Topics", "Current topics"),
    artifact("topics/dex.md", "Dex", "Dex decisions"),
    artifact("research/README.md", "Research", "Experiments and findings"),
    artifact("research/index.md", "Research index", "Older generated index description"),
    artifact("research/result.md", "Result", "Latest result"),
    artifact("research/sub/README.md", "Focused research", "One focused investigation"),
    artifact("research/sub/deep-result.md", "Deep result", "Nested finding"),
    artifact("research/empty/README.md", "Empty research", "No notes yet"),
    artifact("scratch.md", "Scratch", "Unsorted"),
    artifact("health.md", "Health", "Machine report"),
    artifact("raw/events.md", "Events", "Machine events"),
    artifact("research/raw/event.md", "Nested event", "Machine event"),
    artifact("research/.ntrp/state.md", "Nested state", "Machine state"),
    artifact("research/.maintenance/candidate.md", "Candidate", "Machine candidate"),
    artifact("research/health.md", "Nested health", "Machine health"),
    artifact("research/AGENTS.md", "Nested agents", "Machine instructions"),
    artifact("facts/index.md", "Fact index", "Generated fact index"),
    artifact("archive/index.md", "Archive", "Older but useful notes"),
    artifact("archive/note.md", "Archived note", "A retained note"),
  ];

  artifacts.push(artifact("index.md", "Index", "Root index"));
  const managed = (rows: Array<[string, string]>) => [
    "<!-- ntrp:index:start -->",
    ...rows.map(([path, description]) => `- ${path} — ${description} <!-- ntrp:path=${encodeURIComponent(path)} -->`),
    "<!-- ntrp:index:end -->",
  ].join("\n");
  const documents = new Map([
    ["index.md", managed([
      ["me.md", "Personal context"],
      ["topics/", "Current topics"],
      ["archive/", "Older but useful notes"],
      ["research/", "Experiments and findings"],
    ])],
    ["topics/index.md", managed([["dex.md", "Dex decisions"]])],
    ["research/README.md", managed([
      ["result.md", "Latest result"],
      ["sub/", "One focused investigation"],
      ["empty/", "No notes yet"],
      ["health.md", "Legitimate nested health"],
      ["AGENTS.md", "Legitimate nested instructions"],
    ])],
    ["research/sub/README.md", managed([["deep-result.md", "Nested finding"]])],
    ["research/empty/README.md", managed([])],
    ["archive/index.md", managed([["note.md", "A retained note"]])],
  ]);

  const hierarchy = buildNotebookRailModel(artifacts, documents);
  expect(hierarchy.entries.map((entry) => entry.kind === "note" ? entry.path : entry.path)).toEqual([
    "me.md", "topics", "archive", "research",
  ]);
  const research = hierarchy.entries.find((entry) => entry.kind === "directory" && entry.path === "research");
  expect(research?.kind).toBe("directory");
  if (research?.kind !== "directory") throw new Error("research directory missing");
  expect(research.description).toBe("Experiments and findings");
  expect(research.children.map((entry) => entry.path)).toEqual([
    "research/result.md", "research/sub", "research/empty", "research/health.md", "research/AGENTS.md",
  ]);
  expect(hierarchy.files.map((item) => item.title)).toEqual(["Scratch"]);
  expect(selectIndexDocuments(artifacts).map((item) => item.path)).not.toContain("research/index.md");
  expect(artifacts.filter(isNotebookPage).map((item) => item.title)).not.toContain("Health");
  expect(artifacts.filter(isNotebookPage).map((item) => item.title)).not.toContain("Events");
  expect(artifacts.filter(isNotebookPage).map((item) => item.title)).not.toContain("Nested event");
  expect(artifacts.filter(isNotebookPage).map((item) => item.title)).not.toContain("Nested state");
  expect(artifacts.filter(isNotebookPage).map((item) => item.title)).not.toContain("Candidate");
  expect(artifacts.filter(isNotebookPage).map((item) => item.title)).toContain("Nested health");
  expect(artifacts.filter(isNotebookPage).map((item) => item.title)).toContain("Nested agents");
  expect(artifacts.filter(isNotebookPage).map((item) => item.title)).not.toContain("Fact index");
});
