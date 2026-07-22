import { afterEach, beforeEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryFindBar } from "@/features/memory/components/MemoryFindBar";

const mountedRoots = new Set<Root>();

// happy-dom has no CSS Custom Highlight API — stub the registry so paint
// calls are observable instead of throwing.
class FakeHighlight {
  ranges: Range[];
  constructor(...ranges: Range[]) { this.ranges = ranges; }
}

// happy-dom exposes `CSS` via an accessor that can hand back a fresh object
// per read, so properties defined on one instance vanish. Replace the global
// binding with a stable stub for this file and restore it afterwards.
const originalCssDescriptor = Object.getOwnPropertyDescriptor(globalThis, "CSS");
const cssStub = { highlights: new Map<string, FakeHighlight>(), escape: (v: string) => v, supports: () => false };

beforeEach(() => {
  (globalThis as { Highlight?: unknown }).Highlight = FakeHighlight;
  cssStub.highlights = new Map();
  Object.defineProperty(globalThis, "CSS", { configurable: true, value: cssStub });
});

afterEach(async () => {
  for (const root of mountedRoots) await act(async () => root.unmount());
  mountedRoots.clear();
  document.body.replaceChildren();
  if (originalCssDescriptor) Object.defineProperty(globalThis, "CSS", originalCssDescriptor);
});

function setup(content: string) {
  const host = document.createElement("div");
  document.body.append(host);
  const scroller = document.createElement("div");
  scroller.innerHTML = content;
  host.append(scroller);
  const mount = document.createElement("div");
  host.append(mount);
  const root = createRoot(mount);
  mountedRoots.add(root);
  return { host, root, scroller };
}

async function openFind() {
  await act(async () => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "f", metaKey: true, bubbles: true }));
    await new Promise((resolve) => setTimeout(resolve, 30));
  });
}

async function type(host: HTMLElement, value: string) {
  const input = host.querySelector<HTMLInputElement>('input[aria-label="Find in note"]')!;
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  return input;
}

test("Cmd+F opens the bar; a query counts matches and paints highlights", async () => {
  const { host, root, scroller } = setup("<p>alpha beta alpha gamma alpha</p>");
  await act(async () => root.render(<MemoryFindBar scrollerRef={{ current: scroller }} />));
  expect(host.querySelector('[role="search"]')).toBeNull();

  await openFind();
  expect(host.querySelector('[role="search"]')).not.toBeNull();

  await type(host, "alpha");
  expect(host.textContent).toContain("1 of 3");
  const highlights = cssStub.highlights;
  expect(highlights.get("memory-find")?.ranges.length).toBe(3);
  expect(highlights.get("memory-find-active")?.ranges.length).toBe(1);
});

test("Enter advances and wraps; Shift+Enter goes back", async () => {
  const { host, root, scroller } = setup("<p>alpha beta alpha</p>");
  await act(async () => root.render(<MemoryFindBar scrollerRef={{ current: scroller }} />));
  await openFind();
  const input = await type(host, "alpha");

  await act(async () => input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
  expect(host.textContent).toContain("2 of 2");
  await act(async () => input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
  expect(host.textContent).toContain("1 of 2");
  await act(async () => input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", shiftKey: true, bubbles: true })));
  expect(host.textContent).toContain("2 of 2");
});

test("Escape closes only the bar and clears highlights", async () => {
  const { host, root, scroller } = setup("<p>alpha</p>");
  await act(async () => root.render(<MemoryFindBar scrollerRef={{ current: scroller }} />));
  await openFind();
  const input = await type(host, "alpha");

  let surfaceEscapes = 0;
  const onWindowKey = (event: KeyboardEvent) => { if (event.key === "Escape") surfaceEscapes += 1; };
  window.addEventListener("keydown", onWindowKey);
  await act(async () => input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
  window.removeEventListener("keydown", onWindowKey);

  expect(host.querySelector('[role="search"]')).toBeNull();
  expect(surfaceEscapes).toBe(0);
  const highlights = cssStub.highlights;
  expect(highlights.has("memory-find")).toBe(false);
});

test("no matches reads 0 of 0 and paints nothing active", async () => {
  const { host, root, scroller } = setup("<p>alpha</p>");
  await act(async () => root.render(<MemoryFindBar scrollerRef={{ current: scroller }} />));
  await openFind();
  await type(host, "zzz");
  expect(host.textContent).toContain("0 of 0");
  const highlights = cssStub.highlights;
  expect(highlights.get("memory-find")?.ranges.length).toBe(0);
  expect(highlights.has("memory-find-active")).toBe(false);
});
