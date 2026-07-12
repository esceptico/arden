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
const originalDesktop = window.ntrpDesktop;
const originalVaultVersion = useStore.getState().memoryVaultVersion;
const roots = new Set<Root>();

const summary = (path: string, title: string, revision = `sha256:${path}`): MemoryArtifactSummary => ({
  path, title, kind: "topic", type: "file", directory: path.includes("/") ? path.split("/")[0]! : "",
  scope: { kind: "user", key: null }, snippet: null, summary: null, revision, recordCount: 0,
  generated: false, editable: true, readonlyReason: null, updatedAt: "2026-07-13T08:00:00Z", labels: [], source: null,
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

function setup() {
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

afterEach(async () => {
  for (const root of roots) await act(async () => root.unmount());
  roots.clear();
  window.ntrpDesktop = originalDesktop;
  useStore.setState({ memoryVaultVersion: originalVaultVersion });
  document.body.replaceChildren();
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
});

test("preview waits for hover intent, opens on focus, caches by revision, and aborts stale targets", async () => {
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
  await act(async () => betaLink!.dispatchEvent(new FocusEvent("focusin", { bubbles: true })));
  expect(requests[0]?.signal.aborted).toBe(true);
  await settle(30);
  expect(document.querySelector('[aria-label="Beta link preview"]')?.textContent).toContain("First meaningful paragraph.");

  await act(async () => betaLink!.dispatchEvent(new FocusEvent("focusout", { bubbles: true })));
  await act(async () => betaLink!.dispatchEvent(new FocusEvent("focusin", { bubbles: true })));
  await settle();
  expect(requests.filter((request) => request.path === "beta.md")).toHaveLength(1);
});

test("notebook history shortcuts restore pages and ignore focused editors", async () => {
  const index = summary("index.md", "Index");
  const a = summary("a.md", "A");
  const b = summary("b.md", "B");
  const rows = [index, a, b];
  const requests: string[] = [];
  window.ntrpDesktop = { api: { request: async (_config, request) => {
    requests.push(request.path);
    if (request.path === "/admin/memory/artifacts") return response({ artifacts: rows.map((item) => rawArtifact(item)) });
    if (request.path.startsWith("/admin/memory/links")) {
      const path = new URL(`http://x${request.path}`).searchParams.get("path")!;
      const outgoing = path === "a.md" ? [
        { source_path: "a.md", target: "Bee", display: "B", heading: null, context: "Go to B", line: 1, column: 1, status: "resolved", resolved_path: "b.md", candidates: ["b.md"], source_revision: "ledger:1" },
        { source_path: "a.md", target: "#Details", display: "Details", heading: null, context: "Jump to Details", line: 2, column: 1, status: "resolved", resolved_path: "a.md", candidates: ["a.md"], source_revision: "ledger:1" },
      ] : [];
      return response({ path, revision: "ledger:1", stale: false, outgoing, backlinks: [], total_outgoing: outgoing.length, total_backlinks: 0, limit: 100, offset: 0 });
    }
    if (request.path.startsWith("/admin/memory/page-edits/history")) return response({ events: [], total: 0, limit: 100, next_before_sequence: null });
    const path = decodeURIComponent(request.path.replace("/admin/memory/artifacts/", ""));
    const item = rows.find((row) => row.path === path)!;
    const content = path === "index.md" ? "<!-- ntrp:index:start -->\n- a.md <!-- ntrp:path=a.md -->\n- b.md <!-- ntrp:path=b.md -->\n<!-- ntrp:index:end -->" : path === "a.md" ? "[[Bee|B]] · `b.md` · [[#Details|Details]]\n\n## Details\n\nMore." : "B body";
    return response({ artifact: rawArtifact(item, content) });
  } } } as Window["ntrpDesktop"];
  const { host, root } = setup();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(350);
  expect(host.querySelector("h1")?.textContent).toBe("A");
  expect(host.querySelector<HTMLButtonElement>('button[aria-label="Back in memory history"]')?.disabled).toBe(true);
  expect(requests.some((path) => path.startsWith("/admin/memory/page-edits/history"))).toBe(false);
  const detailsLink = host.querySelector<HTMLAnchorElement>('[data-wikilink="#Details"]')!;
  await act(async () => detailsLink.click());
  await settle(30);
  expect(document.activeElement?.textContent).toBe("Details");
  const inlinePath = Array.from(host.querySelectorAll<HTMLAnchorElement>('a[href="#wikilink"]'))
    .find((link) => link.textContent === "b.md")!;
  expect(inlinePath).not.toBeNull();
  await act(async () => inlinePath.click());
  await settle(250);
  expect(host.querySelector("h1")?.textContent).toBe("B");
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Back in memory history"]')?.click());
  await settle(250);
  const bee = host.querySelector<HTMLAnchorElement>('[data-wikilink="Bee"]')!;
  expect(bee.classList.contains("wikilink--unresolved")).toBe(false);
  await act(async () => bee.click());
  await settle(250);
  expect(requests.some((path) => path === "/admin/memory/artifacts/b.md")).toBe(true);
  expect(host.querySelector("h1")?.textContent).toBe("B");
  const back = host.querySelector<HTMLButtonElement>('button[aria-label="Back in memory history"]')!;
  const forward = host.querySelector<HTMLButtonElement>('button[aria-label="Forward in memory history"]')!;
  expect(back.disabled).toBe(false);
  expect(forward.disabled).toBe(true);
  await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "[", metaKey: true, bubbles: true })));
  await settle(250);
  expect(host.querySelector("h1")?.textContent).toBe("A");
  expect(back.disabled).toBe(false);
  expect(forward.disabled).toBe(false);

  const input = host.querySelector<HTMLInputElement>('input[aria-label="Search memory notes…"]')!;
  input.focus();
  await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "]", metaKey: true, bubbles: true })));
  await settle(250);
  expect(host.querySelector("h1")?.textContent).toBe("A");
  input.blur();
  await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "]", ctrlKey: true, bubbles: true })));
  await settle(250);
  expect(host.querySelector("h1")?.textContent).toBe("B");
  expect(requests.some((path) => path.startsWith("/admin/memory/page-edits/history"))).toBe(false);
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Open memory trust inspector"]')?.click());
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
  window.ntrpDesktop = { api: { request: async (_config, request) => {
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
      ? "<!-- ntrp:index:start -->\n- a.md <!-- ntrp:path=a.md -->\n- b.md <!-- ntrp:path=b.md -->\n<!-- ntrp:index:end -->"
      : path === "a.md" || path === "b.md" ? "[[Shared]]" : `${item.title} body`;
    return response({ artifact: rawArtifact(item, content) });
  } } } as Window["ntrpDesktop"];
  const { host, root } = setup();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(350);
  expect(host.querySelector<HTMLAnchorElement>('[data-wikilink="Shared"]')?.classList.contains("wikilink--unresolved")).toBe(false);

  await act(async () => host.querySelector<HTMLButtonElement>('[data-memory-entry="b.md"]')?.click());
  await settle(250);
  expect(host.querySelector("h1")?.textContent).toBe("B");
  expect(host.querySelector<HTMLAnchorElement>('[data-wikilink="Shared"]')?.classList.contains("wikilink--unresolved")).toBe(true);

  await act(async () => host.querySelector<HTMLButtonElement>('[data-memory-entry="a.md"]')?.click());
  await settle(250);
  releaseB?.();
  await settle(100);
  const current = host.querySelector<HTMLAnchorElement>('[data-wikilink="Shared"]')!;
  expect(host.querySelector("h1")?.textContent).toBe("A");
  expect(current.classList.contains("wikilink--unresolved")).toBe(false);
  await act(async () => current.click());
  await settle(250);
  expect(host.querySelector("h1")?.textContent).toBe("X");
});

test("vault revision refresh invalidates the selected detail cache", async () => {
  const index = summary("index.md", "Index");
  let note = summary("note.md", "Note", "r1");
  let detailReads = 0;
  window.ntrpDesktop = { api: { request: async (_config, request) => {
    if (request.path === "/admin/memory/artifacts") return response({ artifacts: [rawArtifact(index), rawArtifact(note)] });
    if (request.path.startsWith("/admin/memory/links")) return response({ path: "note.md", revision: "ledger:1", stale: false, outgoing: [], backlinks: [], total_outgoing: 0, total_backlinks: 0, limit: 100, offset: 0 });
    if (request.path.startsWith("/admin/memory/page-edits/history")) return response({ events: [], total: 0, limit: 100, next_before_sequence: null });
    const path = decodeURIComponent(request.path.replace("/admin/memory/artifacts/", ""));
    if (path === "index.md") return response({ artifact: rawArtifact(index, "<!-- ntrp:index:start -->\n- note.md <!-- ntrp:path=note.md -->\n<!-- ntrp:index:end -->") });
    detailReads += 1;
    return response({ artifact: rawArtifact(note, `version ${detailReads}`) });
  } } } as Window["ntrpDesktop"];
  const { host, root } = setup();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(250);
  expect(host.textContent).toContain("version 1");
  note = summary("note.md", "Note", "r2");
  await act(async () => useStore.setState((state) => ({ memoryVaultVersion: state.memoryVaultVersion + 1 })));
  await settle(250);
  expect(detailReads).toBe(2);
  expect(host.textContent).toContain("version 2");
});
