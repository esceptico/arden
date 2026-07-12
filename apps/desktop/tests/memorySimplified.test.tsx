import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { buildNotebookSections, isNotebookArtifact } from "@/features/memory/components/NotebookRail";
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

test("notebook hierarchy keeps described directories semantic and machine pages hidden", () => {
  const artifact = (path: string, title: string, summary: string): MemoryArtifactSummary => ({
    path, title, summary, kind: "topic", type: "file", directory: path.includes("/") ? path.split("/")[0]! : "",
    scope: { kind: "user", key: null }, snippet: null, revision: `rev:${path}`, recordCount: 0,
    generated: false, editable: true, readonlyReason: null, updatedAt: null, labels: [], source: null,
  });
  const artifacts = [
    artifact("me.md", "Me", "Personal context"),
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

  const hierarchy = buildNotebookSections(artifacts);

  expect(hierarchy.sections.map((section) => section.title)).toEqual(["Memory", "Topics", "Archive", "Research", "Focused research"]);
  expect(hierarchy.sections.find((section) => section.title === "Archive")?.description).toBe("Older but useful notes");
  expect(hierarchy.sections.find((section) => section.title === "Archive")?.artifacts.map((item) => item.title)).toEqual(["Archived note"]);
  expect(hierarchy.sections.find((section) => section.title === "Research")?.description).toBe("Experiments and findings");
  expect(hierarchy.sections.find((section) => section.title === "Research")?.artifacts.map((item) => item.title)).toEqual(["Result"]);
  expect(hierarchy.sections.find((section) => section.title === "Focused research")?.artifacts.map((item) => item.title)).toEqual(["Deep result"]);
  expect(hierarchy.sections.some((section) => section.title === "Empty research")).toBe(false);
  expect(hierarchy.files.map((item) => item.title)).toEqual(["Scratch"]);
  expect(artifacts.filter(isNotebookArtifact).map((item) => item.title)).not.toContain("Health");
  expect(artifacts.filter(isNotebookArtifact).map((item) => item.title)).not.toContain("Events");
  expect(artifacts.filter(isNotebookArtifact).map((item) => item.title)).not.toContain("Nested event");
  expect(artifacts.filter(isNotebookArtifact).map((item) => item.title)).not.toContain("Nested state");
  expect(artifacts.filter(isNotebookArtifact).map((item) => item.title)).not.toContain("Candidate");
  expect(artifacts.filter(isNotebookArtifact).map((item) => item.title)).not.toContain("Nested health");
  expect(artifacts.filter(isNotebookArtifact).map((item) => item.title)).not.toContain("Nested agents");
  expect(artifacts.filter(isNotebookArtifact).map((item) => item.title)).not.toContain("Fact index");
});
