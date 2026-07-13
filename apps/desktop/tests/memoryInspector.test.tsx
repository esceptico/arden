import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryInspector } from "@/features/memory/components/MemoryInspector";
import type { MemoryArtifactDetail, PageEditHistory, PageLinks } from "@/features/memory/lib/notebookTypes";

const roots = new Set<Root>();
const originalDesktop = window.ntrpDesktop;

function setup() {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  roots.add(root);
  return { host, root };
}

afterEach(async () => {
  for (const root of roots) await act(async () => root.unmount());
  roots.clear();
  window.ntrpDesktop = originalDesktop;
  document.body.replaceChildren();
});

const page: MemoryArtifactDetail = {
  path: "topics/dex.md", title: "Dex", kind: "topic", type: "file", directory: "topics",
  scope: { kind: "user", key: null }, snippet: null, summary: null, revision: "page:r2", recordCount: 1,
  generated: false, editable: true, readonlyReason: null, updatedAt: "2026-07-13T08:00:00Z",
  labels: [], source: null, content: "Dex", editableContent: "Dex", timeline: [], frontmatter: {},
};

const links: PageLinks = {
  path: page.path, revision: "ledger:r2", stale: false, totalOutgoing: 1, totalBacklinks: 1, limit: 100, offset: 0,
  outgoing: [{ sourcePath: page.path, target: "Roadmap", display: "Roadmap", heading: "Work", context: "See [[Roadmap]] next", line: 4, column: 5, status: "resolved", resolvedPath: "roadmap.md", candidates: ["roadmap.md"], sourceRevision: "ledger:r2" }],
  backlinks: [{ sourcePath: "daily/2026-07-13.md", target: "Dex", display: "Dex", heading: "Morning", context: "Worked on [[Dex]]", line: 8, column: 12, status: "resolved", resolvedPath: page.path, candidates: [page.path], sourceRevision: "ledger:r2" }],
};

const history: PageEditHistory = {
  total: 1, limit: 100, nextBeforeSequence: null,
  events: [{
    eventType: "PAGE_EDIT", id: "event-1", occurredAt: "2026-07-13T08:20:31.123+04:00", sequence: 5,
    actor: "user:desktop", origin: "desktop", path: page.path, baseRevision: "page:r1", resultRevision: "page:r2",
    patch: "@@", reconciliation: "needs_review", analysis: null, reconcilesEventId: null,
    reviewEventId: null, observationId: null, sourceCanonicalRevision: "ledger:r1",
    operations: [{
      kind: "RETRACT", id: "event-1:operation:0", targetIds: ["record-actual"],
      sources: [{ kind: "web", ref: "https://example.com/applied", role: "support", occurredAt: null, capturedAt: "2026-07-13T04:19:00Z", timePrecision: "unknown", excerptHash: "sha256:applied", scope: null, metadata: {} }],
    }],
    reviewOperations: [{
      kind: "SUPERSEDE", id: "event-1:operation:0", text: "Dex uses ledger v2", memoryKind: "fact",
      scope: { kind: "area", key: "engineering" }, targetIds: ["record-old"], metaLabels: [], entityLabels: [],
      sources: [{ kind: "chat", ref: "session:abc#message:9", role: "claim", occurredAt: "2026-07-13T08:18:00+04:00", capturedAt: "2026-07-13T04:18:02Z", timePrecision: "second", excerptHash: "sha256:evidence", scope: { kind: "session", key: "abc" }, metadata: {} }],
    }, { kind: "ASK", id: "event-1:operation:1", question: "Forget the old durable fact?", targetIds: ["record-old"] }],
    questions: [{ id: "question-1", operationIndex: 1, question: "Forget the old durable fact?" }],
  }],
};

test("inspector separates placement, record scope, links, lifecycle, exact evidence, and pending review", async () => {
  const copied: string[] = [];
  window.ntrpDesktop = { clipboard: { writeText: async (value) => (copied.push(value), true) } } as Window["ntrpDesktop"];
  const navigated: Array<{ path: string; anchor: string | null }> = [];
  const { host, root } = setup();
  await act(async () => root.render(
    <MemoryInspector
      page={page}
      links={links}
      history={history}
      linksLoading={false}
      historyLoading={false}
      linkError={null}
      historyError={null}
      onNavigate={(path, anchor) => navigated.push({ path, anchor })}
    />,
  ));

  expect(host.querySelector('[data-memory-inspector-section="links"]')?.textContent).toContain("Worked on [[Dex]]");
  expect(host.textContent).toContain("daily/2026-07-13.md");
  expect(host.querySelector('[data-memory-inspector-section="scope"]')?.textContent).toContain("Page placementtopics/dex.md");
  expect(host.querySelector('[data-memory-inspector-section="scope"]')?.textContent).toContain("Page scopeuser");
  expect(host.querySelector('[data-memory-inspector-section="scope"]')?.textContent).toContain("Proposed scopearea:engineering");
  const lifecycle = host.querySelector('[data-memory-inspector-section="lifecycle"]')!;
  expect(lifecycle.textContent).toContain("record-old");
  expect(lifecycle.textContent).toContain("Proposed target");
  expect(lifecycle.textContent).toContain("Target record-actual");
  expect(lifecycle.textContent).not.toContain("successor event-1:operation:0");
  const evidence = host.querySelector('[data-memory-inspector-section="evidence"]')!;
  expect(evidence.textContent).toContain("Applied evidence");
  expect(evidence.textContent).toContain("Proposed evidence");
  expect(evidence.textContent).toContain("chat");
  expect(evidence.textContent).toContain("claim");
  expect(evidence.textContent).toContain("2026-07-13T08:18:00+04:00");
  expect(evidence.textContent).toContain("2026-07-13T04:18:02Z");
  expect(evidence.textContent).toContain("second");
  expect(evidence.textContent).toContain("sha256:evidence");
  const event = host.querySelector('[data-memory-inspector-section="page-events"]')!;
  expect(event.textContent).toContain("user:desktop");
  expect(event.textContent).toContain("page:r1 → page:r2");
  expect(event.textContent).toContain("needs review");
  expect(event.textContent).toContain("1 applied · 2 review");
  expect(host.querySelector('[data-memory-inspector-section="pending-review"]')?.textContent).toContain("Forget the old durable fact?");

  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Open backlink from daily/2026-07-13.md"]')?.click());
  expect(navigated).toEqual([{ path: "daily/2026-07-13.md", anchor: "Morning" }]);
  const source = host.querySelector<HTMLButtonElement>('button[aria-label="Copy source session:abc#message:9"]')!;
  source.focus();
  await act(async () => source.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
  expect(copied).toEqual(["session:abc#message:9"]);
});

test("inspector exposes safe URLs but never guesses a route for arbitrary refs", async () => {
  const urlHistory: PageEditHistory = {
    ...history,
    events: [{ ...history.events[0]!, reviewOperations: [], operations: [{
      kind: "ADD", id: "op", text: "Evidence", memoryKind: "source", scope: { kind: "user", key: null },
      metaLabels: [], entityLabels: [], sources: [{ kind: "web", ref: "https://example.com/evidence", role: "support", occurredAt: null, capturedAt: null, timePrecision: "unknown", excerptHash: null, scope: null, metadata: {} }],
    }] }],
  };
  const { host, root } = setup();
  await act(async () => root.render(<MemoryInspector page={page} links={links} history={urlHistory} linksLoading={false} historyLoading={false} linkError={null} historyError={null} onNavigate={() => {}} />));
  const source = host.querySelector<HTMLAnchorElement>('a[aria-label="Open source https://example.com/evidence"]')!;
  expect(source.href).toBe("https://example.com/evidence");
  expect(source.target).toBe("_blank");
  expect(host.querySelector('a[href^="session:"]')).toBeNull();
});

test("credentialed URLs and every non-URL source copy instead of opening or guessing routes", async () => {
  const copied: string[] = [];
  window.ntrpDesktop = { clipboard: { writeText: async (value) => (copied.push(value), true) } } as Window["ntrpDesktop"];
  const unsafeHistory: PageEditHistory = {
    ...history,
    events: [{ ...history.events[0]!, reviewOperations: [], operations: [{
      kind: "ADD", id: "unsafe", text: "Unsafe refs", memoryKind: "source", scope: { kind: "user", key: null }, metaLabels: [], entityLabels: [],
      sources: [
        { kind: "web", ref: "https://user:secret@example.com/private", role: null, occurredAt: null, capturedAt: null, timePrecision: "unknown", excerptHash: null, scope: null, metadata: {} },
        { kind: "file", ref: "roadmap.md", role: null, occurredAt: null, capturedAt: null, timePrecision: "unknown", excerptHash: null, scope: null, metadata: {} },
      ],
    }] }],
  };
  const { host, root } = setup();
  await act(async () => root.render(<MemoryInspector page={page} links={links} history={unsafeHistory} linksLoading={false} historyLoading={false} linkError={null} historyError={null} onNavigate={() => { throw new Error("must not navigate"); }} />));
  expect(host.querySelector('a[href*="user:secret"]')).toBeNull();
  const credentialed = host.querySelector<HTMLButtonElement>('button[aria-label="Copy source https://user:secret@example.com/private"]')!;
  const path = host.querySelector<HTMLButtonElement>('button[aria-label="Copy source roadmap.md"]')!;
  await act(async () => credentialed.click());
  await act(async () => path.click());
  expect(copied).toEqual(["https://user:secret@example.com/private", "roadmap.md"]);
});

test("stale links are visible but never activatable", async () => {
  const navigated: string[] = [];
  const { host, root } = setup();
  await act(async () => root.render(<MemoryInspector page={page} links={{ ...links, stale: true }} history={history} linksLoading={false} historyLoading={false} linkError={null} historyError={null} onNavigate={(path) => navigated.push(path)} />));
  expect(host.textContent).toContain("Link index is refreshing");
  const backlink = host.querySelector<HTMLButtonElement>('button[aria-label="Open backlink from daily/2026-07-13.md"]')!;
  expect(backlink.disabled).toBe(true);
  await act(async () => backlink.click());
  expect(navigated).toEqual([]);
});

test("link and history errors stay independent and pagination remains honest", async () => {
  const more: string[] = [];
  const partialLinks = { ...links, totalOutgoing: 4, totalBacklinks: 3 };
  const partialHistory = { ...history, total: 3, nextBeforeSequence: 4 };
  const { host, root } = setup();
  await act(async () => root.render(
    <MemoryInspector
      page={page}
      links={partialLinks}
      history={partialHistory}
      linksLoading={false}
      historyLoading={false}
      linkError={null}
      historyError="History unavailable"
      onNavigate={() => {}}
      onLoadMoreLinks={() => more.push("links")}
      onLoadMoreHistory={() => more.push("history")}
    />,
  ));
  expect(host.querySelector('[data-memory-inspector-section="links"]')?.textContent).toContain("Worked on [[Dex]]");
  expect(host.querySelector('[data-memory-inspector-section="page-events"]')?.textContent).toContain("History unavailable");
  expect(host.textContent).toContain("Showing 1 of 4 outgoing");
  expect(host.textContent).toContain("Based on 1 of 3 events");
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Load more memory links"]')?.click());
  expect(more).toEqual(["links"]);

  await act(async () => root.render(
    <MemoryInspector
      page={page}
      links={null}
      history={partialHistory}
      linksLoading={false}
      historyLoading={false}
      linkError="Links unavailable"
      historyError={null}
      onNavigate={() => {}}
      onLoadMoreHistory={() => more.push("history")}
    />,
  ));
  expect(host.querySelector('[data-memory-inspector-section="links"]')?.textContent).toContain("Links unavailable");
  expect(host.querySelector('[data-memory-inspector-section="page-events"]')?.textContent).toContain("user:desktop");
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Load older page events"]')?.click());
  expect(more).toEqual(["links", "history"]);
});

test("pending questions disappear after a later applied event resolves the review event id", async () => {
  const review = history.events[0]!;
  const resolved: PageEditHistory = {
    ...history,
    total: 2,
    events: [
      {
        ...review,
        id: "event-applied",
        reconciliation: "applied",
        operations: [],
        reviewOperations: [],
        questions: [],
        reconcilesEventId: "event-pending",
        reviewEventId: review.id,
        sequence: 6,
      },
      review,
    ],
  };
  const { host, root } = setup();
  await act(async () => root.render(<MemoryInspector page={page} links={links} history={resolved} linksLoading={false} historyLoading={false} linkError={null} historyError={null} onNavigate={() => {}} />));
  expect(host.querySelector('[data-memory-inspector-section="pending-review"]')?.textContent).not.toContain("Forget the old durable fact?");
  expect(host.querySelector('[data-memory-inspector-section="pending-review"]')?.textContent).toContain("No pending questions");
});

test("lifecycle actions preserve the exact target and require confirmation before forget", async () => {
  const calls: Array<{ action: string; target: string }> = [];
  const { host, root } = setup();
  await act(async () => root.render(
    <MemoryInspector
      page={page}
      links={links}
      history={history}
      linksLoading={false}
      historyLoading={false}
      linkError={null}
      historyError={null}
      onNavigate={() => {}}
      onCorrect={(target) => calls.push({ action: "correct", target })}
      onForget={async (target, eventId, questionId) => { calls.push({ action: `forget:${eventId}:${questionId}`, target }); }}
    />,
  ));

  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Correct record record-old"]')?.click());
  expect(calls).toEqual([{ action: "correct", target: "record-old" }]);

  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Forget record record-old"]')?.click());
  expect(calls).toHaveLength(1);
  expect(host.textContent).toContain("Forget record-old?");
  expect(host.textContent).toContain("This appends a RETRACT event");
  expect(host.querySelector<HTMLButtonElement>('button[aria-label="Dispute record record-old"]')?.disabled).toBe(true);
  expect(host.querySelector<HTMLButtonElement>('button[aria-label="Archive record record-old"]')?.disabled).toBe(true);

  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Confirm forget record-old"]')?.click());
  expect(calls).toEqual([
    { action: "correct", target: "record-old" },
    { action: "forget:event-1:question-1", target: "record-old" },
  ]);
});
