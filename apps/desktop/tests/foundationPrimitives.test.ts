import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
const tokens = read("../src/design/tokens.css");
const foundation = read("../src/design/foundation.css");
const styles = read("../src/styles.css");
const motion = read("../src/lib/tokens/motion.ts");
const palette = read("../src/features/command-palette/components/PaletteBody.tsx");
const paletteSurface = read("../src/features/command-palette/components/CommandPalette.tsx");
const pageModal = read("../src/components/ui/PageModal.tsx");
const toolViewer = read("../src/features/chat/components/ToolViewer.tsx");
const approvalReview = read("../src/features/chat/components/ApprovalReviewModal.tsx");
const depthSources = [
  read("../src/components/ui/RadioGroup.tsx"),
  read("../src/components/ui/RawDiffRenderer.tsx"),
  read("../src/components/ui/Slider.tsx"),
  read("../src/components/ui/Tabs.tsx"),
  read("../src/components/workspace/SidebarResizeHandle.tsx"),
  read("../src/features/background-agents/components/RightPanelResizeHandle.tsx"),
  read("../src/features/chat/components/ApprovalBanner.tsx"),
  read("../src/features/memory/components/PaneResizeHandle.tsx"),
];

test("shared primitives own the exact mock control and overlay contracts", () => {
  for (const value of [
    "--interactive-row-height: 1.6875rem;",
    "--border-width: 1px;",
    "--text-section: var(--text-xl);",
    "--swatch-shadow: inset 0 0 0 1px rgb(0 0 0 / 12%);",
    "--floating-edge: 1rem;",
    "--icon-swap-start-scale: .25;",
    "--line: rgb(255 255 255 / 10%);",
    "--line-strong: rgb(255 255 255 / 15%);",
    "--edge: var(--line);",
    "--edge-strong: var(--line-strong);",
    "--ink-soft: #c9c9c9;",
    "--muted: #b3b3b3;",
    "--faint: #ababab;",
  ]) expect(tokens).toContain(value);

  for (const role of [
    ".arden-search-shell",
    ".arden-segmented",
    ".arden-tooltip",
    ".arden-toast",
    ".shell-control::before",
    ".surface-bottom-sheet",
    ".arden-code-well",
    ".surface-command-palette",
  ]) expect(foundation).toContain(role);

  expect(foundation).toContain("min-height: var(--control-size-large)");
  expect(foundation).toContain("button:not(:disabled):active { transform: scale(var(--press-scale)); }");
  expect(foundation).toContain(".arden-row:not(:disabled):active { transform: scale(var(--press-scale-subtle)); }");
  expect(foundation).toContain("grid-template-columns: var(--icon-size) minmax(0, 1fr) auto");
  expect(foundation).toContain("padding: var(--space-1-5) var(--space-2)");
  expect(foundation).toMatch(/\.surface-bottom-sheet\s*\{[\s\S]*?border-radius:\s*var\(--r-panel\) var\(--r-panel\) 0 0;/);
  expect(foundation).toMatch(/\.surface-bottom-sheet\s*\{[\s\S]*?width:\s*min\(60rem, calc\(100vw - 2rem\)\);/);
  expect(foundation).toMatch(/\.surface-bottom-sheet\s*\{[\s\S]*?max-height:\s*82vh;/);
  expect(foundation).toMatch(/\.arden-code-well\s*\{[\s\S]*?border-radius:\s*var\(--r-house\);/);
  expect(pageModal).toContain("page-modal-frame--bottom-sheet");
  expect(pageModal).not.toContain("max-w-[60rem]");
  expect(pageModal).not.toContain("max-h-[82vh]");
  expect(toolViewer).toContain("<CodeWell");
  expect(toolViewer).not.toContain("rounded-[var(--r-row)]");
  expect(approvalReview).toContain("<CodeWell");
  expect(approvalReview).not.toContain("rounded-[var(--r-row)]");
  expect(foundation).toMatch(/\.command-palette__search\s*\{[\s\S]*?padding:\s*\.625rem var\(--space-4\) \.25rem;/);
  expect(foundation).toContain("font: 450 var(--text-control) / 1.2 var(--sans);");
  expect(palette).toContain('className="command-palette__search"');
  expect(palette).toContain('className="command-palette__input"');
  expect(palette).not.toContain("AnimatePresence");
  expect(palette).not.toContain("layout=");
  expect(paletteSurface).not.toContain("SPRING_POPOVER");
  expect(paletteSurface).not.toContain("layout=");
  expect(palette).not.toContain("<Search");
  expect(foundation).toContain("width: min(21.25rem, calc(100vw - 2rem))");
  expect(tokens).toContain("--swatch-shadow: inset 0 0 0 1px rgb(255 255 255 / 14%);");
  expect(styles).not.toContain(".page-entrance-skeleton__line");
  expect(styles).toMatch(/body\s*\{[\s\S]*?font-size:\s*var\(--text-base\);[\s\S]*?line-height:\s*var\(--leading-body\);/);
  expect(styles).not.toContain(".surface-rail {");
  expect(styles).not.toContain('@import "./design/area.css"');
  expect(styles).not.toContain('@import "./design/settings.css"');
  expect(motion).toContain("pageSkeletonHold: 0.18");
  expect(motion).toContain("pageSkeletonReveal: 0.22");
  expect(motion).not.toContain("SPRING_THEME_TOGGLE");
  expect(motion).not.toContain("POSE_MODAL");
});

test("shared controls use named paint layers instead of numeric z utilities", () => {
  for (const token of [
    "--z-control-pips: 1;",
    "--z-control-occlusion: 2;",
    "--z-control-fill: 3;",
    "--z-control-content: 4;",
    "--z-control-interaction: 5;",
  ]) expect(tokens).toContain(token);

  for (const source of depthSources) {
    expect(source).not.toMatch(/\bz-(?:\d+|\[\d+\])/);
    expect(source).not.toMatch(/zIndex:\s*(?:\d|[^,\n]*-\s*index)/);
  }
  expect(styles).not.toMatch(/z-index:\s*\d/);
});
