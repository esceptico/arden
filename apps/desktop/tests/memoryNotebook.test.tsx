import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { AppConfig } from "@/api/core";
import { ArtifactMemoryView } from "@/features/memory/components/ArtifactMemoryView";

const config: AppConfig = { serverUrl: "http://localhost:6877", apiKey: "test-key" };
const originalDesktop = window.ntrpDesktop;
const mountedRoots = new Set<Root>();

const base = {
  kind: "topic",
  type: "file",
  scope: { kind: "user", key: null },
  snippet: null,
  revision: "sha256:list",
  record_count: 0,
  generated: false,
  editable: true,
  readonly_reason: null,
  updated_at: "2026-07-12T10:00:00Z",
  labels: [],
  source: null,
};

const summaries = [
  { ...base, path: "index.md", directory: "", title: "Index", summary: null, generated: true, editable: false },
  { ...base, path: "me.md", directory: "", title: "Me", summary: "Identity, preferences, and durable context." },
  { ...base, path: "topics/index.md", directory: "topics", title: "Topics", summary: "Active subjects and decisions.", generated: true, editable: false },
  { ...base, path: "topics/dex.md", directory: "topics", title: "Dex", summary: "Current work and decisions about Dex." },
  { ...base, path: "research/README.md", directory: "research", title: "Research", summary: "Research notes and experiments." },
  { ...base, path: "research/latency.md", directory: "research", title: "Latency", summary: "Observed latency behavior." },
  { ...base, path: "daily/README.md", directory: "daily", title: "Daily", summary: "Chronological memory activity.", generated: true, editable: false },
  { ...base, path: "daily/2026-07-13.md", directory: "daily", title: "2026-07-13", summary: null, generated: true },
  { ...base, path: "scratch.md", directory: "", title: "Scratch", summary: "Unsorted working notes." },
  { ...base, path: "health.md", directory: "", title: "Health", summary: "Machine health report.", generated: true, editable: false },
  { ...base, path: "raw/events/1.md", directory: "raw/events", title: "Raw event", summary: "Machine event." },
  { ...base, path: ".ntrp/maintenance/state.md", directory: ".ntrp/maintenance", title: "State", summary: "Machine state." },
];

function detail(path: string) {
  const item = summaries.find((artifact) => artifact.path === path)!;
  return {
    ...item,
    revision: `sha256:${path}`,
    content: path === "me.md"
      ? "# Me\n\nI build personal tools.\n\nSee [[topics/dex|Dex]]."
      : path === "index.md"
        ? "Start here."
        : item.summary ?? "",
    editable_content: `# ${item.title}\n\n${item.summary}\n`,
    timeline: path === "me.md" ? [{
      id: "record-me", text: "I build personal tools.", kind: "fact", date: "2026-07-12",
      src: "chat", pinned: false, superseded: false,
    }] : [],
    frontmatter: { updated: "2026-07-12", labels: ["personal"] },
  };
}

function installBridge(options: { list?: typeof summaries; directories?: string[]; failList?: string; delayList?: boolean } = {}) {
  const requests: string[] = [];
  let resolveList: (() => void) | null = null;
  const listGate = new Promise<void>((resolve) => { resolveList = resolve; });
  window.ntrpDesktop = {
    api: {
      request: async (_config, request) => {
        requests.push(request.path);
        if (request.path.startsWith("/admin/memory/artifacts?") || request.path === "/admin/memory/artifacts") {
          if (options.delayList) await listGate;
          if (options.failList) {
            return { ok: false, status: 500, statusText: "Error", contentType: "application/json", data: { detail: options.failList }, text: "" };
          }
          const artifacts = options.list ?? summaries;
          return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data: { artifacts, directories: options.directories ?? [] }, text: "" };
        }
        if (request.path.startsWith("/admin/memory/items")) {
          return {
            ok: true, status: 200, statusText: "OK", contentType: "application/json",
            data: {
              items: [{
                id: "record-1", content: "Raw diagnostic fact", kind: "fact", canonical_subject: "test",
                labels: [], scope: { kind: "user", key: null }, pinned: false, status: "active",
                valid_from: null, invalid_at: null, source_refs: [], corroboration: 1,
                last_relevant_at: null, feedback: "none", created_at: "2026-07-12T10:00:00Z",
                updated_at: "2026-07-12T10:00:00Z",
              }],
              limit: 100,
            },
          };
        }
        if (request.path.startsWith("/admin/memory/links")) {
          const path = new URL(`http://local${request.path}`).searchParams.get("path")!;
          const outgoing = path === "me.md" ? [{
            source_path: "me.md", target: "topics/dex", display: "Dex", heading: null,
            context: "See [[topics/dex|Dex]].", line: 3, column: 5, status: "resolved",
            resolved_path: "topics/dex.md", candidates: ["topics/dex.md"], source_revision: "ledger:1",
          }] : [];
          return {
            ok: true, status: 200, statusText: "OK", contentType: "application/json",
            data: { path, revision: "ledger:1", stale: false, outgoing, backlinks: [], total_outgoing: outgoing.length, total_backlinks: 0, limit: 100, offset: 0 }, text: "",
          };
        }
        if (request.path.startsWith("/admin/memory/page-edits/history")) {
          return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data: { events: [], total: 0, limit: 100, next_before_sequence: null }, text: "" };
        }
        const path = decodeURIComponent(request.path.replace("/admin/memory/artifacts/", ""));
        return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data: { artifact: detail(path) }, text: "" };
      },
    },
  } as Window["ntrpDesktop"];
  return { requests, releaseList: () => resolveList?.() };
}

function setupDom(): { host: HTMLElement; root: Root } {
  // Inspector defaults open (persisted); seed closed to keep request counts
  // scoped to the tree/note flows these tests exercise.
  localStorage.setItem("ntrp.desktop.memory.inspectorOpen", "false");
  const app = document.createElement("div");
  app.id = "app";
  const host = document.createElement("div");
  host.style.height = "800px";
  app.append(host);
  document.body.append(app);
  const root = createRoot(host);
  mountedRoots.add(root);
  return { host, root };
}

async function settle(delay = 0) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, delay));
  });
}

async function unmountRoot(root: Root) {
  await act(async () => root.unmount());
  mountedRoots.delete(root);
}

afterEach(async () => {
  for (const root of mountedRoots) await act(async () => root.unmount());
  mountedRoots.clear();
  window.ntrpDesktop = originalDesktop;
  document.body.replaceChildren();
  for (const key of [
    "ntrp.desktop.memory.inspectorOpen",
    "ntrp.desktop.memory.lastPath",
    "ntrp.desktop.memory.pins",
    "ntrp.desktop.memory.rail.collapsed",
  ]) localStorage.removeItem(key);
});

test("memory opens as a filesystem notebook with a plain tree, tabs, and stems", async () => {
  const bridge = installBridge({ directories: ["archive"] });
  const { host, root } = setupDom();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(250);

  const rail = host.querySelector<HTMLElement>('[data-memory-zone="rail"]');
  const workspace = host.querySelector<HTMLElement>('[data-memory-zone="workspace"]');
  const inspector = host.querySelector<HTMLElement>('[data-memory-zone="inspector"]');
  expect(rail?.getAttribute("aria-label")).toBe("Memory notebook");
  expect(workspace?.querySelector("main")?.getAttribute("aria-label")).toBe("Memory note");
  expect(inspector).not.toBeNull();
  const layout = host.querySelector<HTMLElement>('[data-memory-layout="notebook"]');
  expect(layout?.classList.contains("memory-ws")).toBe(true);

  // Top strip: history buttons plus document tabs labeled with path stems.
  expect(host.querySelector('button[aria-label="Back in memory history"]')).not.toBeNull();
  const tablist = host.querySelector<HTMLElement>('[role="tablist"][aria-label="Open notes"]');
  expect(tablist).not.toBeNull();
  expect(tablist?.querySelector('[role="tab"]')?.textContent).toContain("index");
  expect(tablist?.querySelector('button[aria-label="New tab"]')).not.toBeNull();

  // The rail is a plain filesystem tree: folder nodes from paths (plus empty
  // directories the server lists), rows labeled with filename stems.
  expect(rail?.querySelector('[data-memory-directory="topics/"]')).not.toBeNull();
  expect(rail?.querySelector('[data-memory-directory="research/"]')).not.toBeNull();
  expect(rail?.querySelector('[data-memory-directory="daily/"]')).not.toBeNull();
  expect(rail?.querySelector('[data-memory-directory="archive/"]')).not.toBeNull();
  const dexRow = rail?.querySelector<HTMLButtonElement>('[data-memory-entry="topics/dex.md"]');
  expect(dexRow?.textContent).toBe("dex");
  expect(rail?.querySelector<HTMLButtonElement>('[data-memory-entry="daily/2026-07-13.md"]')?.textContent).toBe("2026-07-13");
  expect(rail?.querySelector('[data-memory-entry="scratch.md"]')?.textContent).toBe("scratch");
  expect(rail?.textContent).not.toContain("topics/dex.md");
  // Reserved machine paths never surface as tree rows.
  expect(rail?.querySelector('[data-memory-entry="raw/events/1.md"]')).toBeNull();
  expect(rail?.querySelector('[data-memory-entry=".ntrp/maintenance/state.md"]')).toBeNull();
  expect(rail?.querySelector('[data-memory-entry="health.md"]')).toBeNull();

  // Pinned cluster at the top of the tree — me.md is pinned by default.
  expect(rail?.querySelector('.mw-tree-pins [data-memory-entry="me.md"]')).not.toBeNull();

  // The tree never fetches index documents to build itself; the only detail
  // read so far is the selected note.
  const detailReads = bridge.requests.filter((path) => path.startsWith("/admin/memory/artifacts/"));
  expect(bridge.requests[0]).toBe("/admin/memory/artifacts");
  expect(detailReads).toEqual(["/admin/memory/artifacts/index.md"]);

  // Folder label clicks only collapse/expand — they never navigate.
  const topicsFolder = rail!.querySelector<HTMLElement>('[data-memory-directory="topics/"]')!;
  const topicsLabel = Array.from(topicsFolder.querySelectorAll<HTMLButtonElement>("button"))
    .find((button) => button.classList.contains("mw-folder-label"))!;
  expect(topicsLabel.textContent).toBe("topics");
  await act(async () => topicsLabel.click());
  expect(topicsFolder.classList.contains("closed")).toBe(true);
  expect(host.querySelector('[data-memory-zone="workspace"] h1')?.textContent).toBe("index");
  await act(async () => topicsFolder.querySelector<HTMLButtonElement>('button[aria-label="Expand topics"]')?.click());
  expect(topicsFolder.classList.contains("closed")).toBe(false);

  // Right-clicking a row opens a context menu portaled to the body.
  await act(async () => dexRow!.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true })));
  const menu = document.body.querySelector<HTMLElement>(".mw-rc-menu");
  expect(menu).not.toBeNull();
  const items = Array.from(menu!.querySelectorAll('[role="menuitem"]')).map((item) => item.textContent);
  expect(items).toEqual(["Copy path", "Pin", "Open in new tab"]);
  await act(async () => Array.from(menu!.querySelectorAll<HTMLButtonElement>('[role="menuitem"]'))
    .find((item) => item.textContent === "Open in new tab")?.click());
  await settle(250);
  expect(host.querySelector('[data-memory-zone="workspace"] h1')?.textContent).toBe("dex");
  const tabs = Array.from(tablist!.querySelectorAll('[role="tab"]')).map((tab) => tab.textContent);
  expect(tabs).toHaveLength(2);
  expect(tabs[1]).toContain("dex");

  // The note surface: h1 is the filename stem, frontmatter renders as a
  // Properties section, and body leading H1s stay in the prose.
  await act(async () => host.querySelector<HTMLButtonElement>('[data-memory-entry="me.md"]')?.click());
  await settle(250);
  const noteTitle = workspace?.querySelector("h1");
  expect(noteTitle?.textContent).toBe("me");
  expect(workspace?.textContent).toContain("Properties");
  expect(workspace?.textContent).toContain("personal");
  const prose = workspace?.querySelector(".mw-prose");
  expect(prose?.textContent).toContain("I build personal tools.");
  expect(prose?.querySelector("h1")?.textContent).toBe("Me");
  const records = Array.from(workspace?.querySelectorAll("button") ?? [])
    .find((button) => button.textContent?.includes("Records"));
  expect(records?.textContent).toContain("1");
  expect(bridge.requests).toContain("/admin/memory/artifacts/me.md");

  // Wiki links resolve through the links index and navigate in place.
  const dexLink = workspace?.querySelector<HTMLAnchorElement>('[data-wikilink="topics/dex"]');
  await act(async () => dexLink?.click());
  await settle(250);
  expect(workspace?.querySelector("h1")?.textContent).toBe("dex");
  expect(bridge.requests).toContain("/admin/memory/artifacts/topics/dex.md");

  // No rail search input — the search affordance opens the quick switcher.
  expect(rail?.querySelector("input")).toBeNull();
  await act(async () => rail?.querySelector<HTMLButtonElement>('button[aria-label="Search notes"]')?.click());
  await settle();
  expect(document.querySelector('[aria-label="Quick switcher"]')).not.toBeNull();
  await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
  await settle();
  expect(document.querySelector('[aria-label="Quick switcher"]')).toBeNull();

  // Raw records stay one diagnostic away.
  const openRecords = host.querySelector<HTMLButtonElement>('button[aria-label="Open raw records diagnostic"]');
  expect(openRecords).not.toBeNull();
  await act(async () => openRecords?.click());
  await settle();
  expect(host.querySelector('section[aria-label="Raw records diagnostic"]')).not.toBeNull();
  expect(host.querySelector('button[aria-label="Close raw records diagnostic"]')).not.toBeNull();
  expect(host.textContent).toContain("Raw diagnostic fact");

  await unmountRoot(root);
});

test("loading, error, and empty states keep the notebook zones stable", async () => {
  const loadingBridge = installBridge({ delayList: true });
  const loadingDom = setupDom();
  await act(async () => loadingDom.root.render(<ArtifactMemoryView config={config} />));
  expect(loadingDom.host.querySelector('[data-memory-zone="rail"]')).not.toBeNull();
  expect(loadingDom.host.querySelector('[data-memory-zone="workspace"]')).not.toBeNull();
  expect(loadingDom.host.querySelector('[data-memory-zone="inspector"]')).not.toBeNull();
  loadingBridge.releaseList();
  await settle();
  await unmountRoot(loadingDom.root);

  installBridge({ failList: "Vault unavailable" });
  const errorDom = setupDom();
  await act(async () => errorDom.root.render(<ArtifactMemoryView config={config} />));
  await settle();
  expect(errorDom.host.querySelector('[data-memory-zone="rail"] [role="alert"]')?.textContent).toContain("Vault unavailable");
  expect(errorDom.host.querySelector('[data-memory-zone="workspace"]')).not.toBeNull();
  await unmountRoot(errorDom.root);

  installBridge({ list: [] });
  const emptyDom = setupDom();
  await act(async () => emptyDom.root.render(<ArtifactMemoryView config={config} />));
  await settle();
  expect(emptyDom.host.querySelector('[data-memory-zone="rail"]')?.textContent).toContain("No memory notes yet");
  expect(emptyDom.host.querySelector('[data-memory-zone="workspace"]')).not.toBeNull();
  await unmountRoot(emptyDom.root);
});

test("default selection restores the last path, falls back to index.md, then the first navigable note", async () => {
  const byPath = (path: string) => summaries.find((artifact) => artifact.path === path)!;
  const shuffled = [byPath("scratch.md"), byPath("topics/dex.md"), byPath("me.md"), byPath("index.md"), byPath("topics/index.md")];

  // index.md wins regardless of transport order.
  installBridge({ list: shuffled });
  const indexDom = setupDom();
  await act(async () => indexDom.root.render(<ArtifactMemoryView config={config} />));
  await settle(250);
  expect(indexDom.host.querySelector('[data-memory-zone="workspace"] h1')?.textContent).toBe("index");
  await unmountRoot(indexDom.root);
  document.body.replaceChildren();

  // A remembered last path beats index.md when it still exists.
  localStorage.setItem("ntrp.desktop.memory.lastPath", "me.md");
  installBridge({ list: shuffled });
  const lastPathDom = setupDom();
  await act(async () => lastPathDom.root.render(<ArtifactMemoryView config={config} />));
  await settle(250);
  expect(lastPathDom.host.querySelector('[data-memory-zone="workspace"] h1')?.textContent).toBe("me");
  await unmountRoot(lastPathDom.root);
  document.body.replaceChildren();

  // Without index.md or a remembered path, the first navigable note wins.
  localStorage.removeItem("ntrp.desktop.memory.lastPath");
  installBridge({ list: [byPath("scratch.md"), byPath("topics/dex.md"), byPath("me.md")] });
  const fallbackDom = setupDom();
  await act(async () => fallbackDom.root.render(<ArtifactMemoryView config={config} />));
  await settle(250);
  expect(fallbackDom.host.querySelector('[data-memory-zone="workspace"] h1')?.textContent).toBe("scratch");
  await unmountRoot(fallbackDom.root);
});
