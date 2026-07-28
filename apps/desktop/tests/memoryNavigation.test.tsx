import { afterEach, expect, test } from "bun:test";
import { act, createRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { AppConfig } from "@/api/core";
import { ArtifactMemoryView } from "@/features/memory/components/ArtifactMemoryView";
import { WikiLinkPreview } from "@/features/memory/components/WikiLinkPreview";
import { ArtifactCache } from "@/features/memory/lib/artifactCache";
import { NavigationHistory } from "@/features/memory/lib/navigationHistory";
import { resolveWikiTarget } from "@/features/memory/lib/wikiResolution";
import type { MemoryArtifactDetail, MemoryArtifactSummary, PageLinks } from "@/features/memory/lib/notebookTypes";
import { useStore } from "@/stores";
import {
  installCanonicalMemoryBridge,
  ok,
  type LinkFixture,
  type WikiPageFixture,
} from "./helpers/canonicalMemoryBridge";

const config: AppConfig = { serverUrl: "http://localhost:6877", apiKey: "test-key" };
const originalDesktop = window.ardenDesktop;
const originalVaultVersion = useStore.getState().memoryVaultVersion;
const roots = new Set<Root>();

const summary = (path: string, title: string, revision = `sha256:${path}`): MemoryArtifactSummary => ({
  path, title, kind: "topic", type: "file", directory: path.includes("/") ? path.split("/")[0]! : "",
  scope: { kind: "user", key: null }, snippet: null, summary: null, revision, recordCount: 0,
  generated: false, editable: true, readonlyReason: null, updatedAt: "2026-07-13T08:00:00Z",
  createdAt: null, labels: [], source: null,
});

const detail = (item: MemoryArtifactSummary, content: string): MemoryArtifactDetail => ({
  ...item, revision: item.revision!, content, editableContent: content, timeline: [], frontmatter: {},
});

function wikiPage(item: MemoryArtifactSummary, content = ""): WikiPageFixture {
  return {
    pageId: `page-${item.path.replaceAll("/", "-")}`,
    path: item.path,
    title: item.title,
    content,
    version: item.revision ?? "wiki-version-1",
    repositoryHead: "wiki-head-1",
  };
}

function wikiLink(
  source: WikiPageFixture,
  target: string,
  targetPage: WikiPageFixture,
  alias?: string,
): LinkFixture {
  return {
    sourcePageId: source.pageId,
    target,
    alias: alias ?? null,
    targetPageId: targetPage.pageId,
    candidates: [targetPage.pageId],
  };
}

function setup(lastPath?: string) {
  // Inspector now defaults open (persisted); most of these tests exercise
  // navigation history/link resolution, so seed it closed to keep prior
  // request counts. Tests that specifically want the inspector open re-seed
  // and click the toggle themselves. Seeding lastPath drives the initial
  // selection (it beats the index.md fallback).
  localStorage.setItem("arden.desktop.memory.inspectorOpen", "false");
  if (lastPath) localStorage.setItem("arden.desktop.memory.lastPath", lastPath);
  const app = document.createElement("div");
  app.id = "app";
  const host = document.createElement("div");
  host.style.height = "800px";
  app.append(host);
  document.body.append(app);
  const root = createRoot(host);
  roots.add(root);
  return { app, host, root };
}

async function settle(ms = 0) {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, ms)); });
}

async function settleUntil(predicate: () => boolean, timeout = 1_500) {
  const started = Date.now();
  while (!predicate() && Date.now() - started < timeout) await settle(50);
}

afterEach(async () => {
  for (const root of roots) await act(async () => root.unmount());
  roots.clear();
  window.ardenDesktop = originalDesktop;
  useStore.setState({ memoryVaultVersion: originalVaultVersion });
  document.body.replaceChildren();
  for (const key of [
    "arden.desktop.memory.inspectorOpen",
    "arden.desktop.memory.lastPath",
    "arden.desktop.memory.pins",
    "arden.desktop.memory.rail.collapsed",
    "arden.desktop.memory.ctxPane",
  ]) localStorage.removeItem(key);
});

test("navigation history is bounded, restores locations, and truncates forward branches", () => {
  const history = new NavigationHistory(3);
  history.push({ path: "a.md", anchor: null, scrollTop: 11, focusSelector: "h1" });
  history.push({ path: "b.md", anchor: "Part", scrollTop: 22, focusSelector: "#part" });
  history.push({ path: "b.md", anchor: "Part", scrollTop: 99, focusSelector: null });
  history.replaceCurrent({ path: "b.md", anchor: "Part", scrollTop: 33, focusSelector: "#part" });
  history.push({ path: "c.md", anchor: null, scrollTop: 44, focusSelector: null });
  history.push({ path: "d.md", anchor: null, scrollTop: 55, focusSelector: null });

  expect(history.length).toBe(3);
  expect(history.back()).toEqual({ path: "c.md", anchor: null, scrollTop: 44, focusSelector: null });
  expect(history.back()).toEqual({ path: "b.md", anchor: "Part", scrollTop: 33, focusSelector: "#part" });
  expect(history.back()).toBeNull();
  history.push({ path: "x.md", anchor: null, scrollTop: 0, focusSelector: null });
  expect(history.canForward).toBe(false);
});

test("artifact cache is revision keyed, evicts old path revisions, and stays bounded", () => {
  const cache = new ArtifactCache(2);
  const a1 = detail(summary("a.md", "A", "r1"), "one");
  const a2 = detail(summary("a.md", "A", "r2"), "two");
  const b = detail(summary("b.md", "B", "r1"), "bee");
  const c = detail(summary("c.md", "C", "r1"), "see");
  cache.set(a1);
  cache.set(a2);
  expect(cache.get("a.md", "r1")).toBeNull();
  expect(cache.get("a.md", "r2")?.content).toBe("two");
  cache.set(b);
  cache.get("a.md", "r2");
  cache.set(c);
  expect(cache.get("b.md", "r1")).toBeNull();
  expect(cache.size).toBe(2);
});

test("artifact cache bounds revision aliases and removes aliases for evicted details", () => {
  const cache = new ArtifactCache(2);
  cache.set(detail(summary("a.md", "A", "server-a"), "A"), "listed-a");
  cache.set(detail(summary("b.md", "B", "server-b"), "B"), "listed-b");
  expect(cache.aliasSize).toBe(2);
  cache.set(detail(summary("c.md", "C", "server-c"), "C"), "listed-c");
  expect(cache.size).toBe(2);
  expect(cache.aliasSize).toBe(2);
  expect(cache.get("a.md", "listed-a")).toBeNull();
  expect(cache.get("a.md", "server-a")).toBeNull();
  expect(cache.get("c.md", "listed-c")?.content).toBe("C");
});

test("a refreshed Memory entry hydrates its active tab and note together", async () => {
  const index = summary("index.md", "Index");
  const me = summary("me.md", "Me");
  const indexPage = wikiPage(index, "Index body");
  const mePage = wikiPage(me, "Me body");
  const bridge = installCanonicalMemoryBridge({ pages: [indexPage, mePage] });

  const { host, root } = setup("me.md");
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(300);
  expect(host.querySelector("h1")?.textContent).toBe("me");
  localStorage.removeItem("arden.desktop.memory.lastPath");

  bridge.state.pages.clear();
  await act(async () => useStore.setState((state) => ({ memoryVaultVersion: state.memoryVaultVersion + 1 })));
  await settle(160);
  bridge.updatePage(indexPage);
  bridge.updatePage(mePage);
  await act(async () => useStore.setState((state) => ({ memoryVaultVersion: state.memoryVaultVersion + 1 })));
  await settle(300);

  expect(host.querySelector(".mw-doc-tab.on")?.textContent).toContain("me");
  expect(host.querySelector("h1")?.textContent).toBe("me");
  expect(host.textContent).not.toContain("Nothing selected");
});

test("wikilink resolution follows the server result including aliases, headings, and ambiguity", () => {
  const links = {
    path: "a.md", revision: "ledger:1", stale: false, backlinks: [], totalBacklinks: 0, totalOutgoing: 2, limit: 100, offset: 0,
    outgoing: [
      { sourcePath: "a.md", target: "Dex alias#Decisions", display: "Dex", heading: null, context: "See Dex", line: 2, column: 5, status: "resolved", resolvedPath: "topics/dex.md", candidates: ["topics/dex.md"], sourceRevision: "ledger:1" },
      { sourcePath: "a.md", target: "Shared", display: "Shared", heading: null, context: "Shared", line: 3, column: 1, status: "ambiguous", resolvedPath: null, candidates: ["x.md", "y.md"], sourceRevision: "ledger:1" },
    ],
  } satisfies PageLinks;
  expect(resolveWikiTarget(links, "Dex alias#Decisions")).toEqual({ path: "topics/dex.md", anchor: "Decisions" });
  expect(resolveWikiTarget(links, "Shared")).toBeNull();
  expect(resolveWikiTarget(links, "topics/dex")).toBeNull();
  expect(resolveWikiTarget({ ...links, stale: true }, "Dex alias#Decisions")).toBeNull();
});

test("preview delays hover and focus, bridges into the tooltip, and exposes its description", async () => {
  const alpha = summary("alpha.md", "Alpha", "r1");
  const beta = summary("beta.md", "Beta", "r1");
  const links: PageLinks = {
    path: "source.md", revision: "ledger:1", stale: false, backlinks: [], totalBacklinks: 0, totalOutgoing: 2, limit: 100, offset: 0,
    outgoing: [alpha, beta].map((item) => ({
      sourcePath: "source.md", target: item.title, display: item.title, heading: null, context: item.title,
      line: 1, column: 1, status: "resolved", resolvedPath: item.path, candidates: [item.path], sourceRevision: "ledger:1",
    })),
  };
  const requests: Array<{ path: string; signal: AbortSignal }> = [];
  const containerRef = createRef<HTMLDivElement>();
  const { host, root } = setup();
  await act(async () => root.render(
    <div ref={containerRef}>
      <a href="#wikilink" data-wikilink="Alpha">Alpha</a>
      <a href="#wikilink" data-wikilink="Beta">Beta</a>
      <WikiLinkPreview
        containerRef={containerRef}
        links={links}
        summaries={[alpha, beta]}
        cache={new ArtifactCache()}
        loadDetail={(path, signal) => new Promise((resolve) => {
          requests.push({ path, signal });
          setTimeout(() => resolve(detail(path === alpha.path ? alpha : beta, `# ${path}\n\n- **First meaningful paragraph.**`)), 20);
        })}
      />
    </div>,
  ));
  const [alphaLink, betaLink] = Array.from(host.querySelectorAll<HTMLAnchorElement>("[data-wikilink]"));
  await act(async () => alphaLink!.dispatchEvent(new MouseEvent("mouseover", { bubbles: true })));
  await settle(250);
  expect(requests).toHaveLength(0);
  await settle(70);
  expect(requests[0]?.path).toBe("alpha.md");
  await settle(30);
  const alphaPreview = document.querySelector<HTMLElement>('[role="tooltip"]')!;
  expect(alphaPreview.id).not.toBe("");
  expect(alphaLink?.getAttribute("aria-describedby")).toBe(alphaPreview.id);
  await act(async () => alphaLink!.dispatchEvent(new MouseEvent("mouseout", { bubbles: true })));
  await settle(60);
  await act(async () => alphaPreview.dispatchEvent(new MouseEvent("mouseenter", { bubbles: true })));
  await settle(100);
  expect(document.querySelector('[role="tooltip"]')).not.toBeNull();
  await act(async () => alphaPreview.dispatchEvent(new MouseEvent("mouseleave", { bubbles: true })));
  // The hide delay is followed by the mock's 180ms portaled-popover exit.
  await settleUntil(() => document.querySelector('[role="tooltip"]') === null);
  expect(document.querySelector('[role="tooltip"]')).toBeNull();

  await act(async () => betaLink!.dispatchEvent(new FocusEvent("focusin", { bubbles: true })));
  await settle(250);
  expect(requests.filter((request) => request.path === "beta.md")).toHaveLength(0);
  await settleUntil(() => document.querySelector('[role="tooltip"]')?.textContent?.includes("First meaningful paragraph.") === true);
  expect(document.querySelector('[role="tooltip"]')?.textContent).toContain("First meaningful paragraph.");
  expect(document.querySelector(".memory-link-preview-markdown li strong")?.textContent).toBe("First meaningful paragraph.");

  await act(async () => betaLink!.dispatchEvent(new FocusEvent("focusout", { bubbles: true })));
  await act(async () => betaLink!.dispatchEvent(new FocusEvent("focusin", { bubbles: true })));
  await settle();
  expect(requests.filter((request) => request.path === "beta.md")).toHaveLength(1);
});

test("preview resolves an exact Markdown artifact path without a wikilink ledger entry", async () => {
  const topic = summary("topics/o-1a.md", "O-1A", "r1");
  const containerRef = createRef<HTMLDivElement>();
  const { host, root } = setup();
  await act(async () => root.render(
    <div ref={containerRef}>
      <a href="topics/o-1a.md" data-memory-path="topics/o-1a.md">O-1A</a>
      <WikiLinkPreview
        containerRef={containerRef}
        links={null}
        summaries={[topic]}
        cache={new ArtifactCache()}
        loadDetail={async () => detail(topic, "# O-1A\n\nPreview body.")}
      />
    </div>,
  ));
  const link = host.querySelector<HTMLAnchorElement>("[data-memory-path]")!;
  await act(async () => link.dispatchEvent(new FocusEvent("focusin", { bubbles: true })));
  await settleUntil(() => document.querySelector('[role="tooltip"]')?.textContent?.includes("Preview body.") === true);
  expect(document.querySelector('[role="tooltip"]')?.textContent).toContain("Preview body.");
});

test("preview clears with its link snapshot and revision mismatch is cached without refetch", async () => {
  const listed = summary("alpha.md", "Alpha", "listed-r1");
  const current = detail(summary("alpha.md", "Alpha", "server-r2"), "Server paragraph.");
  const links: PageLinks = {
    path: "source.md", revision: "ledger:1", stale: false, backlinks: [], totalBacklinks: 0, totalOutgoing: 1, limit: 100, offset: 0,
    outgoing: [{ sourcePath: "source.md", target: "Alpha", display: "Alpha", heading: null, context: "Alpha", line: 1, column: 1, status: "resolved", resolvedPath: "alpha.md", candidates: ["alpha.md"], sourceRevision: "ledger:1" }],
  };
  let reads = 0;
  const containerRef = createRef<HTMLDivElement>();
  const cache = new ArtifactCache();
  const { host, root } = setup();
  const renderPreview = async (snapshot: PageLinks | null, listedSummaries = [listed]) => {
    await act(async () => root.render(
      <div ref={containerRef}>
        <a href="#wikilink" data-wikilink="Alpha">Alpha</a>
        <WikiLinkPreview
          containerRef={containerRef}
          links={snapshot}
          summaries={listedSummaries}
          cache={cache}
          loadDetail={async () => { reads += 1; return current; }}
        />
      </div>,
    ));
  };
  await renderPreview(links);
  const alpha = host.querySelector<HTMLAnchorElement>('[data-wikilink="Alpha"]')!;
  await act(async () => alpha.dispatchEvent(new FocusEvent("focusin", { bubbles: true })));
  await settle(320);
  expect(reads).toBe(1);
  await act(async () => alpha.dispatchEvent(new FocusEvent("focusout", { bubbles: true })));
  await settle(140);
  await act(async () => alpha.dispatchEvent(new FocusEvent("focusin", { bubbles: true })));
  await settle(320);
  expect(reads).toBe(1);
  expect(document.querySelector('[role="tooltip"]')).not.toBeNull();

  const refreshedSummary = summary("alpha.md", "Alpha", "server-r2");
  await renderPreview(links, [refreshedSummary]);
  expect(alpha.getAttribute("aria-describedby")).toBeNull();
  await settle(200);
  expect(document.querySelector('[role="tooltip"]')).toBeNull();
  const refreshedAlpha = host.querySelector<HTMLAnchorElement>('[data-wikilink="Alpha"]')!;
  await act(async () => refreshedAlpha.dispatchEvent(new FocusEvent("focusin", { bubbles: true })));
  await settle(320);
  expect(reads).toBe(1);

  await renderPreview({ ...links, revision: "ledger:2", outgoing: [] }, [refreshedSummary]);
  expect(refreshedAlpha.getAttribute("aria-describedby")).toBeNull();
  await settle(200);
  expect(document.querySelector('[role="tooltip"]')).toBeNull();
});

test("preview hover bridge covers the full padded surface", async () => {
  const alpha = summary("alpha.md", "Alpha", "r1");
  const links: PageLinks = {
    path: "source.md", revision: "ledger:1", stale: false, backlinks: [], totalBacklinks: 0, totalOutgoing: 1, limit: 100, offset: 0,
    outgoing: [{ sourcePath: "source.md", target: "Alpha", display: "Alpha", heading: null, context: "Alpha", line: 1, column: 1, status: "resolved", resolvedPath: "alpha.md", candidates: ["alpha.md"], sourceRevision: "ledger:1" }],
  };
  const containerRef = createRef<HTMLDivElement>();
  const { host, root } = setup();
  await act(async () => root.render(
    <div ref={containerRef}>
      <a href="#wikilink" data-wikilink="Alpha">Alpha</a>
      <WikiLinkPreview containerRef={containerRef} links={links} summaries={[alpha]} cache={new ArtifactCache()} loadDetail={async () => detail(alpha, "Paragraph")} />
    </div>,
  ));
  const link = host.querySelector<HTMLAnchorElement>('[data-wikilink="Alpha"]')!;
  await act(async () => link.dispatchEvent(new MouseEvent("mouseover", { bubbles: true })));
  await settle(320);
  const surface = document.querySelector<HTMLElement>('[data-memory-link-preview-surface]')!;
  expect(surface.classList.contains("p-3")).toBe(true);
  await act(async () => link.dispatchEvent(new MouseEvent("mouseout", { bubbles: true })));
  await settle(60);
  await act(async () => surface.dispatchEvent(new MouseEvent("mouseenter", { bubbles: true })));
  await settle(100);
  expect(document.querySelector('[role="tooltip"]')).not.toBeNull();
});

test("notebook history shortcuts restore pages and ignore focused editors", async () => {
  const index = summary("index.md", "Index");
  const a = summary("a.md", "A");
  const b = summary("b.md", "B");
  const indexPage = wikiPage(index, "Index body");
  const aPage = wikiPage(a, "[[Bee|B first]] · [[Bee|B second]] · `b.md` · `b.md` · [[#Details|Details]]\n\n## Details\n\nMore.");
  const bPage = wikiPage(b, "B body");
  const bridge = installCanonicalMemoryBridge({
    pages: [indexPage, aPage, bPage],
    links: {
      [aPage.pageId]: {
        outgoing: [
          wikiLink(aPage, "Bee", bPage, "B first"),
          wikiLink(aPage, "Bee", bPage, "B second"),
          wikiLink(aPage, "#Details", aPage, "Details"),
        ],
      },
    },
  });
  const { host, root } = setup("a.md");
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(350);
  expect(host.querySelector("h1")?.textContent).toBe("a");
  expect(host.querySelector('button[aria-label="Back in memory history"]')).toBeNull();
  expect(bridge.requests.some((request) => request.path.includes("/history"))).toBe(false);
  let inlinePaths = Array.from(host.querySelectorAll<HTMLAnchorElement>('[data-memory-path="b.md"]'));
  inlinePaths[1]!.focus();
  await act(async () => inlinePaths[1]!.click());
  await settle(250);
  await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "[", metaKey: true, bubbles: true })));
  await settleUntil(() => {
    const links = Array.from(host.querySelectorAll<HTMLAnchorElement>('[data-memory-path="b.md"]'));
    return links.indexOf(document.activeElement as HTMLAnchorElement) === 1;
  });
  inlinePaths = Array.from(host.querySelectorAll<HTMLAnchorElement>('[data-memory-path="b.md"]'));
  expect(inlinePaths.indexOf(document.activeElement as HTMLAnchorElement)).toBe(1);

  const detailsLink = host.querySelector<HTMLAnchorElement>('[data-wikilink="#Details"]')!;
  await act(async () => detailsLink.click());
  await settle(30);
  expect(document.activeElement?.textContent).toBe("Details");
  await act(async () => host.querySelector<HTMLButtonElement>('[data-memory-entry="a.md"]')?.click());
  await settle(30);
  let beeLinks = Array.from(host.querySelectorAll<HTMLAnchorElement>('[data-wikilink="Bee"]'));
  beeLinks[1]!.focus();
  await act(async () => beeLinks[1]!.click());
  await settle(250);
  await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "[", metaKey: true, bubbles: true })));
  await settleUntil(() => {
    const links = Array.from(host.querySelectorAll<HTMLAnchorElement>('[data-wikilink="Bee"]'));
    return links.indexOf(document.activeElement as HTMLAnchorElement) === 1;
  });
  beeLinks = Array.from(host.querySelectorAll<HTMLAnchorElement>('[data-wikilink="Bee"]'));
  expect(beeLinks.indexOf(document.activeElement as HTMLAnchorElement)).toBe(1);
  expect(beeLinks[1]!.classList.contains("wikilink--unresolved")).toBe(false);
  await act(async () => beeLinks[1]!.click());
  await settle(250);
  expect(bridge.requests.some((request) => request.path === `/admin/wiki/pages/${bPage.pageId}`)).toBe(true);
  expect(host.querySelector("h1")?.textContent).toBe("b");
  await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "[", metaKey: true, bubbles: true })));
  await settle(250);
  expect(host.querySelector("h1")?.textContent).toBe("a");

  // A focused quick-switcher input must swallow history shortcuts.
  await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "o", metaKey: true, bubbles: true })));
  await settle();
  const input = document.querySelector<HTMLInputElement>('[aria-label="Quick switcher"] input')!;
  await act(async () => input.focus());
  await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "]", metaKey: true, bubbles: true })));
  await settle(250);
  expect(host.querySelector("h1")?.textContent).toBe("a");
  await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
  await settle();
  await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "]", ctrlKey: true, bubbles: true })));
  await settle(250);
  expect(host.querySelector("h1")?.textContent).toBe("b");
  expect(bridge.requests.some((request) => request.path.includes("/history"))).toBe(false);
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Open links"]')?.click());
  await settle(30);
  expect(bridge.requests.some((request) => request.path.includes("/history?limit=100"))).toBe(true);
});

test("a delayed link snapshot cannot resolve the next page or overwrite a later selection", async () => {
  const index = summary("index.md", "Index");
  const a = summary("a.md", "A");
  const b = summary("b.md", "B");
  const x = summary("x.md", "X");
  const y = summary("y.md", "Y");
  const indexPage = wikiPage(index, "Index body");
  const aPage = wikiPage(a, "[[Shared]]");
  const bPage = wikiPage(b, "[[Shared]]");
  const xPage = wikiPage(x, "X body");
  const yPage = wikiPage(y, "Y body");
  let releaseB: (() => void) | null = null;
  const bGate = new Promise<void>((resolve) => { releaseB = resolve; });
  installCanonicalMemoryBridge({
    pages: [indexPage, aPage, bPage, xPage, yPage],
    onRequest: async ({ path }) => {
      if (path === `/admin/wiki/pages/${bPage.pageId}/links`) await bGate;
      if (path === `/admin/wiki/pages/${aPage.pageId}/links` || path === `/admin/wiki/pages/${bPage.pageId}/links`) {
        const source = path.includes(aPage.pageId) ? aPage : bPage;
        const target = source === aPage ? xPage : yPage;
        return ok({
          page_id: source.pageId,
          repository_head: "wiki-head-1",
          outgoing: [{
            source_page_id: source.pageId,
            node: { target: "Shared", alias: null, heading: null },
            status: "resolved",
            target_page_id: target.pageId,
            candidates: [target.pageId],
          }],
          backlinks: [],
        });
      }
      return undefined;
    },
  });
  const { host, root } = setup("a.md");
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(350);
  expect(host.querySelector<HTMLAnchorElement>('[data-wikilink="Shared"]')?.classList.contains("wikilink--unresolved")).toBe(false);

  await act(async () => host.querySelector<HTMLButtonElement>('[data-memory-entry="b.md"]')?.click());
  await settle(250);
  expect(host.querySelector("h1")?.textContent).toBe("b");
  expect(host.querySelector<HTMLAnchorElement>('[data-wikilink="Shared"]')?.classList.contains("wikilink--unresolved")).toBe(true);

  await act(async () => host.querySelector<HTMLButtonElement>('[data-memory-entry="a.md"]')?.click());
  await settle(250);
  releaseB?.();
  await settle(100);
  const current = host.querySelector<HTMLAnchorElement>('[data-wikilink="Shared"]')!;
  expect(host.querySelector("h1")?.textContent).toBe("a");
  expect(current.classList.contains("wikilink--unresolved")).toBe(false);
  await act(async () => current.click());
  await settle(250);
  expect(host.querySelector("h1")?.textContent).toBe("x");
});

test("opening the inspector fetches full-page links and history once and pane switches stay local", async () => {
  const index = summary("index.md", "Index");
  const note = summary("note.md", "Note");
  const roadmap = summary("roadmap.md", "Roadmap");
  const indexPage = wikiPage(index, "Index body");
  const notePage = wikiPage(note, "Note body");
  const roadmapPage = wikiPage(roadmap, "Roadmap body");
  const bridge = installCanonicalMemoryBridge({
    pages: [indexPage, notePage, roadmapPage],
    links: {
      [notePage.pageId]: {
        outgoing: [wikiLink(notePage, "Roadmap", roadmapPage)],
        backlinks: [{
          sourcePageId: roadmapPage.pageId,
          target: "Note",
          targetPageId: notePage.pageId,
          candidates: [notePage.pageId],
        }],
      },
    },
    history: {
      [notePage.pageId]: [
        {
          commitId: "latest",
          parentId: "older",
          actor: "user:latest",
          changes: [{ action: "update", before: { resourceId: notePage.pageId, path: notePage.path, state: "active", versionId: "r4" }, after: { resourceId: notePage.pageId, path: notePage.path, state: "active", versionId: "r5" } }],
          diff: "@@",
        },
        {
          commitId: "older",
          actor: "agent:older",
          changes: [{ action: "update", before: { resourceId: notePage.pageId, path: notePage.path, state: "active", versionId: "r3" }, after: { resourceId: notePage.pageId, path: notePage.path, state: "active", versionId: "r4" } }],
          diff: "@@",
        },
      ],
    },
  });
  const { host, root } = setup("note.md");
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(300);
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Open links"]')?.click());
  await settle(100);

  // The canonical page links and history endpoints are fetched once.
  const linkRequests = bridge.requests.filter((request) => request.path === `/admin/wiki/pages/${notePage.pageId}/links`);
  expect(linkRequests).toHaveLength(1);
  const historyRequests = bridge.requests.filter((request) => request.path === `/admin/wiki/pages/${notePage.pageId}/history?limit=100`);
  expect(historyRequests).toHaveLength(1);

  // The mock keeps the links instrument compact: outgoing links are the
  // default view; incoming mentions remain one local tab away.
  const aside = host.querySelector<HTMLElement>('aside[aria-label="Page peek"]')!;
  expect(aside.textContent).toContain("Roadmap");
  await act(async () => aside.querySelectorAll<HTMLButtonElement>('[role="tab"]')[1]?.click());
  await settleUntil(() => aside.textContent?.includes("Note") === true);
  expect(aside.textContent).toContain("Note");
  expect(aside.textContent).not.toContain("[[Note]]");
  expect(aside.querySelector("mark")?.textContent).toBe("Note");

  // Switching panes is local state — no refetch.
  const requestCount = bridge.requests.length;
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Open activity"]')?.click());
  await settleUntil(() => aside.textContent?.includes("user:latest") === true);
  expect(aside.textContent).toContain("user:latest");
  expect(aside.textContent).toContain("agent:older");
  expect(bridge.requests.length).toBe(requestCount);
});

test("a vault change refetches the canonical link snapshot", async () => {
  const index = summary("index.md", "Index");
  const note = summary("note.md", "Note");
  const target = summary("target.md", "Target");
  const indexPage = wikiPage(index, "Index body");
  const notePage = wikiPage(note, "[[Target]]");
  const targetPage = wikiPage(target, "Target body");
  let linkRead = 0;
  installCanonicalMemoryBridge({
    pages: [indexPage, notePage, targetPage],
    onRequest: ({ path }) => {
      if (path !== `/admin/wiki/pages/${notePage.pageId}/links`) return undefined;
      linkRead += 1;
      return ok({
        page_id: notePage.pageId,
        repository_head: "wiki-head-1",
        outgoing: [{
          source_page_id: notePage.pageId,
          node: { target: "Target", alias: null, heading: null },
          status: "resolved",
          target_page_id: targetPage.pageId,
          candidates: [targetPage.pageId],
        }],
        backlinks: [],
      });
    },
  });
  const { host, root } = setup("note.md");
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(350);
  expect(host.querySelector<HTMLAnchorElement>('[data-wikilink="Target"]')?.classList.contains("wikilink--unresolved")).toBe(false);
  expect(linkRead).toBe(1);

  // A vault change invalidates and refetches the same canonical link report.
  await act(async () => useStore.setState((state) => ({ memoryVaultVersion: state.memoryVaultVersion + 1 })));
  await settle(250);
  expect(linkRead).toBe(2);
  const link = host.querySelector<HTMLAnchorElement>('[data-wikilink="Target"]')!;
  expect(link.classList.contains("wikilink--unresolved")).toBe(false);
  await act(async () => link.click());
  await settle(250);
  expect(host.querySelector("h1")?.textContent).toBe("target");
});

test("vault revision refresh invalidates the selected detail cache", async () => {
  const index = summary("index.md", "Index");
  let note = summary("note.md", "Note", "r1");
  const indexPage = wikiPage(index, "Index body");
  const notePage = wikiPage(note, "version 1");
  const bridge = installCanonicalMemoryBridge({ pages: [indexPage, notePage] });
  const { host, root } = setup("note.md");
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(250);
  expect(host.textContent).toContain("version 1");
  note = summary("note.md", "Note", "r2");
  bridge.updatePage(wikiPage(note, "version 2"));
  await act(async () => useStore.setState((state) => ({ memoryVaultVersion: state.memoryVaultVersion + 1 })));
  await settle(250);
  expect(bridge.requests.filter((request) => request.path === `/admin/wiki/pages/${notePage.pageId}`)).toHaveLength(2);
  expect(host.textContent).toContain("version 2");
});
