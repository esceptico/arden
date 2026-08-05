import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { AppConfig } from "@/api/core";
import {
  listMemoryArtifactSummaries,
  readMemoryArtifactDetail,
} from "@/api/memoryArtifacts";
import { handleAutomationEvent } from "@/features/automations/hooks/useAutomationEvents";
import { ArtifactMemoryView } from "@/features/memory/components/ArtifactMemoryView";
import { clearDrafts, draftKey, getDraft, setDraft } from "@/features/memory/lib/draftStore";
import { splitFrontmatter } from "@/features/memory/lib/format";
import { useStore } from "@/stores";
import {
  error,
  installCanonicalMemoryBridge,
  ok,
  type HistoryCommitFixture,
  type WikiPageFixture,
} from "./helpers/canonicalMemoryBridge";

const config: AppConfig = { serverUrl: "http://localhost:6877", apiKey: "test-key" };
const originalDesktop = window.ardenDesktop;
const originalConfig = useStore.getState().config;
const originalVaultVersion = useStore.getState().memoryVaultVersion;
const originalCurrentSessionId = useStore.getState().currentSessionId;
const originalAutomations = useStore.getState().automations;
const originalSessions = useStore.getState().sessions;
const originalAreas = useStore.getState().areas;
const originalAutomationStream = useStore.getState().automationStream;
const roots = new Set<Root>();

function page(
  overrides: Partial<WikiPageFixture> = {},
): WikiPageFixture {
  return {
    pageId: "page-a",
    path: "topics/a.md",
    title: "A",
    content: "---\ntitle: A\n---\n# A\n\nOld source bytes.\n",
    version: "note-r1",
    repositoryHead: "wiki-head-1",
    metadata: { summary: "A note" },
    ...overrides,
  };
}

function setup(path = "topics/a.md") {
  localStorage.setItem("arden.desktop.memory.inspectorOpen", "false");
  localStorage.setItem("arden.desktop.memory.lastPath", path);
  const host = document.createElement("div");
  host.style.height = "800px";
  document.body.append(host);
  const root = createRoot(host);
  roots.add(root);
  return { host, root };
}

async function settle(delay = 0) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, delay));
  });
}

async function renderView(path = "topics/a.md") {
  const view = setup(path);
  await act(async () => view.root.render(<ArtifactMemoryView config={config} />));
  await settle(260);
  return view;
}

async function shortcut(key: string, target: EventTarget = window) {
  await act(async () => {
    target.dispatchEvent(new KeyboardEvent("keydown", {
      key,
      metaKey: true,
      bubbles: true,
    }));
  });
  await settle();
}

async function changeDraft(textarea: HTMLTextAreaElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(textarea, value);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((accept) => {
    resolve = accept;
  });
  return { promise, resolve };
}

afterEach(async () => {
  for (const root of roots) await act(async () => root.unmount());
  roots.clear();
  clearDrafts();
  window.ardenDesktop = originalDesktop;
  useStore.setState({
    areas: originalAreas,
    automations: originalAutomations,
    automationStream: originalAutomationStream,
    config: originalConfig,
    currentSessionId: originalCurrentSessionId,
    memoryVaultVersion: originalVaultVersion,
    memoryVaultChanges: [],
    sessions: originalSessions,
  });
  document.body.replaceChildren();
  localStorage.removeItem("arden.desktop.memory.inspectorOpen");
  localStorage.removeItem("arden.desktop.memory.lastPath");
});

test("Cmd+E edits only the body and Cmd+S reviews exact recomposed bytes", async () => {
  const bridge = installCanonicalMemoryBridge({ pages: [page()] });
  const { host } = await renderView();

  const edit = host.querySelector<HTMLButtonElement>('button[aria-label="Edit memory note"]')!;
  edit.focus();
  await shortcut("e", edit);
  const textarea = host.querySelector<HTMLTextAreaElement>('textarea[aria-label="Markdown source for topics/a.md"]')!;
  expect(textarea.value).toBe(splitFrontmatter(page().content).body);

  await changeDraft(textarea, "# A\n\nDraft source bytes.\n");
  expect(host.textContent).toContain("unsaved draft");
  await shortcut("s", textarea);

  expect(host.querySelector("[data-diff-review]")).not.toBeNull();
  expect(host.textContent).toContain("Draft source bytes.");
  expect(bridge.requests.some(({ method }) => method === "PUT")).toBe(false);

  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Apply changes"]')?.click());
  await settle(280);

  const write = bridge.requests.find(({ method }) => method === "PUT");
  expect(write?.body).toEqual({
    content: "---\ntitle: A\n---\n# A\n\nDraft source bytes.\n",
    expected_version: "note-r1",
    expected_head: "wiki-head-1",
  });
  expect(getDraft("topics/a.md", "note-r1")).toBeNull();
});

test("the visible Edit button opens the source editor", async () => {
  installCanonicalMemoryBridge({ pages: [page()] });
  const { host } = await renderView();

  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Edit memory note"]')?.click());
  await settle(100);

  expect(host.querySelector("[data-memory-editor-mode=source]")).not.toBeNull();
  expect(host.querySelector<HTMLTextAreaElement>("textarea")?.value).toBe("# A\n\nOld source bytes.\n");
});

test("edit shortcuts ignore focused interactive controls", async () => {
  installCanonicalMemoryBridge({ pages: [page()] });
  const { host } = await renderView();
  const controls = [
    Object.assign(document.createElement("input"), { type: "text" }),
    Object.assign(document.createElement("a"), { href: "#test" }),
    document.createElement("div"),
  ];
  controls[2]!.setAttribute("role", "slider");
  controls[2]!.tabIndex = 0;
  host.append(...controls);

  for (const control of controls) {
    control.focus();
    await shortcut("e", control);
    expect(host.querySelector("textarea")).toBeNull();
    expect(document.activeElement).toBe(control);
  }
});

test("review cancel returns to the byte-identical draft", async () => {
  installCanonicalMemoryBridge({ pages: [page()] });
  const { host } = await renderView();
  await shortcut("e");
  const bytes = "# A\n\nDraft  \n\n```ts\nconst x = 1;\n```\n";
  await changeDraft(host.querySelector<HTMLTextAreaElement>("textarea")!, bytes);
  await shortcut("s", host.querySelector<HTMLTextAreaElement>("textarea")!);

  await act(async () => Array.from(host.querySelectorAll<HTMLButtonElement>("button"))
    .find((button) => button.textContent === "Back to edit")?.click());
  await settle(220);

  expect(host.querySelector<HTMLTextAreaElement>("textarea")?.value).toBe(bytes);
  expect(getDraft("topics/a.md", "note-r1")).toBe(`---\ntitle: A\n---\n${bytes}`);
});

test("a changed canonical revision opens a three-way conflict and preserves the draft", async () => {
  const bridge = installCanonicalMemoryBridge({ pages: [page()] });
  const { host } = await renderView();
  await shortcut("e");
  const draft = "# A\n\nMy exact draft.\n";
  await changeDraft(host.querySelector<HTMLTextAreaElement>("textarea")!, draft);
  bridge.updatePage(page({
    content: "---\ntitle: A\n---\n# A\n\nExternal source bytes.\n",
    version: "note-r2",
    repositoryHead: "wiki-head-2",
  }));

  await shortcut("s", host.querySelector<HTMLTextAreaElement>("textarea")!);

  expect(host.querySelector('[aria-label="Three-way conflict for topics/a.md"]')).not.toBeNull();
  expect(host.textContent).toContain("Current page");
  expect(host.textContent).toContain("Your draft");
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Continue with current page"]')?.click());
  await settle(220);
  expect(host.querySelector<HTMLTextAreaElement>("textarea")?.value).toBe(draft);
  expect(getDraft("topics/a.md", "note-r2")).toBe(`---\ntitle: A\n---\n${draft}`);
});

test("vault changes refresh clean pages and protect dirty drafts", async () => {
  useStore.setState({ memoryVaultVersion: 0, memoryVaultChanges: [] });
  const bridge = installCanonicalMemoryBridge({ pages: [page()] });
  const { host } = await renderView();
  bridge.updatePage(page({
    content: "---\ntitle: A\n---\n# A\n\nExternal update.\n",
    version: "note-r2",
    repositoryHead: "wiki-head-2",
  }));
  await act(async () => useStore.getState().memoryVaultChanged({
    paths: ["topics/a.md"],
    revision: "note-r2",
    seq: 90,
  }));
  await settle(280);
  expect(host.textContent).toContain("External update.");

  await shortcut("e");
  const draft = "# A\n\nKeep this draft exactly.\n";
  await changeDraft(host.querySelector<HTMLTextAreaElement>("textarea")!, draft);
  bridge.updatePage(page({
    content: "---\ntitle: A\n---\n# A\n\nSecond external update.\n",
    version: "note-r3",
    repositoryHead: "wiki-head-3",
  }));
  await act(async () => useStore.getState().memoryVaultChanged({
    paths: ["topics/a.md"],
    revision: "note-r3",
    seq: 91,
  }));
  await settle(280);

  expect(host.querySelector('[aria-label="Three-way conflict for topics/a.md"]')).not.toBeNull();
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Back to draft"]')?.click());
  await settle(220);
  expect(host.querySelector<HTMLTextAreaElement>("textarea")?.value).toBe(draft);
});

test("retained changes are covered by the initial Memory load", async () => {
  useStore.setState({
    memoryVaultVersion: 1,
    memoryVaultChanges: [{
      paths: ["topics/preexisting.md"],
      revision: "note-preexisting-r1",
      seq: 99,
    }],
  });
  const bridge = installCanonicalMemoryBridge({ pages: [page()] });
  await renderView();

  expect(bridge.requests.filter(({ path }) => path === "/admin/wiki/pages")).toHaveLength(1);
  expect(bridge.requests.filter(({ path }) => path === "/admin/wiki/pages/page-a")).toHaveLength(1);
});

test("sequenced non-selected create rename and archive refresh the rail once", async () => {
  useStore.setState({ memoryVaultVersion: 0, memoryVaultChanges: [] });
  const selected = page();
  const renamed = page({
    pageId: "page-b",
    path: "topics/b.md",
    title: "B",
    content: "# B\n",
    version: "note-b-r1",
  });
  const archived = page({
    pageId: "page-archived",
    path: "topics/archived.md",
    title: "Archived",
    content: "# Archived\n",
    version: "note-archived-r1",
  });
  const bridge = installCanonicalMemoryBridge({ pages: [selected, renamed, archived] });
  const { host } = await renderView();
  const summaryReads = () => bridge.requests.filter(({ path }) => path === "/admin/wiki/pages").length;
  const detailReads = () => bridge.requests.filter(({ path }) => path === "/admin/wiki/pages/page-a").length;
  const readsBefore = summaryReads();
  const detailReadsBefore = detailReads();

  bridge.state.pages.delete("page-b");
  bridge.state.pages.delete("page-archived");
  bridge.updatePage({
    ...renamed,
    path: "topics/renamed.md",
    title: "Renamed",
    version: "note-b-r2",
  });
  bridge.updatePage(page({
    pageId: "page-created",
    path: "topics/created.md",
    title: "Created",
    content: "# Created\n",
    version: "note-created-r1",
  }));
  await act(async () => {
    useStore.getState().memoryVaultChanged({
      paths: ["topics/b.md", "topics/renamed.md"],
      revision: "note-b-r2",
      seq: 100,
    });
    useStore.getState().memoryVaultChanged({
      paths: ["topics/created.md"],
      revision: "note-created-r1",
      seq: 101,
    });
    useStore.getState().memoryVaultChanged({
      paths: ["topics/archived.md"],
      revision: "note-archived-r2",
      seq: 102,
    });
  });
  await settle(320);

  expect(summaryReads()).toBe(readsBefore + 1);
  expect(detailReads()).toBe(detailReadsBefore);
  expect(host.querySelector('[data-memory-entry="topics/renamed.md"]')).not.toBeNull();
  expect(host.querySelector('[data-memory-entry="topics/created.md"]')).not.toBeNull();
  expect(host.querySelector('[data-memory-entry="topics/b.md"]')).toBeNull();
  expect(host.querySelector('[data-memory-entry="topics/archived.md"]')).toBeNull();
});

test("a selected sequenced burst refreshes summaries and detail once", async () => {
  useStore.setState({ memoryVaultVersion: 0, memoryVaultChanges: [] });
  const bridge = installCanonicalMemoryBridge({ pages: [page()] });
  const { host } = await renderView();
  const summaryReads = () => bridge.requests.filter(({ path }) => path === "/admin/wiki/pages").length;
  const detailReads = () => bridge.requests.filter(({ path }) => path === "/admin/wiki/pages/page-a").length;
  const summariesBefore = summaryReads();
  const detailsBefore = detailReads();

  bridge.updatePage(page({
    content: "---\ntitle: A\n---\n# A\n\nFinal external update.\n",
    version: "note-r3",
    repositoryHead: "wiki-head-3",
  }));
  await act(async () => {
    useStore.getState().memoryVaultChanged({
      paths: ["topics/a.md"],
      revision: "note-r2",
      seq: 110,
    });
    useStore.getState().memoryVaultChanged({
      paths: ["topics/a.md"],
      revision: "note-r3",
      seq: 111,
    });
    useStore.getState().memoryVaultChanged({
      paths: ["topics/a.md"],
      revision: "note-r3",
      seq: 112,
    });
  });
  await settle(360);

  expect(summaryReads()).toBe(summariesBefore + 1);
  expect(detailReads()).toBe(detailsBefore + 1);
  expect(host.textContent).toContain("Final external update.");
});

test("coarse and sequenced changes in one render reconcile once", async () => {
  useStore.setState({ memoryVaultVersion: 0, memoryVaultChanges: [] });
  const bridge = installCanonicalMemoryBridge({ pages: [page()] });
  const { host } = await renderView();
  const summaryReads = () => bridge.requests.filter(({ path }) => path === "/admin/wiki/pages").length;
  const detailReads = () => bridge.requests.filter(({ path }) => path === "/admin/wiki/pages/page-a").length;
  const summariesBefore = summaryReads();
  const detailsBefore = detailReads();

  bridge.updatePage(page({
    content: "---\ntitle: A\n---\n# A\n\nCoarse external update.\n",
    version: "note-r2",
    repositoryHead: "wiki-head-2",
  }));
  await act(async () => {
    useStore.getState().memoryVaultChanged();
    useStore.getState().memoryVaultChanged({
      paths: ["topics/other.md"],
      revision: "note-other-r1",
      seq: 115,
    });
  });
  await settle(340);

  expect(summaryReads()).toBe(summariesBefore + 1);
  expect(detailReads()).toBe(detailsBefore + 1);
  expect(host.textContent).toContain("Coarse external update.");
});

test("automation replay gaps reconcile every replayable projection and reject stale Memory reads", async () => {
  const staleSummaryGate = deferred();
  let holdStaleSummary = false;
  useStore.setState({
    areas: { ...originalAreas, openAreaKey: "area-a" },
    automationStream: {
      ...originalAutomationStream,
      phase: "connected",
      statuses: { "stale-task": "working: old tool" },
    },
    config,
    currentSessionId: null,
    memoryVaultVersion: 8,
    memoryVaultChanges: [{ paths: ["topics/old.md"], revision: "old", seq: 72 }],
  });
  const stalePage = page();
  const bridge = installCanonicalMemoryBridge({
    pages: [stalePage],
    onRequest: async ({ path }) => {
      if (path === "/admin/wiki/pages" && holdStaleSummary) {
        holdStaleSummary = false;
        await staleSummaryGate.promise;
        return ok({
          repository_head: stalePage.repositoryHead,
          pages: [{
            page_id: stalePage.pageId,
            path: stalePage.path,
            resource_state: "active",
            title: stalePage.title,
            aliases: [],
            lifecycle: "active",
            redirect_to: null,
            metadata: stalePage.metadata,
            version: stalePage.version,
            repository_head: stalePage.repositoryHead,
            created_at: null,
            updated_at: null,
          }],
        });
      }
      if (path === "/automations") return ok({ automations: [] });
      if (path.startsWith("/sessions?")) return ok({ sessions: [] });
      if (path === "/areas") return ok({ areas: [] });
      if (path === "/areas/overview") {
        return ok({ areas: [], focus: [], brief: { done: [], in_progress: [], needs_you: [] } });
      }
      if (path === "/areas/area-a") return ok({ key: "area-a" });
      return undefined;
    },
  });
  const { host } = await renderView();
  const summaryReads = () => bridge.requests.filter(({ path }) => path === "/admin/wiki/pages").length;
  const detailReads = () => bridge.requests.filter(({ path }) => /^\/admin\/wiki\/pages\/[^/]+$/.test(path)).length;
  const detailsBefore = detailReads();
  const versionBefore = useStore.getState().memoryVaultVersion;

  holdStaleSummary = true;
  const staleRead = listMemoryArtifactSummaries(config);
  await settle(20);
  const summariesBeforeReset = summaryReads();
  bridge.state.pages.delete(stalePage.pageId);
  bridge.updatePage(page({
    pageId: "page-new",
    content: "---\ntitle: A\n---\n# A\n\nRecovered after gap.\n",
    version: "note-r2",
    repositoryHead: "wiki-head-2",
  }));
  await act(async () => handleAutomationEvent({
    type: "stream_reset",
    reason: "replay_gap",
    seq: 77,
  }));
  await settle(340);

  expect(useStore.getState().memoryVaultVersion).toBe(versionBefore + 1);
  expect(useStore.getState().memoryVaultChanges).toEqual([]);
  expect(useStore.getState().automationStream.statuses).toEqual({});
  expect(summaryReads()).toBe(summariesBeforeReset + 1);
  expect(detailReads()).toBe(detailsBefore + 1);
  expect(bridge.requests.filter(({ path }) => path === "/automations")).toHaveLength(1);
  expect(bridge.requests.filter(({ path }) => path.startsWith("/sessions?"))).toHaveLength(1);
  expect(bridge.requests.filter(({ path }) => path === "/areas")).toHaveLength(1);
  expect(bridge.requests.filter(({ path }) => path === "/areas/overview")).toHaveLength(1);
  expect(bridge.requests.filter(({ path }) => path === "/areas/area-a")).toHaveLength(1);
  expect(host.textContent).toContain("Recovered after gap.");

  staleSummaryGate.resolve();
  await staleRead;
  const summariesBeforeProbe = summaryReads();
  const probe = await readMemoryArtifactDetail(config, stalePage.path);
  expect(probe.artifact.pageId).toBe("page-new");
  expect(summaryReads()).toBe(summariesBeforeProbe);
});

test("changes received during reconciliation form one trailing batch", async () => {
  useStore.setState({ memoryVaultVersion: 0, memoryVaultChanges: [] });
  const summaryGate = deferred();
  let holdNextSummary = false;
  const bridge = installCanonicalMemoryBridge({
    pages: [page()],
    onRequest: async ({ path }) => {
      if (path !== "/admin/wiki/pages" || !holdNextSummary) return undefined;
      holdNextSummary = false;
      await summaryGate.promise;
      return undefined;
    },
  });
  await renderView();
  const summaryReads = () => bridge.requests.filter(({ path }) => path === "/admin/wiki/pages").length;
  const readsBefore = summaryReads();

  holdNextSummary = true;
  await act(async () => useStore.getState().memoryVaultChanged({
    paths: ["topics/first.md"],
    revision: "note-first-r1",
    seq: 120,
  }));
  await settle(20);
  expect(summaryReads()).toBe(readsBefore + 1);

  await act(async () => {
    useStore.getState().memoryVaultChanged({
      paths: ["topics/second.md"],
      revision: "note-second-r1",
      seq: 121,
    });
    useStore.getState().memoryVaultChanged({
      paths: ["topics/third.md"],
      revision: "note-third-r1",
      seq: 122,
    });
  });
  summaryGate.resolve();
  await settle(320);

  expect(summaryReads()).toBe(readsBefore + 2);
});

test("a failed reconciliation remains dirty until the next change", async () => {
  useStore.setState({ memoryVaultVersion: 0, memoryVaultChanges: [] });
  let failNextSummary = false;
  const bridge = installCanonicalMemoryBridge({
    pages: [page()],
    onRequest: ({ path }) => {
      if (path !== "/admin/wiki/pages" || !failNextSummary) return undefined;
      failNextSummary = false;
      return error(503, "temporary list failure");
    },
  });
  const { host } = await renderView();
  const summaryReads = () => bridge.requests.filter(({ path }) => path === "/admin/wiki/pages").length;
  const detailReads = () => bridge.requests.filter(({ path }) => path === "/admin/wiki/pages/page-a").length;
  const readsBefore = summaryReads();
  const detailsBefore = detailReads();

  bridge.updatePage(page({
    content: "---\ntitle: A\n---\n# A\n\nUpdate retained across failure.\n",
    version: "note-r2",
    repositoryHead: "wiki-head-2",
  }));
  failNextSummary = true;
  await act(async () => useStore.getState().memoryVaultChanged({
    paths: ["topics/a.md"],
    revision: "note-r2",
    seq: 130,
  }));
  await settle(240);
  expect(summaryReads()).toBe(readsBefore + 1);
  expect(detailReads()).toBe(detailsBefore);

  await act(async () => useStore.getState().memoryVaultChanged({
    paths: ["topics/second.md"],
    revision: "note-second-r1",
    seq: 131,
  }));
  await settle(300);

  expect(summaryReads()).toBe(readsBefore + 2);
  expect(detailReads()).toBe(detailsBefore + 1);
  expect(host.textContent).toContain("Update retained across failure.");
});

test("a pending canonical write locks navigation and commits once", async () => {
  const gate = deferred();
  const bridge = installCanonicalMemoryBridge({
    pages: [
      page(),
      page({
        pageId: "page-b",
        path: "topics/b.md",
        title: "B",
        content: "# B\n",
        version: "note-b-r1",
      }),
    ],
    onRequest: async ({ method }) => {
      if (method === "PUT") await gate.promise;
      return undefined;
    },
  });
  const { host } = await renderView();
  await shortcut("e");
  await changeDraft(host.querySelector<HTMLTextAreaElement>("textarea")!, "# A\n\nLocked candidate.\n");
  await shortcut("s", host.querySelector<HTMLTextAreaElement>("textarea")!);
  const apply = host.querySelector<HTMLButtonElement>('button[aria-label="Apply changes"]')!;
  await act(async () => apply.click());

  expect(apply.disabled).toBe(true);
  expect(host.querySelector<HTMLButtonElement>('[data-memory-entry="topics/b.md"]')?.disabled).toBe(true);
  await act(async () => apply.click());
  expect(bridge.requests.filter(({ method }) => method === "PUT")).toHaveLength(1);

  await act(async () => gate.resolve());
  await settle(280);
  expect(getDraft("topics/a.md", "note-r1")).toBeNull();
});

test("editor and review retain focus, keyboard help, and long-content contracts", async () => {
  installCanonicalMemoryBridge({ pages: [page()] });
  const { host } = await renderView();
  await shortcut("e");
  const textarea = host.querySelector<HTMLTextAreaElement>("textarea")!;
  expect(document.activeElement).toBe(textarea);
  await changeDraft(textarea, Array.from({ length: 320 }, (_, line) => `line ${line}`).join("\n"));
  const editor = host.querySelector<HTMLElement>("[data-memory-editor]")!;
  expect(editor.textContent).toContain("Cmd/Ctrl+S");
  expect(textarea.getAttribute("aria-describedby")).not.toBeNull();
  await shortcut("s", textarea);

  const review = host.querySelector<HTMLElement>("[data-memory-edit-review]")!;
  expect(document.activeElement).toBe(review);
  expect(review.dataset.reducedMotionReady).toBe("true");
  expect(review.dataset.longContentReady).toBe("true");
  expect(review.querySelector("[data-diff-review] > header")?.className).toContain("flex-wrap");
  expect(review.querySelector("[data-diff-review] > footer")?.className).toContain("flex-wrap");
});

test("canonical history opens the read-only activity diff above its peek", async () => {
  const history: HistoryCommitFixture = {
    commitId: "commit-2",
    parentId: "commit-1",
    actor: "user:desktop",
    origin: "desktop",
    timestamp: "2026-07-13T08:00:00Z",
    changes: [{
      action: "update",
      before: { resourceId: "page-a", path: "topics/a.md", state: "active", versionId: "note-r1" },
      after: { resourceId: "page-a", path: "topics/a.md", state: "active", versionId: "note-r2" },
    }],
    diff: "--- a/topics/a.md\n+++ b/topics/a.md\n@@ -1 +1 @@\n-old durable fact\n+new durable fact",
  };
  installCanonicalMemoryBridge({ pages: [page()], history: { "page-a": [history] } });
  const view = setup();
  view.host.id = "app";
  await act(async () => view.root.render(<ArtifactMemoryView config={config} />));
  await settle(300);

  await act(async () => view.host.querySelector<HTMLButtonElement>('button[aria-label="Open activity"]')?.click());
  await settle(300);
  const activityRow = view.host.querySelector<HTMLButtonElement>(".mw-rec button")!;
  await act(async () => activityRow.click());
  await settle(100);

  const dialog = document.querySelector<HTMLElement>('[role="dialog"][aria-label="Page edit diff"]');
  expect(dialog).not.toBeNull();
  expect(dialog?.textContent).toContain("new durable fact");
  expect(dialog?.textContent).toContain("user:desktop");
  expect(dialog?.querySelector(".mw-dv-switch")).toBeNull();
  expect(document.querySelector<HTMLElement>('[aria-label="Page peek"]')?.closest("[inert]")).not.toBeNull();
});

test("draft identity is exact path plus base revision", () => {
  setDraft("topics/a.md", "r1", "first");
  setDraft("topics/a.md", "r2", "second");
  expect(draftKey("topics/a.md", "r1")).not.toBe(draftKey("topics/a.md", "r2"));
  expect(getDraft("topics/a.md", "r1")).toBe("first");
  expect(getDraft("topics/a.md", "r2")).toBe("second");
});

test("draft store evicts the least-recently-used entry after 50", () => {
  for (let index = 0; index < 50; index += 1) {
    setDraft("topics/a.md", `r${index}`, `draft-${index}`);
  }
  expect(getDraft("topics/a.md", "r0")).toBe("draft-0");
  setDraft("topics/a.md", "r50", "draft-50");
  expect(getDraft("topics/a.md", "r1")).toBeNull();
  expect(getDraft("topics/a.md", "r0")).toBe("draft-0");
});
