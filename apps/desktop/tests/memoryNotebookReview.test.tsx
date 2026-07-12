import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { AppConfig } from "@/api/core";
import { ArtifactMemoryView } from "@/features/memory/components/ArtifactMemoryView";
import { useStore } from "@/stores";

const config: AppConfig = { serverUrl: "http://localhost:6877", apiKey: "test-key" };
const originalDesktop = window.ntrpDesktop;
const originalVaultVersion = useStore.getState().memoryVaultVersion;
const mountedRoots = new Set<Root>();

const base = {
  kind: "topic",
  type: "file",
  scope: { kind: "user", key: null },
  snippet: null,
  summary: null,
  revision: "sha256:stable",
  record_count: 0,
  generated: false,
  editable: true,
  readonly_reason: null,
  updated_at: "2026-07-13T08:00:00Z",
  labels: [],
  source: null,
};

function artifact(path: string, title: string, summary: string | null = null, extra: Record<string, unknown> = {}) {
  return { ...base, path, title, summary, directory: path.includes("/") ? path.split("/")[0] : "", ...extra };
}

function managed(rows: Array<[string, string]>): string {
  const body = rows.length
    ? rows.map(([path, description]) => `- ${path} — ${description} <!-- ntrp:path=${encodeURIComponent(path)} -->`).join("\n")
    : "_No entries._";
  return `# Index\n\n<!-- ntrp:index:start -->\n${body}\n<!-- ntrp:index:end -->\n`;
}

function response(data: unknown) {
  return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data, text: "" };
}

function failure(message: string) {
  return { ok: false, status: 500, statusText: "Error", contentType: "application/json", data: { detail: message }, text: "" };
}

function detail(raw: ReturnType<typeof artifact>, content = `${raw.title} prose`, timeline: unknown[] = []) {
  return { ...raw, content, editable_content: content, timeline, frontmatter: {} };
}

function installBridge(handler: (
  path: string,
  method: string,
) => Promise<ReturnType<typeof response> | ReturnType<typeof failure>> | ReturnType<typeof response> | ReturnType<typeof failure>) {
  const requests: Array<{ path: string; method: string }> = [];
  window.ntrpDesktop = {
    api: {
      request: async (_config, request) => {
        const method = request.method ?? "GET";
        requests.push({ path: request.path, method });
        return handler(request.path, method);
      },
    },
  } as Window["ntrpDesktop"];
  return requests;
}

function setupDom(): { host: HTMLElement; root: Root } {
  const host = document.createElement("div");
  host.style.height = "800px";
  document.body.append(host);
  const root = createRoot(host);
  mountedRoots.add(root);
  return { host, root };
}

async function settle(delay = 0) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, delay));
  });
}

async function typeInto(input: HTMLInputElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

afterEach(async () => {
  for (const root of mountedRoots) await act(async () => root.unmount());
  mountedRoots.clear();
  window.ntrpDesktop = originalDesktop;
  useStore.setState({ memoryVaultVersion: originalVaultVersion });
  document.body.replaceChildren();
});

test("rail follows managed index order, descriptions, nesting, and precise reserved paths", async () => {
  const rows = [
    artifact("index.md", "Index", null, { generated: true, editable: false }),
    artifact("lab/README.md", "Lab guide"),
    artifact("lab/index.md", "Shadowed lab index"),
    artifact("lab/first.md", "First"),
    artifact("lab/second.md", "Second"),
    artifact("lab/sub/index.md", "Sub index"),
    artifact("lab/sub/nested.md", "Nested"),
    artifact("empty/README.md", "Empty guide"),
    artifact("research/index.md", "Research index"),
    artifact("research/health.md", "Health notes"),
    artifact("research/AGENTS.md", "Agent notes"),
    artifact("research/changelog/index.md", "Research changelog"),
    artifact("research/changelog/note.md", "Change note"),
    artifact("alpha.md", "Alpha"),
    artifact("zeta.md", "Zeta"),
    artifact("topics/dex.md", "Dex"),
    artifact("health.md", "Generated health", null, { generated: true, editable: false }),
    artifact("lab/raw/secret.md", "Reserved raw"),
  ];
  const documents = new Map([
    ["index.md", managed([
      ["lab/", "Lab description from root index"],
      ["zeta.md", "Zeta description from root index"],
      ["empty/", "A described directory with no notes"],
      ["research/", "User research, including ordinary health and changelog notes"],
      ["alpha.md", "Alpha description from root index"],
    ])],
    ["lab/README.md", managed([
      ["second.md", "Second comes first"],
      ["sub/", "Nested lab work"],
      ["first.md", "First comes last"],
    ])],
    ["lab/index.md", managed([["first.md", "This index must lose to README"]])],
    ["lab/sub/index.md", managed([["nested.md", "Nested managed note"]])],
    ["empty/README.md", managed([])],
    ["research/index.md", managed([
      ["health.md", "A legitimate user health note"],
      ["AGENTS.md", "A legitimate nested agent note"],
      ["changelog/", "A legitimate nested changelog directory"],
    ])],
    ["research/changelog/index.md", managed([["note.md", "A legitimate nested changelog note"]])],
  ]);

  const requests = installBridge((path) => {
    if (path === "/admin/memory/artifacts") return response({ artifacts: rows });
    if (path.startsWith("/admin/memory/items")) return response({ items: [], limit: 100 });
    const artifactPath = decodeURIComponent(path.replace("/admin/memory/artifacts/", ""));
    const raw = rows.find((item) => item.path === artifactPath)!;
    return response({ artifact: detail(raw, documents.get(artifactPath) ?? `${raw.title} prose`) });
  });
  const { host, root } = setupDom();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(400);

  const rail = host.querySelector<HTMLElement>('[data-memory-zone="rail"]')!;
  const lab = rail.querySelector<HTMLElement>('[data-memory-directory="lab"]')!;
  const sub = rail.querySelector<HTMLElement>('[data-memory-directory="lab/sub"]')!;
  const empty = rail.querySelector<HTMLElement>('[data-memory-directory="empty"]')!;
  const research = rail.querySelector<HTMLElement>('[data-memory-directory="research"]')!;
  expect(lab.textContent).toContain("Lab description from root index");
  expect(empty.textContent).toContain("A described directory with no notes");
  expect(lab.contains(sub)).toBe(true);
  expect(research.textContent).toContain("Health notes");
  expect(research.textContent).toContain("Agent notes");
  expect(research.textContent).toContain("Change note");
  expect(rail.textContent).not.toContain("Reserved raw");
  expect(rail.textContent).not.toContain("Generated health");

  const rootOrder = ["lab", "zeta.md", "empty", "research", "alpha.md"]
    .map((entry) => rail.querySelector<HTMLElement>(`[data-memory-entry="${entry}"]`)!);
  for (let index = 1; index < rootOrder.length; index += 1) {
    expect(rootOrder[index - 1]!.compareDocumentPosition(rootOrder[index]!) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
  }
  const labOrder = ["lab/second.md", "lab/sub", "lab/first.md"]
    .map((entry) => lab.querySelector<HTMLElement>(`[data-memory-entry="${entry}"]`)!);
  for (let index = 1; index < labOrder.length; index += 1) {
    expect(labOrder[index - 1]!.compareDocumentPosition(labOrder[index]!) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
  }
  expect(host.querySelector('[data-memory-zone="workspace"] h1')?.textContent).toBe("Second");
  expect(requests.some((request) => request.path.endsWith("/index.md"))).toBe(true);
  expect(requests.some((request) => request.path.endsWith("/lab/README.md"))).toBe(true);
  expect(requests.some((request) => request.path.endsWith("/lab/index.md"))).toBe(false);

  const files = rail.querySelector<HTMLDetailsElement>('details[data-memory-files]')!;
  await act(async () => files.querySelector("summary")?.click());
  expect(files.textContent).toContain("Dex");
});

test("search is debounced to server q and uses full-content result summaries", async () => {
  const index = artifact("index.md", "Index", null, { generated: true, editable: false });
  const me = artifact("me.md", "Me");
  const found = artifact("found.md", "Found", "Matched inside the full note body");
  const requests = installBridge((path) => {
    if (path === "/admin/memory/artifacts") return response({ artifacts: [index, me] });
    if (path === "/admin/memory/artifacts?q=deep-body-term") return response({ artifacts: [found] });
    const artifactPath = decodeURIComponent(path.replace("/admin/memory/artifacts/", ""));
    const raw = artifactPath === "index.md" ? index : me;
    return response({ artifact: detail(raw, artifactPath === "index.md" ? managed([["me.md", "Profile from index"]]) : "Me prose") });
  });
  const { host, root } = setupDom();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(220);
  const input = host.querySelector<HTMLInputElement>('input[aria-label="Search memory notes…"]')!;
  await typeInto(input, "deep-body-term");
  await settle(220);

  expect(requests.some((request) => request.path === "/admin/memory/artifacts?q=deep-body-term")).toBe(true);
  expect(host.querySelector('[data-memory-zone="rail"]')?.textContent).toContain("Matched inside the full note body");
});

test("stale server search cannot overwrite a newer query", async () => {
  const index = artifact("index.md", "Index", null, { generated: true, editable: false });
  const me = artifact("me.md", "Me");
  const alpha = artifact("alpha.md", "Alpha result", "Old alpha result");
  const beta = artifact("beta.md", "Beta result", "Current beta result");
  let releaseAlpha: (() => void) | null = null;
  const alphaGate = new Promise<void>((resolve) => { releaseAlpha = resolve; });
  installBridge(async (path) => {
    if (path === "/admin/memory/artifacts") return response({ artifacts: [index, me] });
    if (path === "/admin/memory/artifacts?q=alpha") {
      await alphaGate;
      return response({ artifacts: [alpha] });
    }
    if (path === "/admin/memory/artifacts?q=beta") return response({ artifacts: [beta] });
    const artifactPath = decodeURIComponent(path.replace("/admin/memory/artifacts/", ""));
    const raw = artifactPath === "index.md" ? index : me;
    return response({ artifact: detail(raw, artifactPath === "index.md" ? managed([["me.md", "Profile"]]) : "Me prose") });
  });
  const { host, root } = setupDom();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(220);
  const input = host.querySelector<HTMLInputElement>('input[aria-label="Search memory notes…"]')!;
  await typeInto(input, "alpha");
  await settle(220);
  await typeInto(input, "beta");
  await settle(220);
  releaseAlpha?.();
  await settle();

  const railText = host.querySelector('[data-memory-zone="rail"]')?.textContent ?? "";
  expect(railText).toContain("Current beta result");
  expect(railText).not.toContain("Old alpha result");
});

test("vault change refetches selected timeline even when page revision is unchanged", async () => {
  useStore.setState({ memoryVaultVersion: 0 });
  const index = artifact("index.md", "Index", null, { generated: true, editable: false });
  const me = artifact("me.md", "Me");
  let meReads = 0;
  installBridge((path) => {
    if (path === "/admin/memory/artifacts") return response({ artifacts: [index, me] });
    const artifactPath = decodeURIComponent(path.replace("/admin/memory/artifacts/", ""));
    if (artifactPath === "index.md") return response({ artifact: detail(index, managed([["me.md", "Profile"]])) });
    meReads += 1;
    const text = meReads === 1 ? "Old evidence" : "Fresh evidence";
    return response({ artifact: detail(me, "Me prose", [{
      id: "record-me", text, kind: "fact", date: "2026-07-13", src: "chat", pinned: false, superseded: false,
    }]) });
  });
  const { host, root } = setupDom();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(220);
  expect(host.textContent).toContain("Old evidence");

  await act(async () => useStore.getState().memoryVaultChanged());
  await settle(220);
  expect(meReads).toBe(2);
  expect(host.textContent).toContain("Fresh evidence");
  expect(host.textContent).not.toContain("Old evidence");
});

test("newer vault reload wins over an older rebuild response", async () => {
  useStore.setState({ memoryVaultVersion: 0 });
  const index = artifact("index.md", "Index", null, { generated: true, editable: false });
  const initial = artifact("me.md", "Initial Me");
  const fresh = artifact("me.md", "Fresh Me");
  const stale = artifact("me.md", "Stale Rebuild Me");
  let listReads = 0;
  let releaseRebuild: (() => void) | null = null;
  const rebuildGate = new Promise<void>((resolve) => { releaseRebuild = resolve; });
  installBridge(async (path, method) => {
    if (path === "/admin/memory/artifacts/rebuild" && method === "POST") {
      await rebuildGate;
      return response({ artifacts: [index, stale] });
    }
    if (path === "/admin/memory/artifacts") {
      listReads += 1;
      return response({ artifacts: [index, listReads === 1 ? initial : fresh] });
    }
    const artifactPath = decodeURIComponent(path.replace("/admin/memory/artifacts/", ""));
    const raw = artifactPath === "index.md" ? index : listReads === 1 ? initial : fresh;
    return response({ artifact: detail(raw, artifactPath === "index.md" ? managed([["me.md", "Profile"]]) : "Me prose") });
  });
  const { host, root } = setupDom();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(220);

  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Reload memory notes"]')?.click());
  await act(async () => useStore.getState().memoryVaultChanged());
  await settle(100);
  releaseRebuild?.();
  await settle(220);

  expect(host.querySelector('[data-memory-zone="workspace"] h1')?.textContent).toBe("Fresh Me");
  expect(host.textContent).not.toContain("Stale Rebuild Me");
});

test("raw diagnostic manages focus and Escape restores its trigger", async () => {
  const index = artifact("index.md", "Index", null, { generated: true, editable: false });
  const me = artifact("me.md", "Me");
  installBridge((path) => {
    if (path === "/admin/memory/artifacts") return response({ artifacts: [index, me] });
    if (path.startsWith("/admin/memory/items")) return response({ items: [], limit: 100 });
    const artifactPath = decodeURIComponent(path.replace("/admin/memory/artifacts/", ""));
    const raw = artifactPath === "index.md" ? index : me;
    return response({ artifact: detail(raw, artifactPath === "index.md" ? managed([["me.md", "Profile"]]) : "Me prose") });
  });
  const { host, root } = setupDom();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(220);
  const trigger = host.querySelector<HTMLButtonElement>('button[aria-label="Open raw records diagnostic"]')!;

  await act(async () => trigger.click());
  await settle();
  const heading = host.querySelector<HTMLElement>('section[aria-label="Raw records diagnostic"] h2')!;
  expect(document.activeElement === heading).toBe(true);
  await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Close raw records diagnostic"]')?.click());
  await settle();
  expect(document.activeElement === trigger).toBe(true);

  await act(async () => trigger.click());
  await settle();
  await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
  await settle();
  expect(host.querySelector('section[aria-label="Raw records diagnostic"]')).toBeNull();
  expect(document.activeElement === trigger).toBe(true);
});

test("root index failure blocks silent file demotion and retry recovers", async () => {
  const index = artifact("index.md", "Index", null, { generated: true, editable: false });
  const me = artifact("me.md", "Me");
  let failIndex = true;
  installBridge((path) => {
    if (path === "/admin/memory/artifacts") return response({ artifacts: [index, me] });
    const artifactPath = decodeURIComponent(path.replace("/admin/memory/artifacts/", ""));
    if (artifactPath === "index.md" && failIndex) return failure("Index temporarily unavailable");
    const raw = artifactPath === "index.md" ? index : me;
    return response({ artifact: detail(raw, artifactPath === "index.md" ? managed([["me.md", "Profile"]]) : "Me prose") });
  });
  const { host, root } = setupDom();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(220);

  const alert = host.querySelector<HTMLElement>('[data-memory-zone="rail"] [role="alert"]');
  expect(alert?.textContent).toContain("Index temporarily unavailable");
  expect(host.querySelector('[data-memory-entry="me.md"]')).toBeNull();
  expect(host.querySelector("details[data-memory-files]")).toBeNull();

  failIndex = false;
  await act(async () => Array.from(alert?.querySelectorAll("button") ?? []).find((button) => button.textContent === "Retry")?.click());
  await settle(220);
  expect(host.querySelector('[data-memory-entry="me.md"]')).not.toBeNull();
  expect(host.querySelector('[data-memory-zone="rail"] [role="alert"]')).toBeNull();
});

test("nested index failure preserves its last-good hierarchy and retry refreshes it", async () => {
  useStore.setState({ memoryVaultVersion: 0 });
  const index = artifact("index.md", "Index", null, { generated: true, editable: false });
  const guideV1 = artifact("lab/README.md", "Lab", null, { revision: "sha256:lab-v1" });
  const guideV2 = artifact("lab/README.md", "Lab", null, { revision: "sha256:lab-v2" });
  const first = artifact("lab/first.md", "First");
  const second = artifact("lab/second.md", "Second");
  let listReads = 0;
  let failNested = false;
  installBridge((path) => {
    if (path === "/admin/memory/artifacts") {
      listReads += 1;
      return response({ artifacts: [index, listReads === 1 ? guideV1 : guideV2, first, second] });
    }
    const artifactPath = decodeURIComponent(path.replace("/admin/memory/artifacts/", ""));
    if (artifactPath === "lab/README.md" && failNested) return failure("Lab index unavailable");
    if (artifactPath === "index.md") return response({ artifact: detail(index, managed([["lab/", "Lab notes"]])) });
    if (artifactPath === "lab/README.md") {
      const guide = listReads === 1 ? guideV1 : guideV2;
      const rows: Array<[string, string]> = listReads === 1
        ? [["first.md", "First note"]]
        : [["second.md", "Second note"]];
      return response({ artifact: detail(guide, managed(rows)) });
    }
    const raw = artifactPath === "lab/first.md" ? first : second;
    return response({ artifact: detail(raw) });
  });
  const { host, root } = setupDom();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(220);
  expect(host.querySelector('[data-memory-directory="lab"] [data-memory-entry="lab/first.md"]')).not.toBeNull();

  failNested = true;
  await act(async () => useStore.getState().memoryVaultChanged());
  await settle(220);
  const alert = host.querySelector<HTMLElement>('[data-memory-zone="rail"] [role="alert"]');
  expect(alert?.textContent).toContain("Lab index unavailable");
  expect(host.querySelector('[data-memory-directory="lab"] [data-memory-entry="lab/first.md"]')).not.toBeNull();

  failNested = false;
  await act(async () => Array.from(alert?.querySelectorAll("button") ?? []).find((button) => button.textContent === "Retry")?.click());
  await settle(220);
  expect(host.querySelector('[data-memory-directory="lab"] [data-memory-entry="lab/second.md"]')).not.toBeNull();
  expect(host.querySelector('[data-memory-directory="lab"] [data-memory-entry="lab/first.md"]')).toBeNull();
  expect(host.querySelector('[data-memory-zone="rail"] [role="alert"]')).toBeNull();
});

test("navigating from raw diagnostics focuses the selected note instead of the raw trigger", async () => {
  const index = artifact("index.md", "Index", null, { generated: true, editable: false });
  const first = artifact("first.md", "First");
  const second = artifact("second.md", "Second");
  installBridge((path) => {
    if (path === "/admin/memory/artifacts") return response({ artifacts: [index, first, second] });
    if (path.startsWith("/admin/memory/items")) return response({ items: [], limit: 100 });
    const artifactPath = decodeURIComponent(path.replace("/admin/memory/artifacts/", ""));
    const raw = artifactPath === "index.md" ? index : artifactPath === "first.md" ? first : second;
    return response({ artifact: detail(raw, artifactPath === "index.md"
      ? managed([["first.md", "First note"], ["second.md", "Second note"]])
      : `${raw.title} prose`) });
  });
  const { host, root } = setupDom();
  await act(async () => root.render(<ArtifactMemoryView config={config} />));
  await settle(220);
  const trigger = host.querySelector<HTMLButtonElement>('button[aria-label="Open raw records diagnostic"]')!;
  await act(async () => trigger.click());
  await settle();

  const secondButton = host.querySelector<HTMLButtonElement>('[data-memory-entry="second.md"]')!;
  secondButton.focus();
  await act(async () => secondButton.click());
  await settle();
  expect(host.querySelector('section[aria-label="Raw records diagnostic"]')).toBeNull();
  expect(document.activeElement === secondButton).toBe(true);
  expect(document.activeElement === trigger).toBe(false);
});
