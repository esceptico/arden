import { afterEach, expect, test } from "bun:test";
import { act, createRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { AppConfig } from "@/api/core";
import { ArtifactMemoryView } from "@/features/memory/components/ArtifactMemoryView";
import { WikiLinkPreview } from "@/features/memory/components/WikiLinkPreview";
import { ArtifactCache, RevisionCache } from "@/features/memory/lib/artifactCache";
import { NavigationHistory } from "@/features/memory/lib/navigationHistory";
import { resolveWikiTarget } from "@/features/memory/lib/wikiResolution";
import type { MemoryArtifactDetail, MemoryArtifactSummary, PageLinks } from "@/features/memory/lib/notebookTypes";
import { useStore } from "@/stores";

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

function rawArtifact(item: MemoryArtifactSummary, content?: string) {
  return {
    path: item.path, title: item.title, kind: item.kind, type: "file", directory: item.directory,
    scope: item.scope, content: content ?? "", snippet: item.snippet, summary: item.summary,
    revision: item.revision, record_count: item.recordCount, generated: item.generated, editable: item.editable,
    editable_content: content ?? "", readonly_reason: item.readonlyReason, updated_at: item.updatedAt,
    labels: item.labels, source: item.source, timeline: [], frontmatter: {},
  };
}

function response(data: unknown) {
  return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data, text: "" };
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

test("revision cache bounds index bodies and replaces obsolete revisions per path", () => {
  const cache = new RevisionCache<string>(2);
  cache.set("index.md", "r1", "old index");
  cache.set("index.md", "r2", "current index");
  expect(cache.get("index.md", "r1")).toBeNull();
  cache.set("a/README.md", "r1", "A");
  cache.set("b/README.md", "r1", "B");
  expect(cache.size).toBe(2);
  expect(cache.get("index.md", "r2")).toBeNull();
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
          setTimeout(() => resolve(detail(path === alpha.path ? alpha : beta, `# ${path}\n\nFirst meaningful paragraph.`)), 20);
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
  await settle(140);
  expect(document.querySelector('[role="tooltip"]')).toBeNull();

  await act(async () => betaLink!.dispatchEvent(new FocusEvent("focusin", { bubbles: true })));
  await settle(250);
  expect(requests.filter((request) => request.path === "beta.md")).toHaveLength(0);
  await settleUntil(() => document.querySelector('[role="tooltip"]')?.textContent?.includes("First meaningful paragraph.") === true);
  expect(document.querySelector('[role="tooltip"]')?.textContent).toContain("First meaningful paragraph.");

  await act(async () => betaLink!.dispatchEvent(new FocusEvent("focusout", { bubbles: true })));
  await act(async () => betaLink!.dispatchEvent(new FocusEvent("focusin", { bubbles: true })));
  await settle();
  expect(requests.filter((request) => request.path === "beta.md")).toHaveLength(1);
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
  const rows = [index, a, b];
  const requests: string[] = [];
  window.ardenDesktop = { api: { request: async (_config, request) => {
    requests.push(request.path);
    if (request.path === "/admin/memory/artifacts") return response({ artifacts: rows.map((item) => rawArtifact(item)) });
    if (request.path.startsWith("/admin/memory/links")) {
      const path = new URL(`http://x${request.path}`).searchParams.get("path")!;
      const outgoing = path === "a.md" ? [
        { source_path: "a.md", target: "Bee", display: "B first", heading: null, context: "Go to B", line: 1, column: 1, status: "resolved", resolved_path: "b.md", candidates: ["b.md"], source_revision: "ledger:1" },
        { source_path: "a.md", target: "Bee", display: "B second", heading: null, context: "Go to B again", line: 1, column: 20, status: "resolved", resolved_path: "b.md", candidates: ["b.md"], source_revision: "ledger:1" },
        { source_path: "a.md", target: "#Details", display: "Details", heading: null, context: "Jump to Details", line: 2, column: 1, status: "resolved", resolved_path: "a.md", candidates: ["a.md"], source_revision: "ledger:1" },
      ] : [];
      return response({ path, revision: "ledger:1", stale: false, outgoing, backlinks: [], total_outgoing: outgoing.length, total_backlinks: 0, limit: 100, offset: 0 });
    }
    if (request.path.startsWith("/admin/memory/page-edits/history")) return response({ events: [], total: 0, limit: 100, next_before_sequence: null });
    const path = decodeURIComponent(request.path.replace("/admin/memory/artifacts/", ""));
    const item = rows.find((row) => row.path === path)!;
    const content = path === "index.md" ? "Index body" : path === "a.md" ? "[[Bee|B first]] · [[Bee|B second]] · `b.md` · `b.md` · [[#Details|Details]]\n\n## Details\n\nMore." : "B body";
    return response({ artifact: rawArtifact(item, content) });
  } } } as Window["ardenDesktop"];
  const { host, root } = setup("a.md");
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(350);
  expect(host.querySelector("h1")?.textContent).toBe("a");
  expect(host.querySelector<HTMLButtonElement>('button[aria-label="Back in memory history"]')?.disabled).toBe(true);
  expect(requests.some((path) => path.startsWith("/admin/memory/page-edits/history"))).toBe(false);
  let inlinePaths = Array.from(host.querySelectorAll<HTMLAnchorElement>('[data-memory-inline-path="b.md"]'));
  inlinePaths[1]!.focus();
  await act(async () => inlinePaths[1]!.click());
  await settle(250);
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Back in memory history"]')?.click());
  await settleUntil(() => {
    const links = Array.from(host.querySelectorAll<HTMLAnchorElement>('[data-memory-inline-path="b.md"]'));
    return links.indexOf(document.activeElement as HTMLAnchorElement) === 1;
  });
  inlinePaths = Array.from(host.querySelectorAll<HTMLAnchorElement>('[data-memory-inline-path="b.md"]'));
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
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Back in memory history"]')?.click());
  await settleUntil(() => {
    const links = Array.from(host.querySelectorAll<HTMLAnchorElement>('[data-wikilink="Bee"]'));
    return links.indexOf(document.activeElement as HTMLAnchorElement) === 1;
  });
  beeLinks = Array.from(host.querySelectorAll<HTMLAnchorElement>('[data-wikilink="Bee"]'));
  expect(beeLinks.indexOf(document.activeElement as HTMLAnchorElement)).toBe(1);
  expect(beeLinks[1]!.classList.contains("wikilink--unresolved")).toBe(false);
  await act(async () => beeLinks[1]!.click());
  await settle(250);
  expect(requests.some((path) => path === "/admin/memory/artifacts/b.md")).toBe(true);
  expect(host.querySelector("h1")?.textContent).toBe("b");
  const back = host.querySelector<HTMLButtonElement>('button[aria-label="Back in memory history"]')!;
  const forward = host.querySelector<HTMLButtonElement>('button[aria-label="Forward in memory history"]')!;
  expect(back.disabled).toBe(false);
  expect(forward.disabled).toBe(true);
  await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "[", metaKey: true, bubbles: true })));
  await settle(250);
  expect(host.querySelector("h1")?.textContent).toBe("a");
  expect(back.disabled).toBe(false);
  expect(forward.disabled).toBe(false);

  // The rail has no search input any more — the create-note input is the
  // editable field that must swallow the history shortcuts.
  await act(async () => host.querySelector<HTMLButtonElement>('button[title="New note"]')?.click());
  const input = host.querySelector<HTMLInputElement>('input[aria-label="New note path"]')!;
  await act(async () => input.focus());
  await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "]", metaKey: true, bubbles: true })));
  await settle(250);
  expect(host.querySelector("h1")?.textContent).toBe("a");
  await act(async () => input.blur());
  await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "]", ctrlKey: true, bubbles: true })));
  await settle(250);
  expect(host.querySelector("h1")?.textContent).toBe("b");
  expect(requests.some((path) => path.startsWith("/admin/memory/page-edits/history"))).toBe(false);
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Open links and provenance"]')?.click());
  await settle(30);
  expect(requests.some((path) => path.startsWith("/admin/memory/page-edits/history"))).toBe(true);
});

test("a delayed link snapshot cannot resolve the next page or overwrite a later selection", async () => {
  const index = summary("index.md", "Index");
  const a = summary("a.md", "A");
  const b = summary("b.md", "B");
  const x = summary("x.md", "X");
  const y = summary("y.md", "Y");
  const rows = [index, a, b, x, y];
  let releaseB: (() => void) | null = null;
  const bGate = new Promise<void>((resolve) => { releaseB = resolve; });
  window.ardenDesktop = { api: { request: async (_config, request) => {
    if (request.path === "/admin/memory/artifacts") return response({ artifacts: rows.map((item) => rawArtifact(item)) });
    if (request.path.startsWith("/admin/memory/links")) {
      const path = new URL(`http://x${request.path}`).searchParams.get("path")!;
      if (path === "b.md") await bGate;
      const target = path === "a.md" ? "x.md" : path === "b.md" ? "y.md" : null;
      const outgoing = target ? [{ source_path: path, target: "Shared", display: "Shared", heading: null, context: "Shared", line: 1, column: 1, status: "resolved", resolved_path: target, candidates: [target], source_revision: "ledger:1" }] : [];
      return response({ path, revision: "ledger:1", stale: false, outgoing, backlinks: [], total_outgoing: outgoing.length, total_backlinks: 0, limit: 100, offset: 0 });
    }
    if (request.path.startsWith("/admin/memory/page-edits/history")) return response({ events: [], total: 0, limit: 100, next_before_sequence: null });
    const path = decodeURIComponent(request.path.replace("/admin/memory/artifacts/", ""));
    const item = rows.find((row) => row.path === path)!;
    const content = path === "index.md"
      ? "Index body"
      : path === "a.md" || path === "b.md" ? "[[Shared]]" : `${item.title} body`;
    return response({ artifact: rawArtifact(item, content) });
  } } } as Window["ardenDesktop"];
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
  const rows = [index, note, roadmap];
  const requests: string[] = [];
  const rawEvent = (id: string, sequence: number, actor: string) => ({
    event_type: "PAGE_EDIT", id, occurred_at: `2026-07-13T08:20:3${sequence}.123+04:00`, sequence,
    actor, origin: "desktop", path: "note.md", base_revision: `r${sequence - 1}`, result_revision: `r${sequence}`,
    patch: "@@", operations: [], reconciliation: "applied", analysis: null, reconciles_event_id: null,
    review_operations: [], questions: [], review_event_id: null, observation_id: null, source_canonical_revision: null,
  });
  window.ardenDesktop = { api: { request: async (_config, request) => {
    requests.push(request.path);
    if (request.path === "/admin/memory/artifacts") return response({ artifacts: rows.map((item) => rawArtifact(item)) });
    if (request.path.startsWith("/admin/memory/links")) {
      const outgoing = [{ source_path: "note.md", target: "Roadmap", display: "Roadmap", heading: null, context: "See [[Roadmap]]", line: 1, column: 1, status: "resolved", resolved_path: "roadmap.md", candidates: ["roadmap.md"], source_revision: "ledger:1" }];
      const backlinks = [{ source_path: "roadmap.md", target: "Note", display: "Note", heading: null, context: "worked on [[Note]]", line: 2, column: 1, status: "resolved", resolved_path: "note.md", candidates: ["note.md"], source_revision: "ledger:1" }];
      const unlinked = [{ source_path: "index.md", context: "note mentioned plainly" }];
      return response({ path: "note.md", revision: "ledger:1", stale: false, outgoing, backlinks, unlinked, total_outgoing: 1, total_backlinks: 1, limit: 100, offset: 0 });
    }
    if (request.path.startsWith("/admin/memory/page-edits/history")) {
      return response({ events: [rawEvent("latest", 5, "user:latest"), rawEvent("older", 4, "agent:older")], total: 2, limit: 100, next_before_sequence: null });
    }
    const path = decodeURIComponent(request.path.replace("/admin/memory/artifacts/", ""));
    if (path === "index.md") return response({ artifact: rawArtifact(index, "Index body") });
    return response({ artifact: rawArtifact(path === "roadmap.md" ? roadmap : note, "Note body") });
  } } } as Window["ardenDesktop"];
  const { host, root } = setup("note.md");
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(300);
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Open links and provenance"]')?.click());
  await settle(100);

  // Exactly one full-page fetch per endpoint — offset 0, limit 100.
  const linkRequests = requests.filter((path) => path.startsWith("/admin/memory/links"));
  expect(linkRequests).toHaveLength(1);
  expect(linkRequests[0]).toContain("limit=100");
  expect(linkRequests[0]).toContain("offset=0");
  const historyRequests = requests.filter((path) => path.startsWith("/admin/memory/page-edits/history"));
  expect(historyRequests).toHaveLength(1);
  expect(historyRequests[0]).toContain("limit=100");

  // Links pane: outgoing rows, linked mentions with cleaned excerpts, and
  // unlinked mentions from the server's unlinked field.
  const aside = host.querySelector<HTMLElement>('aside[aria-label="Links and provenance"]')!;
  expect(aside.textContent).toContain("Linked mentions");
  expect(aside.textContent).toContain("worked on Note");
  expect(aside.textContent).not.toContain("[[Note]]");
  expect(aside.querySelector("mark")?.textContent).toBe("Note");
  expect(aside.textContent).toContain("Unlinked mentions");
  expect(aside.textContent).toContain("note mentioned plainly");

  // Switching panes is local state — no refetch.
  const requestCount = requests.length;
  await act(async () => aside.querySelector<HTMLButtonElement>('button[aria-label="Activity"]')?.click());
  await settle(50);
  expect(aside.textContent).toContain("user:latest");
  expect(aside.textContent).toContain("agent:older");
  expect(requests.length).toBe(requestCount);
});

test("stale links stay unresolved and a vault change refetches the snapshot from offset zero", async () => {
  const index = summary("index.md", "Index");
  const note = summary("note.md", "Note");
  const target = summary("target.md", "Target");
  const rows = [index, note, target];
  const offsets: number[] = [];
  let linkRead = 0;
  window.ardenDesktop = { api: { request: async (_config, request) => {
    if (request.path === "/admin/memory/artifacts") return response({ artifacts: rows.map((item) => rawArtifact(item)) });
    if (request.path.startsWith("/admin/memory/links")) {
      const offset = Number(new URL(`http://x${request.path}`).searchParams.get("offset") ?? 0);
      offsets.push(offset);
      linkRead += 1;
      const stale = linkRead === 1;
      const outgoing = [{ source_path: "note.md", target: "Target", display: "Target", heading: null, context: "See [[Target]]", line: 1, column: 1, status: "resolved", resolved_path: "target.md", candidates: ["target.md"], source_revision: stale ? "ledger:stale" : "ledger:fresh" }];
      return response({ path: "note.md", revision: stale ? "ledger:stale" : "ledger:fresh", stale, outgoing, backlinks: [], total_outgoing: 1, total_backlinks: 0, limit: 100, offset });
    }
    if (request.path.startsWith("/admin/memory/page-edits/history")) return response({ events: [], total: 0, limit: 100, next_before_sequence: null });
    const path = decodeURIComponent(request.path.replace("/admin/memory/artifacts/", ""));
    if (path === "index.md") return response({ artifact: rawArtifact(index, "Index body") });
    return response({ artifact: rawArtifact(path === "note.md" ? note : target, path === "note.md" ? "[[Target]]" : "Target body") });
  } } } as Window["ardenDesktop"];
  const { host, root } = setup("note.md");
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(350);
  // A stale snapshot renders the wikilink but never resolves it.
  expect(host.querySelector<HTMLAnchorElement>('[data-wikilink="Target"]')?.classList.contains("wikilink--unresolved")).toBe(true);
  expect(offsets).toEqual([0]);

  // A vault change invalidates and refetches — always a full page from
  // offset zero, never a cursor continuation.
  await act(async () => useStore.setState((state) => ({ memoryVaultVersion: state.memoryVaultVersion + 1 })));
  await settle(250);
  expect(offsets).toEqual([0, 0]);
  const link = host.querySelector<HTMLAnchorElement>('[data-wikilink="Target"]')!;
  expect(link.classList.contains("wikilink--unresolved")).toBe(false);
  await act(async () => link.click());
  await settle(250);
  expect(host.querySelector("h1")?.textContent).toBe("target");
});

test("vault revision refresh invalidates the selected detail cache", async () => {
  const index = summary("index.md", "Index");
  let note = summary("note.md", "Note", "r1");
  let detailReads = 0;
  window.ardenDesktop = { api: { request: async (_config, request) => {
    if (request.path === "/admin/memory/artifacts") return response({ artifacts: [rawArtifact(index), rawArtifact(note)] });
    if (request.path.startsWith("/admin/memory/links")) return response({ path: "note.md", revision: "ledger:1", stale: false, outgoing: [], backlinks: [], total_outgoing: 0, total_backlinks: 0, limit: 100, offset: 0 });
    if (request.path.startsWith("/admin/memory/page-edits/history")) return response({ events: [], total: 0, limit: 100, next_before_sequence: null });
    const path = decodeURIComponent(request.path.replace("/admin/memory/artifacts/", ""));
    if (path === "index.md") return response({ artifact: rawArtifact(index, "Index body") });
    detailReads += 1;
    return response({ artifact: rawArtifact(note, `version ${detailReads}`) });
  } } } as Window["ardenDesktop"];
  const { host, root } = setup("note.md");
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(250);
  expect(host.textContent).toContain("version 1");
  note = summary("note.md", "Note", "r2");
  await act(async () => useStore.setState((state) => ({ memoryVaultVersion: state.memoryVaultVersion + 1 })));
  await settle(250);
  expect(detailReads).toBe(2);
  expect(host.textContent).toContain("version 2");
});
