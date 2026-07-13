import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { RawDiffRenderer } from "@/components/ui/RawDiffRenderer";
import { buildDiffLineGroups } from "@/components/ui/diffReviewTypes";

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

  const beforeCells = Array.from(host.querySelectorAll<HTMLElement>('[data-side="before"]'));
  const afterCells = Array.from(host.querySelectorAll<HTMLElement>('[data-side="after"]'));
  expect(beforeCells.map((cell) => cell.textContent).join("\n")).toContain("scope: user");
  expect(beforeCells.map((cell) => cell.textContent).join("\n")).toContain("old  ");
  expect(afterCells.map((cell) => cell.textContent).join("\n")).toContain("new\t");
  expect(host.querySelector('[data-side="before"] [data-line-number="5"]')).not.toBeNull();
  expect(host.querySelector('[data-side="after"] [data-line-number="5"]')).not.toBeNull();
  expect(host.querySelector<HTMLElement>('[data-side="after"] [data-raw-line]')?.style.whiteSpace).toBe("pre-wrap");
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
  expect(expand.closest('[role="cell"]')?.getAttribute("aria-colspan")).toBe("2");
  expect(host.textContent).not.toContain("shared 10");
  await act(async () => {
    expand.focus();
    expand.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  });
  expect(host.textContent).toContain("shared 10");
  expect(document.activeElement).toBe(expand);
});

test("preserves repeated unchanged lines as ordered context", async () => {
  const { host, root } = setup();
  await act(async () => root.render(
    <RawDiffRenderer
      before={{ path: "repeated.md", content: "old A\nsame\nsame\nsame\nold B" }}
      after={{ path: "repeated.md", content: "new A\nsame\nsame\nsame\nnew B" }}
      layout="split"
    />,
  ));

  const contextRows = host.querySelectorAll('[data-paired-row][data-context-row="true"]');
  expect(contextRows.length).toBe(3);
  for (const row of contextRows) {
    expect(row.querySelector('[data-side="before"]')?.textContent).toContain("same");
    expect(row.querySelector('[data-side="after"]')?.textContent).toContain("same");
    expect(row.querySelector("[data-change-line]")).toBeNull();
  }
});

test("split rows align one-sided deletions and additions with explicit placeholders", async () => {
  const { host, root } = setup();
  await act(async () => root.render(
    <RawDiffRenderer
      before={{ path: "alignment.md", content: "alpha\nremoved only\nshared\nomega" }}
      after={{ path: "alignment.md", content: "alpha\nshared\nadded one\nadded two\nomega" }}
      layout="split"
    />,
  ));

  const rows = Array.from(host.querySelectorAll<HTMLElement>("[data-paired-row]"));
  const deletion = rows.find((row) => row.textContent?.includes("removed only"));
  expect(deletion).not.toBeUndefined();
  expect(deletion?.querySelector('[data-change-line="removed"]')).not.toBeNull();
  expect(deletion?.querySelector('[data-placeholder="after"]')).not.toBeNull();

  const addition = rows.find((row) => row.textContent?.includes("added one"));
  expect(addition).not.toBeUndefined();
  expect(addition?.querySelector('[data-placeholder="before"]')).not.toBeNull();
  expect(addition?.querySelector('[data-change-line="added"]')).not.toBeNull();

  const omega = rows.find((row) => row.textContent?.includes("omega"));
  expect(omega).not.toBeUndefined();
  expect(omega?.querySelector('[data-side="before"]')?.textContent).toContain("omega");
  expect(omega?.querySelector('[data-side="after"]')?.textContent).toContain("omega");
});

test("split cells with unequal wrapping share one logical grid row", async () => {
  const long = "new " + "wrapped content ".repeat(30);
  const { host, root } = setup();
  await act(async () => root.render(
    <RawDiffRenderer
      before={{ path: "wrap.md", content: "old short" }}
      after={{ path: "wrap.md", content: long }}
      layout="split"
    />,
  ));

  const row = host.querySelector<HTMLElement>('[data-paired-row][data-change-row="true"]')!;
  expect(row).not.toBeNull();
  expect(row.className).toContain("grid");
  expect(row.className).toContain("items-stretch");
  const beforeCell = row.querySelector<HTMLElement>('[data-side="before"]')!;
  const afterCell = row.querySelector<HTMLElement>('[data-side="after"]')!;
  expect(beforeCell.parentElement).toBe(row);
  expect(afterCell.parentElement).toBe(row);
  expect(afterCell.textContent).toContain("wrapped content");
  expect(afterCell.querySelector<HTMLElement>('[data-raw-line]')?.style.whiteSpace).toBe("pre-wrap");
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

test("bounds fully disjoint 5,000-line comparison with a safe coarse result", async () => {
  const beforeLines = Array.from({ length: 5_000 }, (_, index) => `old disjoint line ${index + 1}`);
  const afterLines = Array.from({ length: 5_000 }, (_, index) => `new disjoint line ${index + 1}`);
  const beforeContent = beforeLines.join("\n");
  const afterContent = afterLines.join("\n");

  const groupingStartedAt = performance.now();
  const groups = buildDiffLineGroups(beforeContent, afterContent);
  const groupingElapsedMs = performance.now() - groupingStartedAt;
  expect(groupingElapsedMs).toBeLessThan(250);
  expect(groups).toHaveLength(1);
  expect(groups[0].kind).toBe("changed");
  expect(groups[0].beforeLines).toEqual(beforeLines);
  expect(groups[0].afterLines).toEqual(afterLines);

  const renderStartedAt = performance.now();
  const { host, root } = setup();
  await act(async () => root.render(
    <RawDiffRenderer
      before={{ path: "disjoint.md", content: beforeContent }}
      after={{ path: "disjoint.md", content: afterContent }}
      layout="stacked"
    />,
  ));
  const renderElapsedMs = performance.now() - renderStartedAt;
  expect(host.querySelectorAll('[data-change-line="removed"]')).toHaveLength(5_000);
  expect(host.querySelectorAll('[data-change-line="added"]')).toHaveLength(5_000);
  expect(renderElapsedMs).toBeLessThan(5_000);
}, 10_000);

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
