import { afterEach, expect, test } from "bun:test";
import { ApiError, type AppConfig } from "@/api/core";
import {
  applyPageEdit,
  archiveMemoryArtifact,
  getPageHistory,
  getPageLinks,
  listMemoryArtifactSummaries,
  previewPageEdit,
  readMemoryArtifactDetail,
  rebuildMemoryArtifactSummaries,
} from "@/api/memoryArtifacts";
import {
  installCanonicalMemoryBridge,
  type WikiPageFixture,
} from "./helpers/canonicalMemoryBridge";

const originalDesktop = window.ardenDesktop;
let configSequence = 0;

function config(): AppConfig {
  configSequence += 1;
  return { serverUrl: "http://localhost:6877", apiKey: `test-key-${configSequence}` };
}

function page(overrides: Partial<WikiPageFixture> = {}): WikiPageFixture {
  return {
    pageId: "page-a",
    path: "topics/a.md",
    title: "A",
    content: "---\ntitle: A\n---\n# A\n\nOriginal\n",
    version: "page-a:v1",
    repositoryHead: "wiki-head-1",
    metadata: {
      summary: "A short summary",
      labels: ["work"],
      fact_citations: [{ fact_id: "fact-1", version: "fact-1:v1" }],
    },
    ...overrides,
  };
}

afterEach(() => {
  window.ardenDesktop = originalDesktop;
});

test("canonical wiki pages map to the established notebook summary and detail contract", async () => {
  const currentConfig = config();
  installCanonicalMemoryBridge({
    pages: [page()],
    facts: [{
      factId: "fact-1",
      text: "A durable fact",
      labels: ["pinned"],
      createdAt: "2026-07-12T10:00:00Z",
      version: "fact-1:v1",
    }],
  });

  const listed = await listMemoryArtifactSummaries(currentConfig);
  expect(listed).toEqual({
    artifacts: [{
      path: "topics/a.md",
      title: "A",
      kind: "topic",
      type: "file",
      directory: "topics",
      scope: { kind: "global", key: null },
      snippet: null,
      summary: "A short summary",
      revision: "page-a:v1",
      recordCount: 1,
      generated: false,
      editable: true,
      readonlyReason: null,
      updatedAt: "2026-07-13T08:00:00Z",
      createdAt: "2026-07-13T08:00:00Z",
      labels: ["work"],
      source: "wiki",
      pageId: "page-a",
      repositoryHead: "wiki-head-1",
    }],
    directories: ["topics"],
  });

  const { artifact } = await readMemoryArtifactDetail(currentConfig, "topics/a.md");
  expect(artifact.content).toBe("# A\n\nOriginal\n");
  expect(artifact.editableContent).toBe(page().content);
  expect(artifact.timeline).toEqual([{
    id: "fact-1",
    text: "A durable fact",
    kind: "fact",
    date: "2026-07-12",
    src: "fact-1:v1",
    pinned: true,
    superseded: false,
  }]);
  expect(artifact.frontmatter).toMatchObject({
    page_id: "page-a",
    title: "A",
    labels: ["work"],
  });
});

test("rebuild is a canonical list refresh, not a legacy rebuild command", async () => {
  const currentConfig = config();
  const bridge = installCanonicalMemoryBridge({ pages: [page()] });

  const rebuilt = await rebuildMemoryArtifactSummaries(currentConfig);

  expect(rebuilt.artifacts[0]?.path).toBe("topics/a.md");
  expect(bridge.requests.map(({ method, path }) => ({ method, path }))).toEqual([
    { method: "GET", path: "/admin/wiki/pages" },
  ]);
});

test("edit review compares exact bytes and applies through canonical optimistic concurrency", async () => {
  const currentConfig = config();
  const bridge = installCanonicalMemoryBridge({ pages: [page()] });
  const candidate = "---\ntitle: A\n---\n# A\n\nChanged\n";

  const preview = await previewPageEdit(currentConfig, {
    path: "topics/a.md",
    baseRevision: "page-a:v1",
    content: candidate,
  });
  expect(preview).toMatchObject({
    path: "topics/a.md",
    baseRevision: "page-a:v1",
    operations: [],
    questions: [],
    analysisPending: false,
  });
  expect(preview.patch).toContain("-Original");
  expect(preview.patch).toContain("+Changed");

  const applied = await applyPageEdit(currentConfig, {
    previewId: preview.id,
    decisions: {},
  });

  expect(applied.revision).toBe("page-a:v2");
  expect(applied.event).toMatchObject({
    eventType: "PAGE_EDIT",
    actor: "user:desktop",
    origin: "desktop",
    path: "topics/a.md",
    baseRevision: "page-a:v1",
    resultRevision: "page-a:v2",
    reconciliation: "applied",
  });
  expect(bridge.requests.at(-1)).toEqual({
    method: "PUT",
    path: "/admin/wiki/pages/page-a",
    body: {
      content: candidate,
      expected_version: "page-a:v1",
      expected_head: "wiki-head-1",
    },
  });
});

test("edit preview reports a structured conflict before any write", async () => {
  const currentConfig = config();
  const bridge = installCanonicalMemoryBridge({ pages: [page()] });

  const error = await previewPageEdit(currentConfig, {
    path: "topics/a.md",
    baseRevision: "stale",
    content: "candidate",
  }).catch((reason) => reason);

  expect(error).toBeInstanceOf(ApiError);
  expect(error.status).toBe(409);
  expect(error.data).toMatchObject({
    detail: {
      error: "page_revision_conflict",
      current_revision: "page-a:v1",
      current_head: "wiki-head-1",
    },
  });
  expect(bridge.requests.some(({ method }) => method === "PUT")).toBe(false);
});

test("history and diffs come from canonical revision commits", async () => {
  const currentConfig = config();
  installCanonicalMemoryBridge({
    pages: [page()],
    history: {
      "page-a": [{
        commitId: "commit-2",
        parentId: "commit-1",
        actor: "user:desktop",
        origin: "desktop",
        reason: "edit wiki page",
        timestamp: "2026-07-13T08:01:00Z",
        changes: [{
          action: "update",
          before: { resourceId: "page-a", path: "topics/a.md", state: "active", versionId: "page-a:v1" },
          after: { resourceId: "page-a", path: "topics/a.md", state: "active", versionId: "page-a:v2" },
        }],
        diff: "@@ -1 +1 @@\n-Original\n+Changed\n",
      }],
    },
  });

  const history = await getPageHistory(currentConfig, { path: "topics/a.md", limit: 10 });

  expect(history).toMatchObject({ total: 1, limit: 10, nextBeforeSequence: null });
  expect(history.events[0]).toMatchObject({
    id: "commit-2",
    actor: "user:desktop",
    path: "topics/a.md",
    baseRevision: "page-a:v1",
    resultRevision: "page-a:v2",
    patch: "@@ -1 +1 @@\n-Original\n+Changed\n",
  });
});

test("canonical links retain resolution and map identities back to notebook paths", async () => {
  const currentConfig = config();
  installCanonicalMemoryBridge({
    pages: [
      page(),
      page({ pageId: "page-b", path: "projects/b.md", title: "B", version: "page-b:v1" }),
    ],
    links: {
      "page-a": {
        outgoing: [{
          sourcePageId: "page-a",
          target: "B",
          alias: "the project",
          heading: "Plan",
          status: "resolved",
          targetPageId: "page-b",
          candidates: ["page-b"],
        }],
      },
    },
  });

  const links = await getPageLinks(currentConfig, { path: "topics/a.md" });

  expect(links.outgoing).toEqual([{
    sourcePath: "topics/a.md",
    target: "B",
    display: "the project",
    heading: "Plan",
    context: "[[B|the project]]",
    line: 0,
    column: 0,
    status: "resolved",
    resolvedPath: "projects/b.md",
    candidates: ["projects/b.md"],
    sourceRevision: "page-a:v1",
  }]);
});

test("malformed metadata and missing cited facts surface instead of disappearing", async () => {
  const malformedConfig = config();
  installCanonicalMemoryBridge({
    pages: [page({ metadata: { labels: ["valid", 7] } })],
  });
  const malformed = await listMemoryArtifactSummaries(malformedConfig).catch((reason) => reason);
  expect(malformed).toBeInstanceOf(Error);
  expect((malformed as Error).message).toBe("Wiki page metadata.labels must be a string array");

  const missingFactConfig = config();
  installCanonicalMemoryBridge({
    pages: [page({ metadata: { fact_citations: [{ fact_id: "missing", version: "missing:v1" }] } })],
  });
  const missing = await readMemoryArtifactDetail(missingFactConfig, "topics/a.md").catch((reason) => reason);
  expect(missing).toBeInstanceOf(ApiError);
  expect(missing.status).toBe(404);
});

test("archive uses canonical identity and both version guards", async () => {
  const currentConfig = config();
  const bridge = installCanonicalMemoryBridge({ pages: [page()] });

  await archiveMemoryArtifact(currentConfig, {
    path: "topics/a.md",
    baseRevision: "page-a:v1",
  });

  expect(bridge.requests.at(-1)).toEqual({
    method: "POST",
    path: "/admin/wiki/pages/page-a/archive",
    body: {
      expected_version: "page-a:v1",
      expected_head: "wiki-head-1",
    },
  });
});

test("artifact APIs reject non-canonical paths before transport", async () => {
  const currentConfig = config();
  const bridge = installCanonicalMemoryBridge();
  for (const path of ["", "/absolute.md", "../escape.md", "topics/./a.md", "topics//a.md", "topics\\a.md", "C:/a.md"]) {
    const error = await readMemoryArtifactDetail(currentConfig, path).catch((reason) => reason);
    expect(error).toBeInstanceOf(Error);
    expect((error as Error).message).toBe("Invalid memory artifact path");
  }
  expect(bridge.requests).toEqual([]);
});
