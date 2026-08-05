import { expect, test } from "bun:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { ApiError, type AppConfig } from "@/api/core";
import { listMemoryArtifactSummaries, readMemoryArtifactDetail } from "@/api/memoryArtifacts";
import { useWikiRenameApprovals } from "@/features/memory/hooks/useWikiRenameApprovals";
import { installCanonicalMemoryBridge, ok, type WikiPageFixture } from "./helpers/canonicalMemoryBridge";

const config: AppConfig = { serverUrl: "http://localhost:6877", apiKey: "rename-cache-test" };

function page(overrides: Partial<WikiPageFixture> = {}): WikiPageFixture {
  return {
    pageId: "page-a",
    path: "topics/a.md",
    title: "A",
    content: "# A\n",
    version: "page-a:v1",
    repositoryHead: "head-1",
    metadata: {},
    ...overrides,
  };
}

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((accept) => {
    resolve = accept;
  });
  return { promise, resolve };
}

test("an accepted rename invalidates Memory cache even after the hook unmounts", async () => {
  const originalDesktop = window.ardenDesktop;
  const renameGate = deferred();
  const renamed = page({ path: "topics/renamed.md", title: "Renamed", version: "page-a:v2" });
  const bridge = installCanonicalMemoryBridge({
    pages: [page()],
    onRequest: async ({ path, method }, state) => {
      if (path !== "/admin/wiki/rename-approvals" || method !== "POST") return undefined;
      await renameGate.promise;
      state.pages.set(renamed.pageId, renamed);
      return ok({
        status: "accepted",
        approval: null,
        commit_id: "head-2",
        replacement_approval_id: null,
      });
    },
  });
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  let requestRename: ReturnType<typeof useWikiRenameApprovals>["request"] | null = null;

  function Harness() {
    const approvals = useWikiRenameApprovals(config, () => undefined);
    requestRename = approvals.request;
    return null;
  }

  try {
    await listMemoryArtifactSummaries(config);
    await act(async () => root.render(<Harness />));
    await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));

    let pending!: Promise<void>;
    await act(async () => {
      pending = requestRename!({ pageId: "page-a", newPath: renamed.path, newTitle: renamed.title });
      await Promise.resolve();
    });
    await act(async () => root.unmount());
    renameGate.resolve();
    await pending;

    const stalePath = await readMemoryArtifactDetail(config, "topics/a.md").catch((reason) => reason);
    expect(stalePath).toBeInstanceOf(ApiError);
    expect(stalePath.status).toBe(404);
    expect(bridge.requests.filter(({ path }) => path === "/admin/wiki/pages")).toHaveLength(2);
  } finally {
    window.ardenDesktop = originalDesktop;
    if (host.isConnected) host.remove();
  }
});
