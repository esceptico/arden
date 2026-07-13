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
  { ...base, path: "scratch.md", directory: "", title: "Scratch", summary: "Unsorted working notes." },
  { ...base, path: "health.md", directory: "", title: "Health", summary: "Machine health report.", generated: true, editable: false },
  { ...base, path: "raw/events/1.md", directory: "raw/events", title: "Raw event", summary: "Machine event." },
  { ...base, path: ".ntrp/maintenance/state.md", directory: ".ntrp/maintenance", title: "State", summary: "Machine state." },
];

function managed(rows: Array<[string, string]>) {
  return [
    "<!-- ntrp:index:start -->",
    ...rows.map(([path, description]) => `- ${path} — ${description} <!-- ntrp:path=${encodeURIComponent(path)} -->`),
    "<!-- ntrp:index:end -->",
  ].join("\n");
}

function detail(path: string) {
  const item = summaries.find((artifact) => artifact.path === path)!;
  const indexContent = path === "index.md"
    ? managed([
        ["me.md", "Identity, preferences, and durable context."],
        ["topics/", "Current topics and decisions."],
        ["research/", "Research notes and experiments."],
      ])
    : path === "topics/index.md"
      ? managed([["dex.md", "Current work and decisions about Dex."]])
      : path === "research/README.md"
        ? managed([["latency.md", "Observed latency behavior."]])
        : null;
  return {
    ...item,
    revision: `sha256:${path}`,
    content: indexContent ?? (path === "me.md" ? "I build personal tools.\n\nSee [[topics/dex|Dex]]." : item.summary),
    editable_content: `# ${item.title}\n\n${item.summary}\n`,
    timeline: path === "me.md" ? [{
      id: "record-me", text: "I build personal tools.", kind: "fact", date: "2026-07-12",
      src: "chat", pinned: false, superseded: false,
    }] : [],
    frontmatter: { updated: "2026-07-12", labels: ["personal"] },
  };
}

function installBridge(options: { list?: typeof summaries; failList?: string; delayList?: boolean } = {}) {
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
          const source = options.list ?? summaries;
          const artifacts = request.path.includes("?q=")
            ? source.filter((artifact) => artifact.path === "topics/dex.md")
            : source;
          return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data: { artifacts }, text: "" };
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
        const path = decodeURIComponent(request.path.replace("/admin/memory/artifacts/", ""));
        return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data: { artifact: detail(path) }, text: "" };
      },
    },
  } as Window["ntrpDesktop"];
  return { requests, releaseList: () => resolveList?.() };
}

function setupDom(): { host: HTMLElement; root: Root } {
  const host = document.createElement("div");
  host.style.height = "800px";
  document.body.append(host);
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
});

test("memory opens as a meaning-first notebook, not a database inspector", async () => {
  const bridge = installBridge();
  const { host, root } = setupDom();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(250);

  const rail = host.querySelector<HTMLElement>('[data-memory-zone="rail"]');
  const workspace = host.querySelector<HTMLElement>('[data-memory-zone="workspace"]');
  const inspector = host.querySelector<HTMLElement>('[data-memory-zone="inspector"]');
  expect(rail?.getAttribute("aria-label")).toBe("Memory notebook");
  expect(workspace?.getAttribute("aria-label")).toBe("Memory note");
  expect(inspector).not.toBeNull();
  const layout = host.querySelector<HTMLElement>('[data-memory-layout="notebook"]');
  expect(layout?.className).toContain("max-[640px]:grid-cols-[180px_minmax(0,1fr)_0px]");
  expect(layout?.className).toContain("max-[900px]:grid-cols-[200px_minmax(0,1fr)_0px]");

  expect(host.querySelector('button[role="tab"]')).toBeNull();
  expect(host.textContent).not.toContain("FilesRecords");
  expect(rail?.textContent).toContain("Topics");
  expect(rail?.textContent).toContain("Current work and decisions about Dex.");
  expect(rail?.textContent).toContain("Research notes and experiments.");
  expect(rail?.textContent).not.toContain("topics/dex.md");
  expect(rail?.textContent).not.toContain("raw/events/1.md");
  expect(rail?.textContent).not.toContain("Machine health report");

  const files = host.querySelector<HTMLDetailsElement>('details[data-memory-files]');
  expect(files?.open).toBe(false);
  await act(async () => files?.querySelector("summary")?.click());
  expect(files?.open).toBe(true);
  expect(Array.from(files?.querySelectorAll("button") ?? []).some((button) => button.textContent?.includes("Scratch"))).toBe(true);

  const openRecords = host.querySelector<HTMLButtonElement>('button[aria-label="Open raw records diagnostic"]');
  expect(openRecords).not.toBeNull();

  const title = workspace?.querySelector("h1");
  const prose = workspace?.querySelector(".md");
  const metadata = workspace?.querySelector('[data-memory-note-metadata]');
  expect(title?.textContent).toBe("Me");
  expect(prose?.textContent).toContain("I build personal tools.");
  expect(title?.compareDocumentPosition(prose!) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
  expect(prose?.compareDocumentPosition(metadata!) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
  expect(workspace?.querySelector<HTMLDetailsElement>('details[data-memory-properties]')?.open).toBe(false);
  const evidence = Array.from(workspace?.querySelectorAll("details") ?? [])
    .find((item) => item.querySelector("summary")?.textContent?.includes("Evidence"));
  expect(evidence?.open).toBe(false);

  expect(bridge.requests[0]).toBe("/admin/memory/artifacts");
  expect(bridge.requests).toContain("/admin/memory/artifacts/me.md");

  const dexLink = workspace?.querySelector<HTMLAnchorElement>('[data-wikilink="topics/dex"]');
  await act(async () => dexLink?.click());
  await settle(250);
  expect(workspace?.querySelector("h1")?.textContent).toBe("Dex");
  expect(bridge.requests).toContain("/admin/memory/artifacts/topics/dex.md");

  const search = rail?.querySelector<HTMLInputElement>('input[aria-label="Search memory notes…"]');
  await act(async () => {
    if (!search) return;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(search, "decisions");
    search.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await settle(220);
  expect(rail?.querySelector("#memory-search-results")?.textContent).toBe("Results");
  expect(rail?.textContent).toContain("Dex");
  expect(rail?.textContent).not.toContain("Identity, preferences, and durable context.");
  await act(async () => rail?.querySelector<HTMLButtonElement>('button[aria-label="Clear search"]')?.click());

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

test("default selection follows notebook meaning instead of transport order", async () => {
  const byPath = (path: string) => summaries.find((artifact) => artifact.path === path)!;
  installBridge({ list: [byPath("scratch.md"), byPath("topics/dex.md"), byPath("me.md"), byPath("index.md"), byPath("topics/index.md")] });
  const { host, root } = setupDom();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(250);

  expect(host.querySelector('[data-memory-zone="workspace"] h1')?.textContent).toBe("Me");

  await unmountRoot(root);
});
