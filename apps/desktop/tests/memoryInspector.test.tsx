import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { AppConfig } from "@/api/core";
import { ArtifactMemoryView } from "@/features/memory/components/ArtifactMemoryView";
import { loadMemoryInspectorPane, MemoryInspector } from "@/features/memory/components/MemoryInspector";
import type { MemoryArtifactDetail, PageEditEvent, PageEditHistory, PageLinks } from "@/features/memory/lib/notebookTypes";

const roots = new Set<Root>();
const originalDesktop = window.ardenDesktop;
const INSPECTOR_OPEN_KEY = "arden.desktop.memory.inspectorOpen";
const CTX_PANE_KEY = "arden.desktop.memory.ctxPane";
const viewConfig: AppConfig = { serverUrl: "http://localhost:6877", apiKey: "test-key" };

function setup() {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  roots.add(root);
  return { host, root };
}

async function settle(delay = 0) {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, delay)); });
}

/** TabPanels preserves the mock's sequential 160ms exit + 260ms enter. */
async function settlePanelSwap() {
  await settle(450);
}

function response(data: unknown) {
  return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data, text: "" };
}

function rawArtifact(path: string, title: string, content = "") {
  return {
    path, title, kind: "topic", type: "file", directory: "",
    scope: { kind: "user", key: null }, content, snippet: null, summary: null,
    revision: "sha256:1", record_count: 0, generated: false, editable: true,
    editable_content: content, readonly_reason: null, updated_at: "2026-07-13T08:00:00Z",
    labels: [], source: null, timeline: [], frontmatter: {},
  };
}

function installViewBridge() {
  const index = rawArtifact("index.md", "Index", "Index body");
  const note = rawArtifact("note.md", "Note", "Note body");
  window.ardenDesktop = { api: { request: async (_config, request) => {
    if (request.path === "/admin/memory/artifacts") return response({ artifacts: [index, note] });
    if (request.path.startsWith("/admin/memory/links")) return response({ path: "note.md", revision: "ledger:1", stale: false, outgoing: [], backlinks: [], unlinked: [], total_outgoing: 0, total_backlinks: 0, limit: 100, offset: 0 });
    if (request.path.startsWith("/admin/memory/page-edits/history")) return response({ events: [], total: 0, limit: 100, next_before_sequence: null });
    const path = decodeURIComponent(request.path.replace("/admin/memory/artifacts/", ""));
    return response({ artifact: path === "index.md" ? index : note });
  } } } as Window["ardenDesktop"];
}

afterEach(async () => {
  for (const root of roots) await act(async () => root.unmount());
  roots.clear();
  window.ardenDesktop = originalDesktop;
  document.body.replaceChildren();
  for (const key of [
    INSPECTOR_OPEN_KEY,
    CTX_PANE_KEY,
    "arden.desktop.memory.lastPath",
    "arden.desktop.memory.pins",
    "arden.desktop.memory.rail.collapsed",
  ]) localStorage.removeItem(key);
});

const page: MemoryArtifactDetail = {
  path: "topics/dex.md", title: "Dex", kind: "topic", type: "file", directory: "topics",
  scope: { kind: "user", key: null }, snippet: null, summary: null, revision: "page:r2", recordCount: 1,
  generated: false, editable: true, readonlyReason: null, updatedAt: "2026-07-13T08:00:00Z", createdAt: null,
  labels: [], source: null,
  content: "# Dex\n\n## Work\n\nBody text.\n\n```\n## Not a heading\n```\n\n### Sub\n\nMore.\n",
  editableContent: "Dex", timeline: [], frontmatter: {},
};

const backlink = (line: number, context: string) => ({
  sourcePath: "daily/2026-07-13.md", target: "Dex", display: "Dex", alias: null, heading: "Morning", context,
  line, column: 12, status: "resolved" as const, resolvedPath: page.path, candidates: [page.path], sourceRevision: "ledger:r2",
});

const links: PageLinks = {
  path: page.path, revision: "ledger:r2", stale: false, totalOutgoing: 1, totalBacklinks: 3, limit: 100, offset: 0,
  outgoing: [{ sourcePath: page.path, target: "Roadmap", display: "Roadmap", alias: null, heading: "Work", context: "See [[Roadmap]] next", line: 4, column: 5, status: "resolved", resolvedPath: "roadmap.md", candidates: ["roadmap.md"], sourceRevision: "ledger:r2" }],
  backlinks: [
    backlink(8, "Worked on [[Dex]]"),
    backlink(9, "Shipped **[[Dex]]** review"),
    backlink(10, "A third [[Dex]] mention"),
  ],
  unlinked: [{ sourcePath: "scratch.md", context: "dex mentioned in passing" }],
};

const PATCH = "--- a/topics/dex.md\n+++ b/topics/dex.md\n@@ -1 +1 @@\n-old line\n+new line";

function event(id: string, sequence: number, actor = "user:desktop"): PageEditEvent {
  return {
    eventType: "PAGE_EDIT", id, occurredAt: `2026-07-13T08:20:${String(10 + sequence).slice(-2)}.123+04:00`, sequence,
    actor, origin: "desktop", path: page.path, baseRevision: `page:r${sequence - 1}`, resultRevision: `page:r${sequence}`,
    patch: PATCH, operations: [], reconciliation: "applied", analysis: null, reconcilesEventId: null,
    reviewOperations: [], questions: [], reviewEventId: null, observationId: null, sourceCanonicalRevision: null,
  };
}

const history: PageEditHistory = { total: 2, limit: 100, nextBeforeSequence: null, events: [event("event-1", 5), event("event-0", 4, "agent:curator")] };

type InspectorProps = Parameters<typeof MemoryInspector>[0];

function inspectorElement(overrides: Partial<InspectorProps> = {}) {
  return (
    <MemoryInspector
      page={page}
      links={links}
      history={history}
      linksLoading={false}
      historyLoading={false}
      linkError={null}
      historyError={null}
      navigationDisabled={false}
      titleForPath={() => undefined}
      onNavigate={() => {}}
      onRetryLinks={() => {}}
      onClose={() => {}}
      {...overrides}
    />
  );
}

test("links peek uses outgoing/incoming tabs and navigates context rows", async () => {
  const navigated: Array<{ path: string; anchor: string | null }> = [];
  const onNavigate = (path: string, anchor: string | null) => navigated.push({ path, anchor });
  const { host, root } = setup();
  await act(async () => root.render(inspectorElement({ onNavigate })));

  expect(host.querySelector(".mw-ctx-label")?.textContent).toBe("links");
  expect(host.querySelectorAll(".mw-ctx-label")).toHaveLength(1);
  expect(host.querySelector('[role="tab"]')?.textContent).toContain("outgoing");
  expect(host.querySelectorAll('[role="tab"]')).toHaveLength(2);

  // An unaliased outgoing link resolves to the canonical title fallback.
  const outgoing = Array.from(host.querySelectorAll<HTMLButtonElement>("button.mw-lk-row"));
  expect(outgoing).toHaveLength(1);
  expect(outgoing[0]!.textContent).toContain("roadmap");
  await act(async () => outgoing[0]!.click());
  expect(navigated).toEqual([{ path: "roadmap.md", anchor: "Work" }]);

  await act(async () => host.querySelectorAll<HTMLButtonElement>('[role="tab"]')[1]!.click());
  await settlePanelSwap();
  const groups = Array.from(host.querySelectorAll(".mw-bl-group"));
  expect(groups).toHaveLength(1);
  const mentions = groups[0]!;
  expect(mentions.querySelector("button")?.textContent).toBe("2026-07-13 · mention");
  const excerpts = Array.from(mentions.querySelectorAll("p")).map((p) => p.textContent);
  expect(excerpts).toEqual(["Worked on Dex", "Shipped Dex review"]);
  expect(mentions.textContent).not.toContain("[[Dex]]");
  expect(mentions.textContent).not.toContain("Line 8:12");
  expect(mentions.querySelector("mark")?.textContent).toBe("Dex");
  await act(async () => mentions.querySelector<HTMLButtonElement>("button")!.click());
  expect(navigated).toEqual([
    { path: "roadmap.md", anchor: "Work" },
    { path: "daily/2026-07-13.md", anchor: null },
  ]);

  expect(host.querySelectorAll('[role="tab"]')[1]?.textContent).toContain("3");

  // No removed affordances resurface.
  expect(host.querySelector('button[aria-label="Load more memory links"]')).toBeNull();
  expect(host.querySelector('button[aria-label="Correct record record-old"]')).toBeNull();
  expect(host.querySelector('button[aria-label="Forget record record-old"]')).toBeNull();
});

test("outgoing links use canonical page titles unless an explicit alias wins", async () => {
  const aliased = {
    ...links.outgoing[0]!,
    target: "Plan",
    display: "Pinned label",
    alias: "Pinned label",
    resolvedPath: "plan.md",
    candidates: ["plan.md"],
  };
  const { host, root } = setup();
  await act(async () => root.render(inspectorElement({
    links: { ...links, outgoing: [links.outgoing[0]!, aliased], totalOutgoing: 2 },
    titleForPath: (path) => path === "roadmap.md" ? "Canonical roadmap" : "Canonical plan",
  })));

  const titles = Array.from(host.querySelectorAll(".mw-lk-title")).map((node) => node.textContent);
  expect(titles).toEqual(["Canonical roadmap", "Pinned label"]);
});

test("links stay visible but never activatable while navigation is disabled", async () => {
  const navigated: string[] = [];
  const { host, root } = setup();
  await act(async () => root.render(inspectorElement({
    navigationDisabled: true,
    onNavigate: (path) => navigated.push(path),
  })));
  const outgoing = host.querySelector<HTMLButtonElement>("button.mw-lk-row")!;
  expect(outgoing.disabled).toBe(true);
  await act(async () => outgoing.click());
  await act(async () => host.querySelectorAll<HTMLButtonElement>('[role="tab"]')[1]!.click());
  await settlePanelSwap();
  const incoming = Array.from(host.querySelectorAll<HTMLButtonElement>("button.mw-bl-title"));
  expect(incoming.length).toBeGreaterThanOrEqual(1);
  for (const button of incoming) {
    expect(button.disabled).toBe(true);
    await act(async () => button.click());
  }
  expect(navigated).toEqual([]);
});

test("link and history errors stay independent and the retry refetches links", async () => {
  const retries: string[] = [];
  const { host, root } = setup();
  await act(async () => root.render(inspectorElement({
    links: null,
    linkError: "Links unavailable",
    onRetryLinks: () => retries.push("links"),
  })));
  expect(host.querySelector('[role="alert"]')?.textContent).toContain("Links unavailable");
  await act(async () => Array.from(host.querySelectorAll<HTMLButtonElement>("button"))
    .find((button) => button.textContent === "Retry")!.click());
  expect(retries).toEqual(["links"]);

  // A history failure only degrades the Activity pane; links render fine.
  await act(async () => root.render(inspectorElement({ history: null, historyError: "History unavailable" })));
  expect(host.textContent).toContain("roadmap");
  expect(host.querySelector('[role="alert"]')).toBeNull();
  await act(async () => root.render(inspectorElement({ history: null, historyError: "History unavailable", activePane: "activity" })));
  await settlePanelSwap();
  expect(host.querySelector('[role="alert"]')?.textContent).toContain("History unavailable");
});

test("legacy outline selection is retired to the links instrument", () => {
  localStorage.setItem(CTX_PANE_KEY, "outline");
  expect(loadMemoryInspectorPane()).toBe("links");
});

test("records pane renders the selected page timeline inside the peek", async () => {
  const recordsPage: MemoryArtifactDetail = {
    ...page,
    timeline: [
      {
        id: "record-1",
        text: "Current durable fact.",
        kind: "fact",
        date: "2026-07-12",
        src: "chat",
        pinned: true,
        superseded: false,
      },
      {
        id: "record-2",
        text: "Older superseded guidance.",
        kind: "directive",
        date: "2026-07-08",
        src: "memory",
        pinned: false,
        superseded: true,
      },
    ],
  };
  const { host, root } = setup();
  await act(async () => root.render(inspectorElement({
    page: recordsPage,
    activePane: "records",
  })));

  expect(host.querySelector(".mw-ctx-label")?.textContent).toBe("records");
  expect(host.querySelectorAll(".mw-page-record")).toHaveLength(1);
  expect(host.textContent).toContain("Current durable fact.");
  expect(host.textContent).not.toContain("Older superseded guidance.");
});

test("activity pane previews eight events, expands to all, and opens an escapable diff overlay", async () => {
  const many: PageEditHistory = {
    total: 10, limit: 100, nextBeforeSequence: null,
    events: Array.from({ length: 10 }, (_, i) => event(`event-${i}`, i + 1, i === 9 ? "user:latest" : "agent:curator")),
  };
  const { host, root } = setup();
  await act(async () => root.render(inspectorElement({ history: many })));
  await act(async () => root.render(inspectorElement({ history: many, activePane: "activity" })));
  await settlePanelSwap();

  // Newest first, capped at eight, with honest totals and patch stats.
  expect(host.textContent).toContain("user:latest");
  expect(host.textContent).toContain("+1 −1");
  expect(host.querySelectorAll(".mw-rec")).toHaveLength(8);
  const expand = Array.from(host.querySelectorAll<HTMLButtonElement>("button"))
    .find((button) => button.textContent?.includes("All 10 events"))!;
  await act(async () => expand.click());
  expect(host.querySelectorAll(".mw-rec")).toHaveLength(10);
  expect(host.textContent).toContain("Show fewer");

  // A row click opens the mock's read-only tier-three review sheet.
  await act(async () => host.querySelector<HTMLButtonElement>(".mw-rec button")!.click());
  const dialog = document.querySelector<HTMLElement>('[role="dialog"][aria-label="Page edit diff"]');
  expect(dialog).not.toBeNull();
  expect(dialog!.textContent).toContain("user:latest / dex");
  expect(dialog!.textContent).toContain("-old line");
  expect(dialog!.textContent).toContain("+new line");
  expect(dialog!.textContent).not.toContain("Split");

  await act(async () => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  });
  await settle(220);
  expect(document.querySelector('[role="dialog"][aria-label="Page edit diff"]')).toBeNull();
  expect(host.querySelectorAll(".mw-rec").length).toBeGreaterThan(0);
});

test("links pane shows empty states when the page has no links or mentions", async () => {
  const empty: PageLinks = { ...links, outgoing: [], backlinks: [], unlinked: [], totalOutgoing: 0, totalBacklinks: 0 };
  const { host, root } = setup();
  await act(async () => root.render(inspectorElement({ links: empty })));
  expect(host.textContent).toContain("No outgoing links.");
  await act(async () => host.querySelectorAll<HTMLButtonElement>('[role="tab"]')[1]!.click());
  await settlePanelSwap();
  expect(host.textContent).toContain("No incoming links.");

  await act(async () => root.render(inspectorElement({ links: null, linksLoading: true })));
  expect(host.textContent).toContain("Loading…");
});

test("the notebook inspector defaults closed and opens the links pane on demand", async () => {
  localStorage.removeItem(INSPECTOR_OPEN_KEY);
  localStorage.setItem("arden.desktop.memory.lastPath", "note.md");
  installViewBridge();
  const { host, root } = setup();
  await act(async () => root.render(<ArtifactMemoryView config={viewConfig} />));
  await settle(350);
  expect(host.querySelector("h1")?.textContent).toBe("note");
  expect(host.querySelector('aside[aria-label="Page peek"]')).toBeNull();
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Open links"]')?.click());
  await settle(100);
  const aside = host.querySelector('aside[aria-label="Page peek"]');
  expect(aside).not.toBeNull();
  expect(aside?.querySelector('[role="tab"]')?.textContent).toContain("outgoing");
  expect(aside?.querySelector('button[aria-label="Close peek"]')).not.toBeNull();
});

test("closing an explicitly opened inspector persists across a remount", async () => {
  localStorage.removeItem(INSPECTOR_OPEN_KEY);
  installViewBridge();
  const { host, root } = setup();
  await act(async () => root.render(<ArtifactMemoryView config={viewConfig} />));
  await settle(350);
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Open links"]')?.click());
  await settle(100);
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Close links"]')?.click());
  expect(localStorage.getItem(INSPECTOR_OPEN_KEY)).toBe("false");
  await act(async () => root.unmount());
  roots.delete(root);
  document.body.replaceChildren();

  const remount = setup();
  await act(async () => remount.root.render(<ArtifactMemoryView config={viewConfig} />));
  await settle(350);
  expect(remount.host.querySelector('aside[aria-label="Page peek"]')).toBeNull();
  expect(remount.host.querySelector('button[aria-label="Open links"]')).not.toBeNull();
});

test("the open inspector keeps its rendered shell while a new page detail loads", async () => {
  localStorage.setItem(INSPECTOR_OPEN_KEY, "true");
  localStorage.setItem("arden.desktop.memory.lastPath", "index.md");
  const index = rawArtifact("index.md", "Index", "Index body");
  const note = rawArtifact("note.md", "Note", "Note body");
  let resolveNote: ((value: ReturnType<typeof response>) => void) | null = null;
  const delayedNote = new Promise<ReturnType<typeof response>>((resolve) => {
    resolveNote = resolve;
  });
  window.ardenDesktop = { api: { request: async (_config, request) => {
    if (request.path === "/admin/memory/artifacts") return response({ artifacts: [index, note] });
    if (request.path.startsWith("/admin/memory/links")) {
      return response({ path: "index.md", revision: "ledger:1", stale: false, outgoing: [], backlinks: [], unlinked: [], total_outgoing: 0, total_backlinks: 0, limit: 100, offset: 0 });
    }
    if (request.path.startsWith("/admin/memory/page-edits/history")) {
      return response({ events: [], total: 0, limit: 100, next_before_sequence: null });
    }
    if (request.path.endsWith("/note.md")) return delayedNote;
    return response({ artifact: index });
  } } } as Window["ardenDesktop"];

  const { host, root } = setup();
  await act(async () => root.render(<ArtifactMemoryView config={viewConfig} />));
  await settle(350);

  const toolbar = host.querySelector<HTMLElement>(".mw-ctx-toolbar")!;
  expect(toolbar.isConnected).toBe(true);
  await act(async () => {
    host.querySelector<HTMLButtonElement>('button[data-memory-entry="note.md"]')!.click();
  });
  await settle(30);

  expect(toolbar.isConnected).toBe(true);
  expect(host.querySelector(".mw-ctx-toolbar")).toBe(toolbar);
  expect(host.querySelector(".mw-ctx-loading")).toBeNull();
  expect(host.querySelector('[data-peek-state="open"]')).not.toBeNull();

  await act(async () => {
    resolveNote?.(response({ artifact: note }));
  });
  await settle(350);
  expect(host.querySelector(".mw-ctx-toolbar")).toBe(toolbar);
  expect(host.querySelector("h1")?.textContent).toBe("note");
});
