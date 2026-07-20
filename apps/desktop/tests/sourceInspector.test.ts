import { beforeEach, expect, test } from "bun:test";
import { getState, setState, type SourceRef, type UiMessage } from "@/stores";
import { DEFAULT_PREFS } from "@/stores/prefs";
import { sourceInspectorSelection } from "@/features/sources/lib/sourceInspector";

function source(overrides: Partial<SourceRef> = {}): SourceRef {
  return {
    provider: "web",
    kind: "page",
    ref: "https://example.test/docs",
    title: "Example docs",
    url: "https://example.test/docs",
    ...overrides,
  };
}

function activity(id: string, refs: SourceRef[]): UiMessage {
  return {
    id: `activity-${id}`,
    role: "activity",
    content: "",
    activity: {
      label: "Called",
      done: true,
      items: [
        {
          id: `tool-${id}`,
          kind: "web_fetch",
          target: "Fetch",
          sourceRefs: refs,
        },
      ],
    },
  };
}

function transcript(messages: UiMessage[]) {
  return {
    messages: new Map(messages.map((message) => [message.id, message])),
    order: messages.map((message) => message.id),
  };
}

beforeEach(() => {
  setState({
    currentSessionId: null,
    messages: new Map(),
    order: [],
    sessionCache: new Map(),
    sourceTurnId: null,
    rightInspectorTab: "activity",
    prefs: { ...DEFAULT_PREFS, rightPanelCollapsed: true },
  });
});

test("opening Sources scopes the exact turn and expands the persisted right panel", () => {
  getState().openSourcesForTurn("user-1");

  expect(getState().rightInspectorTab).toBe("sources");
  expect(getState().sourceTurnId).toBe("user-1");
  expect(getState().prefs.rightPanelCollapsed).toBe(false);
  expect(JSON.parse(localStorage.getItem("ntrp.desktop.prefs") ?? "{}").rightPanelCollapsed).toBe(false);
});

test("manual Sources selection clears exact scope so the latest source-bearing turn is selected", () => {
  const earlier = source({ ref: "earlier", title: "Earlier" });
  const latest = source({ ref: "latest", title: "Latest" });
  const state = transcript([
    { id: "user-1", role: "user", content: "first" },
    activity("1", [earlier]),
    { id: "assistant-1", role: "assistant", content: "first answer" },
    { id: "user-2", role: "user", content: "second" },
    activity("2", [latest]),
    { id: "assistant-2", role: "assistant", content: "second answer" },
  ]);

  getState().openSourcesForTurn("user-1");
  getState().setRightInspectorTab("sources");

  expect(getState().sourceTurnId).toBeNull();
  expect(sourceInspectorSelection({ ...state, sourceTurnId: getState().sourceTurnId })).toMatchObject({
    turnId: "user-2",
    sources: [{ source: latest }],
  });
});

test("an exact source-less turn stays empty instead of falling back to a later turn", () => {
  const later = source({ ref: "later", title: "Later" });
  const state = transcript([
    { id: "user-1", role: "user", content: "first" },
    { id: "assistant-1", role: "assistant", content: "no tools" },
    { id: "user-2", role: "user", content: "second" },
    activity("2", [later]),
    { id: "assistant-2", role: "assistant", content: "with sources" },
  ]);

  expect(sourceInspectorSelection({ ...state, sourceTurnId: "user-1" })).toEqual({
    turnId: "user-1",
    sources: [],
  });
});

test("manual scope returns an empty inspector when no turn has sources", () => {
  const state = transcript([
    { id: "user-1", role: "user", content: "question" },
    { id: "assistant-1", role: "assistant", content: "answer" },
  ]);

  expect(sourceInspectorSelection({ ...state, sourceTurnId: null })).toEqual({
    turnId: null,
    sources: [],
  });
});

test("non-URL sources retain their originating tool-call target", () => {
  const opaque = source({
    provider: "file",
    kind: "file",
    ref: "/tmp/notes.md",
    title: "notes.md",
    url: undefined,
  });
  const state = transcript([
    { id: "user-1", role: "user", content: "read notes" },
    activity("1", [opaque]),
    { id: "assistant-1", role: "assistant", content: "summary" },
  ]);

  expect(sourceInspectorSelection({ ...state, sourceTurnId: "user-1" }).sources[0]).toMatchObject({
    source: { ref: "/tmp/notes.md", url: undefined },
    toolCall: { id: "tool-1", target: "Fetch" },
  });
});

test("changing sessions clears exact Sources scope", () => {
  getState().setCurrentSession("session-1");
  getState().openSourcesForTurn("user-1");

  getState().setCurrentSession("session-2");

  expect(getState().sourceTurnId).toBeNull();
  expect(getState().rightInspectorTab).toBe("sources");
});

test("hidden meta assistant turns support exact and latest source selection", () => {
  const metaSource = source({ ref: "meta-source", title: "Meta source" });
  const state = transcript([
    { id: "meta-user-1", role: "user", content: "hidden", isMeta: true },
    activity("meta", [metaSource]),
    { id: "assistant-meta", role: "assistant", content: "automated answer" },
  ]);

  expect(sourceInspectorSelection({ ...state, sourceTurnId: "meta-user-1" })).toMatchObject({
    turnId: "meta-user-1",
    sources: [{ source: metaSource }],
  });
  expect(sourceInspectorSelection({ ...state, sourceTurnId: null })).toMatchObject({
    turnId: "meta-user-1",
    sources: [{ source: metaSource }],
  });
});

test("edit truncation and canonical history replacement clear stale exact scope", () => {
  setState({
    messages: new Map([
      ["user-1", { id: "user-1", role: "user", content: "first" }],
      ["user-2", { id: "user-2", role: "user", content: "second" }],
    ]),
    order: ["user-1", "user-2"],
    sourceTurnId: "user-2",
  });

  getState().truncateFrom("user-2");
  expect(getState().sourceTurnId).toBeNull();

  setState({ sourceTurnId: "user-1" });
  getState().setHistory([{ id: "user-3", role: "user", content: "replacement" }]);
  expect(getState().sourceTurnId).toBeNull();
});

test("source revision ignores streamed content and bumps for live and history source changes", () => {
  setState({
    sourceRefsRevision: 10,
    messages: new Map([
      ["assistant-1", { id: "assistant-1", role: "assistant", content: "a" }],
      ["activity-1", activity("1", [])],
    ]),
    order: ["assistant-1", "activity-1"],
  });

  getState().mutateMessage("assistant-1", { content: "a streamed token" });
  expect(getState().sourceRefsRevision).toBe(10);

  getState().mergeActivityItem("tool-1", { outcome: { status: "failed" } });
  expect(getState().sourceRefsRevision).toBe(10);

  getState().mergeActivityItem("tool-1", { sourceRefs: [source({ ref: "live" })] });
  expect(getState().sourceRefsRevision).toBeGreaterThan(10);
  const afterLive = getState().sourceRefsRevision;

  getState().setHistory([
    { id: "user-history", role: "user", content: "history" },
    activity("history", [source({ ref: "history" })]),
  ]);
  expect(getState().sourceRefsRevision).toBeGreaterThan(afterLive);
});
