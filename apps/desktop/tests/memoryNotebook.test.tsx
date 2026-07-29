import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { AppConfig } from "@/api/core";
import { ArtifactMemoryView } from "@/features/memory/components/ArtifactMemoryView";
import { error, installCanonicalMemoryBridge, type WikiPageFixture } from "./helpers/canonicalMemoryBridge";

const config: AppConfig = { serverUrl: "http://localhost:6877", apiKey: "test-key" };
const originalDesktop = window.ardenDesktop;
const mountedRoots = new Set<Root>();

const pages: WikiPageFixture[] = [
  { pageId: "wiki-home", path: "README.md", title: "Wiki", content: "Start here.", metadata: { generated_from_revision: "facts:1" } },
  { pageId: "me", path: "me.md", title: "Me", content: "# Me\n\nI build personal tools.\n\nSee [[topics/dex|Dex]].", metadata: { summary: "Identity, preferences, and durable context.", labels: ["personal"], fact_citations: [{ fact_id: "record-me", version: "record-me:v1" }] } },
  { pageId: "topics-home", path: "topics/README.md", title: "Topics", content: "Active subjects and decisions.", metadata: { summary: "Active subjects and decisions.", generated_from_revision: "facts:1" } },
  { pageId: "dex", path: "topics/dex.md", title: "Dex", content: "Current work and decisions about Dex.", metadata: { summary: "Current work and decisions about Dex." } },
  { pageId: "research-home", path: "research/README.md", title: "Research", content: "Research notes and experiments.", metadata: { summary: "Research notes and experiments." } },
  { pageId: "latency", path: "research/latency.md", title: "Latency", content: "Observed latency behavior.", metadata: { summary: "Observed latency behavior." } },
  { pageId: "daily-home", path: "daily/README.md", title: "Daily", content: "Chronological memory activity.", metadata: { summary: "Chronological memory activity.", generated_from_revision: "facts:1" } },
  { pageId: "daily-2026-07-13", path: "daily/2026-07-13.md", title: "2026-07-13", content: "", metadata: { generated_from_revision: "facts:1" } },
  { pageId: "scratch", path: "scratch.md", title: "Scratch", content: "Unsorted working notes.", metadata: { summary: "Unsorted working notes." } },
  { pageId: "health", path: "health.md", title: "Health", content: "Machine health report.", metadata: { summary: "Machine health report.", generated_from_revision: "facts:1" } },
  { pageId: "raw-event", path: "raw/events/1.md", title: "Raw event", content: "Machine event." },
  { pageId: "maintenance-state", path: ".arden/maintenance/state.md", title: "State", content: "Machine state." },
];

function installBridge(options: { list?: WikiPageFixture[]; failList?: string; delayList?: boolean } = {}) {
  let resolveList: (() => void) | null = null;
  const listGate = new Promise<void>((resolve) => { resolveList = resolve; });
  const bridge = installCanonicalMemoryBridge({
    pages: options.list ?? pages,
    facts: [{ factId: "record-1", text: "Raw diagnostic fact", subjects: ["test"] }, { factId: "record-me", text: "I build personal tools.", sources: [{ kind: "chat", ref: "message-1" }] }],
    links: {
      me: { outgoing: [{ sourcePageId: "me", target: "topics/dex", alias: "Dex", targetPageId: "dex" }] },
    },
    onRequest: async ({ path }) => {
      if (path !== "/admin/wiki/pages") return undefined;
      if (options.delayList) await listGate;
      return options.failList ? error(500, options.failList) : undefined;
    },
  });
  return { ...bridge, releaseList: () => resolveList?.() };
}

function setupDom(): { host: HTMLElement; root: Root } {
  // Inspector defaults open (persisted); seed closed to keep request counts
  // scoped to the tree/note flows these tests exercise.
  localStorage.setItem("arden.desktop.memory.inspectorOpen", "false");
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
  window.ardenDesktop = originalDesktop;
  document.body.replaceChildren();
  for (const key of [
    "arden.desktop.memory.inspectorOpen",
    "arden.desktop.memory.lastPath",
    "arden.desktop.memory.pins",
    "arden.desktop.memory.rail.collapsed",
  ]) localStorage.removeItem(key);
});

test("memory opens as a filesystem notebook with a plain tree, tabs, and titles", async () => {
  installBridge();
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

  // Top furniture mirrors the redesign: document tabs and page instruments
  // are separate clusters on one desk band.
  const tablist = host.querySelector<HTMLElement>('[role="tablist"][aria-label="Open pages"]');
  expect(tablist).not.toBeNull();
  expect(tablist?.querySelector('[role="tab"]')?.textContent).toContain("Wiki");
  expect(host.querySelector('[aria-label="Page instruments"]')).not.toBeNull();
  expect(host.querySelector('button[aria-label="Open links"]')).not.toBeNull();

  // The rail is a plain filesystem tree: folder nodes derive from managed
  // paths and rows are labeled with the page's frontmatter title.
  expect(rail?.querySelector('[data-memory-directory="topics/"]')).not.toBeNull();
  expect(rail?.querySelector('[data-memory-directory="research/"]')).not.toBeNull();
  expect(rail?.querySelector('[data-memory-directory="daily/"]')).not.toBeNull();
  const dexRow = rail?.querySelector<HTMLButtonElement>('[data-memory-entry="topics/dex.md"]');
  expect(dexRow?.textContent).toBe("Dex");
  expect(rail?.querySelector<HTMLButtonElement>('[data-memory-entry="daily/2026-07-13.md"]')?.textContent).toBe("2026-07-13");
  expect(rail?.querySelector('[data-memory-entry="scratch.md"]')?.textContent).toBe("Scratch");
  expect(rail?.textContent).not.toContain("topics/dex.md");
  // Raw machine paths stay hidden; the readable health report remains visible.
  expect(rail?.querySelector('[data-memory-entry="raw/events/1.md"]')).toBeNull();
  expect(rail?.querySelector('[data-memory-entry=".arden/maintenance/state.md"]')).toBeNull();
  expect(rail?.querySelector('[data-memory-entry="health.md"]')?.textContent).toBe("Health");

  // The mock rail is intentionally bare: no pinned cluster or hidden toolbar.
  expect(rail?.querySelector(".mw-tree-pins")).toBeNull();
  expect(rail?.querySelector(".mw-rail-top")).toBeNull();

  // Folder label clicks only collapse/expand — they never navigate.
  const topicsFolder = rail!.querySelector<HTMLElement>('[data-memory-directory="topics/"]')!;
  const topicsLabel = topicsFolder.querySelector<HTMLButtonElement>(".mw-fold")!;
  expect(topicsLabel.textContent).toBe("topics");
  await act(async () => topicsLabel.click());
  expect(topicsFolder.classList.contains("closed")).toBe(true);
  expect(host.querySelector('[data-memory-zone="workspace"] h1')?.textContent).toBe("Wiki");
  await act(async () => topicsFolder.querySelector<HTMLButtonElement>('button[aria-label="Expand topics"]')?.click());
  expect(topicsFolder.classList.contains("closed")).toBe(false);

  // File rows navigate directly; the rail has no legacy context-menu layer.
  await act(async () => dexRow?.click());
  await settle(250);
  expect(host.querySelector('[data-memory-zone="workspace"] h1')?.textContent).toBe("Dex");

  // The note surface: h1 is the page title, frontmatter renders as a
  // Properties section, and the body's own repeat of that title is dropped
  // so the page is not headed twice.
  await act(async () => host.querySelector<HTMLButtonElement>('[data-memory-entry="me.md"]')?.click());
  await settle(250);
  const noteTitle = workspace?.querySelector("h1");
  expect(noteTitle?.textContent).toBe("Me");
  expect(workspace?.textContent).toContain("properties");
  expect(workspace?.textContent).toContain("personal");
  const prose = workspace?.querySelector(".mw-prose");
  expect(prose?.textContent).toContain("I build personal tools.");
  expect(prose?.querySelector("h1")).toBeNull();
  // Records live in the instrument panel only — no ledger under the prose.
  const ledger = Array.from(workspace?.querySelectorAll("button") ?? [])
    .find((button) => button.textContent?.includes("Records"));
  expect(ledger).toBeUndefined();
  expect(workspace?.querySelector('[aria-label="Open records"]')).not.toBeNull();
  // Wiki links resolve through the canonical link report and navigate in place.
  const dexLink = workspace?.querySelector<HTMLAnchorElement>('[data-wikilink="topics/dex"]');
  await act(async () => dexLink?.click());
  await settle(250);
  expect(workspace?.querySelector("h1")?.textContent).toBe("Dex");

  // The three mock modes share one compact rail and never replace it with a
  // diagnostic toolbar.
  expect(rail?.querySelector("input")).toBeNull();
  const modeTabs = Array.from(rail?.querySelectorAll<HTMLButtonElement>('[role="tab"]') ?? []);
  expect(modeTabs.map((tab) => tab.dataset.tabValue)).toEqual(["files", "notebook", "facts"]);

  await act(async () => modeTabs.find((tab) => tab.dataset.tabValue === "notebook")?.click());
  await settle(500);
  expect(rail?.querySelector('[role="tab"][data-tab-value="notebook"]')?.getAttribute("aria-selected")).toBe("true");
  expect(rail?.querySelector("[data-memory-notebook-list]")).not.toBeNull();
  expect(rail?.querySelector('[data-memory-entry="me.md"]')?.textContent).toContain("Me");

  await act(async () => modeTabs.find((tab) => tab.dataset.tabValue === "facts")?.click());
  await settle(500);
  expect(rail?.querySelector('[role="tab"][data-tab-value="facts"]')?.getAttribute("aria-selected")).toBe("true");
  const fact = rail?.querySelector<HTMLButtonElement>('[data-memory-fact="record-1"]');
  expect(fact?.textContent).toContain("Raw diagnostic fact");
  expect(host.querySelector('section[aria-label="Raw records diagnostic"]')).toBeNull();
  await act(async () => fact?.click());
  expect(workspace?.querySelector('main')?.getAttribute("aria-label")).toBe("Memory note");

  await unmountRoot(root);
});

test("Memory document tabs append, dedupe, rove, close, and preserve focus semantics", async () => {
  installBridge();
  const { host, root } = setupDom();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(250);

  const openTabs = () => Array.from(
    host.querySelectorAll<HTMLButtonElement>('[role="tablist"][aria-label="Open pages"] [role="tab"]'),
  );
  const labels = () => openTabs().map((tab) => tab.textContent);

  expect(labels()).toEqual(["Wiki"]);
  await act(async () => host.querySelector<HTMLButtonElement>('[data-memory-entry="topics/dex.md"]')?.click());
  await settle(250);
  expect(labels()).toEqual(["Wiki", "Dex"]);

  await act(async () => host.querySelector<HTMLButtonElement>('[data-memory-entry="me.md"]')?.click());
  await settle(250);
  expect(labels()).toEqual(["Wiki", "Dex", "Me"]);
  expect(openTabs().map((tab) => tab.tabIndex)).toEqual([-1, -1, 0]);

  await act(async () => host.querySelector<HTMLButtonElement>('[data-memory-entry="topics/dex.md"]')?.click());
  await settle(250);
  expect(labels()).toEqual(["Wiki", "Dex", "Me"]);
  expect(openTabs().map((tab) => tab.tabIndex)).toEqual([-1, 0, -1]);

  const dexTab = openTabs()[1]!;
  dexTab.focus();
  await act(async () => host.querySelector<HTMLButtonElement>('[data-memory-entry="me.md"]')?.click());
  await settle(250);
  const openPages = host.querySelector('[role="tablist"][aria-label="Open pages"]');
  expect(openPages?.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe("Me");
  expect(host.querySelector("h1")?.textContent).toBe("Me");

  const closeButtons = Array.from(host.querySelectorAll<HTMLButtonElement>(".mw-doc-tab-x"));
  expect(closeButtons.map((button) => button.getAttribute("aria-label")))
    .toEqual(["Close Wiki", "Close Dex", "Close Me"]);
  expect(closeButtons.map((button) => button.tabIndex)).toEqual([-1, -1, 0]);

  const meTab = openTabs()[2]!;
  meTab.focus();
  await act(async () => meTab.dispatchEvent(new KeyboardEvent("keydown", {
    key: "Delete",
    bubbles: true,
    cancelable: true,
  })));
  await settle(250);
  expect(labels()).toEqual(["Wiki", "Dex"]);
  expect(openPages?.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe("Dex");
  expect(document.activeElement?.textContent).toBe("Dex");

  await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", {
    key: "w",
    metaKey: true,
    bubbles: true,
    cancelable: true,
  })));
  await settle(250);
  expect(labels()).toEqual(["Wiki"]);
  expect(openPages?.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe("Wiki");

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

test("Cmd/Ctrl+B toggles Memory's own visible rail", async () => {
  installBridge();
  const { host, root } = setupDom();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(250);

  const layout = host.querySelector<HTMLElement>('[data-memory-layout="notebook"]')!;
  expect(layout.classList.contains("rail-hidden")).toBe(false);

  const commandB = new KeyboardEvent("keydown", { key: "b", metaKey: true, bubbles: true, cancelable: true });
  await act(async () => window.dispatchEvent(commandB));
  expect(commandB.defaultPrevented).toBe(true);
  expect(layout.classList.contains("rail-hidden")).toBe(true);

  const controlB = new KeyboardEvent("keydown", { key: "b", ctrlKey: true, bubbles: true, cancelable: true });
  await act(async () => window.dispatchEvent(controlB));
  expect(controlB.defaultPrevented).toBe(true);
  expect(layout.classList.contains("rail-hidden")).toBe(false);

  await unmountRoot(root);
});

test("default selection restores the last path, falls back to README.md, then the first navigable note", async () => {
  const byPath = (path: string) => pages.find((page) => page.path === path)!;
  const shuffled = [byPath("scratch.md"), byPath("topics/dex.md"), byPath("me.md"), byPath("README.md"), byPath("topics/README.md")];

  // README.md wins regardless of transport order.
  installBridge({ list: shuffled });
  const readmeDom = setupDom();
  await act(async () => readmeDom.root.render(<ArtifactMemoryView config={config} />));
  await settle(250);
  expect(readmeDom.host.querySelector('[data-memory-zone="workspace"] h1')?.textContent).toBe("Wiki");
  await unmountRoot(readmeDom.root);
  document.body.replaceChildren();

  // A remembered last path beats README.md when it still exists.
  localStorage.setItem("arden.desktop.memory.lastPath", "me.md");
  installBridge({ list: shuffled });
  const lastPathDom = setupDom();
  await act(async () => lastPathDom.root.render(<ArtifactMemoryView config={config} />));
  await settle(250);
  expect(lastPathDom.host.querySelector('[data-memory-zone="workspace"] h1')?.textContent).toBe("Me");
  await unmountRoot(lastPathDom.root);
  document.body.replaceChildren();

  // Without README.md or a remembered path, the first navigable note wins.
  localStorage.removeItem("arden.desktop.memory.lastPath");
  installBridge({ list: [byPath("scratch.md"), byPath("topics/dex.md"), byPath("me.md")] });
  const fallbackDom = setupDom();
  await act(async () => fallbackDom.root.render(<ArtifactMemoryView config={config} />));
  await settle(250);
  expect(fallbackDom.host.querySelector('[data-memory-zone="workspace"] h1')?.textContent).toBe("Scratch");
  await unmountRoot(fallbackDom.root);
});
