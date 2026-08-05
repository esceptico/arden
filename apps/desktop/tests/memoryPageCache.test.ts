import { expect, test } from "bun:test";
import type { AppConfig } from "@/api/core";
import { MemoryPageCache } from "@/api/memoryPageCache";
import type { WikiPageSummary } from "@/api/wiki";

const configA: AppConfig = { serverUrl: "http://arden.test", apiKey: "a" };
const configB: AppConfig = { serverUrl: "http://arden.test", apiKey: "b" };

function page(overrides: Partial<WikiPageSummary> = {}): WikiPageSummary {
  return {
    pageId: "page-a",
    path: "topics/a.md",
    resourceState: "active",
    title: "A",
    excerpt: null,
    aliases: [],
    lifecycle: "active",
    redirectTo: null,
    metadata: {},
    version: "page-a:v1",
    repositoryHead: "head-1",
    createdAt: null,
    updatedAt: null,
    ...overrides,
  };
}

test("only the latest read can replace a snapshot", () => {
  const cache = new MemoryPageCache();
  const older = cache.beginRead(configA);
  const newer = cache.beginRead(configA);
  const renamed = page({ path: "topics/renamed.md", version: "page-a:v2" });

  expect(cache.commitSnapshot(newer, "head-1", [renamed])).toBe(true);
  expect(cache.commitSnapshot(older, "head-1", [page()])).toBe(false);
  expect(cache.getByPath(configA, renamed.path)).toBe(renamed);
  expect(cache.getByPath(configA, "topics/a.md")).toBeUndefined();
});

test("one read can publish its snapshot and detail", () => {
  const cache = new MemoryPageCache();
  const read = cache.beginRead(configA);
  const detailed = page({ version: "page-a:v2" });

  expect(cache.commitSnapshot(read, "head-1", [page()])).toBe(true);
  expect(cache.commitPage(read, detailed)).toBe(true);
  expect(cache.getById(configA, detailed.pageId)).toBe(detailed);
});

test("a newer read rejects an older point response", () => {
  const cache = new MemoryPageCache();
  const older = cache.beginRead(configA);
  cache.beginRead(configA);

  expect(cache.commitPage(older, page())).toBe(false);
  expect(cache.getById(configA, "page-a")).toBeUndefined();
});

test("a head change drops pages from the previous snapshot", () => {
  const cache = new MemoryPageCache();
  const first = page();
  const second = page({ pageId: "page-b", path: "topics/b.md", title: "B" });
  cache.commitSnapshot(cache.beginRead(configA), "head-1", [first, second]);

  const changed = { ...first, version: "page-a:v2", repositoryHead: "head-2" };
  cache.commitMutation(configA, changed);

  expect(cache.getById(configA, changed.pageId)).toBe(changed);
  expect(cache.getById(configA, second.pageId)).toBeUndefined();
});

test("path and page identity stay bijective", () => {
  const cache = new MemoryPageCache();
  const first = page();
  cache.commitMutation(configA, first);

  const renamed = { ...first, path: "topics/renamed.md" };
  const read = cache.beginRead(configA);
  expect(cache.commitPage(read, renamed)).toBe(true);
  expect(cache.getByPath(configA, first.path)).toBeUndefined();

  const replacement = page({ pageId: "page-b", path: renamed.path, title: "B" });
  expect(cache.commitPage(read, replacement)).toBe(true);
  expect(cache.getById(configA, first.pageId)).toBeUndefined();
  expect(cache.getByPath(configA, replacement.path)).toBe(replacement);
});

test("snapshot identity and head conflicts fail explicitly", () => {
  const cache = new MemoryPageCache();
  expect(() => cache.commitSnapshot(cache.beginRead(configA), "head-2", [page()]))
    .toThrow("Wiki snapshot head mismatch");

  const duplicate = page({ pageId: "page-b" });
  expect(() => cache.commitSnapshot(cache.beginRead(configA), "head-1", [page(), duplicate]))
    .toThrow("Duplicate wiki page path");
});

test("invalidation clears state and rejects older reads", () => {
  const cache = new MemoryPageCache();
  cache.commitMutation(configA, page());
  const stale = cache.beginRead(configA);
  cache.invalidate(configA);

  expect(cache.commitSnapshot(stale, "head-1", [page()])).toBe(false);
  expect(cache.getById(configA, "page-a")).toBeUndefined();
});

test("aborted, foreign, and other-config reads cannot disturb state", () => {
  const cache = new MemoryPageCache();
  const kept = page();
  cache.commitMutation(configA, kept);
  const aborted = cache.beginRead(configA);
  const controller = new AbortController();
  controller.abort();

  expect(cache.commitSnapshot(aborted, "head-1", [], controller.signal)).toBe(false);
  const foreign = new MemoryPageCache().beginRead(configA);
  expect(cache.commitSnapshot(foreign, "head-1", [])).toBe(false);
  cache.commitMutation(configB, page({ path: "topics/b.md" }));
  expect(cache.getByPath(configA, kept.path)).toBe(kept);
  expect(cache.getByPath(configB, "topics/b.md")?.path).toBe("topics/b.md");
});
