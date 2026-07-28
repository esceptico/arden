import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { AppConfig } from "@/api/core";
import { ArtifactMemoryView } from "@/features/memory/components/ArtifactMemoryView";
import { clearDrafts, draftKey, getDraft, setDraft } from "@/features/memory/lib/draftStore";
import { splitFrontmatter } from "@/features/memory/lib/format";
import { useStore } from "@/stores";
import {
  installCanonicalMemoryBridge,
  type HistoryCommitFixture,
  type WikiPageFixture,
} from "./helpers/canonicalMemoryBridge";

const config: AppConfig = { serverUrl: "http://localhost:6877", apiKey: "test-key" };
const originalDesktop = window.ardenDesktop;
const originalVaultVersion = useStore.getState().memoryVaultVersion;
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
  useStore.setState({ memoryVaultVersion: originalVaultVersion, memoryVaultChanges: [] });
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
    reviewRequired: false,
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
    reviewRequired: false,
    seq: 91,
  }));
  await settle(280);

  expect(host.querySelector('[aria-label="Three-way conflict for topics/a.md"]')).not.toBeNull();
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="Back to draft"]')?.click());
  await settle(220);
  expect(host.querySelector<HTMLTextAreaElement>("textarea")?.value).toBe(draft);
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
