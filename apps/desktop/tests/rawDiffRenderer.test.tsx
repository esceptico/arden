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

afterEach(async () => {
  for (const root of roots) await act(async () => root.unmount());
  roots.clear();
  document.documentElement.classList.remove("dark");
  document.body.replaceChildren();
});

test("renders exact raw Markdown locally with wrapping, line numbers, and selectable whitespace", async () => {
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

  const renderer = host.querySelector<HTMLElement>('[data-raw-diff-renderer]')!;
  expect(renderer.dataset.layout).toBe("split");
  expect(renderer.dataset.language).toBe("markdown");
  expect(renderer.dataset.wrap).toBe("true");
  expect(renderer.dataset.lineNumbers).toBe("true");
  expect(renderer.dataset.renderer).toBe("local");
  expect(renderer.dataset.motion).toBe("reduced");
  expect(renderer.getAttribute("aria-label")).toBe("Raw Markdown changes for topics/a.md");
  expect(renderer.style.userSelect).toBe("text");
  expect(renderer.style.transition).toBe("none");
  expect(host.querySelector("diffs-container")).toBeNull();

  const beforePane = host.querySelector<HTMLElement>('[aria-label="Before raw Markdown"]')!;
  const afterPane = host.querySelector<HTMLElement>('[aria-label="After raw Markdown"]')!;
  expect(beforePane.textContent).toContain("scope: user");
  expect(beforePane.textContent).toContain("old  ");
  expect(afterPane.textContent).toContain("new\t");
  expect(beforePane.querySelector('[data-line-number="5"]')).not.toBeNull();
  expect(afterPane.querySelector('[data-line-number="5"]')).not.toBeNull();
  expect(afterPane.querySelector<HTMLElement>('[data-raw-line]')?.style.whiteSpace).toBe("pre-wrap");
});

test("collapses unchanged regions and expands them with keyboard activation", async () => {
  const prefix = Array.from({ length: 20 }, (_, index) => `shared ${index + 1}`);
  const before = [...prefix, "old fact", "tail"].join("\n");
  const after = [...prefix, "new fact", "tail"].join("\n");
  const { host, root } = setup();
  await act(async () => root.render(
    <RawDiffRenderer
      before={{ path: "topic.md", content: before }}
      after={{ path: "topic.md", content: after }}
      layout="split"
    />,
  ));

  const expand = host.querySelector<HTMLButtonElement>('button[aria-label="Show 17 unchanged lines"]')!;
  expect(expand).not.toBeNull();
  expect(host.textContent).not.toContain("shared 10");
  await act(async () => {
    expand.focus();
    expand.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  });
  expect(host.textContent).toContain("shared 10");
  expect(document.activeElement).toBe(expand);
});

test("renders a 5,000-line file with a substantial changed block", async () => {
  const changedBefore = Array.from({ length: 1_000 }, (_, index) => `old changed line ${index + 1}`);
  const changedAfter = Array.from({ length: 1_000 }, (_, index) => `new changed line ${index + 1}`);
  const stable = Array.from({ length: 4_000 }, (_, index) => `stable line ${index + 1001}`);
  const beforeContent = [...changedBefore, ...stable].join("\n");
  const afterContent = [...changedAfter, ...stable].join("\n");
  const startedAt = performance.now();
  const { host, root } = setup();
  await act(async () => root.render(
    <RawDiffRenderer
      before={{ path: "long.md", content: beforeContent }}
      after={{ path: "long.md", content: afterContent }}
      layout="stacked"
    />,
  ));
  const elapsedMs = performance.now() - startedAt;

  const renderer = host.querySelector<HTMLElement>('[data-raw-diff-renderer]')!;
  expect(renderer.dataset.layout).toBe("stacked");
  expect(renderer.dataset.beforeLines).toBe("5000");
  expect(renderer.dataset.afterLines).toBe("5000");
  expect(renderer.dataset.beforeBytes).toBe(String(beforeContent.length));
  expect(renderer.dataset.afterBytes).toBe(String(afterContent.length));
  expect(host.querySelectorAll('[data-change-line="removed"]').length).toBe(1_000);
  expect(host.querySelectorAll('[data-change-line="added"]').length).toBe(1_000);
  expect(host.textContent).toContain("old changed line 1000");
  expect(host.textContent).toContain("new changed line 1000");
  expect(host.textContent).not.toContain("stable line 3000");
  expect(elapsedMs).toBeLessThan(2_000);
});

test("updates theme and layout inputs without motion or selection regressions", async () => {
  const { host, root } = setup();
  await act(async () => root.render(
    <RawDiffRenderer
      before={{ path: "theme.md", content: "old" }}
      after={{ path: "theme.md", content: "new" }}
      layout="split"
      reducedMotion
    />,
  ));
  const renderer = host.querySelector<HTMLElement>('[data-raw-diff-renderer]')!;
  expect(renderer.dataset.theme).toBe("light");
  expect(renderer.dataset.layout).toBe("split");
  expect(renderer.style.getPropertyValue("--diff-review-bg")).toBe("var(--color-bg-main)");

  await act(async () => document.documentElement.classList.add("dark"));
  expect(renderer.dataset.theme).toBe("dark");
  await act(async () => root.render(
    <RawDiffRenderer
      before={{ path: "theme.md", content: "old" }}
      after={{ path: "theme.md", content: "new" }}
      layout="stacked"
      reducedMotion
    />,
  ));
  expect(renderer.dataset.layout).toBe("stacked");
  expect(renderer.style.userSelect).toBe("text");
  expect(renderer.style.transition).toBe("none");
});

test("contains no rejected renderer dependency or import", async () => {
  const manifest = await Bun.file("package.json").text();
  const lockfile = await Bun.file("bun.lock").text();
  const source = await Bun.file("src/components/ui/RawDiffRenderer.tsx").text();
  const rejectedPackage = ["@pierre", "diffs"].join("/");
  expect(manifest).not.toContain(rejectedPackage);
  expect(lockfile).not.toContain(rejectedPackage);
  expect(source).not.toContain(rejectedPackage);
});
