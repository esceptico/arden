import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { AppConfig } from "@/api/core";
import { ArtifactMemoryView } from "@/features/memory/components/ArtifactMemoryView";
import { useStore } from "@/stores";

const config: AppConfig = { serverUrl: "http://localhost:6877", apiKey: "test-key" };
const originalDesktop = window.ardenDesktop;
const originalVaultVersion = useStore.getState().memoryVaultVersion;
const mountedRoots = new Set<Root>();

const base = {
  kind: "topic",
  type: "file",
  scope: { kind: "user", key: null },
  snippet: null,
  summary: null,
  revision: "sha256:stable",
  record_count: 0,
  generated: false,
  editable: true,
  readonly_reason: null,
  updated_at: "2026-07-13T08:00:00Z",
  labels: [],
  source: null,
};

function artifact(path: string, title: string, summary: string | null = null, extra: Record<string, unknown> = {}) {
  return { ...base, path, title, summary, directory: path.includes("/") ? path.split("/")[0] : "", ...extra };
}

function response(data: unknown) {
  return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data, text: "" };
}

function failure(message: string) {
  return { ok: false, status: 500, statusText: "Error", contentType: "application/json", data: { detail: message }, text: "" };
}

function detail(raw: ReturnType<typeof artifact>, content = `${raw.title} prose`, timeline: unknown[] = []) {
  return { ...raw, content, editable_content: content, timeline, frontmatter: {} };
}

function rawRecord(id: string, content: string) {
  return {
    id, content, kind: "fact", canonical_subject: "test", labels: [], scope: { kind: "user", key: null },
    pinned: false, status: "active", valid_from: null, invalid_at: null, source_refs: [], corroboration: 1,
    last_relevant_at: null, feedback: "none", created_at: "2026-07-12T10:00:00Z", updated_at: "2026-07-12T10:00:00Z",
  };
}

function installBridge(handler: (
  path: string,
  method: string,
) => Promise<ReturnType<typeof response> | ReturnType<typeof failure>> | ReturnType<typeof response> | ReturnType<typeof failure>) {
  const requests: Array<{ path: string; method: string }> = [];
  window.ardenDesktop = {
    api: {
      request: async (_config, request) => {
        const method = request.method ?? "GET";
        requests.push({ path: request.path, method });
        return handler(request.path, method);
      },
    },
  } as Window["ardenDesktop"];
  return requests;
}

function setupDom(): { host: HTMLElement; root: Root } {
  // Inspector now defaults open (persisted); these tests exercise unrelated
  // review/refetch behavior, so seed it closed to keep prior request counts.
  localStorage.setItem("arden.desktop.memory.inspectorOpen", "false");
  // The initial selection prefers the persisted last path; clear any leak from
  // other test files so the index.md fallback stays deterministic.
  localStorage.removeItem("arden.desktop.memory.lastPath");
  const host = document.createElement("div");
  host.id = "app"; // quick switcher / popovers portal into #app
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

afterEach(async () => {
  for (const root of mountedRoots) await act(async () => root.unmount());
  mountedRoots.clear();
  window.ardenDesktop = originalDesktop;
  useStore.setState({ memoryVaultVersion: originalVaultVersion });
  document.body.replaceChildren();
  // happy-dom localStorage is shared across test files in one bun invocation.
  localStorage.removeItem("arden.desktop.memory.inspectorOpen");
  localStorage.removeItem("arden.desktop.memory.lastPath");
});

test("rail mirrors the plain directory structure with filename stems and precise reserved paths", async () => {
  const rows = [
    artifact("index.md", "Index", null, { generated: true, editable: false }),
    artifact("lab/README.md", "Lab guide"),
    artifact("lab/index.md", "Lab index"),
    artifact("lab/first.md", "First"),
    artifact("lab/second.md", "Second"),
    artifact("lab/sub/index.md", "Sub index"),
    artifact("lab/sub/nested.md", "Nested"),
    artifact("research/index.md", "Research index"),
    artifact("research/health.md", "Health notes"),
    artifact("research/AGENTS.md", "Agent notes"),
    artifact("research/changelog/index.md", "Research changelog"),
    artifact("research/changelog/note.md", "Change note"),
    artifact("alpha.md", "Alpha"),
    artifact("zeta.md", "Zeta"),
    artifact("topics/dex.md", "Dex"),
    artifact("health.md", "Generated health", null, { generated: true, editable: false }),
    artifact("lab/raw/secret.md", "Reserved raw"),
  ];

  const requests = installBridge((path) => {
    if (path === "/admin/memory/artifacts") return response({ artifacts: rows, directories: ["empty"] });
    const artifactPath = decodeURIComponent(path.replace("/admin/memory/artifacts/", ""));
    const raw = rows.find((item) => item.path === artifactPath)!;
    return response({ artifact: detail(raw) });
  });
  const { host, root } = setupDom();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(400);

  const rail = host.querySelector<HTMLElement>('[data-memory-zone="rail"]')!;
  const lab = rail.querySelector<HTMLElement>('[data-memory-directory="lab/"]')!;
  const sub = rail.querySelector<HTMLElement>('[data-memory-directory="lab/sub/"]')!;
  const research = rail.querySelector<HTMLElement>('[data-memory-directory="research/"]')!;
  expect(lab.contains(sub)).toBe(true);
  expect(research.contains(rail.querySelector('[data-memory-directory="research/changelog/"]')!)).toBe(true);
  // Server-listed directories with no notes stay visible.
  expect(rail.querySelector('[data-memory-directory="empty/"]')).not.toBeNull();

  // Every artifact with a valid notebook path appears — no managed-index
  // gating, no README/index shadowing.
  for (const path of [
    "lab/README.md", "lab/index.md", "lab/first.md", "lab/second.md", "lab/sub/nested.md",
    "research/health.md", "research/AGENTS.md", "research/changelog/note.md", "topics/dex.md",
  ]) {
    expect(rail.querySelector(`[data-memory-entry="${path}"]`)).not.toBeNull();
  }
  // Rows are labelled by filename stem.
  expect(rail.querySelector('[data-memory-entry="lab/first.md"]')?.textContent).toBe("first");
  // Reserved paths never render: raw/ anywhere, engine files only at the root.
  expect(rail.querySelector('[data-memory-entry="lab/raw/secret.md"]')).toBeNull();
  expect(rail.querySelector('[data-memory-directory="lab/raw/"]')).toBeNull();
  expect(rail.querySelector('[data-memory-entry="health.md"]')).toBeNull();

  // Root order: name-sorted directories, then files with index.md first.
  const rootOrder = [
    '[data-memory-directory="empty/"]',
    '[data-memory-directory="lab/"]',
    '[data-memory-directory="research/"]',
    '[data-memory-directory="topics/"]',
    '[data-memory-entry="index.md"]',
    '[data-memory-entry="alpha.md"]',
    '[data-memory-entry="zeta.md"]',
  ].map((selector) => rail.querySelector<HTMLElement>(selector)!);
  for (let index = 1; index < rootOrder.length; index += 1) {
    expect(rootOrder[index - 1]!.compareDocumentPosition(rootOrder[index]!) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
  }
  // Folder order: nested directories, then stem-sorted files.
  const labOrder = [
    '[data-memory-directory="lab/sub/"]',
    '[data-memory-entry="lab/first.md"]',
    '[data-memory-entry="lab/index.md"]',
    '[data-memory-entry="lab/second.md"]',
  ].map((selector) => lab.querySelector<HTMLElement>(selector)!);
  for (let index = 1; index < labOrder.length; index += 1) {
    expect(labOrder[index - 1]!.compareDocumentPosition(labOrder[index]!) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
  }

  // Initial fallback selection is index.md; only the selected note is read —
  // the tree needs no index-document fetches.
  expect(host.querySelector('[data-memory-zone="workspace"] h1')?.textContent).toBe("index");
  expect(requests.some((request) => request.path === "/admin/memory/artifacts/index.md")).toBe(true);
  expect(requests.some((request) => request.path.endsWith("/lab/README.md"))).toBe(false);
  expect(requests.some((request) => request.path.endsWith("/lab/index.md"))).toBe(false);
});

test("a stale Facts response cannot overwrite a newer rail entry", async () => {
  const index = artifact("index.md", "Index", null, { generated: true, editable: false });
  let releaseFirst: (() => void) | null = null;
  const firstGate = new Promise<void>((resolve) => { releaseFirst = resolve; });
  let factsRequests = 0;
  installBridge(async (path) => {
    if (path === "/admin/memory/artifacts") return response({ artifacts: [index] });
    if (path.startsWith("/admin/memory/items")) {
      factsRequests += 1;
      if (factsRequests === 1) {
        await firstGate;
        return response({ items: [rawRecord("record-old", "Old facts result")], limit: 100 });
      }
      return response({ items: [rawRecord("record-new", "Current facts result")], limit: 100 });
    }
    return response({ artifact: detail(index) });
  });
  const { host, root } = setupDom();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(220);
  const facts = host.querySelector<HTMLButtonElement>('button[data-tab-value="facts"]')!;
  const notebook = host.querySelector<HTMLButtonElement>('button[data-tab-value="notebook"]')!;
  await act(async () => facts.click());
  await settle(20);
  await act(async () => notebook.click());
  await settle(500);
  await act(async () => facts.click());
  await settle(500);
  releaseFirst?.();
  await settle(40);

  const railText = host.querySelector('[data-memory-zone="rail"]')?.textContent ?? "";
  expect(railText).toContain("Current facts result");
  expect(railText).not.toContain("Old facts result");
});

test("vault change refetches selected timeline even when page revision is unchanged", async () => {
  useStore.setState({ memoryVaultVersion: 0 });
  const index = artifact("index.md", "Index", null, { generated: true, editable: false });
  const me = artifact("me.md", "Me");
  let meReads = 0;
  let vaultChanged = false;
  installBridge((path) => {
    if (path === "/admin/memory/artifacts") return response({ artifacts: [index, me] });
    const artifactPath = decodeURIComponent(path.replace("/admin/memory/artifacts/", ""));
    if (artifactPath === "index.md") return response({ artifact: detail(index) });
    meReads += 1;
    const text = vaultChanged ? "Fresh evidence" : "Old evidence";
    return response({ artifact: detail(me, "Me prose", [{
      id: "record-me", text, kind: "fact", date: "2026-07-13", src: "chat", pinned: false, superseded: false,
    }]) });
  });
  const { host, root } = setupDom();
  localStorage.setItem("arden.desktop.memory.lastPath", "me.md");
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(220);
  // Records render in the instrument panel — open it once; the pane
  // survives the refetch (same note stays mounted).
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Open records"]')?.click());
  await settle(120);
  expect(host.textContent).toContain("Old evidence");

  const readsBefore = meReads;
  vaultChanged = true;
  await act(async () => useStore.getState().memoryVaultChanged());
  await settle(220);
  expect(meReads).toBeGreaterThan(readsBefore);
  expect(host.textContent).toContain("Fresh evidence");
  expect(host.textContent).not.toContain("Old evidence");
});

test("newer vault reload wins over an older rebuild response", async () => {
  useStore.setState({ memoryVaultVersion: 0 });
  const index = artifact("index.md", "Index", null, { generated: true, editable: false });
  const fresh = artifact("fresh.md", "Fresh Me");
  const stale = artifact("stale.md", "Stale Rebuild Me");
  let listReads = 0;
  let releaseRebuild: (() => void) | null = null;
  const rebuildGate = new Promise<void>((resolve) => { releaseRebuild = resolve; });
  installBridge(async (path, method) => {
    if (path === "/admin/memory/artifacts/rebuild" && method === "POST") {
      await rebuildGate;
      return response({ artifacts: [index, stale] });
    }
    if (path === "/admin/memory/artifacts") {
      listReads += 1;
      return response({ artifacts: listReads === 1 ? [] : [index, fresh] });
    }
    const artifactPath = decodeURIComponent(path.replace("/admin/memory/artifacts/", ""));
    return response({ artifact: detail(artifactPath === "fresh.md" ? fresh : index) });
  });
  const { host, root } = setupDom();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(220);

  // Rebuild is reachable from the empty rail's Refresh action.
  await act(async () => Array.from(host.querySelectorAll<HTMLButtonElement>("button"))
    .find((button) => button.textContent?.trim() === "Refresh")?.click());
  await act(async () => useStore.getState().memoryVaultChanged());
  await settle(100);
  releaseRebuild?.();
  await settle(220);

  expect(host.querySelector('[data-memory-entry="fresh.md"]')).not.toBeNull();
  expect(host.querySelector('[data-memory-entry="stale.md"]')).toBeNull();
});

test("list failure blocks the rail and retry recovers", async () => {
  const index = artifact("index.md", "Index", null, { generated: true, editable: false });
  const me = artifact("me.md", "Me");
  let failList = true;
  installBridge((path) => {
    if (path === "/admin/memory/artifacts") {
      if (failList) return failure("Vault temporarily unavailable");
      return response({ artifacts: [index, me] });
    }
    const artifactPath = decodeURIComponent(path.replace("/admin/memory/artifacts/", ""));
    return response({ artifact: detail(artifactPath === "me.md" ? me : index) });
  });
  const { host, root } = setupDom();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(220);

  const alert = host.querySelector<HTMLElement>('[data-memory-zone="rail"] [role="alert"]');
  expect(alert?.textContent).toContain("Vault temporarily unavailable");
  expect(host.querySelector("[data-memory-entry]")).toBeNull();

  failList = false;
  await act(async () => Array.from(alert?.querySelectorAll("button") ?? []).find((button) => button.textContent === "Retry")?.click());
  await settle(220);
  expect(host.querySelector('[data-memory-entry="me.md"]')).not.toBeNull();
  expect(host.querySelector('[data-memory-zone="rail"] [role="alert"]')).toBeNull();
});
