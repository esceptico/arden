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

test("an older snapshot cannot replace a newer committed snapshot", () => {
  const cache = new MemoryPageCache();
  const older = cache.beginRead(configA);
  const newer = cache.beginRead(configA);
  const renamed = page({ path: "topics/renamed.md", version: "page-a:v2" });

  expect(cache.commitSnapshot(newer, [renamed])).toBe(true);
  expect(cache.commitSnapshot(older, [page()])).toBe(false);
  expect(cache.getByPath(configA, renamed.path)).toBe(renamed);
  expect(cache.getByPath(configA, "topics/a.md")).toBeUndefined();
});

test("an older full snapshot merges unrelated pages around a newer point read", () => {
  const cache = new MemoryPageCache();
  const a = page();
  const b = page({ pageId: "page-b", path: "topics/b.md", title: "B" });
  cache.commitSnapshot(cache.beginRead(configA), [a, b]);
  const fullRead = cache.beginRead(configA);
  const pointRead = cache.beginRead(configA);
  const freshA = { ...a, version: "page-a:v2" };
  const renamedB = { ...b, path: "topics/renamed-b.md", version: "page-b:v2" };

  expect(cache.commitPage(pointRead, freshA)).toBe(true);
  expect(cache.commitSnapshot(fullRead, [a, renamedB])).toBe(true);
  expect(cache.getById(configA, a.pageId)).toBe(freshA);
  expect(cache.getByPath(configA, renamedB.path)).toBe(renamedB);
  expect(cache.getByPath(configA, b.path)).toBeUndefined();
});

test("older snapshots preserve newer mutations and tombstones while updating other pages", () => {
  const cache = new MemoryPageCache();
  const a = page();
  const b = page({ pageId: "page-b", path: "topics/b.md", title: "B" });
  cache.commitSnapshot(cache.beginRead(configA), [a, b]);

  const beforeEdit = cache.beginRead(configA);
  const editedA = { ...a, path: "topics/edited-a.md", version: "page-a:v2" };
  const renamedB = { ...b, path: "topics/renamed-b.md", version: "page-b:v2" };
  cache.commitMutation(configA, editedA);
  expect(cache.commitSnapshot(beforeEdit, [a, renamedB])).toBe(true);
  expect(cache.getByPath(configA, editedA.path)).toBe(editedA);
  expect(cache.getByPath(configA, renamedB.path)).toBe(renamedB);

  const beforeArchive = cache.beginRead(configA);
  const renamedAgainB = { ...renamedB, path: "topics/final-b.md", version: "page-b:v3" };
  cache.commitRemoval(configA, editedA);
  expect(cache.commitSnapshot(beforeArchive, [editedA, renamedAgainB])).toBe(true);
  expect(cache.getById(configA, editedA.pageId)).toBeUndefined();
  expect(cache.getByPath(configA, renamedAgainB.path)).toBe(renamedAgainB);
});

test("one read can publish a snapshot then a page only until a newer read begins", () => {
  const cache = new MemoryPageCache();
  const read = cache.beginRead(configA);
  const detailed = page({ version: "page-a:v2" });

  expect(cache.commitSnapshot(read, [page()])).toBe(true);
  expect(cache.commitPage(read, detailed)).toBe(true);
  cache.beginRead(configA);
  expect(cache.commitPage(read, page())).toBe(false);
  expect(cache.getById(configA, detailed.pageId)).toBe(detailed);
});

test("path and page identity stay bijective through rename and reuse", () => {
  const cache = new MemoryPageCache();
  const first = page();
  cache.commitMutation(configA, first);
  cache.commitMutation(configA, { ...first, path: "topics/renamed.md" });
  expect(cache.getByPath(configA, first.path)).toBeUndefined();

  const replacement = page({ pageId: "page-b", path: "topics/renamed.md", title: "B" });
  cache.commitMutation(configA, replacement);
  expect(cache.getById(configA, first.pageId)).toBeUndefined();
  expect(cache.getByPath(configA, replacement.path)).toBe(replacement);

  cache.commitRemoval(configA, first);
  expect(cache.getByPath(configA, replacement.path)).toBe(replacement);
});

test("invalidation clears state and rejects reads started before an unknown mutation", () => {
  const cache = new MemoryPageCache();
  cache.commitMutation(configA, page());
  const stale = cache.beginRead(configA);
  cache.invalidate(configA);

  expect(cache.commitSnapshot(stale, [page()])).toBe(false);
  expect(cache.getById(configA, "page-a")).toBeUndefined();
  const refreshed = page({ path: "topics/refreshed.md" });
  expect(cache.commitSnapshot(cache.beginRead(configA), [refreshed])).toBe(true);
  expect(cache.getByPath(configA, refreshed.path)).toBe(refreshed);
});

test("aborted, foreign, and other-config reads cannot disturb committed state", () => {
  const cache = new MemoryPageCache();
  const kept = page();
  cache.commitMutation(configA, kept);
  const aborted = cache.beginRead(configA);
  const controller = new AbortController();
  controller.abort();

  expect(cache.commitSnapshot(aborted, [], controller.signal)).toBe(false);
  const foreign = new MemoryPageCache().beginRead(configA);
  expect(cache.commitSnapshot(foreign, [])).toBe(false);
  cache.commitMutation(configB, page({ path: "topics/b.md" }));
  expect(cache.getByPath(configA, kept.path)).toBe(kept);
  expect(cache.getByPath(configB, "topics/b.md")?.path).toBe("topics/b.md");
});
