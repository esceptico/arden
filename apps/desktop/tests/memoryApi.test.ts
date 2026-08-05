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
  readMemoryArtifactTimeline,
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

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((accept) => {
    resolve = accept;
  });
  return { promise, resolve };
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

  const response = await readMemoryArtifactDetail(currentConfig, "topics/a.md");
  const { artifact } = response;
  expect(artifact.content).toBe("# A\n\nOriginal\n");
  expect(artifact.editableContent).toBe(page().content);
  expect(artifact.timeline).toEqual([]);
  expect(await readMemoryArtifactTimeline(currentConfig, response.citations)).toEqual([{
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

test("citation timeline chunks batches at the server boundary and preserves order", async () => {
  const currentConfig = config();
  const facts = Array.from({ length: 101 }, (_, index) => ({
    factId: `fact-${index}`,
    text: `Fact ${index}`,
  }));
  const bridge = installCanonicalMemoryBridge({ facts });
  const refs = facts.map((fact, index) => ({ factId: fact.factId, version: `cited-${index}` }));

  const timeline = await readMemoryArtifactTimeline(currentConfig, refs);

  const batches = bridge.requests.filter((request) => request.path === "/admin/facts/batch");
  expect(batches.map((request) => (request.body as { fact_ids: string[] }).fact_ids.length)).toEqual([100, 1]);
  expect(timeline.map((entry) => entry.id)).toEqual(refs.map((ref) => ref.factId));
  expect(timeline.map((entry) => entry.src)).toEqual(refs.map((ref) => ref.version));
});

test("wiki health is visible through the notebook contract but remains read-only", async () => {
  const currentConfig = config();
  installCanonicalMemoryBridge({
    pages: [page({
      pageId: "health",
      path: "health.md",
      title: "Wiki health",
      content: "---\npage_id: health\ntitle: Wiki health\nlifecycle: active\n---\n# Wiki health\n",
      metadata: {},
    })],
  });

  const listed = await listMemoryArtifactSummaries(currentConfig);
  expect(listed.artifacts[0]).toMatchObject({
    path: "health.md",
    editable: false,
    readonlyReason: "Generated health report",
  });

  const { artifact } = await readMemoryArtifactDetail(currentConfig, "health.md");
  expect(artifact.editableContent).toBeNull();
});

test("a refreshed page list evicts renamed paths before the next detail read", async () => {
  const currentConfig = config();
  const bridge = installCanonicalMemoryBridge({ pages: [page({ metadata: {} })] });

  await listMemoryArtifactSummaries(currentConfig);
  bridge.updatePage(page({ path: "topics/renamed.md", metadata: {} }));
  const refreshed = await listMemoryArtifactSummaries(currentConfig);

  expect(refreshed.artifacts.map((artifact) => artifact.path)).toEqual(["topics/renamed.md"]);
  const oldPath = await readMemoryArtifactDetail(currentConfig, "topics/a.md").catch((reason) => reason);
  expect(oldPath).toBeInstanceOf(ApiError);
  expect(oldPath.status).toBe(404);
  expect((await readMemoryArtifactDetail(currentConfig, "topics/renamed.md")).artifact.path).toBe("topics/renamed.md");
});

test("an older list response cannot replace newer page cache state", async () => {
  const currentConfig = config();
  const olderGate = deferred();
  let listRequests = 0;
  const source = page({ pageId: "page-source", path: "source.md", title: "Source", metadata: {} });
  const original = page({ pageId: "page-target", path: "topics/original.md", title: "Target", metadata: {} });
  const renamed = { ...original, path: "topics/renamed.md", version: "page-target:v2" };
  const bridge = installCanonicalMemoryBridge({
    pages: [source, original],
    links: {
      "page-source": {
        outgoing: [{
          sourcePageId: "page-source",
          target: "Target",
          status: "resolved",
          targetPageId: "page-target",
        }],
      },
    },
    onRequest: async ({ path }) => {
      if (path !== "/admin/wiki/pages") return undefined;
      listRequests += 1;
      if (listRequests === 1) await olderGate.promise;
      return undefined;
    },
  });

  const older = listMemoryArtifactSummaries(currentConfig);
  bridge.updatePage(renamed);
  const newer = await listMemoryArtifactSummaries(currentConfig);
  expect(newer.artifacts.map((artifact) => artifact.path)).toContain("topics/renamed.md");

  bridge.updatePage(original);
  olderGate.resolve();
  const olderResult = await older;
  expect(olderResult.artifacts.map((artifact) => artifact.path)).toContain("topics/original.md");
  bridge.updatePage(renamed);

  const links = await getPageLinks(currentConfig, { path: "source.md" });
  expect(links.outgoing[0]?.resolvedPath).toBe("topics/renamed.md");
  const listsBeforeDetail = bridge.requests.filter(({ path }) => path === "/admin/wiki/pages").length;
  expect(listsBeforeDetail).toBe(2);
  expect((await readMemoryArtifactDetail(currentConfig, "topics/renamed.md")).artifact.path).toBe("topics/renamed.md");
  expect(bridge.requests.filter(({ path }) => path === "/admin/wiki/pages")).toHaveLength(listsBeforeDetail);
  expect(bridge.requests.at(-1)?.path).toBe("/admin/wiki/pages/page-target");
});

test("an older detail response cannot restore a path replaced by a newer list", async () => {
  const currentConfig = config();
  const detailGate = deferred();
  let holdDetail = false;
  const original = page({ metadata: {} });
  const renamed = { ...original, path: "topics/renamed.md", version: "page-a:v2" };
  const bridge = installCanonicalMemoryBridge({
    pages: [original],
    onRequest: async ({ path }) => {
      if (path === "/admin/wiki/pages/page-a" && holdDetail) {
        holdDetail = false;
        await detailGate.promise;
      }
      return undefined;
    },
  });
  await listMemoryArtifactSummaries(currentConfig);

  holdDetail = true;
  const olderDetail = readMemoryArtifactDetail(currentConfig, original.path);
  await Promise.resolve();
  bridge.updatePage(renamed);
  await listMemoryArtifactSummaries(currentConfig);

  bridge.updatePage(original);
  detailGate.resolve();
  expect((await olderDetail).artifact.path).toBe(original.path);
  bridge.updatePage(renamed);

  const listsBeforeDetail = bridge.requests.filter(({ path }) => path === "/admin/wiki/pages").length;
  expect(listsBeforeDetail).toBe(2);
  expect((await readMemoryArtifactDetail(currentConfig, renamed.path)).artifact.path).toBe(renamed.path);
  expect(bridge.requests.filter(({ path }) => path === "/admin/wiki/pages")).toHaveLength(listsBeforeDetail);
});

test("a stale full list cannot overwrite a newer detail cache entry", async () => {
  const currentConfig = config();
  const listGate = deferred();
  let holdNextList = false;
  const originalA = page({ metadata: {} });
  const freshA = { ...originalA, path: "topics/fresh-a.md", version: "page-a:v2" };
  const originalB = page({ pageId: "page-b", path: "topics/b.md", title: "B", metadata: {} });
  const renamedB = { ...originalB, path: "topics/renamed-b.md", version: "page-b:v2" };
  const bridge = installCanonicalMemoryBridge({
    pages: [originalA, originalB],
    onRequest: async ({ path }) => {
      if (path === "/admin/wiki/pages" && holdNextList) {
        holdNextList = false;
        await listGate.promise;
      }
      return undefined;
    },
  });
  await listMemoryArtifactSummaries(currentConfig);

  holdNextList = true;
  const fullList = listMemoryArtifactSummaries(currentConfig);
  await Promise.resolve();
  bridge.updatePage(freshA);
  expect((await readMemoryArtifactDetail(currentConfig, originalA.path)).artifact.path).toBe(freshA.path);

  bridge.updatePage(originalA);
  bridge.updatePage(renamedB);
  listGate.resolve();
  expect((await fullList).artifacts.map((artifact) => artifact.path)).toContain(renamedB.path);
  bridge.updatePage(freshA);

  expect((await readMemoryArtifactDetail(currentConfig, freshA.path)).artifact.path).toBe(freshA.path);
  expect((await readMemoryArtifactDetail(currentConfig, renamedB.path)).artifact.path).toBe(renamedB.path);
  // The stale snapshot is rejected as one coherent revision. Resolving B therefore
  // performs one fresh list read instead of merging rows from incompatible heads.
  expect(bridge.requests.filter(({ path }) => path === "/admin/wiki/pages")).toHaveLength(3);
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
        }],
      },
    },
  });

  const links = await getPageLinks(currentConfig, { path: "topics/a.md" });

  // The target is the link exactly as written in the source — page and
  // fragment rejoined — because that is what the renderer matches against.
  expect(links.outgoing).toEqual([{
    sourcePath: "topics/a.md",
    target: "B#Plan",
    display: "the project",
    heading: "Plan",
    context: "[[B#Plan|the project]]",
    line: 0,
    column: 0,
    status: "resolved",
    resolvedPath: "projects/b.md",
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
  const missingPage = await readMemoryArtifactDetail(missingFactConfig, "topics/a.md");
  const missing = await readMemoryArtifactTimeline(missingFactConfig, missingPage.citations).catch((reason) => reason);
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

  const archived = await readMemoryArtifactDetail(currentConfig, "topics/a.md").catch((reason) => reason);
  expect(archived).toBeInstanceOf(ApiError);
  expect(archived.status).toBe(404);
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
