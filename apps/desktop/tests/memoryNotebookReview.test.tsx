import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { AppConfig } from "@/api/core";
import { ArtifactMemoryView } from "@/features/memory/components/ArtifactMemoryView";
import { useStore } from "@/stores";
import {
  error,
  installCanonicalMemoryBridge,
  ok,
  type FactFixture,
  type WikiPageFixture,
} from "./helpers/canonicalMemoryBridge";

const config: AppConfig = { serverUrl: "http://localhost:6877", apiKey: "test-key" };
const originalDesktop = window.ardenDesktop;
const originalVaultVersion = useStore.getState().memoryVaultVersion;
const mountedRoots = new Set<Root>();

function artifact(path: string, title: string, summary: string | null = null, extra: Record<string, unknown> = {}): WikiPageFixture {
  const metadata = {
    ...(summary == null ? {} : { summary }),
    ...(extra.generated ? { generated_from_revision: "test" } : {}),
    ...((extra.metadata as Record<string, unknown> | undefined) ?? {}),
  };
  return {
    pageId: `page-${path.replace(/[^a-z0-9]+/gi, "-")}`,
    path,
    title,
    content: `${title} prose`,
    metadata,
  };
}

function rawRecord(id: string, text: string): FactFixture {
  return {
    factId: id,
    text,
    subjects: ["test"],
    createdAt: "2026-07-12T10:00:00Z",
  };
}

function factPage(fact: FactFixture) {
  return {
    fact_id: fact.factId,
    text: fact.text,
    kind: fact.kind ?? "fact",
    labels: fact.labels ?? [],
    subjects: fact.subjects ?? [],
    scope: fact.scope ?? { kind: "user", key: null },
    lifecycle: fact.lifecycle ?? "durable",
    status: fact.status ?? "active",
    certainty: fact.certainty ?? "confirmed",
    evidence_class: fact.evidenceClass ?? "direct",
    sources: [],
    created_at: fact.createdAt ?? "2026-07-12T10:00:00Z",
    reviewed_at: null,
    review_at: null,
    expires_at: null,
    version: fact.version ?? `${fact.factId}:v1`,
  };
}

function setupDom(): { host: HTMLElement; root: Root } {
  // Inspector now defaults open (persisted); these tests exercise unrelated
  // review/refetch behavior, so seed it closed to keep prior request counts.
  localStorage.setItem("arden.desktop.memory.inspectorOpen", "false");
  // The initial selection prefers the persisted last path; clear any leak from
  // other test files so the README.md fallback stays deterministic.
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

test("rail mirrors the plain directory structure with page titles and precise reserved paths", async () => {
  const rows = [
    artifact("README.md", "Wiki", null, { generated: true }),
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

  const { requests } = installCanonicalMemoryBridge({ pages: rows });
  const { host, root } = setupDom();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(400);

  const rail = host.querySelector<HTMLElement>('[data-memory-zone="rail"]')!;
  const tree = rail.querySelector<HTMLElement>("[data-memory-tree]")!;
  const lab = tree.querySelector<HTMLElement>('[data-memory-directory="lab/"]')!;
  const sub = tree.querySelector<HTMLElement>('[data-memory-directory="lab/sub/"]')!;
  const research = tree.querySelector<HTMLElement>('[data-memory-directory="research/"]')!;
  expect(lab.contains(sub)).toBe(true);
  expect(research.contains(tree.querySelector('[data-memory-directory="research/changelog/"]')!)).toBe(true);

  // Every artifact with a valid notebook path appears — no managed-index
  // gating, no README/index shadowing.
  for (const path of [
    "lab/README.md", "lab/index.md", "lab/first.md", "lab/second.md", "lab/sub/nested.md",
    "research/health.md", "research/AGENTS.md", "research/changelog/note.md", "topics/dex.md",
  ]) {
    expect(tree.querySelector(`[data-memory-entry="${path}"]`)).not.toBeNull();
  }
  // Rows are labelled by the page's frontmatter title.
  expect(tree.querySelector('[data-memory-entry="lab/first.md"]')?.textContent).toBe("First");
  // Reserved paths never render: raw/ anywhere, engine files only at the root.
  expect(tree.querySelector('[data-memory-entry="lab/raw/secret.md"]')).toBeNull();
  expect(tree.querySelector('[data-memory-directory="lab/raw/"]')).toBeNull();
  expect(tree.querySelector('[data-memory-entry="health.md"]')).toBeNull();

  // Root order: name-sorted directories, then files with README.md first.
  const rootOrder = [
    '[data-memory-directory="lab/"]',
    '[data-memory-directory="research/"]',
    '[data-memory-directory="topics/"]',
    '[data-memory-entry="README.md"]',
    '[data-memory-entry="alpha.md"]',
    '[data-memory-entry="zeta.md"]',
  ].map((selector) => tree.querySelector<HTMLElement>(selector)!);
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

  // Initial fallback selection is README.md; only the selected page is read.
  expect(host.querySelector('[data-memory-zone="workspace"] h1')?.textContent).toBe("Wiki");
  const home = rows.find((row) => row.path === "README.md")!;
  expect(requests.some((request) => request.path === `/admin/wiki/pages/${home.pageId}`)).toBe(true);
  expect(requests.some((request) => request.path.endsWith("/page-lab-README-md"))).toBe(false);
  expect(requests.some((request) => request.path.endsWith("/page-lab-index-md"))).toBe(false);
});

test("a stale Facts response cannot overwrite a newer rail entry", async () => {
  const index = artifact("README.md", "Wiki", null, { generated: true });
  let releaseFirst: (() => void) | null = null;
  const firstGate = new Promise<void>((resolve) => { releaseFirst = resolve; });
  let factsRequests = 0;
  installCanonicalMemoryBridge({
    pages: [index],
    onRequest: async ({ path }) => {
      if (path.startsWith("/admin/facts?")) {
      factsRequests += 1;
      if (factsRequests === 1) {
        await firstGate;
          return ok({ facts: [factPage(rawRecord("record-old", "Old facts result"))], has_more: false, next_after: null });
      }
        return ok({ facts: [factPage(rawRecord("record-new", "Current facts result"))], has_more: false, next_after: null });
      }
      return undefined;
    },
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
  const index = artifact("README.md", "Wiki", null, { generated: true });
  const me = artifact("me.md", "Me", null, {
    metadata: { fact_citations: [{ fact_id: "record-me", version: "record-me:v1" }] },
  });
  let meReads = 0;
  let vaultChanged = false;
  const bridge = installCanonicalMemoryBridge({
    pages: [index, me],
    facts: [rawRecord("record-me", "Old evidence")],
    onRequest: ({ path }) => {
      if (path === `/admin/wiki/pages/${me.pageId}`) meReads += 1;
      return undefined;
    },
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
  bridge.state.facts.set("record-me", rawRecord("record-me", "Fresh evidence"));
  await act(async () => useStore.getState().memoryVaultChanged());
  await settle(220);
  expect(meReads).toBeGreaterThan(readsBefore);
  expect(host.textContent).toContain("Fresh evidence");
  expect(host.textContent).not.toContain("Old evidence");
});

test("vault reload replaces the current page list", async () => {
  useStore.setState({ memoryVaultVersion: 0 });
  const index = artifact("README.md", "Wiki", null, { generated: true });
  const fresh = artifact("fresh.md", "Fresh Me");
  let listReads = 0;
  installCanonicalMemoryBridge({
    pages: [index, fresh],
    onRequest: ({ path }) => {
      if (path === "/admin/wiki/pages") {
      listReads += 1;
        return ok({ repository_head: "wiki-head-1", pages: listReads === 1 ? [] : [
          {
            page_id: index.pageId, path: index.path, resource_state: "active", title: index.title,
            aliases: [], lifecycle: "active", redirect_to: null, metadata: {}, version: "index:v1",
            repository_head: "wiki-head-1", created_at: null, updated_at: null,
          },
          {
            page_id: fresh.pageId, path: fresh.path, resource_state: "active", title: fresh.title,
            aliases: [], lifecycle: "active", redirect_to: null, metadata: {}, version: "fresh:v1",
            repository_head: "wiki-head-1", created_at: null, updated_at: null,
          },
        ] });
      }
      return undefined;
    },
  });
  const { host, root } = setupDom();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(220);

  await act(async () => useStore.getState().memoryVaultChanged());
  await settle(220);

  expect(host.querySelector('[data-memory-entry="fresh.md"]')).not.toBeNull();
});

test("list failure blocks the rail and retry recovers", async () => {
  const index = artifact("README.md", "Wiki", null, { generated: true });
  const me = artifact("me.md", "Me");
  let failList = true;
  installCanonicalMemoryBridge({
    pages: [index, me],
    onRequest: ({ path }) => {
      if (path === "/admin/wiki/pages" && failList) return error(500, "Vault temporarily unavailable");
      return undefined;
    },
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
