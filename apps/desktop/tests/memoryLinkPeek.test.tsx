import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { Markdown } from "@/components/ui/Markdown";
import { ChatWikiLinks } from "@/features/chat/components/ChatWikiLinks";
import { useStore } from "@/stores";
import { installCanonicalMemoryBridge } from "./helpers/canonicalMemoryBridge";

// Interactive contract of the chat memory-link peek: hovering an agent-cited
// path opens the preview card with the page's title and first lines; clicking
// hands over to the Memory view at that exact path.

const originalDesktop = window.ardenDesktop;
const roots = new Set<Root>();

function mount(node: React.ReactNode): HTMLElement {
  let app = document.querySelector<HTMLElement>("#app");
  if (!app) {
    app = document.createElement("div");
    app.id = "app";
    document.body.appendChild(app);
  }
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  roots.add(root);
  act(() => root.render(node));
  return container;
}

function hover(element: Element): void {
  act(() => {
    element.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
  });
}

async function settle(ms = 0): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, ms));
  });
}

async function settleUntil(predicate: () => boolean, timeout = 1_500): Promise<void> {
  const started = Date.now();
  while (!predicate() && Date.now() - started < timeout) await settle(50);
}

afterEach(async () => {
  for (const root of roots) await act(async () => root.unmount());
  roots.clear();
  window.ardenDesktop = originalDesktop;
  useStore.setState({ memoryOpen: false, memoryTargetPath: null, memoryTargetAnchor: null });
  document.body.replaceChildren();
});

test("clicking an anchored citation carries the heading into the memory target", () => {
  installCanonicalMemoryBridge({ pages: [] });
  const container = mount(
    <ChatWikiLinks>
      <Markdown content="See [Sleep](peek/health.md#sleep-quality)." />
    </ChatWikiLinks>,
  );
  const link = container.querySelector('a[data-memory-path="peek/health.md"]');
  if (!link) throw new Error("memory link did not render");
  act(() => {
    link.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  expect(useStore.getState().memoryOpen).toBe(true);
  expect(useStore.getState().memoryTargetPath).toBe("peek/health.md");
  expect(useStore.getState().memoryTargetAnchor).toBe("sleep-quality");
});

test("hovering a cited path opens the peek card; clicking opens Memory there", async () => {
  installCanonicalMemoryBridge({
    pages: [{
      pageId: "page-peek-health",
      path: "peek/health.md",
      title: "Health",
      content: "# Health\n\nSleep is trending up this month.",
      updatedAt: "2026-08-01T08:00:00Z",
    }],
  });
  const container = mount(
    <ChatWikiLinks>
      <Markdown content="See [Health](peek/health.md) for details." />
    </ChatWikiLinks>,
  );
  const link = container.querySelector('a[data-memory-path="peek/health.md"]');
  if (!link) throw new Error("memory link did not render");

  hover(link);
  await settleUntil(() =>
    document.querySelector('[role="tooltip"]')?.textContent?.includes("Sleep is trending up") === true);
  const card = document.querySelector('[role="tooltip"]');
  expect(card?.textContent).toContain("Health");
  expect(card?.textContent).toContain("Sleep is trending up this month.");
  expect(card?.textContent).toContain("peek/health.md");

  act(() => {
    link.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  expect(useStore.getState().memoryOpen).toBe(true);
  expect(useStore.getState().memoryTargetPath).toBe("peek/health.md");
  await settleUntil(() => document.querySelector('[role="tooltip"]') === null);
  expect(document.querySelector('[role="tooltip"]')).toBeNull();
});

test("a dangling path degrades to a visible missing-page card", async () => {
  installCanonicalMemoryBridge({ pages: [] });
  const container = mount(
    <ChatWikiLinks>
      <Markdown content="See [Ghost](peek/ghost.md)." />
    </ChatWikiLinks>,
  );
  const link = container.querySelector('a[data-memory-path="peek/ghost.md"]');
  if (!link) throw new Error("memory link did not render");

  hover(link);
  await settleUntil(() =>
    document.querySelector('[role="tooltip"]')?.textContent?.includes("No page at this path yet.") === true);
  expect(document.querySelector('[role="tooltip"]')?.textContent).toContain("No page at this path yet.");
});

test("leaving the link closes the card after the grace delay", async () => {
  installCanonicalMemoryBridge({
    pages: [{
      pageId: "page-peek-notes",
      path: "peek/notes.md",
      title: "Notes",
      content: "Some notes.",
    }],
  });
  const container = mount(
    <ChatWikiLinks>
      <Markdown content="See [Notes](peek/notes.md)." />
    </ChatWikiLinks>,
  );
  const link = container.querySelector('a[data-memory-path="peek/notes.md"]');
  if (!link) throw new Error("memory link did not render");

  hover(link);
  await settleUntil(() => document.querySelector('[role="tooltip"]') !== null);
  expect(document.querySelector('[role="tooltip"]')).not.toBeNull();

  act(() => {
    link.dispatchEvent(new MouseEvent("mouseout", { bubbles: true }));
  });
  await settleUntil(() => document.querySelector('[role="tooltip"]') === null);
  expect(document.querySelector('[role="tooltip"]')).toBeNull();
});
