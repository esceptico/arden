import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { AppConfig } from "@/api/core";
import { ArtifactMemoryView } from "@/features/memory/components/ArtifactMemoryView";
import { clearDrafts, draftKey, getDraft, setDraft } from "@/features/memory/lib/draftStore";
import { useStore } from "@/stores";

const config: AppConfig = { serverUrl: "http://localhost:6877", apiKey: "test-key" };
const originalDesktop = window.ntrpDesktop;
const originalVaultVersion = useStore.getState().memoryVaultVersion;
const roots = new Set<Root>();

type BridgeResponse = {
  ok: boolean;
  status: number;
  statusText: string;
  contentType: string;
  data: unknown;
  text: string;
};

const index = {
  path: "index.md", title: "Index", kind: "topic", type: "file", directory: "",
  scope: { kind: "user", key: null }, content: "", snippet: null, summary: null,
  revision: "index-r1", record_count: 0, generated: true, editable: false,
  readonly_reason: "Generated index", updated_at: null, labels: [], source: null,
};

function note(revision = "note-r1", extra: Record<string, unknown> = {}) {
  return {
    path: "topics/a.md", title: "A", kind: "topic", type: "file", directory: "topics",
    scope: { kind: "user", key: null }, content: "Rendered old prose", editable_content: "# A\n\nOld source bytes.\n",
    snippet: null, summary: "A note", revision, record_count: 0, generated: false, editable: true,
    readonly_reason: null, updated_at: null, labels: [], source: null, timeline: [], frontmatter: {},
    ...extra,
  };
}

function ok(data: unknown): BridgeResponse {
  return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data, text: "" };
}

function conflict(currentRevision: string, currentContent: string): BridgeResponse {
  return {
    ok: false, status: 409, statusText: "Conflict", contentType: "application/json", text: "",
    data: { detail: { error: "page_revision_conflict", current_revision: currentRevision, current_content: currentContent, base_revision: "note-r1", candidate_revision: "candidate-r2" } },
  };
}

function rawEvent(overrides: Record<string, unknown> = {}) {
  return {
    event_type: "PAGE_EDIT", id: "event-1", occurred_at: "2026-07-13T08:00:00Z", sequence: 1,
    actor: "user:desktop", origin: "desktop", path: "topics/a.md", base_revision: "note-r1",
    result_revision: "note-r2", patch: "patch", operations: [], reconciliation: "applied",
    analysis: null, reconciles_event_id: null, review_operations: [], questions: [], review_event_id: null,
    observation_id: null, source_canonical_revision: null, ...overrides,
  };
}

function installBridge(options: {
  readonly?: boolean;
  analysisPending?: boolean;
  conflictPreview?: boolean;
  event?: ReturnType<typeof rawEvent> | null;
} = {}) {
  let current = note("note-r1", options.readonly ? {
    editable: false, editable_content: null, readonly_reason: "Engine-owned page",
  } : {});
  let conflictRemaining = options.conflictPreview ?? false;
  const requests: Array<{ path: string; method: string; body: unknown }> = [];
  window.ntrpDesktop = { api: { request: async (_config, request) => {
    const method = request.method ?? "GET";
    const body = request.body ? JSON.parse(request.body) : null;
    requests.push({ path: request.path, method, body });
    if (request.path === "/admin/memory/artifacts") return ok({ artifacts: [index, current] });
    if (request.path === "/admin/memory/artifacts/index.md") {
      return ok({ artifact: { ...index, content: "<!-- ntrp:index:start -->\n- topics/a.md <!-- ntrp:path=topics%2Fa.md -->\n<!-- ntrp:index:end -->", editable_content: null, timeline: [], frontmatter: {} } });
    }
    if (request.path === "/admin/memory/artifacts/topics/a.md") return ok({ artifact: current });
    if (request.path.startsWith("/admin/memory/links")) return ok({ path: "topics/a.md", revision: current.revision, stale: false, outgoing: [], backlinks: [], total_outgoing: 0, total_backlinks: 0, limit: 100, offset: 0 });
    if (request.path.startsWith("/admin/memory/page-edits/history")) {
      return ok({ events: options.event ? [options.event] : [], total: options.event ? 1 : 0, limit: 100, next_before_sequence: null });
    }
    if (request.path === "/admin/memory/page-edits/preview") {
      if (conflictRemaining) {
        conflictRemaining = false;
        current = note("note-r2", { content: "Rendered external source", editable_content: "# A\n\nExternal source bytes.\n" });
        return conflict("note-r2", "# A\n\nExternal source bytes.\n");
      }
      return ok({ preview: {
        id: "preview-1", path: "topics/a.md", base_revision: "note-r1", result_revision: "note-r2",
        patch: "patch", analysis_pending: options.analysisPending ?? false,
        operations: options.analysisPending ? [] : [
          { op: "ADD", text: "New durable fact", kind: "fact", scope: { kind: "user", key: null } },
          { op: "ASK", question: "Forget the old memory?", target_ids: ["record-old"] },
        ],
        questions: options.analysisPending ? [] : [{ id: "preview-1:operation:1", operation_index: 1, question: "Forget the old memory?" }],
      } });
    }
    if (request.path === "/admin/memory/page-edits/apply") {
      current = note("note-r2", { content: "Rendered draft prose", editable_content: body.content ?? "# A\n\nDraft source bytes.\n" });
      return ok({ event: rawEvent(), revision: "note-r2" });
    }
    if (request.path === "/admin/memory/page-edits/retry") {
      return ok({ event: rawEvent({ id: "resolved-1", reconciliation: "applied", reconciles_event_id: "external-1" }), revision: current.revision });
    }
    throw new Error(`Unexpected request: ${method} ${request.path}`);
  } } } as Window["ntrpDesktop"];
  return {
    requests,
    update(next: ReturnType<typeof note>) { current = next; },
  };
}

function setup() {
  const host = document.createElement("div");
  host.style.height = "800px";
  document.body.append(host);
  const root = createRoot(host);
  roots.add(root);
  return { host, root };
}

async function settle(delay = 0) {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, delay)); });
}

async function renderView(hostRoot = setup()) {
  await act(async () => hostRoot.root.render(<ArtifactMemoryView config={config} />));
  await settle(260);
  return hostRoot;
}

async function shortcut(key: string, target: EventTarget = window) {
  await act(async () => target.dispatchEvent(new KeyboardEvent("keydown", { key, metaKey: true, bubbles: true })));
  await settle();
}

async function changeDraft(textarea: HTMLTextAreaElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, value);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

afterEach(async () => {
  for (const root of roots) await act(async () => root.unmount());
  roots.clear();
  clearDrafts();
  window.ntrpDesktop = originalDesktop;
  useStore.setState({ memoryVaultVersion: originalVaultVersion });
  document.body.replaceChildren();
});

test("Cmd+E edits exact source bytes and Cmd+S sends an exact non-mutating preview", async () => {
  const bridge = installBridge();
  const { host } = await renderView();
  await shortcut("e");
  const textarea = host.querySelector<HTMLTextAreaElement>('textarea[aria-label="Markdown source for topics/a.md"]')!;
  expect(textarea.value).toBe("# A\n\nOld source bytes.\n");
  await changeDraft(textarea, "# A\n\nDraft source bytes.\n");
  expect(host.textContent).toContain("Unsaved draft");
  await shortcut("s", textarea);

  const preview = bridge.requests.find((request) => request.path.endsWith("/preview"));
  expect(preview?.body).toEqual({ path: "topics/a.md", base_revision: "note-r1", content: "# A\n\nDraft source bytes.\n", actor: "user:desktop" });
  expect(host.querySelector('[data-diff-review]')).not.toBeNull();
  expect(bridge.requests.some((request) => request.path.endsWith("/apply"))).toBe(false);
});

test("read-only pages explain their boundary and never enter edit mode", async () => {
  installBridge({ readonly: true });
  const { host } = await renderView();
  await shortcut("e");
  expect(host.querySelector("textarea")).toBeNull();
  expect(host.textContent).toContain("Engine-owned page");
});

test("edit shortcuts do not hijack unrelated focused controls", async () => {
  installBridge();
  const { host } = await renderView();
  const search = host.querySelector<HTMLInputElement>('input[aria-label="Search memory notes…"]')!;
  search.focus();
  await shortcut("e", search);
  expect(host.querySelector("textarea")).toBeNull();
  expect(document.activeElement).toBe(search);
  await shortcut("s", search);
  expect(host.querySelector('[data-memory-edit-review]')).toBeNull();
});

test("ASK has no default; explicit decision applies and clears only its exact draft", async () => {
  const bridge = installBridge();
  setDraft("other.md", "other-r1", "unrelated draft");
  const { host } = await renderView();
  await shortcut("e");
  const textarea = host.querySelector<HTMLTextAreaElement>("textarea")!;
  await changeDraft(textarea, "# A\n\nDraft source bytes.\n");
  await shortcut("s", textarea);
  const apply = host.querySelector<HTMLButtonElement>('button[aria-label="Apply changes"]')!;
  expect(apply.disabled).toBe(true);
  expect(host.querySelector('[aria-label="Note only"]')?.getAttribute("aria-checked")).toBe("false");
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Note only"]')?.click());
  expect(apply.disabled).toBe(false);
  await act(async () => apply.click());
  await settle(260);

  expect(bridge.requests.find((request) => request.path.endsWith("/apply"))?.body).toEqual({
    preview_id: "preview-1",
    decisions: { "preview-1:operation:1": { choice: "note_only", target_ids: ["record-old"] } },
    save_pending: false,
  });
  expect(getDraft("topics/a.md", "note-r1")).toBeNull();
  expect(getDraft("other.md", "other-r1")).toBe("unrelated draft");
});

test("analysis-unavailable preview requires an explicit pending save", async () => {
  const bridge = installBridge({ analysisPending: true });
  const { host } = await renderView();
  await shortcut("e");
  const textarea = host.querySelector<HTMLTextAreaElement>("textarea")!;
  await changeDraft(textarea, "# A\n\nPending analysis.\n");
  await shortcut("s", textarea);
  expect(host.textContent).toContain("Memory analysis is unavailable");
  const pending = host.querySelector<HTMLButtonElement>('button[aria-label="Save as pending"]')!;
  await act(async () => pending.click());
  await settle(260);
  expect(bridge.requests.find((request) => request.path.endsWith("/apply"))?.body).toEqual({
    preview_id: "preview-1", decisions: {}, save_pending: true,
  });
});

test("review cancel returns to the byte-identical draft", async () => {
  installBridge();
  const { host } = await renderView();
  await shortcut("e");
  let textarea = host.querySelector<HTMLTextAreaElement>("textarea")!;
  const bytes = "# A\n\nDraft  \n\n```ts\nconst x = 1;\n```\n";
  await changeDraft(textarea, bytes);
  await shortcut("s", textarea);
  await act(async () => Array.from(host.querySelectorAll<HTMLButtonElement>("button")).find((button) => button.textContent === "Back to edit")?.click());
  textarea = host.querySelector<HTMLTextAreaElement>("textarea")!;
  expect(textarea.value).toBe(bytes);
  await shortcut("e", textarea);
  expect(host.querySelector("textarea")).toBeNull();
  expect(getDraft("topics/a.md", "note-r1")).toBe(bytes);
});

test("409 opens a three-way conflict without changing draft bytes", async () => {
  const bridge = installBridge({ conflictPreview: true });
  const { host } = await renderView();
  await shortcut("e");
  const textarea = host.querySelector<HTMLTextAreaElement>("textarea")!;
  const draft = "# A\n\nMy exact draft.\n";
  await changeDraft(textarea, draft);
  await shortcut("s", textarea);
  expect(host.querySelector('[aria-label="Three-way conflict for topics/a.md"]')).not.toBeNull();
  expect(host.textContent).toContain("Current page");
  expect(host.textContent).toContain("Your draft");
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Continue with current page"]')?.click());
  expect(host.querySelector<HTMLTextAreaElement>("textarea")?.value).toBe(draft);
  await shortcut("s", host.querySelector<HTMLTextAreaElement>("textarea")!);
  const previews = bridge.requests.filter((request) => request.path.endsWith("/preview"));
  expect(previews.at(-1)?.body).toEqual({
    path: "topics/a.md", base_revision: "note-r2", content: draft, actor: "user:desktop",
  });
  expect(getDraft("topics/a.md", "note-r1")).toBe(draft);
  expect(getDraft("topics/a.md", "note-r2")).toBe(draft);
});

test("SSE refreshes a clean page but preserves a dirty draft and opens conflict", async () => {
  useStore.setState({ memoryVaultVersion: 0 });
  const bridge = installBridge();
  const { host } = await renderView();
  bridge.update(note("note-r2", { content: "Rendered external update", editable_content: "# A\n\nExternal update.\n" }));
  await act(async () => useStore.getState().memoryVaultChanged({
    paths: ["topics/a.md"], revision: "note-r2", reviewRequired: false, seq: 90,
  }));
  await settle(280);
  expect(host.textContent).toContain("Rendered external update");

  await shortcut("e");
  const textarea = host.querySelector<HTMLTextAreaElement>("textarea")!;
  const draft = "# A\n\nKeep this draft exactly.\n";
  await changeDraft(textarea, draft);
  bridge.update(note("note-r3", { content: "Second external update", editable_content: "# A\n\nSecond external update.\n" }));
  await act(async () => useStore.getState().memoryVaultChanged({
    paths: ["topics/a.md"], revision: "note-r3", reviewRequired: false, seq: 91,
  }));
  await settle(280);
  expect(host.querySelector('[aria-label="Three-way conflict for topics/a.md"]')).not.toBeNull();
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Back to draft"]')?.click());
  expect(host.querySelector<HTMLTextAreaElement>("textarea")?.value).toBe(draft);
});

test("external review resolves only memory decisions and never reapplies page bytes", async () => {
  useStore.setState({ memoryVaultVersion: 0 });
  const external = rawEvent({
    id: "external-1", actor: "external:filesystem", origin: "external", reconciliation: "needs_review",
    result_revision: "note-r2", review_operations: [{ op: "ASK", question: "Forget the old memory?", target_ids: ["record-old"] }],
    questions: [{ id: "external-1:review-operation:0", operation_index: 0, question: "Forget the old memory?" }],
    analysis: { path: "topics/a.md", before: ["Old source bytes."], after: ["External source bytes."], changed_before: ["Old source bytes."], changed_after: ["External source bytes."], patch: "patch" },
  });
  const bridge = installBridge({ event: external });
  bridge.update(note("note-r2", { content: "Rendered external update", editable_content: "# A\n\nExternal source bytes.\n" }));
  const { host } = await renderView();
  await act(async () => useStore.getState().memoryVaultChanged({
    paths: ["topics/a.md"], revision: "note-r2", reviewRequired: true, seq: 93,
  }));
  await settle(280);
  expect(host.querySelector<HTMLButtonElement>('button[aria-label="Resolve memory effects"]')?.disabled).toBe(true);
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Forget memory"]')?.click());
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Resolve memory effects"]')?.click());
  await settle();
  expect(bridge.requests.find((request) => request.path.endsWith("/retry"))?.body).toEqual({
    event_id: "external-1",
    decisions: { "external-1:review-operation:0": { choice: "forget_memory", target_ids: ["record-old"] } },
  });
  expect(bridge.requests.filter((request) => request.path.endsWith("/preview") || request.path.endsWith("/apply"))).toHaveLength(0);
});

test("dirty review-required SSE prioritizes conflict and queues external decisions", async () => {
  useStore.setState({ memoryVaultVersion: 0, memoryVaultChange: null });
  const external = rawEvent({
    id: "external-dirty", actor: "external:filesystem", origin: "external", reconciliation: "needs_review",
    result_revision: "note-r2", review_operations: [{ op: "ASK", question: "Forget it?", target_ids: ["record-old"] }],
    questions: [{ id: "operation:0", operation_index: 0, question: "Forget it?" }],
    analysis: { path: "topics/a.md", before: ["Old source bytes."], after: ["External source bytes."], changed_before: ["Old source bytes."], changed_after: ["External source bytes."], patch: "patch" },
  });
  const bridge = installBridge({ event: external });
  const { host } = await renderView();
  await shortcut("e");
  const draft = "# A\n\nMy concurrent draft.\n";
  await changeDraft(host.querySelector<HTMLTextAreaElement>("textarea")!, draft);
  bridge.update(note("note-r2", { content: "Rendered external", editable_content: "# A\n\nExternal source bytes.\n" }));
  await act(async () => useStore.getState().memoryVaultChanged({
    paths: ["topics/a.md"], revision: "note-r2", reviewRequired: true, seq: 94,
  }));
  await settle(320);

  expect(host.querySelector('[aria-label="Three-way conflict for topics/a.md"]')).not.toBeNull();
  expect(host.querySelector('[aria-label="Resolve external memory effects for topics/a.md"]')).toBeNull();
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Back to draft"]')?.click());
  expect(host.querySelector('[aria-label="Resolve external memory effects for topics/a.md"]')).not.toBeNull();
  await act(async () => Array.from(host.querySelectorAll<HTMLButtonElement>("button")).find((button) => button.textContent === "Not now")?.click());
  expect(host.querySelector('[aria-label="Three-way conflict for topics/a.md"]')).not.toBeNull();
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Back to draft"]')?.click());
  expect(host.querySelector<HTMLTextAreaElement>("textarea")?.value).toBe(draft);
});

test("SSE for an unrelated path does not refresh or interrupt the selected draft", async () => {
  useStore.setState({ memoryVaultVersion: 0, memoryVaultChange: null });
  const bridge = installBridge();
  const { host } = await renderView();
  await shortcut("e");
  const textarea = host.querySelector<HTMLTextAreaElement>("textarea")!;
  const draft = "# A\n\nSelected draft.\n";
  await changeDraft(textarea, draft);
  const selectedReads = bridge.requests.filter((request) => request.path === "/admin/memory/artifacts/topics/a.md").length;
  await act(async () => useStore.getState().memoryVaultChanged({
    paths: ["topics/b.md"], revision: "note-b-r2", reviewRequired: true, seq: 92,
  }));
  await settle(280);
  expect(host.querySelector<HTMLTextAreaElement>("textarea")?.value).toBe(draft);
  expect(host.querySelector('[data-memory-edit-review]')).toBeNull();
  expect(bridge.requests.filter((request) => request.path === "/admin/memory/artifacts/topics/a.md")).toHaveLength(selectedReads);
});

test("editor and review expose keyboard, theme, responsive, reduced-motion, and long-content evidence", async () => {
  installBridge();
  document.documentElement.classList.add("dark");
  const { host } = await renderView();
  await shortcut("e");
  const textarea = host.querySelector<HTMLTextAreaElement>("textarea")!;
  await changeDraft(textarea, Array.from({ length: 320 }, (_, line) => `line ${line}`).join("\n"));
  expect(host.querySelector('[data-memory-editor]')?.getAttribute("data-theme-ready")).toBe("true");
  expect(host.querySelector('[data-memory-editor]')?.getAttribute("data-responsive")).toBe("true");
  expect(textarea.getAttribute("aria-describedby")).not.toBeNull();
  await shortcut("s", textarea);
  const review = host.querySelector<HTMLElement>('[data-memory-edit-review]')!;
  expect(review.dataset.reducedMotionReady).toBe("true");
  expect(review.dataset.longContentReady).toBe("true");
  expect(review.className).toContain("min-w-0");
  document.documentElement.classList.remove("dark");
});

test("draft identity is exact path plus base revision", () => {
  setDraft("topics/a.md", "r1", "first");
  setDraft("topics/a.md", "r2", "second");
  expect(draftKey("topics/a.md", "r1")).not.toBe(draftKey("topics/a.md", "r2"));
  expect(getDraft("topics/a.md", "r1")).toBe("first");
  expect(getDraft("topics/a.md", "r2")).toBe("second");
});
