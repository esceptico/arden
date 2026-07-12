import { lazy, Suspense, useEffect, useMemo, useState, type CSSProperties } from "react";
import type { RawDiffRendererProps } from "@/components/ui/diffReviewTypes";

type ThemeStyle = CSSProperties & Record<`--${string}`, string>;

const PierreMultiFileDiff = lazy(async () => {
  const module = await import("@pierre/diffs/react");
  return { default: module.MultiFileDiff };
});

const NTRP_SHADOW_CSS = `
:host {
  --diffs-font-family: var(--font-mono, "SF Mono", monospace);
  --diffs-header-font-family: var(--font-sans, system-ui, sans-serif);
  --diffs-font-size: 12px;
  --diffs-line-height: 19px;
  --diffs-light-bg: var(--color-bg-main, #fff);
  --diffs-dark-bg: var(--color-bg-main, #0f0f0f);
  --diffs-light: var(--color-ink, #171717);
  --diffs-dark: var(--color-ink, #ededed);
  --diffs-addition-color: var(--color-ok, #4cc38a);
  --diffs-deletion-color: var(--color-bad, #e5484d);
  --diffs-modified-color: var(--color-accent, #0070f3);
  background: var(--color-bg-main);
  color: var(--color-ink);
  user-select: text;
}
[data-separator-content] { border-radius: 0; }
`;

const REDUCED_MOTION_CSS = `
*, *::before, *::after {
  animation-duration: 0.001ms !important;
  animation-iteration-count: 1 !important;
  scroll-behavior: auto !important;
  transition-duration: 0.001ms !important;
}
`;

function lineCount(content: string) {
  return content === "" ? 0 : content.split("\n").length;
}

function useDarkTheme() {
  const read = () => document.documentElement.classList.contains("dark");
  const [dark, setDark] = useState(read);
  useEffect(() => {
    const observer = new MutationObserver(() => setDark(read()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return dark;
}

export function RawDiffRenderer({ before, after, layout, reducedMotion = false }: RawDiffRendererProps) {
  const dark = useDarkTheme();
  const oldFile = useMemo(() => ({ name: before.path, contents: before.content, lang: "markdown" }), [before.content, before.path]);
  const newFile = useMemo(() => ({ name: after.path, contents: after.content, lang: "markdown" }), [after.content, after.path]);
  const options = useMemo(() => ({
    diffStyle: layout === "split" ? "split" as const : "unified" as const,
    overflow: "wrap" as const,
    disableLineNumbers: false,
    collapsed: false,
    expandUnchanged: false,
    collapsedContextThreshold: 3,
    hunkSeparators: "line-info" as const,
    lineDiffType: "word" as const,
    diffIndicators: "bars" as const,
    disableFileHeader: true,
    stickyHeader: false,
    theme: { light: "github-light", dark: "github-dark" },
    themeType: dark ? "dark" as const : "light" as const,
    unsafeCSS: `${NTRP_SHADOW_CSS}${reducedMotion ? REDUCED_MOTION_CSS : ""}`,
  }), [dark, layout, reducedMotion]);
  const style: ThemeStyle = {
    "--diffs-bg": "var(--color-bg-main)",
    "--diffs-fg": "var(--color-ink)",
    "--diffs-light-bg": "var(--color-bg-main)",
    "--diffs-dark-bg": "var(--color-bg-main)",
    "--diffs-light": "var(--color-ink)",
    "--diffs-dark": "var(--color-ink)",
    "--diffs-addition-color": "var(--color-ok)",
    "--diffs-deletion-color": "var(--color-bad)",
    "--diffs-modified-color": "var(--color-accent)",
    userSelect: "text",
    transition: reducedMotion ? "none" : undefined,
  };
  return (
    <div
      data-raw-diff-renderer
      data-layout={layout}
      data-language="markdown"
      data-wrap="true"
      data-line-numbers="true"
      data-collapsed-hunks="true"
      data-worker-pool="disabled"
      data-motion={reducedMotion ? "reduced" : "standard"}
      data-theme={dark ? "dark" : "light"}
      data-before-lines={lineCount(before.content)}
      data-after-lines={lineCount(after.content)}
      data-before-bytes={before.content.length}
      data-after-bytes={after.content.length}
      aria-label={`Raw Markdown changes for ${after.path || before.path}`}
      className="min-w-0 overflow-auto bg-bg-main text-ink scroll-thin"
      style={style}
    >
      <span className="sr-only">Exact Markdown diff preserves frontmatter and whitespace</span>
      <Suspense fallback={<div role="status" className="p-4 text-xs text-muted">Preparing raw diff…</div>}>
        <PierreMultiFileDiff
          oldFile={oldFile}
          newFile={newFile}
          options={options}
          selectedLines={null}
          disableWorkerPool
          style={style}
        />
      </Suspense>
    </div>
  );
}
