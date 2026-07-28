import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ArtifactMemoryView } from "@/features/memory/components/ArtifactMemoryView";

let root: Root | null = null;
let host: HTMLElement | null = null;

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  host?.remove();
  host = null;
  document.body.replaceChildren();
});

function response(data: unknown) {
  return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data, text: JSON.stringify(data) };
}

async function settle(ms = 0) {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, ms));
}

test("canonical wiki view shows links and history, saves with page tokens, and keeps health read-only", async () => {
  const originalDesktop = window.ardenDesktop;
  const requests: Array<{ path: string; method?: string; body?: string }> = [];
  const atlasFrontmatter = [
    "---",
    "page_id: page-a",
    "title: Atlas",
    "aliases: []",
    "lifecycle: active",
    'fact_citations: [{"fact_id":"fact-a","version":"fact-v1"}]',
    "generated_from_revision: facts-v1",
    "---",
    "",
  ].join("\n");
  const pages = [
    { page_id: "page-a", path: "projects/atlas.md", resource_state: "active", title: "Atlas", aliases: [], lifecycle: "active", redirect_to: null, metadata: { fact_citations: [{ fact_id: "fact-a", version: "fact-v1" }], generated_from_revision: "facts-v1" }, version: "v1", repository_head: "head-1" },
    { page_id: "health", path: "health.md", resource_state: "active", title: "Health", aliases: [], lifecycle: "active", redirect_to: null, metadata: {}, version: "health-v1", repository_head: "head-1" },
  ];
  let updateCount = 0;
  window.ardenDesktop = {
    ...originalDesktop,
    api: {
      request: async (_config, request) => {
        requests.push(request);
        if (request.path === "/admin/wiki/rename-approvals") return response({ approvals: [] });
        if (request.path === "/admin/wiki/maintenance-reviews") return response({ reviews: [] });
        if (request.path === "/admin/wiki/pages") return response({ repository_head: "head-1", pages });
        if (request.path === "/admin/wiki/pages/page-a") {
          if (request.method === "PUT") {
            updateCount += 1;
            const content = String(JSON.parse(request.body ?? "{}").content);
            const title = /^title:\s*(.+)$/m.exec(content)?.[1]?.replace(/^"|"$/g, "") ?? "Atlas";
            return response({
              ...pages[0],
              title,
              content,
              version: `v${updateCount + 1}`,
              repository_head: `head-${updateCount + 1}`,
            });
          }
          return response({ ...pages[0], content: `${atlasFrontmatter}# Atlas\n\nSee [[Health]].` });
        }
        if (request.path === "/admin/wiki/pages/health") return response({ ...pages[1], content: "# Health" });
        if (request.path === "/admin/wiki/pages/page-a/links") return response({ page_id: "page-a", repository_head: "head-1", outgoing: [{ source_page_id: "page-a", node: { target: "Health" }, status: "resolved", target_page_id: "health", candidates: [] }], backlinks: [] });
        if (request.path === "/admin/wiki/pages/page-a/history") return response({ page_id: "page-a", repository_head: "head-1", commits: [{ commit_id: "commit-1", actor: "user", origin: "manual", reason: "Created Atlas", timestamp: "2026-07-28T09:00:00Z" }] });
        if (request.path === "/admin/wiki/pages/health/links") return response({ page_id: "health", repository_head: "head-1", outgoing: [], backlinks: [] });
        if (request.path === "/admin/wiki/pages/health/history") return response({ page_id: "health", repository_head: "head-1", commits: [] });
        throw new Error(`Unexpected request: ${request.method ?? "GET"} ${request.path}`);
      },
    },
  } as NonNullable<Window["ardenDesktop"]>;

  host = document.createElement("div");
  host.id = "app";
  document.body.append(host);
  root = createRoot(host);
  try {
    await act(async () => { root?.render(<ArtifactMemoryView config={{ serverUrl: "http://arden.test", apiKey: "key" }} />); await settle(); });
    expect(host.textContent).toContain("Outgoing 1 · Incoming 0");
    expect(host.textContent).toContain("Created Atlas");
    expect(host.textContent).not.toContain("page_id:");
    expect(host.textContent).not.toContain("fact-a");

    const collapse = host.querySelector<HTMLButtonElement>('[aria-label="Compact instruments"]')!;
    expect(collapse.getAttribute("aria-expanded")).toBe("true");
    await act(async () => collapse.click());
    expect(host.querySelector(".memory-ws")?.classList.contains("instruments-collapsed")).toBe(true);
    expect(collapse.getAttribute("aria-expanded")).toBe("false");
    await act(async () => collapse.click());

    expect(host.textContent).not.toContain("Rename page");
    await act(async () => host?.querySelector<HTMLButtonElement>('[aria-label="Page actions"]')?.click());
    expect(document.body.textContent).toContain("Rename page");
    expect(document.body.textContent).toContain("Archive page");
    await act(async () => document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));

    await act(async () => host?.querySelector<HTMLButtonElement>('[aria-label="Edit memory note"]')?.click());
    expect(Array.from(host.querySelectorAll("button")).some((button) => button.textContent === "Save")).toBe(true);
    expect(Array.from(host.querySelectorAll("button")).some((button) => button.textContent === "Cancel")).toBe(true);
    expect(host.querySelector<HTMLButtonElement>('[data-memory-entry="health.md"]')?.disabled).toBe(true);
    const textarea = host.querySelector<HTMLTextAreaElement>('textarea[aria-label="Markdown source for projects/atlas.md"]')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, "# Atlas updated");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => { window.dispatchEvent(new KeyboardEvent("keydown", { key: "s", metaKey: true, bubbles: true })); await settle(); });
    const updates = requests.filter((request) => request.path === "/admin/wiki/pages/page-a" && request.method === "PUT");
    expect(JSON.parse(updates[0]?.body ?? "{}")).toEqual({ content: `${atlasFrontmatter}# Atlas updated`, expected_version: "v1", expected_head: "head-1" });

    await act(async () => host?.querySelector<HTMLButtonElement>(".mw-props-head")?.click());
    const titleRow = Array.from(host.querySelectorAll<HTMLElement>(".mw-prop-row"))
      .find((row) => row.querySelector(".mw-prop-key")?.textContent === "title")!;
    await act(async () => titleRow.querySelector<HTMLButtonElement>(".mw-prop-editable")?.click());
    const titleInput = titleRow.querySelector<HTMLInputElement>(".mw-prop-edit-input")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(titleInput, "Atlas Prime");
      titleInput.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      await settle();
    });
    const propertyUpdate = requests.filter((request) =>
      request.path === "/admin/wiki/pages/page-a" && request.method === "PUT"
    )[1]!;
    const propertyPayload = JSON.parse(propertyUpdate.body ?? "{}");
    expect(propertyPayload.expected_version).toBe("v2");
    expect(propertyPayload.expected_head).toBe("head-2");
    expect(propertyPayload.content).toContain('title: "Atlas Prime"');
    expect(propertyPayload.content).toContain('fact_citations: [{"fact_id":"fact-a","version":"fact-v1"}]');
    expect(propertyPayload.content).not.toContain("[object Object]");

    await act(async () => host?.querySelector<HTMLButtonElement>('[data-memory-entry="health.md"]')?.click());
    await act(async () => { await settle(); });
    expect(host.querySelector('button[aria-label="Edit memory note"]')).toBeNull();
    expect(host.querySelector('button[aria-label="Page actions"]')).toBeNull();
    expect(host.textContent).not.toContain("Saved page properties.");
  } finally {
    window.ardenDesktop = originalDesktop;
  }
});

test("rename approval discovery fails closed and has a retry surface", async () => {
  const originalDesktop = window.ardenDesktop;
  let renameChecks = 0;
  const page = {
    page_id: "page-a",
    path: "atlas.md",
    resource_state: "active",
    title: "Atlas",
    aliases: [],
    lifecycle: "active",
    redirect_to: null,
    metadata: {},
    version: "v1",
    repository_head: "head-1",
    created_at: "2026-07-28T09:00:00Z",
    updated_at: "2026-07-28T09:00:00Z",
  };
  window.ardenDesktop = {
    ...originalDesktop,
    api: {
      request: async (_config, request) => {
        if (request.path === "/admin/wiki/rename-approvals") {
          renameChecks += 1;
          if (renameChecks === 1) throw new Error("rename store unavailable");
          return response({ approvals: [] });
        }
        if (request.path === "/admin/wiki/maintenance-reviews") return response({ reviews: [] });
        if (request.path === "/admin/wiki/pages") return response({ repository_head: "head-1", pages: [page] });
        if (request.path === "/admin/wiki/pages/page-a") return response({ ...page, content: "---\npage_id: page-a\ntitle: Atlas\naliases: []\nlifecycle: active\n---\n# Atlas\n" });
        if (request.path === "/admin/wiki/pages/page-a/links") return response({ page_id: "page-a", repository_head: "head-1", outgoing: [], backlinks: [] });
        if (request.path === "/admin/wiki/pages/page-a/history") return response({ page_id: "page-a", repository_head: "head-1", commits: [] });
        throw new Error(`Unexpected request: ${request.method ?? "GET"} ${request.path}`);
      },
    },
  } as NonNullable<Window["ardenDesktop"]>;

  host = document.createElement("div");
  host.id = "app";
  document.body.append(host);
  root = createRoot(host);
  try {
    await act(async () => {
      root?.render(<ArtifactMemoryView config={{ serverUrl: "http://arden.test", apiKey: "key" }} />);
      await settle();
    });
    expect(host.querySelector("[data-wiki-rename-status]")?.textContent).toContain("rename store unavailable");
    expect(host.querySelector('[aria-label="Edit memory note"]')).toBeNull();
    expect(host.querySelector('[aria-label="Page actions"]')).toBeNull();

    await act(async () => {
      Array.from(host.querySelectorAll<HTMLButtonElement>("button"))
        .find((button) => button.textContent === "Retry check")?.click();
      await settle();
    });
    expect(host.querySelector("[data-wiki-rename-status]")).toBeNull();
    expect(host.querySelector('[aria-label="Edit memory note"]')).not.toBeNull();
    expect(renameChecks).toBe(2);
  } finally {
    window.ardenDesktop = originalDesktop;
  }
});

test("facts failures are visible and retryable", async () => {
  const originalDesktop = window.ardenDesktop;
  let factAttempts = 0;
  window.ardenDesktop = {
    ...originalDesktop,
    api: {
      request: async (_config, request) => {
        if (request.path === "/admin/wiki/rename-approvals") return response({ approvals: [] });
        if (request.path === "/admin/wiki/maintenance-reviews") return response({ reviews: [] });
        if (request.path === "/admin/wiki/pages") return response({ repository_head: null, pages: [] });
        if (request.path.startsWith("/admin/facts?")) {
          factAttempts += 1;
          if (factAttempts === 1) throw new Error("fact index unavailable");
          return response({
            facts: [{
              fact_id: "fact-1",
              text: "The retry worked.",
              kind: "fact",
              labels: [],
              subjects: ["Arden"],
              lifecycle: "durable",
              status: "active",
              certainty: "confirmed",
              evidence_class: "direct",
              created_at: "2026-07-28T10:00:00Z",
              review_at: null,
              version: "fact-v1",
            }],
            has_more: false,
            next_after: null,
          });
        }
        throw new Error(`Unexpected request: ${request.method ?? "GET"} ${request.path}`);
      },
    },
  } as NonNullable<Window["ardenDesktop"]>;

  host = document.createElement("div");
  host.id = "app";
  document.body.append(host);
  root = createRoot(host);
  try {
    await act(async () => { root?.render(<ArtifactMemoryView config={{ serverUrl: "http://arden.test", apiKey: "key" }} />); await settle(); });
    await act(async () => { host?.querySelector<HTMLButtonElement>('[aria-label="Facts"]')?.click(); await settle(260); });
    expect(host.textContent).toContain("Couldn't load memory facts");
    expect(host.textContent).toContain("fact index unavailable");
    await act(async () => {
      Array.from(host?.querySelectorAll<HTMLButtonElement>("button") ?? [])
        .find((button) => button.textContent === "Retry")?.click();
      await settle(260);
    });
    expect(host.textContent).toContain("The retry worked.");
    expect(factAttempts).toBe(2);
  } finally {
    window.ardenDesktop = originalDesktop;
  }
});

test("a durable maintenance question blocks Memory until answered", async () => {
  const originalDesktop = window.ardenDesktop;
  let pending = true;
  const requests: Array<{ path: string; method?: string; body?: string }> = [];
  const review = {
    review_id: "review-1",
    blocking_commit_id: "a".repeat(64),
    generation: 3,
    status: "needs_review",
    summary: "Should this related page use the canonical project name?",
    proposal: {
      kind: "maintenance_updates",
      summary: "Use the canonical name.",
      updates: [{ pageId: "page-a", title: "Atlas", aliases: [], body: "Atlas is current." }],
    },
    created_at: "2026-07-28T10:00:00Z",
    updated_at: "2026-07-28T10:00:00Z",
    resolved_at: null,
    decision_note: null,
  };
  window.ardenDesktop = {
    ...originalDesktop,
    api: {
      request: async (_config, request) => {
        requests.push(request);
        if (request.path === "/admin/wiki/rename-approvals") return response({ approvals: [] });
        if (request.path === "/admin/wiki/maintenance-reviews" && request.method !== "POST") {
          return response({ reviews: pending ? [review] : [] });
        }
        if (request.path === "/admin/wiki/maintenance-reviews/review-1/reject") {
          pending = false;
          return response({ ...review, status: "rejected", resolved_at: "2026-07-28T10:01:00Z" });
        }
        if (request.path === "/admin/wiki/pages") return response({ repository_head: null, pages: [] });
        throw new Error(`Unexpected request: ${request.method ?? "GET"} ${request.path}`);
      },
    },
  } as NonNullable<Window["ardenDesktop"]>;

  host = document.createElement("div");
  host.id = "app";
  document.body.append(host);
  root = createRoot(host);
  try {
    await act(async () => { root?.render(<ArtifactMemoryView config={{ serverUrl: "http://arden.test", apiKey: "key" }} />); await settle(); });
    const sheet = host.querySelector<HTMLElement>("[data-wiki-maintenance-review]")!;
    expect(sheet.textContent).toContain("Wiki Maintenance needs an answer");
    expect(host.querySelector(".memory-focus-cache")?.inert).toBe(true);
    await act(async () => sheet.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(host.querySelector("[data-wiki-maintenance-review]")).not.toBeNull();

    const reject = Array.from(sheet.querySelectorAll<HTMLButtonElement>("button"))
      .find((button) => button.textContent === "Reject")!;
    await act(async () => { reject.click(); await settle(30); });
    expect(JSON.parse(requests.find((request) =>
      request.path.endsWith("/review-1/reject")
    )?.body ?? "{}")).toEqual({ generation: 3 });
    expect(host.querySelector("[data-wiki-maintenance-review]")).toBeNull();
    expect(Boolean(host.querySelector("[data-wiki-maintenance-status]"))).toBe(false);
    expect(requests.filter((request) => request.path === "/admin/wiki/maintenance-reviews").length).toBeGreaterThan(1);
    expect(host.querySelector(".memory-focus-cache")?.hasAttribute("inert")).toBe(false);
  } finally {
    window.ardenDesktop = originalDesktop;
  }
});
