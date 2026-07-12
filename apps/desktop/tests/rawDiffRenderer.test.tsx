import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { RawDiffRenderer } from "@/components/ui/RawDiffRenderer";

const roots = new Set<Root>();

function setup() {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  roots.add(root);
  return { host, root };
}

async function waitFor(read: () => Element | null) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const value = read();
    if (value) return value;
    await act(async () => new Promise((resolve) => setTimeout(resolve, 10)));
  }
  return null;
}

afterEach(async () => {
  for (const root of roots) await act(async () => root.unmount());
  roots.clear();
  document.body.replaceChildren();
});

test("declares exact raw Markdown presentation without leaking renderer vocabulary", async () => {
  const { host, root } = setup();
  await act(async () => root.render(
    <RawDiffRenderer
      before={{ path: "topics/a.md", content: "---\nscope: user\n---\n# A\nold  " }}
      after={{ path: "topics/a.md", content: "---\nscope: user\n---\n# A\nnew\t" }}
      layout="split"
      reducedMotion
    />,
  ));

  const renderer = host.querySelector<HTMLElement>('[data-raw-diff-renderer]')!;
  expect(renderer.dataset.layout).toBe("split");
  expect(renderer.dataset.language).toBe("markdown");
  expect(renderer.dataset.wrap).toBe("true");
  expect(renderer.dataset.lineNumbers).toBe("true");
  expect(renderer.dataset.collapsedHunks).toBe("true");
  expect(renderer.dataset.workerPool).toBe("disabled");
  expect(renderer.dataset.motion).toBe("reduced");
  expect(renderer.getAttribute("aria-label")).toBe("Raw Markdown changes for topics/a.md");
  expect(renderer.style.userSelect).toBe("text");
  expect(renderer.style.getPropertyValue("--diffs-light-bg")).toBe("var(--color-bg-main)");
  expect(renderer.style.getPropertyValue("--diffs-dark-bg")).toBe("var(--color-bg-main)");
  expect(renderer.textContent).toContain("Exact Markdown diff preserves frontmatter and whitespace");
});

test("mounts the controlled exact renderer with wrapped numbered collapsed hunks", async () => {
  const before = "---\nscope: user\n---\n# A\nold  ";
  const after = "---\nscope: user\n---\n# A\nnew\t";
  const { host, root } = setup();
  await act(async () => root.render(
    <RawDiffRenderer
      before={{ path: "topics/a.md", content: before }}
      after={{ path: "topics/a.md", content: after }}
      layout="split"
      reducedMotion
    />,
  ));

  const pre = await waitFor(() => host.querySelector("diffs-container")?.shadowRoot?.querySelector('pre[data-overflow="wrap"]') ?? null);
  const exact = host.querySelector("diffs-container");
  expect(exact).not.toBeNull();
  const shadow = exact?.shadowRoot;
  expect(shadow).not.toBeNull();
  expect(pre).not.toBeNull();
  expect(pre?.getAttribute("data-diff-type")).toBe("split");
  expect(shadow?.textContent).toContain("scope: user");
  expect(shadow?.textContent).toContain("new\t");
  expect(shadow?.querySelector("[data-line-number-content]")).not.toBeNull();
  expect(shadow?.querySelector('style[data-unsafe-css]')?.textContent).toContain("transition-duration: 0.001ms");
});

test("accepts stacked layout and preserves a 5,000-line Markdown payload", async () => {
  const beforeContent = Array.from({ length: 5_000 }, (_, index) => `line ${index + 1}`).join("\n");
  const afterContent = `${beforeContent}\nline 5001`;
  const { host, root } = setup();
  await act(async () => root.render(
    <RawDiffRenderer
      before={{ path: "long.md", content: beforeContent }}
      after={{ path: "long.md", content: afterContent }}
      layout="stacked"
    />,
  ));

  const renderer = host.querySelector<HTMLElement>('[data-raw-diff-renderer]')!;
  expect(renderer.dataset.layout).toBe("stacked");
  expect(renderer.dataset.beforeLines).toBe("5000");
  expect(renderer.dataset.afterLines).toBe("5001");
  expect(renderer.dataset.beforeBytes).toBe(String(beforeContent.length));
  expect(renderer.dataset.afterBytes).toBe(String(afterContent.length));
  const pre = await waitFor(() => host.querySelector("diffs-container")?.shadowRoot?.querySelector('pre[data-overflow="wrap"]') ?? null);
  expect(pre).not.toBeNull();
  expect(pre?.getAttribute("data-diff-type")).toBe("single");
  const shadow = host.querySelector("diffs-container")?.shadowRoot;
  expect(shadow?.textContent).toContain("line 5001");
  expect(shadow?.querySelector("[data-separator]")).not.toBeNull();
});

test("uses ntrp light and dark theme state across the renderer host", async () => {
  document.documentElement.classList.add("dark");
  const { host, root } = setup();
  await act(async () => root.render(
    <RawDiffRenderer
      before={{ path: "theme.md", content: "old" }}
      after={{ path: "theme.md", content: "new" }}
      layout="split"
    />,
  ));
  const renderer = host.querySelector<HTMLElement>('[data-raw-diff-renderer]')!;
  expect(renderer.dataset.theme).toBe("dark");
  expect(renderer.style.getPropertyValue("--diffs-bg")).toBe("var(--color-bg-main)");
  expect(renderer.style.getPropertyValue("--diffs-fg")).toBe("var(--color-ink)");
  await act(async () => document.documentElement.classList.remove("dark"));
});
