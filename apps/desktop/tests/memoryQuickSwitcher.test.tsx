import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { AppConfig } from "@/api/core";
import { ArtifactMemoryView } from "@/features/memory/components/ArtifactMemoryView";
import { rankSwitcherMatches } from "@/features/memory/components/MemoryQuickSwitcher";
import type { MemoryArtifactSummary } from "@/features/memory/lib/notebookTypes";

const config: AppConfig = {
  serverUrl: "http://localhost:6877",
  apiKey: "test-key",
};
const originalDesktop = window.ardenDesktop;
const roots = new Set<Root>();

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
  {
    ...base,
    path: "me.md",
    directory: "",
    title: "Me",
    summary: "Identity, preferences, and durable context.",
  },
  {
    ...base,
    path: "topics/dex.md",
    directory: "topics",
    title: "Dex",
    summary: "Current work and decisions about Dex.",
  },
  {
    ...base,
    path: "topics/dax.md",
    directory: "topics",
    title: "Dax",
    summary: "A different topic entirely.",
  },
  {
    ...base,
    path: "research/latency.md",
    directory: "research",
    title: "Latency",
    summary: "Observed latency behavior.",
  },
];

function detail(path: string) {
  const item = summaries.find((artifact) => artifact.path === path)!;
  return {
    ...item,
    revision: `sha256:${path}`,
    content: `# ${item.title}\n\n${item.summary}`,
    editable_content: `# ${item.title}\n\n${item.summary}\n`,
    timeline: [],
    frontmatter: {},
  };
}

function installBridge() {
  window.ardenDesktop = {
    api: {
      request: async (_config, request) => {
        if (request.path.startsWith("/admin/memory/artifacts?") || request.path === "/admin/memory/artifacts") {
          return {
            ok: true,
            status: 200,
            statusText: "OK",
            contentType: "application/json",
            data: { artifacts: summaries },
            text: "",
          };
        }
        if (request.path.startsWith("/admin/memory/items")) {
          return {
            ok: true,
            status: 200,
            statusText: "OK",
            contentType: "application/json",
            data: { items: [], limit: 100 },
            text: "",
          };
        }
        if (request.path.startsWith("/admin/memory/links")) {
          const path = new URL(`http://local${request.path}`).searchParams.get("path")!;
          return {
            ok: true,
            status: 200,
            statusText: "OK",
            contentType: "application/json",
            data: {
              path,
              revision: "ledger:1",
              stale: false,
              outgoing: [],
              backlinks: [],
              total_outgoing: 0,
              total_backlinks: 0,
              limit: 100,
              offset: 0,
            },
            text: "",
          };
        }
        const path = decodeURIComponent(request.path.replace("/admin/memory/artifacts/", ""));
        return {
          ok: true,
          status: 200,
          statusText: "OK",
          contentType: "application/json",
          data: { artifact: detail(path) },
          text: "",
        };
      },
    },
  } as Window["ardenDesktop"];
}

function setup() {
  // Inspector now defaults open (persisted); seed closed so switcher tests
  // don't pull in extra link requests. happy-dom localStorage is shared
  // across test files in one bun invocation — cleaned up in afterEach.
  localStorage.setItem("arden.desktop.memory.inspectorOpen", "false");
  const app = document.createElement("div");
  app.id = "app";
  const host = document.createElement("div");
  host.style.height = "800px";
  app.append(host);
  document.body.append(app);
  const root = createRoot(host);
  roots.add(root);
  return { host, root };
}

async function settle(delay = 0) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, delay));
  });
}

async function shortcut(key: string, target: EventTarget = window) {
  await act(async () => target.dispatchEvent(new KeyboardEvent("keydown", { key, metaKey: true, bubbles: true })));
  await settle();
}

afterEach(async () => {
  for (const root of roots) await act(async () => root.unmount());
  roots.clear();
  window.ardenDesktop = originalDesktop;
  document.body.replaceChildren();
  for (const key of ["arden.desktop.memory.inspectorOpen", "arden.desktop.memory.lastPath", "arden.desktop.memory.pins", "arden.desktop.memory.rail.collapsed"])
    localStorage.removeItem(key);
});

function artifact(path: string, extra: Partial<MemoryArtifactSummary> = {}): MemoryArtifactSummary {
  return {
    path,
    title: path,
    kind: "topic",
    type: "file",
    directory: path.includes("/") ? path.split("/")[0]! : "",
    scope: { kind: "user", key: null },
    snippet: null,
    summary: null,
    revision: "r",
    recordCount: 0,
    generated: false,
    editable: true,
    readonlyReason: null,
    updatedAt: "2026-07-12T10:00:00Z",
    createdAt: null,
    labels: [],
    source: null,
    ...extra,
  };
}

test("rankSwitcherMatches orders by recency on empty query and match quality otherwise, bounded", () => {
  const artifacts: MemoryArtifactSummary[] = Array.from({ length: 20 }, (_, i) => artifact(`note-${i}.md`, { title: `Note ${i}`, revision: `r${i}` }));

  const empty = rankSwitcherMatches(artifacts, "", ["note-5.md", "note-2.md"]);
  expect(empty.length).toBe(20);
  expect(empty[0]?.path).toBe("note-5.md");
  expect(empty[1]?.path).toBe("note-2.md");
  expect(empty[2]?.title).toBe("Note 0");

  const dex = artifact("topics/dex.md");
  const index = artifact("topics/index.md");
  // "dex" is an exact substring of the "dex" stem (tier 0); "index" only
  // matches as a scattered subsequence (in-D-E-X) — substring beats subsequence.
  const ranked = rankSwitcherMatches([index, dex], "dex", []);
  expect(ranked.map((a) => a.path)).toEqual(["topics/dex.md", "topics/index.md"]);
});

test("rankSwitcherMatches uses managed wiki titles instead of filename stems", () => {
  const managed = artifact("topics/opaque-id.md", {
    source: "wiki",
    title: "Canonical Decisions",
  });
  const legacy = artifact("topics/decisions.md", {
    title: "Ignored legacy title",
  });

  expect(rankSwitcherMatches([managed, legacy], "canonical", []).map((item) => item.path)).toEqual(["topics/opaque-id.md"]);
  expect(rankSwitcherMatches([managed, legacy], "ignored", [])).toEqual([]);
});

test("rankSwitcherMatches narrows with folder:, @month, and #kind operator tokens", () => {
  const dex = artifact("topics/dex.md", { updatedAt: "2026-07-12T10:00:00Z" });
  const dax = artifact("topics/dax.md", { updatedAt: "2026-06-30T10:00:00Z" });
  const latency = artifact("research/latency.md", {
    kind: "lesson",
    updatedAt: null,
    createdAt: "2026-07-01T09:00:00Z",
  });
  const all = [dex, dax, latency];

  expect(rankSwitcherMatches(all, "folder:topics", []).map((a) => a.path)).toEqual(["topics/dax.md", "topics/dex.md"]);
  expect(rankSwitcherMatches(all, "folder:topics dex", []).map((a) => a.path)).toEqual(["topics/dex.md"]);
  // @month matches updated OR created month.
  expect(rankSwitcherMatches(all, "@2026-07", []).map((a) => a.path)).toEqual(["topics/dex.md", "research/latency.md"]);
  expect(rankSwitcherMatches(all, "#lesson", []).map((a) => a.path)).toEqual(["research/latency.md"]);
  expect(rankSwitcherMatches(all, "#topic @2026-06", []).map((a) => a.path)).toEqual(["topics/dax.md"]);
});

test("Cmd+O opens the switcher, typing filters, Enter navigates", async () => {
  installBridge();
  const { host, root } = setup();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(260);
  const initialTitle = host.querySelector("h1")?.textContent;
  expect(initialTitle).toBeTruthy();
  expect(initialTitle).not.toBe("dex");

  await shortcut("o");
  const dialog = document.querySelector<HTMLElement>('[aria-label="Quick switcher"]');
  expect(dialog).not.toBeNull();

  const input = dialog!.querySelector<HTMLInputElement>("input")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, "dex");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await settle();
  // Rows render path STEMS (with the parent folder as a subtitle), not titles.
  const options = dialog!.querySelectorAll('[role="option"]');
  expect(options.length).toBe(1);
  expect(options[0]?.textContent).toContain("dex");
  expect(options[0]?.textContent).toContain("topics");

  await act(async () => input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
  await settle(260);
  expect(document.querySelector('[aria-label="Quick switcher"]')).toBeNull();
  expect(host.querySelector("h1")?.textContent).toBe("dex");

  // History recorded the jump — Cmd+[ returns to the previous note.
  await shortcut("[");
  await settle(260);
  expect(host.querySelector("h1")?.textContent).toBe(initialTitle);
});

test("Escape closes only the switcher, memory view stays mounted", async () => {
  installBridge();
  const { host, root } = setup();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(260);
  const initialTitle = host.querySelector("h1")?.textContent;

  await shortcut("p");
  expect(document.querySelector('[aria-label="Quick switcher"]')).not.toBeNull();

  await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
  await settle();
  expect(document.querySelector('[aria-label="Quick switcher"]')).toBeNull();
  expect(host.querySelector('[data-memory-zone="workspace"]')).not.toBeNull();
  expect(host.querySelector("h1")?.textContent).toBe(initialTitle);
});

test("the mock rail has no legacy search button", async () => {
  installBridge();
  const { host, root } = setup();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(260);

  expect(host.querySelector('button[aria-label="Search notes"]')).toBeNull();
  expect(host.querySelector(".mw-rail-top")).toBeNull();
});

test("Cmd+O while editing does not open the switcher", async () => {
  installBridge();
  const { host, root } = setup();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(260);

  await shortcut("e");
  expect(host.querySelector("[data-memory-editor]")).not.toBeNull();

  await shortcut("o");
  expect(document.querySelector('[aria-label="Quick switcher"]')).toBeNull();
});
