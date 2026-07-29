import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { AppConfig } from "@/api/core";
import { getPageHistory } from "@/api/memoryArtifacts";
import { ArtifactMemoryView } from "@/features/memory/components/ArtifactMemoryView";
import {
  canRestorePageEdit,
  MemoryDiffOverlay,
} from "@/features/memory/components/MemoryDiffOverlay";
import type { PageEditEvent } from "@/features/memory/lib/notebookTypes";
import {
  error,
  installCanonicalMemoryBridge,
  ok,
  type CanonicalMemoryBridgeOptions,
  type HistoryCommitFixture,
  type WikiPageFixture,
} from "./helpers/canonicalMemoryBridge";

const originalDesktop = window.ardenDesktop;
const roots = new Set<Root>();
let configSequence = 0;

function config(): AppConfig {
  configSequence += 1;
  return { serverUrl: "http://localhost:6877", apiKey: `restore-test-${configSequence}` };
}

function page(overrides: Partial<WikiPageFixture> = {}): WikiPageFixture {
  return {
    pageId: "page-a",
    path: "topics/a.md",
    title: "Maintained A",
    content: "---\ntitle: Maintained A\n---\n# Maintained A\n\nMaintained notes.\n",
    version: "note-r2",
    repositoryHead: "maintenance-2",
    metadata: {},
    ...overrides,
  };
}

function rawPage(value: WikiPageFixture) {
  return {
    page_id: value.pageId,
    path: value.path,
    resource_state: value.resourceState ?? "active",
    title: value.title,
    aliases: value.aliases ?? [],
    lifecycle: value.lifecycle ?? "active",
    redirect_to: value.redirectTo ?? null,
    metadata: value.metadata ?? {},
    version: value.version,
    repository_head: value.repositoryHead,
    created_at: value.createdAt ?? "2026-07-13T08:00:00Z",
    updated_at: value.updatedAt ?? "2026-07-13T08:02:00Z",
    content: value.content,
  };
}

const maintenanceCommit: HistoryCommitFixture = {
  commitId: "maintenance-2",
  parentId: "commit-1",
  actor: "agent:wiki-maintenance",
  origin: "wiki.maintenance",
  reason: "clarify project status",
  timestamp: "2026-07-13T08:01:00Z",
  changes: [{
    action: "update",
    before: { resourceId: "page-a", path: "topics/a.md", state: "active", versionId: "note-r1" },
    after: { resourceId: "page-a", path: "topics/a.md", state: "active", versionId: "note-r2" },
  }],
  diff: "@@ -1 +1 @@\n-Original notes.\n+Maintained notes.\n",
};

function event(overrides: Partial<PageEditEvent> = {}): PageEditEvent {
  return {
    eventType: "PAGE_EDIT",
    id: "maintenance-2",
    occurredAt: "2026-07-13T08:01:00Z",
    sequence: 1,
    actor: "agent:wiki-maintenance",
    origin: "external",
    rawOrigin: "wiki.maintenance",
    reason: "clarify project status",
    path: "topics/a.md",
    baseRevision: "note-r1",
    resultRevision: "note-r2",
    patch: maintenanceCommit.diff ?? "",
    operations: [],
    reconciliation: "applied",
    analysis: null,
    reconcilesEventId: null,
    reviewOperations: [],
    questions: [],
    reviewEventId: null,
    observationId: null,
    sourceCanonicalRevision: "maintenance-2",
    ...overrides,
  };
}

function setup(appRoot = false) {
  const host = document.createElement("div");
  if (appRoot) host.id = "app";
  document.body.append(host);
  const root = createRoot(host);
  roots.add(root);
  return { host, root };
}

async function settle(delay = 0) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, delay));
  });
}

async function renderView(currentConfig: AppConfig) {
  localStorage.setItem("arden.desktop.memory.inspectorOpen", "false");
  localStorage.setItem("arden.desktop.memory.lastPath", "topics/a.md");
  const view = setup(true);
  await act(async () => view.root.render(<ArtifactMemoryView config={currentConfig} />));
  await settle(300);
  return view;
}

async function openMaintenanceDiff(host: HTMLElement) {
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Open activity"]')!.click());
  await settle(300);
  await act(async () => host.querySelector<HTMLButtonElement>(".mw-rec button")!.click());
  await settle(100);
}

afterEach(async () => {
  for (const root of roots) await act(async () => root.unmount());
  roots.clear();
  window.ardenDesktop = originalDesktop;
  document.body.replaceChildren();
  localStorage.removeItem("arden.desktop.memory.inspectorOpen");
  localStorage.removeItem("arden.desktop.memory.lastPath");
});

test("history keeps the canonical Maintenance origin and recorded reason", async () => {
  const currentConfig = config();
  installCanonicalMemoryBridge({
    pages: [page()],
    history: { "page-a": [maintenanceCommit] },
  });

  const history = await getPageHistory(currentConfig, { path: "topics/a.md" });

  expect(history.events[0]).toMatchObject({
    origin: "external",
    rawOrigin: "wiki.maintenance",
    reason: "clarify project status",
  });
});

test("Restore appears only for the exact latest Maintenance edit and Close is unchanged", async () => {
  const { root } = setup();
  const openChanges: boolean[] = [];
  const render = async (value: PageEditEvent, currentPageVersion: string) => {
    await act(async () => root.render(
      <MemoryDiffOverlay
        event={value}
        path={value.path}
        open
        currentPageVersion={currentPageVersion}
        onRestore={() => {}}
        onOpenChange={(open) => openChanges.push(open)}
        onExitComplete={() => {}}
      />,
    ));
  };

  await render(event(), "note-r2");
  expect(document.body.textContent).toContain("clarify project status");
  expect(Array.from(document.querySelectorAll("button")).some((button) => button.textContent === "Restore")).toBe(true);
  expect(canRestorePageEdit(event(), "note-r2")).toBe(true);

  await render(event({ rawOrigin: "desktop" }), "note-r2");
  expect(Array.from(document.querySelectorAll("button")).some((button) => button.textContent === "Restore")).toBe(false);
  await render(event({ baseRevision: "" }), "note-r2");
  expect(Array.from(document.querySelectorAll("button")).some((button) => button.textContent === "Restore")).toBe(false);
  await render(event(), "note-r3");
  expect(Array.from(document.querySelectorAll("button")).some((button) => button.textContent === "Restore")).toBe(false);

  await act(async () => Array.from(document.querySelectorAll<HTMLButtonElement>("button"))
    .find((button) => button.textContent === "Close")!.click());
  expect(openChanges.at(-1)).toBe(false);
});

test("successful Restore publishes page, history, links, and caches together", async () => {
  const currentConfig = config();
  const commits: HistoryCommitFixture[] = [maintenanceCommit];
  const linkReport: NonNullable<CanonicalMemoryBridgeOptions["links"]> = {
    "page-a": { outgoing: [], backlinks: [] },
  };
  const restored = page({
    title: "Original A",
    content: "---\ntitle: Original A\n---\n# Original A\n\nOriginal notes.\n",
    version: "note-r3",
    repositoryHead: "restore-3",
  });
  const bridge = installCanonicalMemoryBridge({
    pages: [
      page(),
      page({
        pageId: "page-b",
        path: "topics/b.md",
        title: "B",
        content: "# B\n",
        version: "note-b-r1",
      }),
    ],
    history: { "page-a": commits },
    links: linkReport,
    onRequest: ({ path, method }, state) => {
      if (path !== "/admin/wiki/pages/page-a/history/maintenance-2/restore" || method !== "POST") return undefined;
      state.pages.set("page-a", restored);
      commits.unshift({
        commitId: "restore-3",
        parentId: "maintenance-2",
        actor: "user:desktop",
        origin: "desktop.restore",
        reason: "restore Maintenance commit maintenance-2",
        changes: [{
          action: "update",
          before: { resourceId: "page-a", path: "topics/a.md", state: "active", versionId: "note-r2" },
          after: { resourceId: "page-a", path: "topics/a.md", state: "active", versionId: "note-r3" },
        }],
        diff: "@@ -1 +1 @@\n-Maintained notes.\n+Original notes.\n",
      });
      linkReport["page-a"] = {
        outgoing: [{
          sourcePageId: "page-a",
          target: "B",
          status: "resolved",
          targetPageId: "page-b",
        }],
        backlinks: [],
      };
      return ok(rawPage(restored));
    },
  });
  const { host } = await renderView(currentConfig);
  await openMaintenanceDiff(host);

  await act(async () => Array.from(document.querySelectorAll<HTMLButtonElement>("button"))
    .find((button) => button.textContent === "Restore")!.click());
  await settle(450);

  const restoreIndex = bridge.requests.findIndex(({ path }) =>
    path === "/admin/wiki/pages/page-a/history/maintenance-2/restore");
  expect(bridge.requests[restoreIndex]).toMatchObject({
    method: "POST",
    body: { expected_version: "note-r2", expected_head: "maintenance-2" },
  });
  const refreshedPaths = bridge.requests.slice(restoreIndex + 1).map(({ path }) => path);
  expect(refreshedPaths).toContain("/admin/wiki/pages");
  expect(refreshedPaths).toContain("/admin/wiki/pages/page-a/history?limit=100");
  expect(refreshedPaths).toContain("/admin/wiki/pages/page-a/links");

  expect(document.querySelector('[role="dialog"][aria-label="Page edit diff"]')).toBeNull();
  const note = host.querySelector<HTMLElement>('[data-memory-note-path="topics/a.md"]')!;
  expect(note.textContent).toContain("Original notes.");
  expect(note.textContent).not.toContain("Maintained notes.");
  expect(host.querySelectorAll(".mw-rec")).toHaveLength(2);
  expect(host.querySelector<HTMLButtonElement>('button[aria-label="Open links"] .mw-instrument-count')?.textContent).toBe("1");
});

test("a 409 keeps the current page and diff visible", async () => {
  const currentConfig = config();
  installCanonicalMemoryBridge({
    pages: [page()],
    history: { "page-a": [maintenanceCommit] },
    onRequest: ({ path, method }) => (
      path === "/admin/wiki/pages/page-a/history/maintenance-2/restore" && method === "POST"
        ? error(409, {
          error: "page_revision_conflict",
          current_revision: "note-r3",
          current_content: "A newer edit",
          current_head: "wiki-head-3",
        })
        : undefined
    ),
  });
  const { host } = await renderView(currentConfig);
  await openMaintenanceDiff(host);

  await act(async () => Array.from(document.querySelectorAll<HTMLButtonElement>("button"))
    .find((button) => button.textContent === "Restore")!.click());
  await settle(100);

  const dialog = document.querySelector<HTMLElement>('[role="dialog"][aria-label="Page edit diff"]')!;
  expect(dialog).not.toBeNull();
  expect(dialog.querySelector('[role="alert"]')?.textContent).toBe("A newer edit prevents automatic restoration.");
  expect(dialog.textContent).toContain("Maintained notes.");
  expect(host.querySelector<HTMLElement>('[data-memory-note-path="topics/a.md"]')?.textContent).toContain("Maintained notes.");
});

test("a committed Restore remains successful when dependent refresh fails", async () => {
  const currentConfig = config();
  let restoredCommitted = false;
  const restored = page({
    title: "Original A",
    content: "---\ntitle: Original A\n---\n# Original A\n\nOriginal notes.\n",
    version: "note-r3",
    repositoryHead: "restore-3",
  });
  installCanonicalMemoryBridge({
    pages: [page()],
    history: { "page-a": [maintenanceCommit] },
    onRequest: ({ path, method }, state) => {
      if (path === "/admin/wiki/pages/page-a/history/maintenance-2/restore" && method === "POST") {
        restoredCommitted = true;
        state.pages.set("page-a", restored);
        return ok(rawPage(restored));
      }
      if (restoredCommitted && path === "/admin/wiki/pages/page-a/history?limit=100") {
        return error(503, "history unavailable");
      }
      return undefined;
    },
  });
  const { host } = await renderView(currentConfig);
  await openMaintenanceDiff(host);

  await act(async () => Array.from(document.querySelectorAll<HTMLButtonElement>("button"))
    .find((button) => button.textContent === "Restore")!.click());
  await settle(450);

  expect(document.querySelector('[role="dialog"][aria-label="Page edit diff"]')).toBeNull();
  expect(host.querySelector<HTMLElement>('[data-memory-note-path="topics/a.md"]')?.textContent).toContain("Original notes.");
  expect(host.textContent).toContain("Related page data refresh failed: history unavailable");
});
