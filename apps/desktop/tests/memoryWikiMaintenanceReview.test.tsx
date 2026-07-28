import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { AppConfig } from "@/api/core";
import { ArtifactMemoryView } from "@/features/memory/components/ArtifactMemoryView";
import { useStore } from "@/stores";

const config: AppConfig = {
  serverUrl: "http://localhost:6877",
  apiKey: "test-key",
};
const originalDesktop = window.ardenDesktop;
const roots = new Set<Root>();

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

const review = {
  review_id: "review-1",
  blocking_commit_id: "a".repeat(64),
  generation: 2,
  status: "needs_review",
  summary: "Should this note use the clearer project name?",
  proposal: {
    kind: "maintenance_updates",
    summary: "Use the canonical project name in this note.",
    updates: [
      {
        pageId: "page-a",
        title: "Canonical A",
        aliases: ["A"],
        body: "The canonical project name is A.\n",
      },
    ],
  },
  created_at: "2026-07-28T10:00:00Z",
  updated_at: "2026-07-28T10:00:00Z",
  resolved_at: null,
  decision_note: null,
};

const evidenceReview = {
  ...review,
  summary: "The changed page was too large for automated review.",
  proposal: {
    kind: "manual_evidence_review",
    section: "change 1 diff",
    actualBytes: 131_072,
    actualBytesAtLeast: true,
    limitBytes: 65_536,
  },
};

const renameApproval = {
  approval_id: "rename-1",
  old_path: "topics/a.md",
  new_path: "topics/renamed.md",
  old_title: "Canonical A",
  new_title: "Renamed A",
  link_count: 1,
  page_count: 1,
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
    reviews?: unknown[];
    loadReviews?: () => unknown[] | Promise<unknown[]>;
    loadEvidence?: (path: string) => unknown | Promise<unknown>;
    resolveReview?: (action: string, body: Record<string, unknown>) => unknown | Promise<unknown>;
    renameApprovals?: unknown[];
    loadRenameApprovals?: () => unknown[] | Promise<unknown[]>;
  } = {},
) {
  const requests: Array<{ path: string; method: string; body: Record<string, unknown> | null }> = [];
  let reviews = options.reviews ?? [];
  let renameApprovals = options.renameApprovals ?? [];

  window.ardenDesktop = {
    api: {
      request: async (_config, request) => {
        const method = request.method ?? "GET";
        const body = request.body ? JSON.parse(request.body) as Record<string, unknown> : null;
        requests.push({ path: request.path, method, body });
        if (request.path === "/admin/memory/artifacts") return ok({ artifacts: [page] });
        if (
          request.path === "/admin/memory/artifacts/topics%2Fa.md"
          || request.path === "/admin/memory/artifacts/topics/a.md"
        ) {
          return ok({ artifact: page });
        }
        if (request.path === "/admin/wiki/rename-approvals" && method === "GET") {
          return ok({ approvals: options.loadRenameApprovals ? await options.loadRenameApprovals() : renameApprovals });
        }
        if (request.path === "/admin/wiki/rename-approvals/rename-1/reject") {
          renameApprovals = [];
          return ok({
            status: "rejected",
            approval: { ...renameApproval, status: "rejected" },
            commit_id: null,
            replacement_approval_id: null,
          });
        }
        if (request.path === "/admin/wiki/maintenance-reviews" && method === "GET") {
          return ok({ reviews: options.loadReviews ? await options.loadReviews() : reviews });
        }
        if (
          request.path.startsWith(`/admin/wiki/maintenance-reviews/${review.review_id}/evidence?`)
          && method === "GET"
        ) {
          return ok(options.loadEvidence ? await options.loadEvidence(request.path) : {
            review_id: review.review_id,
            generation: review.generation,
            actor: "user:desktop",
            origin: "desktop",
            reason: "Updated the canonical project note",
            occurred_at: "2026-07-28T09:45:00Z",
            changeIndex: 0,
            changeCount: 1,
            diffOffset: 0,
            diffEndOffset: 57,
            moreInChange: false,
            previousCursor: null,
            nextCursor: null,
            change: {
              resourceId: "page-a",
              path: "topics/a.md",
              action: "update",
              unifiedDiff: "@@ -1 +1 @@\n-Old project name\n+Canonical project name",
              displayLossy: false,
            },
          });
        }
        const match = request.path.match(/^\/admin\/wiki\/maintenance-reviews\/review-1\/(.+)$/);
        if (match && method === "POST") {
          const action = match[1]!;
          if (options.resolveReview) {
            const result = await options.resolveReview(action, body ?? {});
            reviews = [];
            return ok(result);
          }
          reviews = [];
          return ok({
            ...review,
            status: action === "accept" ? "accepted" : action === "reject" ? "rejected" : "resolved_manual",
            resolved_at: "2026-07-28T11:00:00Z",
            decision_note: body?.note ?? null,
          });
        }
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

function buttonWithText(host: ParentNode, text: string): HTMLButtonElement {
  const button = Array.from(host.querySelectorAll<HTMLButtonElement>("button"))
    .find((candidate) => candidate.textContent?.trim() === text);
  if (!button) throw new Error(`Missing button: ${text}`);
  return button;
}

function setTextareaValue(textarea: HTMLTextAreaElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, value);
  textarea.dispatchEvent(new Event("input", { bubbles: true }));
  textarea.dispatchEvent(new Event("change", { bubbles: true }));
}

afterEach(async () => {
  for (const root of roots) await act(async () => root.unmount());
  roots.clear();
  window.ardenDesktop = originalDesktop;
  document.body.replaceChildren();
  localStorage.removeItem("arden.desktop.memory.inspectorOpen");
  localStorage.removeItem("arden.desktop.memory.lastPath");
  useStore.setState({ memoryVaultVersion: 0, memoryVaultChanges: [] });
});

test("pending maintenance reviews rehydrate as persistent blocking questions", async () => {
  installBridge({ reviews: [review] });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();

  const sheet = host.querySelector<HTMLElement>("[data-wiki-maintenance-review]")!;
  expect(sheet.textContent).toContain("Maintenance review · 1 of 1 · generation 2");
  expect(sheet.textContent).toContain("Use the canonical project name");
  expect(sheet.textContent).toContain("next scheduled pass");
  expect(host.querySelector<HTMLElement>(".memory-focus-cache")?.inert).toBe(true);
  expect(document.activeElement).toBe(buttonWithText(sheet, "Accept change"));

  await act(async () => sheet.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
  expect(host.querySelector("[data-wiki-maintenance-review]")).not.toBeNull();
});

test("accept sends the exact review generation without giving Reject destructive styling", async () => {
  const requests = installBridge({ reviews: [review] });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();

  const sheet = host.querySelector<HTMLElement>("[data-wiki-maintenance-review]")!;
  const reject = buttonWithText(sheet, "Reject");
  expect(reject.classList.contains("danger")).toBe(false);
  await act(async () => buttonWithText(sheet, "Accept change").click());
  await settle(360);
  await settle(80);

  expect(requests.find((request) => request.path.endsWith("/accept"))?.body).toEqual({ generation: 2 });
  expect(host.querySelector("[data-wiki-maintenance-review]")).toBeNull();
  const focused = document.activeElement as HTMLElement;
  expect(focused).not.toBe(document.body);
  expect(focused.closest("[data-memory-note-path]")).toBe(
    host.querySelector("[data-memory-note-path='topics/a.md']"),
  );
});

test("reject records the exact review generation", async () => {
  const requests = installBridge({ reviews: [review] });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();
  await act(async () => buttonWithText(host, "Reject").click());
  await settle();

  expect(requests.find((request) => request.path.endsWith("/reject"))?.body).toEqual({ generation: 2 });
  expect(host.querySelector("[data-wiki-maintenance-review]")).toBeNull();
});

test("manual resolution requires an auditable note and Cancel only exits note mode", async () => {
  const requests = installBridge({ reviews: [review] });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();

  let sheet = host.querySelector<HTMLElement>("[data-wiki-maintenance-review]")!;
  await act(async () => buttonWithText(sheet, "Resolve manually").click());
  await act(async () => buttonWithText(sheet, "Resolve manually").click());
  expect(sheet.querySelector('[role="alert"]')?.textContent).toContain("Describe the manual decision");
  expect(requests.some((request) => request.path.endsWith("/resolve-manually"))).toBe(false);

  await act(async () => buttonWithText(sheet, "Cancel").click());
  expect(host.querySelector("[data-wiki-maintenance-review]")).not.toBeNull();
  expect(host.querySelector("[data-wiki-maintenance-review] textarea")).toBeNull();

  sheet = host.querySelector<HTMLElement>("[data-wiki-maintenance-review]")!;
  await act(async () => buttonWithText(sheet, "Resolve manually").click());
  const textarea = sheet.querySelector<HTMLTextAreaElement>("textarea")!;
  await act(async () => setTextareaValue(textarea, "Verified against the project source."));
  await act(async () => buttonWithText(sheet, "Resolve manually").click());
  await settle();

  expect(requests.find((request) => request.path.endsWith("/resolve-manually"))?.body).toEqual({
    generation: 2,
    note: "Verified against the project source.",
  });
  expect(host.querySelector("[data-wiki-maintenance-review]")).toBeNull();
});

test("a lost response reconciles the durable row before dismissing the question", async () => {
  let pendingReviews: unknown[] = [review];
  const requests = installBridge({
    loadReviews: () => pendingReviews,
    resolveReview: () => {
      pendingReviews = [];
      throw new Error("connection lost");
    },
  });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();
  await act(async () => buttonWithText(host, "Accept change").click());
  await settle();

  expect(requests.filter((request) => request.path === "/admin/wiki/maintenance-reviews")).toHaveLength(2);
  expect(host.querySelector("[data-wiki-maintenance-review]")).toBeNull();
});

test("a failed decision stays blocking when the durable review remains pending", async () => {
  installBridge({
    reviews: [review],
    resolveReview: () => {
      throw new Error("connection lost");
    },
  });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();
  await act(async () => buttonWithText(host, "Accept change").click());
  await settle();

  const sheet = host.querySelector("[data-wiki-maintenance-review]");
  expect(sheet).not.toBeNull();
  expect(sheet?.querySelector('[role="alert"]')?.textContent).toContain("Could not save this decision");
});

test("a fresh review check disables every stale decision until it completes", async () => {
  let loads = 0;
  let finishRefresh: ((value: unknown[]) => void) | null = null;
  const requests = installBridge({
    loadReviews: () => {
      loads += 1;
      if (loads === 1) return [review];
      return new Promise<unknown[]>((resolve) => {
        finishRefresh = resolve;
      });
    },
  });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();

  await act(async () => {
    useStore.getState().memoryVaultChanged({
      paths: [],
      revision: "e".repeat(64),
      reviewRequired: true,
      seq: 9_012,
    });
  });
  await settle(40);

  const sheet = host.querySelector<HTMLElement>("[data-wiki-maintenance-review]")!;
  for (const label of ["Reject", "Resolve manually", "Accept change"]) {
    expect(buttonWithText(sheet, label).disabled).toBe(true);
    buttonWithText(sheet, label).click();
  }
  expect(requests.some((request) => request.method === "POST")).toBe(false);

  await act(async () => finishRefresh?.([review]));
  await settle();
  expect(buttonWithText(sheet, "Accept change").disabled).toBe(false);
});

test("a failed fresh review check exposes only reconciliation", async () => {
  let loads = 0;
  installBridge({
    loadReviews: () => {
      loads += 1;
      if (loads === 1 || loads === 3) return [review];
      throw new Error("fresh review check failed");
    },
  });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();

  await act(async () => {
    useStore.getState().memoryVaultChanged({
      paths: [],
      revision: "f".repeat(64),
      reviewRequired: true,
      seq: 9_013,
    });
  });
  await settle();

  let sheet = host.querySelector<HTMLElement>("[data-wiki-maintenance-review]")!;
  expect(sheet.textContent).toContain("fresh review check failed");
  expect(Array.from(sheet.querySelectorAll("footer button")).map((button) => button.textContent?.trim())).toEqual([
    "Check status",
  ]);

  await act(async () => buttonWithText(sheet, "Check status").click());
  await settle();
  sheet = host.querySelector<HTMLElement>("[data-wiki-maintenance-review]")!;
  expect(buttonWithText(sheet, "Accept change").disabled).toBe(false);
});

test("non-executable maintenance proposals cannot be accepted", async () => {
  installBridge({
    reviews: [{
      ...review,
      proposal: {
        kind: "maintenance_updates",
        summary: "Nothing executable remains.",
        updates: [],
      },
    }],
  });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();

  const sheet = host.querySelector<HTMLElement>("[data-wiki-maintenance-review]")!;
  expect(sheet.textContent).toContain("No executable proposal is available");
  expect(Array.from(sheet.querySelectorAll("button")).some((button) => (
    button.textContent?.includes("Accept")
  ))).toBe(false);
  expect(document.activeElement).toBe(buttonWithText(sheet, "Resolve manually"));
});

test("a live review-required event refreshes Memory without polling", async () => {
  let pendingReviews: unknown[] = [];
  const requests = installBridge({ loadReviews: () => pendingReviews });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();
  expect(host.querySelector("[data-wiki-maintenance-review]")).toBeNull();

  pendingReviews = [review];
  await act(async () => {
    useStore.getState().memoryVaultChanged({
      paths: [],
      revision: "b".repeat(64),
      reviewRequired: true,
      seq: 9_001,
    });
  });
  await settle();

  expect(host.querySelector("[data-wiki-maintenance-review]")?.textContent).toContain(
    "Wiki Maintenance needs a decision",
  );
  expect(requests.filter((request) => request.path === "/admin/wiki/maintenance-reviews")).toHaveLength(2);
});

test("maintenance resolution restores empty workspace focus and clears its restoration state", async () => {
  let pendingReviews: unknown[] = [];
  installBridge({
    loadReviews: () => pendingReviews,
    resolveReview: (action, body) => {
      pendingReviews = [];
      return {
        ...review,
        status: action === "accept" ? "accepted" : "rejected",
        resolved_at: "2026-07-28T11:00:00Z",
        decision_note: body.note ?? null,
      };
    },
  });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();

  await act(async () => host.querySelector<HTMLButtonElement>(".mw-doc-tab-x")?.click());
  await settle(260);
  expect(host.textContent).toContain("No page open");

  pendingReviews = [review];
  await act(async () => {
    useStore.getState().memoryVaultChanged({
      paths: [], revision: "c".repeat(64), reviewRequired: true, seq: 9_010,
    });
  });
  await settle();
  await act(async () => buttonWithText(host, "Accept change").click());
  await settle(320);

  const sidebarToggle = host.querySelector<HTMLButtonElement>("button.sidebar-toggle")!;
  expect(document.activeElement).toBe(sidebarToggle);

  // A later review must capture a fresh return target rather than retaining
  // the empty-workspace fallback state from the first resolution.
  const returnTarget = document.createElement("button");
  document.body.append(returnTarget);
  returnTarget.focus();
  pendingReviews = [review];
  await act(async () => {
    useStore.getState().memoryVaultChanged({
      paths: [], revision: "d".repeat(64), reviewRequired: true, seq: 9_011,
    });
  });
  await settle();
  await act(async () => buttonWithText(host, "Accept change").click());
  await settle(320);

  expect(document.activeElement).toBe(returnTarget);
});

test("a rename approval stays above a queued maintenance question", async () => {
  installBridge({ reviews: [review], renameApprovals: [renameApproval] });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();

  expect(host.querySelector("[data-wiki-rename-approval]")).not.toBeNull();
  expect(host.querySelector("[data-wiki-maintenance-review]")).toBeNull();
  await act(async () => host.querySelector<HTMLButtonElement>("[data-wiki-rename-approval] button.danger")?.click());
  await settle();

  expect(host.querySelector("[data-wiki-rename-approval]")).toBeNull();
  expect(host.querySelector("[data-wiki-maintenance-review]")?.textContent).toContain(
    "Wiki Maintenance needs a decision",
  );
});

test("an initial review-list failure keeps Memory blocked until a successful check", async () => {
  let loads = 0;
  installBridge({
    loadReviews: () => {
      loads += 1;
      if (loads === 1) throw new Error("maintenance store unavailable");
      return [];
    },
  });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();

  const status = host.querySelector<HTMLElement>("[data-wiki-maintenance-status]")!;
  expect(status.textContent).toContain("Couldn’t check maintenance questions");
  expect(host.querySelector<HTMLElement>(".memory-focus-cache")?.inert).toBe(true);
  expect(document.activeElement).toBe(buttonWithText(status, "Check again"));

  await act(async () => buttonWithText(status, "Check again").click());
  await settle();
  expect(host.querySelector("[data-wiki-maintenance-status]")).toBeNull();
  expect(host.querySelector<HTMLElement>(".memory-focus-cache")?.inert).toBe(false);
});

test("an unknown decision outcome exposes only Check status until reconciliation succeeds", async () => {
  let loads = 0;
  installBridge({
    loadReviews: () => {
      loads += 1;
      if (loads === 1) return [review];
      if (loads === 2) throw new Error("reconciliation unavailable");
      return [];
    },
    resolveReview: () => {
      throw new Error("connection lost");
    },
  });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();
  await act(async () => buttonWithText(host, "Accept change").click());
  await settle();

  const sheet = host.querySelector<HTMLElement>("[data-wiki-maintenance-review]")!;
  expect(sheet.textContent).toContain("Decision outcome is unknown");
  expect(Array.from(sheet.querySelectorAll("footer button")).map((button) => button.textContent?.trim())).toEqual([
    "Check status",
  ]);
  expect(document.activeElement).toBe(buttonWithText(sheet, "Check status"));

  await act(async () => buttonWithText(sheet, "Check status").click());
  await settle();
  expect(host.querySelector("[data-wiki-maintenance-review]")).toBeNull();
});

test("focus remains inside the blocking sheet while a decision is saving", async () => {
  let finishSave: (() => void) | null = null;
  installBridge({
    reviews: [review],
    resolveReview: () => new Promise((resolve) => {
      finishSave = () => resolve({ ...review, status: "accepted" });
    }),
  });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();

  await act(async () => buttonWithText(host, "Accept change").click());
  await settle(30);
  const sheet = host.querySelector<HTMLElement>("[data-wiki-maintenance-review]")!;
  expect(document.activeElement).toBe(sheet);
  expect(sheet.contains(document.activeElement)).toBe(true);

  await act(async () => finishSave?.());
  await settle();
});

test("a manual note survives a higher-priority rename question", async () => {
  let renameApprovals: unknown[] = [];
  installBridge({
    reviews: [review],
    loadRenameApprovals: () => renameApprovals,
  });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();

  await act(async () => buttonWithText(host, "Resolve manually").click());
  const textarea = host.querySelector<HTMLTextAreaElement>("[data-wiki-maintenance-review] textarea")!;
  await act(async () => setTextareaValue(textarea, "Checked against the source notebook."));

  renameApprovals = [renameApproval];
  await act(async () => window.dispatchEvent(new FocusEvent("focus")));
  await settle();
  expect(host.querySelector("[data-wiki-rename-approval]")).not.toBeNull();
  expect(host.querySelector("[data-wiki-maintenance-review]")).toBeNull();

  renameApprovals = [];
  await act(async () => host.querySelector<HTMLButtonElement>("[data-wiki-rename-approval] button.danger")?.click());
  await settle();
  expect(host.querySelector<HTMLTextAreaElement>("[data-wiki-maintenance-review] textarea")?.value).toBe(
    "Checked against the source notebook.",
  );
});

test("another desktop resolving a review dismisses the durable question through SSE", async () => {
  let pendingReviews: unknown[] = [review];
  installBridge({ loadReviews: () => pendingReviews });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();
  expect(host.querySelector("[data-wiki-maintenance-review]")).not.toBeNull();

  pendingReviews = [];
  await act(async () => {
    useStore.getState().memoryVaultChanged({
      paths: [],
      revision: "c".repeat(64),
      reviewRequired: true,
      seq: 9_002,
    });
  });
  await settle();
  expect(host.querySelector("[data-wiki-maintenance-review]")).toBeNull();
});

test("manual evidence can be inspected as exact changed-page diffs", async () => {
  const requests = installBridge({ reviews: [evidenceReview] });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();

  const sheet = host.querySelector<HTMLElement>("[data-wiki-maintenance-review]")!;
  expect(sheet.textContent).toContain("change 1 diff");
  expect(sheet.textContent).toContain("at least 131,072 bytes");
  expect(sheet.textContent).toContain("Reject records disapproval");
  expect(buttonWithText(sheet, "Accept as-is").disabled).toBe(false);
  await act(async () => buttonWithText(sheet, "Show reviewed changes").click());
  await settle();

  expect(sheet.textContent).toContain("topics/a.md");
  expect(sheet.textContent).toContain("Updated the canonical project note");
  expect(sheet.querySelector("pre")?.textContent).toContain("+Canonical project name");
  expect(document.activeElement).toBe(sheet.querySelector(".wiki-maintenance-review__evidence-page"));
  expect(requests.some((request) => (
    request.path.endsWith("/evidence?generation=2&change_index=0&diff_offset=0")
  ))).toBe(true);
});

test("evidence ranges use server code-point offsets for Unicode", async () => {
  installBridge({
    reviews: [evidenceReview],
    loadEvidence: () => ({
      review_id: review.review_id,
      generation: review.generation,
      actor: "user:desktop",
      origin: "desktop",
      reason: "Added one emoji",
      occurred_at: "2026-07-28T09:45:00Z",
      changeIndex: 0,
      changeCount: 1,
      diffOffset: 0,
      diffEndOffset: 1,
      moreInChange: false,
      previousCursor: null,
      nextCursor: null,
      change: {
        resourceId: "page-a",
        path: "topics/a.md",
        action: "update",
        unifiedDiff: "😀",
        displayLossy: false,
      },
    }),
  });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();
  await act(async () => buttonWithText(host, "Show reviewed changes").click());
  await settle();

  const position = host.querySelector(".wiki-maintenance-review__evidence-position")?.textContent ?? "";
  expect(position).toContain("characters 1–1");
  expect(position).not.toContain("1–2");
});

test("failed evidence loading remains retryable without dismissing the question", async () => {
  let attempts = 0;
  installBridge({
    reviews: [evidenceReview],
    loadEvidence: () => {
      attempts += 1;
      if (attempts === 1) throw new Error("evidence unavailable");
      return {
        review_id: review.review_id,
        generation: review.generation,
        actor: "user:desktop",
        origin: "desktop",
        reason: "Updated the canonical project note",
        occurred_at: "2026-07-28T09:45:00Z",
        changeIndex: 0,
        changeCount: 1,
        diffOffset: 0,
        diffEndOffset: 23,
        moreInChange: false,
        previousCursor: null,
        nextCursor: null,
        change: {
          resourceId: "page-a",
          path: "topics/a.md",
          action: "update",
          unifiedDiff: "+Canonical project name",
          displayLossy: false,
        },
      };
    },
  });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();

  await act(async () => buttonWithText(host, "Show reviewed changes").click());
  await settle();
  expect(host.querySelector("[data-wiki-maintenance-review]")?.textContent).toContain("evidence unavailable");
  expect(document.activeElement).toBe(buttonWithText(host, "Retry evidence"));

  await act(async () => buttonWithText(host, "Retry evidence").click());
  await settle();
  expect(host.querySelector("[data-wiki-maintenance-review] pre")?.textContent).toContain(
    "+Canonical project name",
  );
  expect(document.activeElement).toBe(
    host.querySelector("[data-wiki-maintenance-review] .wiki-maintenance-review__evidence-page"),
  );
  expect(attempts).toBe(2);
});

test("large evidence replaces one bounded diff page at a time", async () => {
  let finishSecond: ((value: unknown) => void) | null = null;
  const requests = installBridge({
    reviews: [evidenceReview],
    loadEvidence: (path) => {
      const second = path.includes("diff_offset=5");
      const response = {
        review_id: review.review_id,
        generation: review.generation,
        actor: "user:desktop",
        origin: "desktop",
        reason: "Updated the canonical project note",
        occurred_at: "2026-07-28T09:45:00Z",
        changeIndex: 0,
        changeCount: 1,
        diffOffset: second ? 5 : 0,
        diffEndOffset: second ? 11 : 5,
        moreInChange: !second,
        previousCursor: second ? { changeIndex: 0, diffOffset: 0 } : null,
        nextCursor: second ? null : { changeIndex: 0, diffOffset: 5 },
        change: {
          resourceId: "page-a",
          path: "topics/a.md",
          action: "update",
          unifiedDiff: second ? "second" : "first",
          displayLossy: false,
        },
      };
      if (!second) return response;
      return new Promise((resolve) => {
        finishSecond = resolve;
      });
    },
  });
  const { host, root } = mount();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle();

  await act(async () => buttonWithText(host, "Show reviewed changes").click());
  await settle();
  let sheet = host.querySelector<HTMLElement>("[data-wiki-maintenance-review]")!;
  expect(sheet.querySelectorAll("pre")).toHaveLength(1);
  expect(sheet.querySelector("pre")?.textContent).toContain("first");
  expect(document.activeElement).toBe(sheet.querySelector(".wiki-maintenance-review__evidence-page"));

  const next = buttonWithText(sheet, "Next");
  next.focus();
  await act(async () => next.click());
  await settle(30);
  expect(sheet.querySelector("pre")?.textContent).toContain("first");
  expect(document.activeElement).toBe(next);
  expect(next.disabled).toBe(false);
  expect(next.getAttribute("aria-disabled")).toBe("true");

  await act(async () => finishSecond?.({
    review_id: review.review_id,
    generation: review.generation,
    actor: "user:desktop",
    origin: "desktop",
    reason: "Updated the canonical project note",
    occurred_at: "2026-07-28T09:45:00Z",
    changeIndex: 0,
    changeCount: 1,
    diffOffset: 5,
    diffEndOffset: 11,
    moreInChange: false,
    previousCursor: { changeIndex: 0, diffOffset: 0 },
    nextCursor: null,
    change: {
      resourceId: "page-a",
      path: "topics/a.md",
      action: "update",
      unifiedDiff: "second",
      displayLossy: false,
    },
  }));
  await settle();
  sheet = host.querySelector<HTMLElement>("[data-wiki-maintenance-review]")!;
  expect(sheet.querySelectorAll("pre")).toHaveLength(1);
  expect(sheet.querySelector("pre")?.textContent).toContain("second");
  expect(sheet.textContent).not.toContain("first");
  expect(document.activeElement).toBe(sheet.querySelector(".wiki-maintenance-review__evidence-page"));
  expect(requests.some((request) => request.path.includes("diff_offset=5"))).toBe(true);
});
