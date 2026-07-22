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
  { ...base, path: "me.md", directory: "", title: "Me", summary: "Identity and durable context." },
];

const record = (id: string, content: string, pinned = false) => ({
  id, content, kind: "fact", canonical_subject: "test",
  labels: [], scope: { kind: "user", key: null }, pinned, status: "active",
  valid_from: null, invalid_at: null, source_refs: [], corroboration: 1,
  last_relevant_at: null, feedback: "none", created_at: "2026-07-12T10:00:00Z",
  updated_at: "2026-07-12T10:00:00Z",
});

interface BridgeOptions {
  items?: () => ReturnType<typeof record>[];
  pin?: (id: string, pinned: boolean) => Promise<{ ok: boolean; pinned: boolean }>;
}

function installBridge(options: BridgeOptions = {}) {
  const requests: string[] = [];
  window.ntrpDesktop = {
    api: {
      request: async (_config, request) => {
        requests.push(request.path);
        if (request.path.startsWith("/admin/memory/artifacts?") || request.path === "/admin/memory/artifacts") {
          return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data: { artifacts: summaries }, text: "" };
        }
        if (request.path.startsWith("/admin/memory/items")) {
          const items = options.items?.() ?? [record("r-alpha", "Alpha fact"), record("r-beta", "Beta fact", true)];
          return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data: { items, limit: 100 }, text: "" };
        }
        if (request.path.includes("/pin")) {
          const id = decodeURIComponent(request.path.split("/")[4]!);
          const pinned = (JSON.parse(request.body as string) as { pinned: boolean }).pinned;
          if (options.pin) {
            try {
              const data = await options.pin(id, pinned);
              return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data, text: "" };
            } catch (reason) {
              return { ok: false, status: 500, statusText: "Error", contentType: "application/json", data: { detail: String(reason) }, text: "" };
            }
          }
          return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data: { ok: true, pinned }, text: "" };
        }
        if (request.path.startsWith("/admin/memory/links")) {
          const path = new URL(`http://local${request.path}`).searchParams.get("path")!;
          return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data: { path, revision: "ledger:1", stale: false, outgoing: [], backlinks: [], total_outgoing: 0, total_backlinks: 0, limit: 100, offset: 0 }, text: "" };
        }
        if (request.path.startsWith("/admin/memory/page-edits/history")) {
          return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data: { events: [], total: 0, limit: 20, next_before_sequence: null }, text: "" };
        }
        const path = decodeURIComponent(request.path.replace("/admin/memory/artifacts/", ""));
        const item = summaries.find((artifact) => artifact.path === path) ?? summaries[1]!;
        return {
          ok: true, status: 200, statusText: "OK", contentType: "application/json",
          data: { artifact: { ...item, revision: `sha256:${path}`, content: item.summary ?? "", editable_content: null, editable: false, timeline: [], frontmatter: {} } }, text: "",
        };
      },
    },
  } as Window["ntrpDesktop"];
  return { requests };
}

function setupDom(): { host: HTMLElement; root: Root } {
  const host = document.createElement("div");
  // AnchoredPopover (the kind Select's listbox) portals into #app.
  host.id = "app";
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

async function openRecords(host: HTMLElement) {
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Open raw records diagnostic"]')?.click());
  await settle(20);
}

afterEach(async () => {
  for (const root of mountedRoots) await act(async () => root.unmount());
  mountedRoots.clear();
  window.ntrpDesktop = originalDesktop;
  document.body.replaceChildren();
});

test("opening raw records queries active items and search re-queries with q", async () => {
  const bridge = installBridge();
  const { host, root } = setupDom();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(30);

  await openRecords(host);
  expect(bridge.requests.some((path) => path.startsWith("/admin/memory/items")
    && path.includes("limit=100") && path.includes("offset=0") && path.includes("status=active"))).toBe(true);
  expect(host.textContent).toContain("Alpha fact");

  const search = host.querySelector<HTMLInputElement>('input[aria-label="Search raw records…"]')!;
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    setter.call(search, "beta");
    search.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await settle(30);
  expect(bridge.requests.some((path) => path.startsWith("/admin/memory/items") && path.includes("q=beta"))).toBe(true);
});

test("selecting a record shows it in the detail pane", async () => {
  installBridge();
  const { host, root } = setupDom();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(30);
  await openRecords(host);

  const betaRow = Array.from(host.querySelectorAll<HTMLButtonElement>('section[aria-label="Raw records diagnostic"] button'))
    .find((button) => button.textContent?.includes("Beta fact"))!;
  await act(async () => betaRow.click());
  await settle(10);
  const workspace = host.querySelector('[data-memory-zone="workspace"]')!;
  expect(workspace.textContent).toContain("Beta fact");
});

test("pin success flips optimistically and posts the new state", async () => {
  const pins: Array<[string, boolean]> = [];
  installBridge({ pin: async (id, pinned) => { pins.push([id, pinned]); return { ok: true, pinned }; } });
  const { host, root } = setupDom();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(30);
  await openRecords(host);

  const pinButton = Array.from(host.querySelectorAll<HTMLButtonElement>('section[aria-label="Raw records diagnostic"] [aria-label="Pin — always keep in context"]'))[0]!;
  await act(async () => pinButton.click());
  await settle(20);
  expect(pins).toEqual([["r-alpha", true]]);
  expect(host.querySelectorAll('section[aria-label="Raw records diagnostic"] [aria-pressed="true"]').length).toBeGreaterThanOrEqual(2);
});

test("pin failure rolls the optimistic flip back and surfaces the error", async () => {
  installBridge({ pin: async () => { throw new Error("pin failed"); } });
  const { host, root } = setupDom();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(30);
  await openRecords(host);

  const pinButton = Array.from(host.querySelectorAll<HTMLButtonElement>('section[aria-label="Raw records diagnostic"] [aria-label="Pin — always keep in context"]'))[0]!;
  await act(async () => pinButton.click());
  await settle(30);
  // r-alpha rolled back to unpinned; only r-beta (fixture-pinned) stays pressed —
  // and the list is still standing (failure surfaces as a toast, not a retry panel).
  expect(host.textContent).toContain("Alpha fact");
  const pressed = Array.from(host.querySelectorAll<HTMLElement>('section[aria-label="Raw records diagnostic"] [aria-pressed="true"]'))
    .filter((el) => el.getAttribute("aria-label")?.startsWith("Unpin"));
  expect(pressed.length).toBe(1);
  const { useStore } = await import("@/stores");
  expect(useStore.getState().toasts.some((toast) => toast.title === "Error: pin failed")).toBe(true);
});

test("a pin failure that lands after a refetch does not clobber fresh records", async () => {
  let rejectPin: ((reason: Error) => void) | null = null;
  let phase = 0;
  installBridge({
    items: () => phase === 0
      ? [record("r-alpha", "Alpha fact")]
      : [record("r-alpha", "Alpha fact", true)], // fresh list: server says pinned
    pin: () => new Promise((_resolve, reject) => { rejectPin = reject; }),
  });
  const { host, root } = setupDom();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(30);
  await openRecords(host);

  const pinButton = host.querySelector<HTMLButtonElement>('section[aria-label="Raw records diagnostic"] [aria-label="Pin — always keep in context"]')!;
  await act(async () => pinButton.click());
  await settle(10);

  // Refetch replaces the list (kind filter change bumps the request id).
  phase = 1;
  const kindTrigger = host.querySelector<HTMLButtonElement>('[aria-label="Filter raw records by kind"]')!;
  await act(async () => kindTrigger.click());
  await settle(30);
  const factsOption = Array.from(document.querySelectorAll<HTMLElement>('[role="option"]'))
    .find((option) => option.textContent?.includes("Facts"))!;
  await act(async () => factsOption.click());
  await settle(30);
  expect(host.querySelectorAll('section[aria-label="Raw records diagnostic"] [aria-label^="Unpin"]').length).toBe(1);

  // Now the stale pin request fails — the rollback must be skipped.
  await act(async () => { rejectPin?.(new Error("stale pin failed")); });
  await settle(20);
  expect(host.querySelectorAll('section[aria-label="Raw records diagnostic"] [aria-label^="Unpin"]').length).toBe(1);
});
