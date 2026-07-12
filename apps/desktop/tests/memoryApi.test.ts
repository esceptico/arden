import { afterEach, expect, test } from "bun:test";
import { ApiError, type AppConfig } from "@/api/core";
import {
  applyPageEdit,
  getPageHistory,
  getPageLinks,
  listMemoryArtifacts,
  listMemoryArtifactSummaries,
  previewPageEdit,
  readMemoryArtifact,
  readMemoryArtifactDetail,
} from "@/api/memoryArtifacts";

const config: AppConfig = { serverUrl: "http://localhost:6877", apiKey: "test-key" };
const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window;
const originalDesktopDescriptor = Object.getOwnPropertyDescriptor(originalWindow as Window, "ntrpDesktop");
const originalFetch = globalThis.fetch;

type BridgeRequest = { path: string; method?: string; body?: string; timeout?: number };
let request: BridgeRequest | null = null;

function bridgeResponse(data: unknown, options: { ok?: boolean; status?: number } = {}) {
  request = null;
  (globalThis as typeof globalThis & { window?: unknown }).window = {
    ntrpDesktop: {
      api: {
        request: async (_config: unknown, next: BridgeRequest) => {
          request = next;
          return {
            ok: options.ok ?? true,
            status: options.status ?? 200,
            statusText: options.ok === false ? "Conflict" : "OK",
            contentType: "application/json",
            data,
            text: "",
          };
        },
      },
    },
  };
}

function lastRequest() {
  if (!request) throw new Error("no request captured");
  return {
    method: request.method,
    path: request.path,
    body: request.body === undefined ? undefined : JSON.parse(request.body),
  };
}

const rawSources = [
  {
    kind: "chat_message",
    ref: "session-1:message-2",
    captured_at: "2026-07-12T10:01:02.345+04:00",
    scope_kind: "area",
    scope_key: "dex",
    occurred_at: "2026-07-12T09:58:00+04:00",
    time_precision: "minute",
    role: "user_statement",
    excerpt_hash: "sha256:excerpt",
    channel: "memory",
  },
];

const rawPreview = {
  id: "preview-1",
  path: "topics/a.md",
  base_revision: "sha256:base",
  result_revision: "sha256:result",
  patch: "@@ -1 +1 @@\n-old\n+new\n",
  operations: [
    {
      op: "ADD",
      text: "New fact",
      kind: "fact",
      scope: { kind: "area", key: "dex" },
      target_ids: [],
      meta_labels: ["work"],
      entity_labels: ["Dex"],
    },
    { op: "SUPERSEDE", text: "Corrected", kind: "fact", scope: null, target_ids: ["rec-1"] },
    { op: "MERGE", text: "Merged", kind: null, scope: null, target_ids: ["rec-2", "rec-3"] },
    { op: "RETRACT", text: null, kind: null, scope: null, target_ids: ["rec-4"] },
    { op: "NOOP", text: null, kind: null, scope: null, target_ids: [] },
    { op: "ASK", text: null, kind: null, scope: null, target_ids: ["rec-5"], question: "Forget this?" },
  ],
  questions: [{ id: "question-1", operation_index: 5, question: "Forget this?" }],
  analysis_pending: false,
};

const rawEvent = {
  event_type: "PAGE_EDIT",
  id: "event-1",
  occurred_at: "2026-07-12T10:02:03.456+04:00",
  sequence: 17,
  actor: "user:desktop",
  origin: "desktop",
  path: "topics/a.md",
  base_revision: "sha256:base",
  result_revision: "sha256:result",
  patch: "@@ -1 +1 @@\n-old\n+new\n",
  operations: [
    {
      op: "ADD",
      text: "New fact",
      kind: "fact",
      scope: { kind: "area", key: "dex" },
      target_ids: [],
      meta_labels: ["work"],
      entity_labels: ["Dex"],
      sources: rawSources,
    },
    { op: "SUPERSEDE", text: "Corrected", kind: "fact", scope: null, target_ids: ["rec-1"], sources: rawSources },
    { op: "MERGE", text: "Merged", kind: null, scope: null, target_ids: ["rec-2", "rec-3"], sources: rawSources },
    { op: "RETRACT", text: null, kind: null, scope: null, target_ids: ["rec-4"], sources: rawSources },
    { op: "NOOP", text: null, kind: null, scope: null, target_ids: [], sources: [] },
  ],
  reconciliation: "applied",
  analysis: {
    path: "topics/a.md",
    before: ["old"],
    after: ["new"],
    changed_before: ["old"],
    changed_after: ["new"],
    patch: "patch",
  },
  reconciles_event_id: "observed-1",
  review_operations: [rawPreview.operations[5]],
  questions: rawPreview.questions,
  review_event_id: "review-1",
  observation_id: "observation-1",
  source_canonical_revision: "canonical-1",
};

afterEach(() => {
  (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
  if (originalDesktopDescriptor) {
    Object.defineProperty(window, "ntrpDesktop", originalDesktopDescriptor);
  } else {
    Reflect.deleteProperty(window, "ntrpDesktop");
  }
  globalThis.fetch = originalFetch;
  request = null;
});

test("preview sends the exact base revision and candidate", async () => {
  bridgeResponse({ preview: rawPreview });

  await previewPageEdit(config, {
    path: "topics/a.md",
    baseRevision: "sha256:base",
    content: "# A\nchanged",
  });

  expect(lastRequest()).toEqual({
    method: "POST",
    path: "/admin/memory/page-edits/preview",
    body: {
      path: "topics/a.md",
      base_revision: "sha256:base",
      content: "# A\nchanged",
      actor: "user:desktop",
    },
  });
});

test("preview maps every operation kind and question", async () => {
  bridgeResponse({ preview: rawPreview });

  const preview = await previewPageEdit(config, {
    path: "topics/a.md",
    baseRevision: "sha256:base",
    content: "candidate",
  });

  expect(preview).toEqual({
    id: "preview-1",
    path: "topics/a.md",
    baseRevision: "sha256:base",
    resultRevision: "sha256:result",
    patch: rawPreview.patch,
    operations: [
      {
        kind: "ADD",
        id: "preview-1:operation:0",
        text: "New fact",
        memoryKind: "fact",
        scope: { kind: "area", key: "dex" },
        metaLabels: ["work"],
        entityLabels: ["Dex"],
        sources: [],
      },
      {
        kind: "SUPERSEDE",
        id: "preview-1:operation:1",
        text: "Corrected",
        memoryKind: "fact",
        scope: null,
        targetIds: ["rec-1"],
        metaLabels: [],
        entityLabels: [],
        sources: [],
      },
      {
        kind: "MERGE",
        id: "preview-1:operation:2",
        text: "Merged",
        memoryKind: null,
        scope: null,
        targetIds: ["rec-2", "rec-3"],
        metaLabels: [],
        entityLabels: [],
        sources: [],
      },
      { kind: "RETRACT", id: "preview-1:operation:3", targetIds: ["rec-4"], sources: [] },
      { kind: "NOOP", id: "preview-1:operation:4", reason: "No memory changes", sources: [] },
      { kind: "ASK", id: "preview-1:operation:5", question: "Forget this?", targetIds: ["rec-5"] },
    ],
    questions: [{ id: "question-1", operationIndex: 5, question: "Forget this?" }],
    analysisPending: false,
  });
});

test("apply serializes decisions and maps the committed event", async () => {
  bridgeResponse({ event: rawEvent, revision: "sha256:result" });

  const applied = await applyPageEdit(config, {
    previewId: "preview-1",
    decisions: {
      "question-1": { choice: "forget_memory", targetIds: ["rec-5"] },
      "question-2": { choice: "note_only", targetIds: [] },
    },
    savePending: true,
  });

  expect(lastRequest()).toEqual({
    method: "PUT",
    path: "/admin/memory/page-edits/apply",
    body: {
      preview_id: "preview-1",
      decisions: {
        "question-1": { choice: "forget_memory", target_ids: ["rec-5"] },
        "question-2": { choice: "note_only", target_ids: [] },
      },
      save_pending: true,
    },
  });
  expect(applied.revision).toBe("sha256:result");
  expect(applied.event).toMatchObject({
    eventType: "PAGE_EDIT",
    id: "event-1",
    occurredAt: "2026-07-12T10:02:03.456+04:00",
    sequence: 17,
    actor: "user:desktop",
    origin: "desktop",
    path: "topics/a.md",
    baseRevision: "sha256:base",
    resultRevision: "sha256:result",
    reconciliation: "applied",
    reconcilesEventId: "observed-1",
    reviewEventId: "review-1",
    observationId: "observation-1",
    sourceCanonicalRevision: "canonical-1",
  });
  expect(applied.event.operations.map((operation) => operation.kind)).toEqual([
    "ADD",
    "SUPERSEDE",
    "MERGE",
    "RETRACT",
    "NOOP",
  ]);
  expect(applied.event.reviewOperations[0]).toEqual({
    kind: "ASK",
    id: "event-1:review-operation:0",
    question: "Forget this?",
    targetIds: ["rec-5"],
  });
});

test("event sources preserve roles, exact times, precision, hashes, scope, and extra fields", async () => {
  bridgeResponse({ event: rawEvent, revision: "sha256:result" });

  const applied = await applyPageEdit(config, { previewId: "preview-1", decisions: {} });
  const operation = applied.event.operations[0];
  if (operation.kind !== "ADD") throw new Error("expected ADD");

  expect(operation.sources[0]).toEqual({
    kind: "chat_message",
    ref: "session-1:message-2",
    capturedAt: "2026-07-12T10:01:02.345+04:00",
    scope: { kind: "area", key: "dex" },
    occurredAt: "2026-07-12T09:58:00+04:00",
    timePrecision: "minute",
    role: "user_statement",
    excerptHash: "sha256:excerpt",
    metadata: { channel: "memory" },
  });
});

test("history serializes a stable cursor and maps pagination", async () => {
  bridgeResponse({
    events: [rawEvent],
    total: 8,
    limit: 2,
    next_before_sequence: 16,
  });

  const history = await getPageHistory(config, {
    path: "topics/a b.md",
    limit: 2,
    beforeSequence: 18,
  });

  expect(lastRequest()).toEqual({
    method: "GET",
    path: "/admin/memory/page-edits/history?path=topics%2Fa+b.md&limit=2&before_sequence=18",
    body: undefined,
  });
  expect(history.total).toBe(8);
  expect(history.limit).toBe(2);
  expect(history.nextBeforeSequence).toBe(16);
  expect(history.events[0]?.sequence).toBe(17);
  expect(history.events[0]?.analysis).toEqual({
    path: "topics/a.md",
    before: ["old"],
    after: ["new"],
    changedBefore: ["old"],
    changedAfter: ["new"],
    patch: "patch",
  });
});

test("links preserve resolution status, context, candidates, and pagination", async () => {
  const rawLink = {
    source_path: "notes/source.md",
    target: "Dex#Plan",
    display: "the plan",
    heading: "Plan",
    context: "We agreed on [[Dex#Plan|the plan]] yesterday.",
    line: 12,
    column: 14,
    status: "ambiguous",
    resolved_path: null,
    candidates: ["topics/dex.md", "projects/dex.md"],
    source_revision: "sha256:source",
  };
  bridgeResponse({
    path: "topics/dex.md",
    revision: "ledger-7",
    stale: true,
    outgoing: [rawLink],
    backlinks: [{ ...rawLink, status: "resolved", resolved_path: "topics/dex.md", candidates: ["topics/dex.md"] }],
    total_outgoing: 4,
    total_backlinks: 9,
    limit: 3,
    offset: 6,
  });

  const links = await getPageLinks(config, { path: "topics/dex.md", limit: 3, offset: 6 });

  expect(lastRequest().path).toBe("/admin/memory/links?path=topics%2Fdex.md&limit=3&offset=6");
  expect(links).toEqual({
    path: "topics/dex.md",
    revision: "ledger-7",
    stale: true,
    outgoing: [{
      sourcePath: "notes/source.md",
      target: "Dex#Plan",
      display: "the plan",
      heading: "Plan",
      context: "We agreed on [[Dex#Plan|the plan]] yesterday.",
      line: 12,
      column: 14,
      status: "ambiguous",
      resolvedPath: null,
      candidates: ["topics/dex.md", "projects/dex.md"],
      sourceRevision: "sha256:source",
    }],
    backlinks: [{
      sourcePath: "notes/source.md",
      target: "Dex#Plan",
      display: "the plan",
      heading: "Plan",
      context: "We agreed on [[Dex#Plan|the plan]] yesterday.",
      line: 12,
      column: 14,
      status: "resolved",
      resolvedPath: "topics/dex.md",
      candidates: ["topics/dex.md"],
      sourceRevision: "sha256:source",
    }],
    totalOutgoing: 4,
    totalBacklinks: 9,
    limit: 3,
    offset: 6,
  });
});

test("notebook artifact list is camelCase metadata-only and detail owns content", async () => {
  const artifact = {
    path: "topics/a.md",
    title: "A",
    kind: "topic",
    type: "file",
    directory: "topics",
    scope: { kind: "user", key: null },
    content: "",
    snippet: "A short summary",
    record_count: 2,
    generated: false,
    editable: true,
    readonly_reason: null,
    updated_at: "2026-07-12T10:00:00Z",
    labels: ["work"],
    source: null,
    timeline: [],
    frontmatter: {},
  };
  bridgeResponse({ artifacts: [artifact] });
  const listed = await listMemoryArtifactSummaries(config);
  expect(listed.artifacts[0]).toEqual({
    path: "topics/a.md",
    title: "A",
    kind: "topic",
    type: "file",
    directory: "topics",
    scope: { kind: "user", key: null },
    snippet: "A short summary",
    summary: null,
    revision: null,
    recordCount: 2,
    generated: false,
    editable: true,
    readonlyReason: null,
    updatedAt: "2026-07-12T10:00:00Z",
    labels: ["work"],
    source: null,
  });
  expect("content" in listed.artifacts[0]!).toBe(false);
  expect("timeline" in listed.artifacts[0]!).toBe(false);
  expect("record_count" in listed.artifacts[0]!).toBe(false);

  bridgeResponse({ artifact: { ...artifact, content: "Rendered prose", revision: "sha256:page", editable_content: "# A\n\nExact prose\n" } });
  const detail = await readMemoryArtifactDetail(config, "topics/a.md");
  expect(detail.artifact).toEqual({
    path: "topics/a.md",
    title: "A",
    kind: "topic",
    type: "file",
    directory: "topics",
    scope: { kind: "user", key: null },
    snippet: "A short summary",
    revision: "sha256:page",
    summary: null,
    editableContent: "# A\n\nExact prose\n",
    content: "Rendered prose",
    recordCount: 2,
    generated: false,
    editable: true,
    readonlyReason: null,
    updatedAt: "2026-07-12T10:00:00Z",
    labels: ["work"],
    source: null,
    timeline: [],
    frontmatter: {},
  });
  expect("editable_content" in detail.artifact).toBe(false);
  expect("record_count" in detail.artifact).toBe(false);
});

test("legacy artifact adapters remain explicit for current components", async () => {
  const raw = {
    path: "topics/a.md", title: "A", kind: "topic", type: "file", directory: "topics",
    scope: { kind: "user", key: null }, content: "", snippet: "Summary", record_count: 2,
    generated: false, editable: true, readonly_reason: null, updated_at: null, labels: [], source: null,
    timeline: [], frontmatter: {},
  };
  bridgeResponse({ artifacts: [raw] });
  const list = await listMemoryArtifacts(config);
  expect(list.artifacts[0]).toMatchObject({ record_count: 2, readonly_reason: null, updated_at: null });

  bridgeResponse({ artifact: { ...raw, content: "body", revision: "rev", editable_content: "exact" } });
  const detail = await readMemoryArtifact(config, "topics/a.md");
  expect(detail.artifact).toMatchObject({ content: "body", editableContent: "exact", revision: "rev" });
});

test("bridge errors retain structured revision-conflict data", async () => {
  const conflict = {
    detail: {
      error: "page_revision_conflict",
      current_content: "current",
      current_revision: "sha256:current",
      base_revision: "sha256:base",
      candidate_revision: "sha256:candidate",
    },
  };
  bridgeResponse(conflict, { ok: false, status: 409 });

  const error = await applyPageEdit(config, { previewId: "preview-1", decisions: {} }).catch((reason) => reason);

  expect(error).toBeInstanceOf(ApiError);
  expect(error.status).toBe(409);
  expect(error.data).toEqual(conflict);
});

test("fetch errors retain structured revision-conflict data", async () => {
  (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
  Object.defineProperty(window, "ntrpDesktop", { configurable: true, value: undefined, writable: true });
  const conflict = { detail: { error: "page_revision_conflict", current_revision: "sha256:current" } };
  globalThis.fetch = async () => new Response(JSON.stringify(conflict), {
    status: 409,
    headers: { "content-type": "application/json" },
  });

  const error = await applyPageEdit(config, { previewId: "preview-1", decisions: {} }).catch((reason) => reason);

  expect(error).toBeInstanceOf(ApiError);
  expect(error.status).toBe(409);
  expect(error.data).toEqual(conflict);
});

test("fetch plain-text errors read the body once and preserve status and text", async () => {
  (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
  Object.defineProperty(window, "ntrpDesktop", { configurable: true, value: undefined, writable: true });
  let textReads = 0;
  globalThis.fetch = async () => ({
    ok: false,
    status: 502,
    headers: new Headers({ "content-type": "text/plain" }),
    text: async () => {
      textReads += 1;
      return "upstream unavailable";
    },
    json: async () => {
      throw new Error("json() must not be called");
    },
  }) as Response;

  const error = await applyPageEdit(config, { previewId: "preview-1", decisions: {} }).catch((reason) => reason);

  expect(textReads).toBe(1);
  expect(error).toBeInstanceOf(ApiError);
  expect(error.status).toBe(502);
  expect(error.responseText).toBe("upstream unavailable");
  expect(error.data).toBeNull();
});

test("fetch malformed JSON errors preserve the raw body and HTTP status", async () => {
  (globalThis as typeof globalThis & { window?: unknown }).window = originalWindow;
  Object.defineProperty(window, "ntrpDesktop", { configurable: true, value: undefined, writable: true });
  globalThis.fetch = async () => new Response("{not-json", {
    status: 500,
    headers: { "content-type": "application/json" },
  });

  const error = await applyPageEdit(config, { previewId: "preview-1", decisions: {} }).catch((reason) => reason);

  expect(error).toBeInstanceOf(ApiError);
  expect(error.status).toBe(500);
  expect(error.responseText).toBe("{not-json");
  expect(error.data).toBeNull();
});

test("artifact APIs reject non-canonical paths before transport", async () => {
  bridgeResponse({ artifact: {} });
  for (const path of ["", "/absolute.md", "../escape.md", "topics/./a.md", "topics//a.md", "topics\\a.md", "C:/a.md"]) {
    const error = await readMemoryArtifactDetail(config, path).catch((reason) => reason);
    expect(error).toBeInstanceOf(Error);
    expect((error as Error).message).toBe("Invalid memory artifact path");
    expect(request).toBeNull();
  }
});
