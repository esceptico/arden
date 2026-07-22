import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { AppConfig } from "@/api/core";
import { ArtifactMemoryView } from "@/features/memory/components/ArtifactMemoryView";
import { clearDrafts, draftKey, getDraft, setDraft } from "@/features/memory/lib/draftStore";
import { splitFrontmatter } from "@/features/memory/lib/format";
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

function noteB(revision = "note-b-r1", extra: Record<string, unknown> = {}) {
  return note(revision, {
    path: "topics/b.md",
    title: "B",
    content: "Rendered B prose",
    editable_content: "# B\n\nB source bytes.\n",
    summary: "B note",
    ...extra,
  });
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

function deferred<T = void>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((accept) => { resolve = accept; });
  return { promise, resolve };
}

function installBridge(options: {
  readonly?: boolean;
  analysisPending?: boolean;
  conflictPreview?: boolean;
  event?: ReturnType<typeof rawEvent> | null;
  events?: ReturnType<typeof rawEvent>[];
  onPreview?: (body: Record<string, unknown>, call: number) => BridgeResponse | Promise<BridgeResponse>;
  onApply?: (body: Record<string, unknown>, call: number) => BridgeResponse | Promise<BridgeResponse>;
  onHistory?: (call: number, path: string | null) => BridgeResponse | Promise<BridgeResponse>;
  onRetry?: (body: Record<string, unknown>, call: number) => BridgeResponse | Promise<BridgeResponse>;
  onList?: (query: string | null, call: number) => BridgeResponse | Promise<BridgeResponse>;
  onRebuild?: (call: number) => BridgeResponse | Promise<BridgeResponse>;
  secondNote?: ReturnType<typeof note>;
} = {}) {
  let current = note("note-r1", options.readonly ? {
    editable: false, editable_content: null, readonly_reason: "Engine-owned page",
  } : {});
  let currentB = options.secondNote ?? null;
  let conflictRemaining = options.conflictPreview ?? false;
  let historyEvents = options.events ?? (options.event ? [options.event] : []);
  let previewCalls = 0;
  let applyCalls = 0;
  let historyCalls = 0;
  let retryCalls = 0;
  let listCalls = 0;
  let rebuildCalls = 0;
  const requests: Array<{ path: string; method: string; body: unknown }> = [];
  window.ntrpDesktop = { api: { request: async (_config, request) => {
    const method = request.method ?? "GET";
    const body = request.body ? JSON.parse(request.body) : null;
    requests.push({ path: request.path, method, body });
    if (request.path.startsWith("/admin/memory/artifacts?") || request.path === "/admin/memory/artifacts") {
      listCalls += 1;
      const query = new URL(request.path, "http://ntrp.test").searchParams.get("q");
      if (options.onList) return options.onList(query, listCalls);
      return ok({ artifacts: [index, current, ...(currentB ? [currentB] : [])] });
    }
    if (request.path === "/admin/memory/artifacts/rebuild") {
      rebuildCalls += 1;
      if (options.onRebuild) return options.onRebuild(rebuildCalls);
      return ok({ artifacts: [index, current, ...(currentB ? [currentB] : [])] });
    }
    if (request.path === "/admin/memory/artifacts/index.md") {
      return ok({ artifact: { ...index, content: `<!-- ntrp:index:start -->\n- topics/a.md <!-- ntrp:path=topics%2Fa.md -->${currentB ? "\n- topics/b.md <!-- ntrp:path=topics%2Fb.md -->" : ""}\n<!-- ntrp:index:end -->`, editable_content: null, timeline: [], frontmatter: {} } });
    }
    if (request.path === "/admin/memory/artifacts/topics/a.md") return ok({ artifact: current });
    if (request.path === "/admin/memory/artifacts/topics/b.md" && currentB) return ok({ artifact: currentB });
    if (request.path.startsWith("/admin/memory/links")) {
      const path = new URL(request.path, "http://ntrp.test").searchParams.get("path") ?? "topics/a.md";
      const revision = path === "topics/b.md" ? currentB?.revision ?? "note-b-r1" : current.revision;
      return ok({ path, revision, stale: false, outgoing: [], backlinks: [], total_outgoing: 0, total_backlinks: 0, limit: 100, offset: 0 });
    }
    if (request.path.startsWith("/admin/memory/page-edits/history")) {
      historyCalls += 1;
      const path = new URL(request.path, "http://ntrp.test").searchParams.get("path");
      if (options.onHistory) return options.onHistory(historyCalls, path);
      return ok({ events: historyEvents, total: historyEvents.length, limit: 100, next_before_sequence: null });
    }
    if (request.path === "/admin/memory/page-edits/preview") {
      previewCalls += 1;
      if (options.onPreview) return options.onPreview(body, previewCalls);
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
      applyCalls += 1;
      if (options.onApply) return options.onApply(body, applyCalls);
      current = note("note-r2", { content: "Rendered draft prose", editable_content: body.content ?? "# A\n\nDraft source bytes.\n" });
      return ok({ event: rawEvent(), revision: "note-r2" });
    }
    if (request.path === "/admin/memory/page-edits/retry") {
      retryCalls += 1;
      if (options.onRetry) return options.onRetry(body, retryCalls);
      return ok({ event: rawEvent({ id: "resolved-1", reconciliation: "applied", reconciles_event_id: "external-1" }), revision: current.revision });
    }
    throw new Error(`Unexpected request: ${method} ${request.path}`);
  } } } as Window["ntrpDesktop"];
  return {
    requests,
    update(next: ReturnType<typeof note>) { current = next; },
    updatePath(next: ReturnType<typeof note>) {
      if (next.path === "topics/b.md") currentB = next;
      else current = next;
    },
    setHistoryEvents(next: ReturnType<typeof rawEvent>[]) { historyEvents = next; },
  };
}

function setup() {
  // Inspector now defaults open (persisted); these tests exercise editing
  // and review flows, so seed it closed to keep prior request counts.
  localStorage.setItem("ntrp.desktop.memory.inspectorOpen", "false");
  // Initial selection prefers the persisted last path (then index.md); pin it
  // to the note under test so the workspace opens on topics/a.md.
  localStorage.setItem("ntrp.desktop.memory.lastPath", "topics/a.md");
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
  useStore.setState({ memoryVaultVersion: originalVaultVersion, memoryVaultChanges: [] });
  document.body.replaceChildren();
  // happy-dom localStorage is shared across test files in one bun invocation.
  localStorage.removeItem("ntrp.desktop.memory.inspectorOpen");
  localStorage.removeItem("ntrp.desktop.memory.lastPath");
});

test("Cmd+E edits the page body and Cmd+S previews the exact recomposed source bytes", async () => {
  const bridge = installBridge();
  // Frontmatter is withheld from the textarea but must round-trip byte-exactly
  // into the preview payload.
  const bytes = "---\ntitle: A\n---\n# A\n\nOld source bytes.\n";
  bridge.update(note("note-r1", { editable_content: bytes }));
  const { host } = await renderView();
  const edit = host.querySelector<HTMLButtonElement>('button[aria-label="Edit memory note"]')!;
  edit.focus();
  await shortcut("e", edit);
  expect(host.querySelector('[data-memory-editor-mode="source"]')).not.toBeNull();
  const textarea = host.querySelector<HTMLTextAreaElement>('textarea[aria-label="Markdown source for topics/a.md"]')!;
  expect(textarea.value).toBe(splitFrontmatter(bytes).body);
  expect(textarea.value).toBe("# A\n\nOld source bytes.\n");
  await changeDraft(textarea, "# A\n\nDraft source bytes.\n");
  expect(host.textContent).toContain("unsaved draft");
  await shortcut("s", textarea);

  const preview = bridge.requests.find((request) => request.path.endsWith("/preview"));
  expect(preview?.body).toEqual({ path: "topics/a.md", base_revision: "note-r1", content: "---\ntitle: A\n---\n# A\n\nDraft source bytes.\n", actor: "user:desktop" });
  expect(host.querySelector('[data-diff-review]')).not.toBeNull();
  expect(bridge.requests.some((request) => request.path.endsWith("/apply"))).toBe(false);
});

test("the edit button opens the Markdown source editor over the exact page body", async () => {
  installBridge();
  const { host } = await renderView();
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Edit memory note"]')?.click());
  await settle(200);
  expect(host.querySelector('[data-memory-editor-mode="source"]')).not.toBeNull();
  expect(host.querySelector<HTMLTextAreaElement>('textarea[aria-label="Markdown source for topics/a.md"]')?.value)
    .toBe(splitFrontmatter("# A\n\nOld source bytes.\n").body);
});

test("a preview response is discarded when the draft changes in flight", async () => {
  const gate = deferred();
  installBridge({ onPreview: async () => {
    await gate.promise;
    return ok({ preview: {
      id: "preview-stale", path: "topics/a.md", base_revision: "note-r1", result_revision: "note-r2",
      patch: "patch", analysis_pending: false, operations: [{ op: "NOOP", text: "Old candidate" }], questions: [],
    } });
  } });
  const { host } = await renderView();
  await shortcut("e");
  const textarea = host.querySelector<HTMLTextAreaElement>("textarea")!;
  await changeDraft(textarea, "# A\n\nFirst candidate.\n");
  await shortcut("s", textarea);
  await changeDraft(textarea, "# A\n\nNewer candidate.\n");
  await act(async () => gate.resolve());
  await settle();

  expect(host.querySelector('[data-memory-edit-review]')).toBeNull();
  expect(host.querySelector<HTMLTextAreaElement>("textarea")?.value).toBe("# A\n\nNewer candidate.\n");
});

test("a stale 409 cannot replace a newer draft with conflict state", async () => {
  const gate = deferred();
  installBridge({ onPreview: async () => {
    await gate.promise;
    return conflict("note-r2", "# A\n\nExternal source bytes.\n");
  } });
  const { host } = await renderView();
  await shortcut("e");
  const textarea = host.querySelector<HTMLTextAreaElement>("textarea")!;
  await changeDraft(textarea, "# A\n\nFirst candidate.\n");
  await shortcut("s", textarea);
  await changeDraft(textarea, "# A\n\nNewer candidate.\n");
  await act(async () => gate.resolve());
  await settle();

  expect(host.querySelector('[aria-label="Three-way conflict for topics/a.md"]')).toBeNull();
  expect(host.querySelector<HTMLTextAreaElement>("textarea")?.value).toBe("# A\n\nNewer candidate.\n");
});

test("read-only pages explain their boundary and never enter edit mode", async () => {
  installBridge({ readonly: true });
  const { host } = await renderView();
  await shortcut("e");
  expect(host.querySelector("textarea")).toBeNull();
  // The boundary explanation now lives on the disabled edit toggle's tooltip.
  const edit = host.querySelector<HTMLButtonElement>('button[aria-label="Edit memory note"]')!;
  expect(edit.disabled).toBe(true);
  expect(edit.title).toBe("Engine-owned page");
});

test("edit shortcuts do not hijack unrelated focused controls", async () => {
  installBridge();
  const { host } = await renderView();
  // The rail's create-note input is the focused text control under test
  // (the old rail search input is now a quick-switcher button).
  await act(async () => host.querySelector<HTMLButtonElement>('button[title="New note"]')?.click());
  const createInput = host.querySelector<HTMLInputElement>('input[aria-label="New note path"]')!;
  createInput.focus();
  await shortcut("e", createInput);
  expect(host.querySelector("textarea")).toBeNull();
  expect(document.activeElement).toBe(createInput);
  await shortcut("s", createInput);
  expect(host.querySelector('[data-memory-edit-review]')).toBeNull();
});

test("shortcuts ignore links, custom ARIA controls, and contenteditable targets", async () => {
  installBridge();
  const { host } = await renderView();
  const link = document.createElement("a");
  link.href = "#test";
  link.textContent = "Interactive link";
  const slider = document.createElement("div");
  slider.setAttribute("role", "slider");
  slider.tabIndex = 0;
  const editable = document.createElement("div");
  editable.contentEditable = "true";
  host.append(link, slider, editable);

  for (const control of [link, slider, editable]) {
    control.focus();
    await shortcut("e", control);
    expect(host.querySelector("textarea") === null).toBe(true);
    expect(document.activeElement === control).toBe(true);
  }

  editable.blur();
  await shortcut("e");
  const outsideControl = document.createElement("div");
  outsideControl.setAttribute("role", "button");
  outsideControl.tabIndex = 0;
  host.append(outsideControl);
  outsideControl.focus();
  await shortcut("s", outsideControl);
  expect(host.querySelector('[data-memory-edit-review]') === null).toBe(true);
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

test("apply locks the exact review transaction and compare-clears only its candidate", async () => {
  const gate = deferred();
  const bridge = installBridge({ onApply: async () => {
    await gate.promise;
    return ok({ event: rawEvent(), revision: "note-r2" });
  } });
  const { host } = await renderView();
  await shortcut("e");
  const candidate = "# A\n\nCandidate under review.\n";
  await changeDraft(host.querySelector<HTMLTextAreaElement>("textarea")!, candidate);
  await shortcut("s", host.querySelector<HTMLTextAreaElement>("textarea")!);
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Note only"]')?.click());
  const apply = host.querySelector<HTMLButtonElement>('[aria-label="Apply changes"]')!;
  await act(async () => apply.click());

  expect(apply.disabled).toBe(true);
  expect(Array.from(host.querySelectorAll<HTMLButtonElement>('[role="radio"]')).every((radio) => radio.disabled)).toBe(true);
  expect(host.querySelector<HTMLButtonElement>('[role="tab"][aria-label="Raw Markdown"]')?.disabled).toBe(true);
  expect(Array.from(host.querySelectorAll<HTMLButtonElement>("button")).find((button) => button.textContent === "Back to edit")?.disabled).toBe(true);
  await shortcut("e");
  expect(host.querySelector('[data-memory-edit-review]')).not.toBeNull();
  await act(async () => apply.click());
  expect(bridge.requests.filter((request) => request.path.endsWith("/apply"))).toHaveLength(1);

  setDraft("topics/a.md", "note-r1", "# A\n\nNewer bytes written while applying.\n");
  await act(async () => gate.resolve());
  await settle(280);
  expect(getDraft("topics/a.md", "note-r1")).toBe("# A\n\nNewer bytes written while applying.\n");
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


test("review-required SSE refreshes the page without opening external review", async () => {
  useStore.setState({ memoryVaultVersion: 0, memoryVaultChanges: [] });
  const bridge = installBridge();
  bridge.update(note("note-r2", { content: "Rendered external update", editable_content: "# A\n\nExternal source bytes.\n" }));
  const { host } = await renderView();
  await act(async () => useStore.getState().memoryVaultChanged({
    paths: ["topics/a.md"], revision: "note-r2", reviewRequired: true, seq: 92,
  }));
  await settle(280);

  expect(host.querySelector('[aria-label="Resolve external memory effects for topics/a.md"]')).toBeNull();
  expect(host.textContent).toContain("Rendered external update");
  expect(bridge.requests.some((request) => request.path.includes("/history"))).toBe(false);
});


test("SSE for an unrelated path does not refresh or interrupt the selected draft", async () => {
  useStore.setState({ memoryVaultVersion: 0, memoryVaultChanges: [] });
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

test("an unrelated latest SSE frame cannot hide an earlier selected-path change", async () => {
  useStore.setState({ memoryVaultVersion: 0, memoryVaultChanges: [] });
  const bridge = installBridge();
  const { host } = await renderView();
  bridge.update(note("note-r2", { content: "Selected event reached the note", editable_content: "# A\n\nSelected event.\n" }));
  await act(async () => {
    useStore.getState().memoryVaultChanged({
      paths: ["topics/a.md"], revision: "note-r2", reviewRequired: false, seq: 501,
    });
    useStore.getState().memoryVaultChanged({
      paths: ["topics/unrelated.md"], revision: "other-r2", reviewRequired: false, seq: 502,
    });
  });
  await settle(320);
  expect(host.textContent).toContain("Selected event reached the note");
});


test("apply centrally locks navigation and still commits its exact draft", async () => {
  const applyGate = deferred();
  const bridge = installBridge({
    secondNote: noteB(),
    onApply: async () => {
      await applyGate.promise;
      return ok({ event: rawEvent(), revision: "note-r2" });
    },
  });
  const { host } = await renderView();
  await shortcut("e");
  const candidate = "# A\n\nLocked candidate.\n";
  await changeDraft(host.querySelector<HTMLTextAreaElement>("textarea")!, candidate);
  await shortcut("s", host.querySelector<HTMLTextAreaElement>("textarea")!);
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Note only"]')?.click());
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Apply changes"]')?.click());

  const bRail = host.querySelector<HTMLButtonElement>('[data-memory-entry="topics/b.md"]')!;
  const rawRecords = host.querySelector<HTMLButtonElement>('[aria-label="Open raw records diagnostic"]')!;
  expect(bRail.disabled).toBe(true);
  expect(rawRecords.disabled).toBe(true);
  await act(async () => bRail.click());
  await shortcut("[");
  expect(host.querySelector('[aria-label="Review memory edit for topics/a.md"]')).not.toBeNull();
  expect(host.querySelector('[aria-label="Review memory edit for topics/b.md"]')).toBeNull();

  await act(async () => applyGate.resolve());
  await settle(260);
  expect(getDraft("topics/a.md", "note-r1")).toBeNull();
  expect(bridge.requests.filter((request) => request.path.endsWith("/apply"))).toHaveLength(1);
});

test("a committed apply after unmount only compare-clears and never reloads", async () => {
  const applyGate = deferred();
  const bridge = installBridge({ onApply: async () => {
    await applyGate.promise;
    return ok({ event: rawEvent(), revision: "note-r2" });
  } });
  const view = await renderView();
  await shortcut("e");
  const candidate = "# A\n\nUnmounted commit.\n";
  await changeDraft(view.host.querySelector<HTMLTextAreaElement>("textarea")!, candidate);
  await shortcut("s", view.host.querySelector<HTMLTextAreaElement>("textarea")!);
  await act(async () => view.host.querySelector<HTMLButtonElement>('[aria-label="Note only"]')?.click());
  await act(async () => view.host.querySelector<HTMLButtonElement>('[aria-label="Apply changes"]')?.click());
  expect(getDraft("topics/a.md", "note-r1")).toBe(candidate);

  await act(async () => view.root.unmount());
  roots.delete(view.root);
  const readsBeforeCommit = bridge.requests.filter((request) => request.path === "/admin/memory/artifacts").length;
  await act(async () => applyGate.resolve());
  await settle(80);
  expect(getDraft("topics/a.md", "note-r1")).toBeNull();
  expect(bridge.requests.filter((request) => request.path === "/admin/memory/artifacts")).toHaveLength(readsBeforeCommit);
});

test("an initial summary result cannot launch note reads after unmount", async () => {
  const listGate = deferred<BridgeResponse>();
  const bridge = installBridge({ onList: () => listGate.promise });
  const view = setup();
  await act(async () => view.root.render(<ArtifactMemoryView config={config} />));
  await settle(20);
  await act(async () => view.root.unmount());
  roots.delete(view.root);
  const requestsBefore = bridge.requests.length;
  await act(async () => listGate.resolve(ok({ artifacts: [index, note()] })));
  await settle(80);
  expect(bridge.requests).toHaveLength(requestsBefore);
});

test("a rebuild result cannot launch note reads after unmount", async () => {
  const rebuildGate = deferred<BridgeResponse>();
  const bridge = installBridge({ onList: () => ok({ artifacts: [] }), onRebuild: () => rebuildGate.promise });
  const view = await renderView();
  // Rebuild is reachable from the empty rail's Refresh action.
  await act(async () => Array.from(view.host.querySelectorAll<HTMLButtonElement>("button"))
    .find((button) => button.textContent?.trim() === "Refresh")?.click());
  await settle(20);
  expect(bridge.requests.some((request) => request.path === "/admin/memory/artifacts/rebuild")).toBe(true);
  await act(async () => view.root.unmount());
  roots.delete(view.root);
  const requestsBefore = bridge.requests.length;
  await act(async () => rebuildGate.resolve(ok({ artifacts: [index, note("rebuild-r2")] })));
  await settle(100);
  expect(bridge.requests).toHaveLength(requestsBefore);
});


test("editor and review expose keyboard, responsive, reduced-motion, and long-content evidence", async () => {
  installBridge();
  const { host } = await renderView();
  await shortcut("e");
  const textarea = host.querySelector<HTMLTextAreaElement>("textarea")!;
  expect(document.activeElement === textarea).toBe(true);
  await changeDraft(textarea, Array.from({ length: 320 }, (_, line) => `line ${line}`).join("\n"));
  const editor = host.querySelector<HTMLElement>('[data-memory-editor]')!;
  expect(editor.textContent).toContain("Cmd/Ctrl+S");
  expect(editor.textContent).not.toContain("⌘");
  expect(textarea.getAttribute("aria-describedby")).not.toBeNull();
  await shortcut("s", textarea);
  const review = host.querySelector<HTMLElement>('[data-memory-edit-review]')!;
  expect(document.activeElement === review).toBe(true);
  expect(review.dataset.reducedMotionReady).toBe("true");
  expect(review.dataset.longContentReady).toBe("true");
  expect(review.className).toContain("min-w-0");
  expect(review.querySelector('[data-diff-review] > header')?.className).toContain("flex-wrap");
  expect(review.querySelector('[data-diff-review] > footer')?.className).toContain("flex-wrap");
  await act(async () => Array.from(review.querySelectorAll<HTMLButtonElement>("button")).find((button) => button.textContent === "Back to edit")?.click());
  await settle(300); // focus reclaim window
  expect(document.activeElement === host.querySelector("textarea")).toBe(true);
});

test("focus moves to conflict and returns to the restored note after apply", async () => {
  const conflictBridge = installBridge({ conflictPreview: true });
  const { host } = await renderView();
  await shortcut("e");
  await changeDraft(host.querySelector<HTMLTextAreaElement>("textarea")!, "# A\n\nConflict candidate.\n");
  await shortcut("s", host.querySelector<HTMLTextAreaElement>("textarea")!);
  const conflictReview = host.querySelector<HTMLElement>('[aria-label="Three-way conflict for topics/a.md"]')!;
  expect(document.activeElement === conflictReview).toBe(true);
  expect(conflictReview.querySelector("header")?.className).toContain("flex-wrap");
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Continue with current page"]')?.click());
  await shortcut("s", host.querySelector<HTMLTextAreaElement>("textarea")!);
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Note only"]')?.click());
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Apply changes"]')?.click());
  await settle(320);
  expect(conflictBridge.requests.some((request) => request.path.endsWith("/apply"))).toBe(true);
  expect(document.activeElement === host.querySelector('[data-memory-note-path="topics/a.md"]')).toBe(true);
});

test("draft identity is exact path plus base revision", () => {
  setDraft("topics/a.md", "r1", "first");
  setDraft("topics/a.md", "r2", "second");
  expect(draftKey("topics/a.md", "r1")).not.toBe(draftKey("topics/a.md", "r2"));
  expect(getDraft("topics/a.md", "r1")).toBe("first");
  expect(getDraft("topics/a.md", "r2")).toBe("second");
});

test("draft store evicts the oldest entry once past 50 and recency survives a get", () => {
  for (let index = 0; index < 50; index += 1) {
    setDraft("topics/a.md", `r${index}`, `draft-${index}`);
  }
  // Touch r0 so it is no longer the least-recently-used entry.
  expect(getDraft("topics/a.md", "r0")).toBe("draft-0");
  setDraft("topics/a.md", "r50", "draft-50");
  // r1 was the least-recently-used entry after touching r0, so it is evicted.
  expect(getDraft("topics/a.md", "r1")).toBeNull();
  expect(getDraft("topics/a.md", "r0")).toBe("draft-0");
  expect(getDraft("topics/a.md", "r50")).toBe("draft-50");
});
