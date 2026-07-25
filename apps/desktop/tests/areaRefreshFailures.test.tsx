import { expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { AreaDetail, AreasOverview } from "@/api/areas";
import { AreaRoom } from "@/features/areas/components/AreaRoom";
import { Home } from "@/features/home/components/Home";
import { getState, setState } from "@/stores";

function setupDom(): { host: HTMLElement; root: Root; restore: () => void } {
  const host = document.createElement("div");
  document.body.append(host);
  return { host, root: createRoot(host), restore: () => host.remove() };
}

async function settleRefresh(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

async function settleContentSwap(): Promise<void> {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 350)); });
}

const allClearOverview: AreasOverview = {
  areas: [],
  focus: [],
  brief: { done: [], in_progress: [], needs_you: [] },
};

const cachedDetail: AreaDetail = {
  key: "area:inbox",
  title: "Inbox",
  autonomy: null,
  page_path: null,
  related: [],
  open_loops: [],
  updated: "2026-07-23T00:00:00Z",
  attention: "ambient",
  interrupts: "asks",
  paused: false,
  agent: null,
  asks: [],
  sessions: [],
  automations: [],
  work: { outcomes: [], work_items: [], events: [] },
};

test("Home keeps cached data visible, marks it stale, and retries a failed refresh", async () => {
  const previous = getState();
  const originalFetch = globalThis.fetch;
  let requests = 0;
  globalThis.fetch = async () => {
    requests += 1;
    throw new Error("offline");
  };
  setState({
    connected: true,
    automations: [],
    areas: { ...previous.areas, overview: allClearOverview, overviewPhase: "ready" },
  });

  const { host, root, restore } = setupDom();
  try {
    await act(async () => {
      root.render(<Home />);
      await settleRefresh();
    });
    // The stale greeting commits after the preceding label exits.
    await settleContentSwap();

    const notice = host.querySelector('[role="alert"]');
    expect(notice?.textContent).toContain("Desk data is stale");
    expect(notice?.textContent).toContain("Showing the last successful overview");
    expect(host.querySelector("h1")?.textContent).toBe("Last check was all clear");
    expect(host.textContent).toContain("No requests in the last successful check");
    expect(host.textContent).not.toContain("All requests handled");

    const retry = Array.from(host.querySelectorAll("button")).find((button) => button.textContent === "Retry");
    await act(async () => {
      retry?.click();
      await settleRefresh();
    });
    expect(requests).toBe(2);
  } finally {
    await act(async () => root.unmount());
    restore();
    globalThis.fetch = originalFetch;
    setState({ connected: previous.connected, automations: previous.automations, areas: previous.areas });
  }
});

test("AreaRoom retains cached detail, labels it stale, and retries a failed refresh", async () => {
  const previous = getState();
  const originalFetch = globalThis.fetch;
  let requests = 0;
  globalThis.fetch = async () => {
    requests += 1;
    throw new Error("offline");
  };
  setState({
    areas: {
      ...previous.areas,
      detailByKey: { ...previous.areas.detailByKey, [cachedDetail.key]: cachedDetail },
      detailPhaseByKey: { ...previous.areas.detailPhaseByKey, [cachedDetail.key]: "ready" },
    },
  });

  const { host, root, restore } = setupDom();
  try {
    await act(async () => {
      root.render(<AreaRoom areaKey={cachedDetail.key} />);
      await settleRefresh();
    });

    const notice = host.querySelector('[role="alert"]');
    expect(notice?.textContent).toContain("Area data is stale");
    expect(notice?.textContent).toContain("Showing the last successful Area");
    expect(host.textContent).toContain(cachedDetail.title);

    const retry = Array.from(host.querySelectorAll("button")).find((button) => button.textContent === "Retry");
    await act(async () => {
      retry?.click();
      await settleRefresh();
    });
    expect(requests).toBe(2);
  } finally {
    await act(async () => root.unmount());
    restore();
    globalThis.fetch = originalFetch;
    setState({ areas: previous.areas });
  }
});
