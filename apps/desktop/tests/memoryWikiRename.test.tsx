import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { AppConfig } from "@/api/core";
import { ArtifactMemoryView } from "@/features/memory/components/ArtifactMemoryView";

const config: AppConfig = {
  serverUrl: "http://localhost:6877",
  apiKey: "test-key",
};
const originalDesktop = window.ardenDesktop;
const roots = new Set<Root>();

const index = {
  path: "index.md",
  title: "Index",
  kind: "topic",
  type: "file",
  directory: "",
  scope: { kind: "user", key: null },
  content: "",
  snippet: null,
  summary: null,
  revision: "index-r1",
  record_count: 0,
  generated: true,
  editable: false,
  readonly_reason: "Generated",
  updated_at: null,
  labels: [],
  source: null,
};
const page = {
  path: "topics/a.md",
  title: "Canonical A",
  kind: "topic",
  type: "file",
  directory: "topics",
  scope: { kind: "user", key: null },
  content: "A prose",
  editable_content: null,
  snippet: null,
  summary: null,
  revision: "a-r1",
  record_count: 0,
  generated: false,
  editable: false,
  readonly_reason: "Managed wiki page",
  updated_at: null,
  labels: [],
  source: "wiki",
  timeline: [],
  frontmatter: { page_id: "page-a", title: "Canonical A" },
};
const approval = {
  approval_id: "approval-1",
  old_path: "topics/a.md",
  new_path: "topics/renamed.md",
  old_title: "Canonical A",
  new_title: "Renamed A",
  link_count: 3,
  page_count: 2,
  generation: 0,
  status: "pending",
  created_at: "2026-07-28T10:00:00Z",
  resolved_at: null,
  commit_id: null,
  resolution: null,
  replacement_approval_id: null,
};

function ok(data: unknown) {
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    contentType: "application/json",
    data,
    text: "",
  };
}

function installBridge(
  options: {
    approvals?: unknown[];
    loadApprovals?: () => unknown[] | Promise<unknown[]>;
    requestRename?: () => unknown | Promise<unknown>;
    accept?: () => unknown;
    reject?: () => unknown;
    artifacts?: () => unknown[];
    detail?: (path: string) => unknown;
  } = {},
) {
  const requests: Array<{ path: string; method: string; body: unknown }> = [];
  let currentApprovals = options.approvals ?? [];
  window.ardenDesktop = {
    api: {
      request: async (_config, request) => {
        const method = request.method ?? "GET";
        const body = request.body ? JSON.parse(request.body) : null;
        requests.push({ path: request.path, method, body });
        if (request.path === "/admin/wiki/maintenance-reviews" && method === "GET") return ok({ reviews: [] });
        if (request.path === "/admin/memory/artifacts") return ok({ artifacts: options.artifacts?.() ?? [index, page] });
        if (request.path.startsWith("/admin/memory/artifacts/")) {
          const path = decodeURIComponent(request.path.replace("/admin/memory/artifacts/", ""));
          if (options.detail) return ok({ artifact: options.detail(path) });
          if (path === "topics/a.md") return ok({ artifact: page });
          if (path === "index.md")
            return ok({
              artifact: {
                ...index,
                editable_content: null,
                timeline: [],
                frontmatter: {},
              },
            });
        }
        if (request.path === "/admin/wiki/rename-approvals" && method === "GET") {
          return ok({
            approvals: options.loadApprovals ? await options.loadApprovals() : currentApprovals,
          });
        }
        if (request.path === "/admin/wiki/rename-approvals" && method === "POST") {
          const result = options.requestRename ? await options.requestRename() : {
            status: "pending",
            approval,
            commit_id: null,
            replacement_approval_id: null,
          };
          const created = (result as { approval?: unknown }).approval;
          if (created) currentApprovals = [created];
          return ok(result);
        }
        if (request.path === "/admin/wiki/rename-approvals/approval-1/accept") {
          const result = options.accept?.() ?? {
            status: "accepted",
            approval: { ...approval, status: "accepted" },
            commit_id: "commit-1",
            replacement_approval_id: null,
          };
          if ((result as { status?: string }).status === "accepted") currentApprovals = [];
          return ok(result);
        }
        if (request.path === "/admin/wiki/rename-approvals/approval-1/reject") {
          const result = options.reject?.() ?? {
            status: "rejected",
            approval: { ...approval, status: "rejected" },
            commit_id: null,
            replacement_approval_id: null,
          };
          if ((result as { status?: string }).status === "rejected") currentApprovals = [];
          return ok(result);
        }
        if (request.path === "/admin/memory/page-edits/preview")
          return ok({
            preview: {
              id: "preview-1",
              path: "topics/a.md",
              base_revision: "a-r1",
              result_revision: "a-r2",
              patch: "patch",
              operations: [],
              questions: [],
              analysis_pending: false,
            },
          });
        throw new Error(`Unexpected request: ${method} ${request.path}`);
      },
    },
  } as Window["ardenDesktop"];
  return requests;
}

function mount() {
  localStorage.setItem("arden.desktop.memory.inspectorOpen", "false");
  localStorage.setItem("arden.desktop.memory.lastPath", "topics/a.md");
  const host = document.createElement("div");
  host.id = "app";
  host.style.height = "800px";
  document.body.append(host);
  const root = createRoot(host);
  roots.add(root);
  return { host, root };
}

async function settle(ms = 220) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, ms));
  });
}

function setInputValue(input: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

afterEach(async () => {
  for (const root of roots) await act(async () => root.unmount());
  roots.clear();
  window.ardenDesktop = originalDesktop;
  document.body.replaceChildren();
  localStorage.removeItem("arden.desktop.memory.inspectorOpen");
  localStorage.removeItem("arden.desktop.memory.lastPath");
});

test("pending rename approvals rehydrate when Memory remounts", async () => {
  const requests = installBridge({ approvals: [approval] });
  const first = mount();
  await act(async () => first.root.render(<ArtifactMemoryView config={config} />));
  await settle();
  expect(first.host.querySelector("[data-wiki-rename-approval]")?.textContent).toContain("topics/renamed.md");
  await act(async () => first.root.unmount());
  roots.delete(first.root);

  const second = mount();
  await act(async () => second.root.render(<ArtifactMemoryView config={config} />));
  await settle();
  expect(second.host.querySelector("[data-wiki-rename-approval]")?.textContent).toContain("Review page rename");
  expect(requests.filter((request) => request.path === "/admin/wiki/rename-approvals" && request.method === "GET")).toHaveLength(2);
});

test("a durable rename approval blocks workspace shortcuts and traps action focus", async () => {
  installBridge({ approvals: [approval] });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();

  const sheet = host.querySelector<HTMLElement>("[data-wiki-rename-approval]")!;
  const reject = sheet.querySelector<HTMLButtonElement>("button.danger")!;
  const accept = sheet.querySelector<HTMLButtonElement>("button.primary")!;
  expect(document.activeElement).toBe(reject);
  expect(host.querySelector<HTMLElement>(".memory-focus-cache")?.inert).toBe(true);

  await act(async () => {
    accept.focus();
    accept.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
  });
  expect(document.activeElement).toBe(reject);
  await act(async () => reject.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true })));
  expect(document.activeElement).toBe(accept);

  const tabsBefore = host.querySelectorAll(".mw-doc-tab").length;
  await act(async () => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "o", metaKey: true, bubbles: true }));
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "w", metaKey: true, bubbles: true }));
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "[", metaKey: true, bubbles: true }));
  });
  await settle();
  expect(host.querySelector('[aria-label="Quick switcher"]')).toBeNull();
  expect(host.querySelectorAll(".mw-doc-tab")).toHaveLength(tabsBefore);
  expect(localStorage.getItem("arden.desktop.memory.lastPath")).toBe("topics/a.md");
});

test("a local rename draft blocks history, switcher, and tab navigation", async () => {
  installBridge();
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();
  await act(async () => host.querySelector<HTMLButtonElement>('[data-memory-entry="index.md"]')?.click());
  await settle();
  await act(async () => host.querySelector<HTMLButtonElement>('[data-memory-entry="topics/a.md"]')?.click());
  await settle();
  const tabsBefore = host.querySelectorAll(".mw-doc-tab").length;
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Rename page"]')?.click());
  await settle();

  await act(async () => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "[", metaKey: true, bubbles: true }));
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "p", metaKey: true, bubbles: true }));
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "w", metaKey: true, bubbles: true }));
  });
  await settle();
  expect(host.querySelector("h1")?.textContent).toBe("Canonical A");
  expect(host.querySelector('[aria-label="Quick switcher"]')).toBeNull();
  expect(host.querySelectorAll(".mw-doc-tab")).toHaveLength(tabsBefore);
});

test("requesting a rename replaces the draft and focuses the approval action", async () => {
  installBridge();
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Rename page"]')?.click());
  const path = host.querySelector<HTMLInputElement>('[data-wiki-rename-draft] input[name="path"]')!;
  await act(async () => setInputValue(path, "topics/renamed.md"));
  await act(async () => host.querySelector<HTMLButtonElement>("[data-wiki-rename-draft] button.primary")?.click());
  await settle();

  const reject = host.querySelector<HTMLButtonElement>("[data-wiki-rename-approval] button.danger");
  expect(host.querySelector("[data-wiki-rename-draft]")).toBeNull();
  expect(document.activeElement).toBe(reject);
});

test("a failed request rehydrates a durable approval before enabling cancellation", async () => {
  let created = false;
  const requests = installBridge({
    loadApprovals: () => created ? [approval] : [],
    requestRename: () => {
      created = true;
      throw new Error("connection lost");
    },
  });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Rename page"]')?.click());
  const path = host.querySelector<HTMLInputElement>('[data-wiki-rename-draft] input[name="path"]')!;
  await act(async () => setInputValue(path, "topics/renamed.md"));
  await act(async () => host.querySelector<HTMLButtonElement>("[data-wiki-rename-draft] button.primary")?.click());
  await settle();

  const reject = host.querySelector<HTMLButtonElement>("[data-wiki-rename-approval] button.danger");
  expect(host.querySelector("[data-wiki-rename-draft]")).toBeNull();
  expect(reject).not.toBeNull();
  expect(document.activeElement).toBe(reject);
  expect(requests.filter((request) => request.path === "/admin/wiki/rename-approvals" && request.method === "GET")).toHaveLength(2);
});

test("a failed reconciliation stays blocking until a retry proves no approval exists", async () => {
  let approvalLoads = 0;
  installBridge({
    loadApprovals: () => {
      approvalLoads += 1;
      if (approvalLoads === 2) throw new Error("offline");
      return [];
    },
    requestRename: () => {
      throw new Error("connection lost");
    },
  });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Rename page"]')?.click());
  const path = host.querySelector<HTMLInputElement>('[data-wiki-rename-draft] input[name="path"]')!;
  await act(async () => setInputValue(path, "topics/renamed.md"));
  await act(async () => host.querySelector<HTMLButtonElement>("[data-wiki-rename-draft] button.primary")?.click());
  await settle();

  const draft = host.querySelector<HTMLElement>("[data-wiki-rename-draft]")!;
  const cancel = draft.querySelector<HTMLButtonElement>("button.quiet")!;
  const reconcile = draft.querySelector<HTMLButtonElement>("button.primary")!;
  expect(draft.textContent).toContain("Rename request outcome is unknown");
  expect(cancel.disabled).toBe(true);
  expect(path.disabled).toBe(true);
  expect(reconcile.textContent).toContain("Check request status");
  expect(document.activeElement).toBe(reconcile);
  await act(async () => draft.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
  expect(host.querySelector("[data-wiki-rename-draft]")).not.toBeNull();

  await act(async () => reconcile.click());
  await settle();
  expect(host.querySelector("[data-wiki-rename-draft]")?.textContent).toContain("Rename request failed: connection lost");
  expect(cancel.disabled).toBe(false);
  expect(path.disabled).toBe(false);
  expect(host.querySelector("[data-wiki-rename-draft] button.primary")?.textContent).toContain("Request rename");
  expect(document.activeElement).toBe(host.querySelector('[data-wiki-rename-draft] input[name="title"]'));
});

test("managed page titles label the note, rail, tabs, and switcher", async () => {
  installBridge();
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();

  expect(host.querySelector("h1")?.textContent).toBe("Canonical A");
  expect(host.querySelector('[data-memory-entry="topics/a.md"]')?.textContent).toContain("Canonical A");
  expect(host.querySelector(".mw-doc-tab")?.textContent).toContain("Canonical A");
  await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "o", metaKey: true, bubbles: true })));
  await settle();
  expect(host.querySelector(".memory-switcher__row-title")?.textContent).toBe("Canonical A");
});

test("accepted rename replaces current navigation state before refresh", async () => {
  const renamed = {
    ...page,
    path: "topics/renamed.md",
    title: "Renamed A",
    revision: "renamed-r1",
    content: "Renamed prose",
    frontmatter: { page_id: "page-a", title: "Renamed A" },
  };
  let artifacts: unknown[] = [index, page];
  let approvalOpen = false;
  installBridge({
    loadApprovals: () => approvalOpen ? [approval] : [],
    artifacts: () => artifacts,
    detail: (path) => (path === "topics/renamed.md" ? renamed : path === "index.md" ? { ...index, editable_content: null, timeline: [], frontmatter: {} } : page),
    accept: () => {
      approvalOpen = false;
      artifacts = [index, renamed];
      return {
        status: "accepted",
        approval: { ...approval, status: "accepted" },
        commit_id: "commit-1",
        replacement_approval_id: null,
      };
    },
  });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();
  await act(async () => host.querySelector<HTMLButtonElement>('[data-memory-entry="index.md"]')?.click());
  await settle();
  await act(async () => host.querySelector<HTMLButtonElement>('[data-memory-entry="topics/a.md"]')?.click());
  await settle();
  approvalOpen = true;
  await act(async () => window.dispatchEvent(new Event("focus")));
  await settle();
  await act(async () => host.querySelector<HTMLButtonElement>("[data-wiki-rename-approval] button.primary")?.click());
  await settle(400);

  expect(host.querySelector("h1")?.textContent).toBe("Renamed A");
  expect(host.querySelector(".mw-doc-tab")?.textContent).toContain("Renamed A");
  expect(host.querySelector(".mw-doc-tab")?.textContent).not.toContain("Canonical A");
  expect(localStorage.getItem("arden.desktop.memory.lastPath")).toBe("topics/renamed.md");
  const replacementRename = host.querySelector('button[aria-label="Rename page"]');
  const renamedNote = host.querySelector('[data-memory-note-path="topics/renamed.md"]');
  expect(document.activeElement === replacementRename || document.activeElement === renamedNote).toBe(true);

  await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "[", metaKey: true, bubbles: true })));
  await settle();
  expect(host.querySelector("h1")?.textContent).toBe("index");
  await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "]", metaKey: true, bubbles: true })));
  await settle();
  expect(host.querySelector("h1")?.textContent).toBe("Renamed A");
});

test("accepted rename never steals focus when another page is current", async () => {
  const renamed = {
    ...page,
    path: "topics/renamed.md",
    title: "Renamed A",
    revision: "renamed-r1",
    content: "Renamed prose",
    frontmatter: { page_id: "page-a", title: "Renamed A" },
  };
  let artifacts: unknown[] = [index, page];
  let approvalOpen = false;
  installBridge({
    loadApprovals: () => approvalOpen ? [approval] : [],
    artifacts: () => artifacts,
    detail: (path) => path === "topics/renamed.md" ? renamed : path === "index.md"
      ? { ...index, editable_content: null, timeline: [], frontmatter: {} }
      : page,
    accept: () => {
      approvalOpen = false;
      artifacts = [index, renamed];
      return {
        status: "accepted",
        approval: { ...approval, status: "accepted" },
        commit_id: "commit-1",
        replacement_approval_id: null,
      };
    },
  });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();
  await act(async () => host.querySelector<HTMLButtonElement>('[data-memory-entry="index.md"]')?.click());
  await settle();
  approvalOpen = true;
  await act(async () => window.dispatchEvent(new Event("focus")));
  await settle();
  await act(async () => host.querySelector<HTMLButtonElement>("[data-wiki-rename-approval] button.primary")?.click());
  await settle(400);
  expect(host.querySelector("h1")?.textContent).toBe("index");

  const renamedEntry = host.querySelector<HTMLButtonElement>('[data-memory-entry="topics/renamed.md"]')!;
  await act(async () => {
    renamedEntry.focus();
    renamedEntry.click();
  });
  await settle(400);
  expect(host.querySelector("h1")?.textContent).toBe("Renamed A");
  expect(document.activeElement).toBe(renamedEntry);
});

test("rejected rename keeps the selected page and navigation path", async () => {
  installBridge({ approvals: [approval] });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();
  await act(async () => host.querySelector<HTMLButtonElement>("[data-wiki-rename-approval] button.danger")?.click());
  await settle();

  expect(host.querySelector("h1")?.textContent).toBe("Canonical A");
  expect(host.querySelector(".mw-doc-tab")?.textContent).toContain("Canonical A");
  expect(localStorage.getItem("arden.desktop.memory.lastPath")).toBe("topics/a.md");
});

test("stale acceptance keeps the sheet open with its replacement approval", async () => {
  installBridge({
    approvals: [approval],
    accept: () => ({
      status: "pending",
      approval: {
        ...approval,
        approval_id: "approval-2",
        generation: 1,
        new_title: "Fresh name",
        replacement_approval_id: "approval-2",
      },
      commit_id: null,
      replacement_approval_id: "approval-2",
    }),
  });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();
  await act(async () => host.querySelector<HTMLButtonElement>("button.primary")?.click());
  await settle();
  const sheet = host.querySelector("[data-wiki-rename-approval]");
  expect(sheet?.textContent).toContain("generation 1");
  expect(sheet?.textContent).toContain("Fresh name");
  expect(document.activeElement).toBe(sheet?.querySelector("button.danger"));
});

test("an applying approval offers an explicit retry", async () => {
  installBridge({ approvals: [{ ...approval, status: "applying" }] });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();
  expect(host.querySelector("[data-wiki-rename-approval]")?.textContent).toContain("Retry rename");
  expect(host.querySelector("[data-wiki-rename-approval] button.danger")).toBeNull();
});

test("an acceptance race keeps a returned applying approval blocking", async () => {
  installBridge({
    approvals: [approval],
    accept: () => ({
      status: "applying",
      approval: { ...approval, status: "applying" },
      commit_id: null,
      replacement_approval_id: null,
    }),
  });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();
  await act(async () => host.querySelector<HTMLButtonElement>("button.primary")?.click());
  await settle();

  expect(host.querySelector("[data-wiki-rename-approval]")?.textContent).toContain("Retry rename");
  expect(host.querySelector("[data-wiki-rename-approval] button.danger")).toBeNull();
});

test("a rehydrated approval replaces an unfinished local rename draft", async () => {
  let release: ((approvals: unknown[]) => void) | undefined;
  const pendingApprovals = new Promise<unknown[]>((resolve) => {
    release = resolve;
  });
  installBridge({ loadApprovals: () => pendingApprovals });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Rename page"]')?.click());
  expect(host.querySelector("[data-wiki-rename-draft]")).not.toBeNull();

  await act(async () => release?.([approval]));
  await settle();

  expect(host.querySelector("[data-wiki-rename-draft]")).toBeNull();
  const sheet = host.querySelector("[data-wiki-rename-approval]");
  expect(sheet?.textContent).toContain("Review page rename");
  expect(document.activeElement).toBe(sheet?.querySelector("button.danger"));
});

test("unchanged rename paths show validation and restore the Rename trigger after cancel", async () => {
  const requests = installBridge();
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();
  const trigger = host.querySelector<HTMLButtonElement>('button[aria-label="Rename page"]')!;
  await act(async () => trigger.click());
  await settle();
  await act(async () => host.querySelector<HTMLButtonElement>("[data-wiki-rename-draft] button.primary")?.click());
  await settle();
  expect(host.querySelector('[role="alert"]')?.textContent).toContain("Choose a different path");
  expect(requests.some((request) => request.path === "/admin/wiki/rename-approvals" && request.method === "POST")).toBe(false);
  await act(async () => host.querySelector<HTMLButtonElement>("[data-wiki-rename-draft] button.quiet")?.click());
  await settle();
  expect(document.activeElement).toBe(trigger);
});

test("managed wiki pages stay read-only while rename remains available", async () => {
  installBridge();
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();
  expect(host.querySelector<HTMLButtonElement>('button[aria-label="Edit memory note"]')?.disabled).toBe(true);
  const rename = host.querySelector<HTMLButtonElement>('button[aria-label="Rename page"]')!;
  expect(rename.disabled).toBe(false);
  await act(async () => rename.click());
  expect(host.querySelector("[data-wiki-rename-draft]")?.textContent).toContain("Title-only editing is not yet available");
  expect(host.querySelector("[data-wiki-rename-draft]")?.textContent).not.toContain("Properties");
});
