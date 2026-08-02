import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

test("memory uses the redesign rail, reading-lane, instrument, and peek geometry", () => {
  const css = read("../src/design/memory.css");
  const tokens = read("../src/design/tokens.css");
  const shell = css.match(/\.memory-surface \.memory-ws \{([\s\S]*?)\}/)?.[1] ?? "";

  expect(shell).toContain("--mw-chrome-top: var(--memory-chrome-top)");
  expect(shell).toContain("--mw-peek-top: var(--memory-peek-top)");
  expect(tokens).toContain("--memory-chrome-top: 3.375rem");
  expect(tokens).toContain("--memory-peek-top: 6.375rem");
  expect(tokens).toContain("--instrument-expanded-room: 32.5rem");
  expect(tokens).toContain("--instrument-tab-clearance: 9rem");
  expect(tokens).toContain("--instrument-layout-gap: var(--space-4)");
  expect(tokens).toContain("--tab-overflow-min-width: 8rem");
  expect(shell).toContain("--mw-rail-edge: .5rem");
  expect(shell).not.toContain("grid-template-columns");
  expect(css).toContain("inset: var(--mw-rail-edge) auto var(--mw-rail-edge) var(--mw-rail-edge)");
  expect(css).toContain("inset: 0 0 0 calc(var(--mw-rail-w) + var(--sidebar-gap))");
  expect(css).toContain("top: var(--mw-chrome-top)");
  expect(css).toContain("top: var(--mw-peek-top)");
  expect(css).toContain("translateX(calc(-1 * var(--motion-distance-sidebar-hide)))");
  expect(css).toContain("filter: blur(var(--motion-blur-sidebar))");
  expect(css).toContain("left: var(--memory-compact-chrome-left)");
  expect(css).toContain("--memory-instrument-current-width: var(--memory-instrument-expanded-width, var(--instrument-min-expanded-width))");
  expect(css).toContain("--memory-instrument-current-width: var(--instrument-collapsed-width)");
  expect(css).toContain("var(--memory-instrument-current-width) - var(--instrument-layout-gap)");
  expect(css).not.toContain("background: color-mix(in oklab, var(--surface-3) 88%, transparent)");
  expect(css).not.toContain("backdrop-filter: blur(1.125rem) saturate(1.08)");
  expect(css).toContain('.memory-surface .mw-rail-segments button[aria-selected="true"] {');
  expect(css).toMatch(/@media \(max-width: 740px\)[\s\S]*?\.memory-surface \.mw-doc \{ left: 0; \}/);
});

test("memory rail keeps the three mock modes in one 288px plane", () => {
  const css = read("../src/design/memory.css");
  const rail = read("../src/features/memory/components/NotebookRail.tsx");
  const tabs = read("../src/components/ui/Tabs.tsx");
  const sharedCss = read("../src/styles.css");
  const view = read("../src/features/memory/components/ArtifactMemoryView.tsx");

  expect(rail).toContain('const RAIL_MODES = ["files", "notebook", "facts"]');
  // The swap goes through TabPanels: `custom` only reaches an EXITING child via
  // variant functions, so an inline exit object sent the outgoing mode the same
  // direction as the incoming one. No hand-rolled AnimatePresence here.
  expect(rail).toContain("const direction = useTabDirection(RAIL_MODES, mode);");
  expect(rail).toContain('<TabPanels value={mode} direction={direction} className="mw-rail-mode">');
  expect(rail).not.toContain("AnimatePresence");
  expect(rail).not.toContain("CONTENT_SWAP_VARIANTS");
  expect(rail).toContain('variant="surface"');
  expect(rail).toContain('indicatorClassName="mw-rail-segment-indicator"');
  expect(tabs).toContain('data-tab-indicator={variant}');
  expect(sharedCss).toContain("transition: transform var(--tabs-dur) var(--tabs-ease)");
  expect(css).not.toContain(".mw-rail-segments[data-mode=");
  expect(css).toContain("height: var(--interactive-row-height)");
  expect(css).toContain("gap: var(--interactive-row-gap)");
  expect(css).not.toContain(".memory-facts-rail");
  expect(css).not.toContain(".mw-rail-top");
  // The notebook rail IS the app sidebar here: one shared width
  // (prefs.sidebarWidth via SidebarResizeHandle), no memory-local key.
  expect(view).toContain("<SidebarResizeHandle />");
  expect(view).not.toContain("memory.railWidth");
  // The context panel keeps its own width — it is not a sidebar.
  expect(view).toContain('cssVar="--mw-ctx-w"');
  expect(view).not.toContain("RecordDetailPane");
  expect(view).not.toContain("setRecordPinned");
  expect(view).not.toContain("516");
  expect(view).not.toContain("720");
});

test("memory open-page tabs keep the mock overflow geometry and shared motion", () => {
  const tabs = read("../src/features/memory/components/MemoryDocumentTabs.tsx");
  const css = read("../src/design/memory.css");
  const tokens = read("../src/design/tokens.css");

  expect(tabs).toContain('aria-label="Open pages"');
  expect(tabs).toContain("getBoardMotion");
  expect(tabs).toContain("motion.tabs.bind(list");
  expect(tabs).toContain('tabSelector: ".tab"');
  expect(tabs).toContain('valueAttribute: "data-page"');
  expect(tabs).toContain('activeClass: "on"');
  expect(tabs).not.toContain('from "@/components/ui/Tabs"');
  expect(tabs).toContain("list.scrollTo");
  expect(tabs).toContain('edgeLeft && "edge-left"');
  expect(tabs).toContain('edgeRight && "edge-right"');
  // The padding / negative-margin pair cancels for layout (tokenised since
  // this pin was written), and the snap inset clears the strip's 10px edge
  // fade so a newly opened tab never parks half-dissolved under the mask.
  expect(css).toContain("padding: var(--tab-bar-padding)");
  expect(css).toContain("margin: calc(var(--tab-bar-padding) * -1)");
  expect(css).toContain("scroll-padding-inline: 14px");
  expect(css).toContain("padding-inline-end: calc(var(--tab-bar-padding) + 14px)");
  expect(tabs).toContain('readLength("--tab-bar-gap")');
  expect(tabs).toContain('readLength("--tab-bar-padding")');
  expect(tabs).toContain('readLength("--tab-overflow-min-width")');
  expect(tabs).toContain('readLength("--instrument-layout-gap")');
  expect(tabs).toContain("root.style.width = width");
  expect(css).toMatch(/\.memory-surface \.mw-doc-tab-x\s*\{[^}]*width:\s*18px;[^}]*height:\s*18px;[^}]*display:\s*flex;[^}]*align-items:\s*center;[^}]*justify-content:\s*center;/s);
  expect(css).toMatch(/\.memory-surface \.mw-doc-tab-x svg\s*\{[^}]*width:\s*var\(--icon-size\);[^}]*height:\s*var\(--icon-size\);/s);
  expect(css).toContain(".memory-surface .mw-doc-tab.on + .mw-doc-tab-x,");
  expect(tabs).toContain('className="tab-close mw-doc-tab-x"');
  expect(tabs).toContain('<use href="#dp-close" />');
  expect(tabs).not.toContain("<X");
  expect(css).toContain("padding: .4375rem .5625rem");
  expect(css).toMatch(/\.memory-surface \.mw-instrument-collapse\s*\{[^}]*position:\s*absolute;[^}]*right:\s*var\(--tab-bar-padding\);/s);
  expect(tokens).toContain("--tab-bar-height: calc(var(--tab-control-size) + var(--tab-bar-padding) * 2)");
  expect(tokens).toContain("--tab-more-min-width: 2rem");
});

test("memory routes content and peek motion through shared primitives", () => {
  const view = read("../src/features/memory/components/ArtifactMemoryView.tsx");
  const css = read("../src/design/memory.css");
  const inspector = read("../src/features/memory/components/MemoryInspector.tsx");
  const note = read("../src/features/memory/components/MemoryNote.tsx");

  expect(view).toContain("<PeekSurface");
  expect(view).toContain('className="mw-context surface-peek"');
  expect(view).toContain('openInspectorPane("records")');
  expect(view).toContain('inspectorPane === "records"');
  expect(view).toContain("CONTENT_SWAP_VARIANTS");
  expect(view).toContain("CONTENT_ENTER_TRANSITION");
  expect(view).toContain('className={clsx("ornament ready mw-instruments"');
  expect(view).toContain('instrumentsCollapsed && "collapsed"');
  expect(view).toContain('className="ornament-surface mw-instrument-surface"');
  expect(view).toContain('className="ornament-items mw-instrument-items"');
  expect(view).toContain("instrumentItems.scrollWidth");
  expect(view).toContain("instrumentCollapse.offsetWidth");
  expect(view).toContain('cssLength("--tab-bar-padding")');
  expect(view).toContain('root.style.setProperty("--memory-instrument-expanded-width"');
  expect(view).toContain('<use href="#dp-edit" />');
  expect(view).toContain('iconA={<ChevronRight');
  expect(view).toContain('<MoreHorizontal size={16} aria-hidden />');
  expect(css).toMatch(/\.memory-surface \.mw-instruments svg\s*\{[^}]*width:\s*var\(--icon-size\);[^}]*height:\s*var\(--icon-size\);/s);
  expect(view).toContain('root.querySelectorAll<HTMLElement>(".mw-instrument-items, .mw-instrument-collapse")');
  expect(view).toContain("--instrument-expanded-room");
  expect(view).toContain("instrumentsCollapsedByFit");
  expect(view).toContain("ResizeObserver");
  expect(view).not.toContain("ctx-hidden");
  expect(view).not.toContain("RISE_IN");
  expect(inspector).toContain("<TabPanels");
  expect(inspector).toContain("useTabDirection");
  expect(inspector).toContain('pane === "records"');
  expect(inspector).toContain("records.map");
  expect(inspector).toContain('label="Link direction"');
  expect(inspector).toContain('aria-label="Close peek"');
  expect(note).not.toContain("CONTENT_SWAP_VARIANTS");
});

test("memory review keeps the sheet mounted through the exact room-release lifecycle", () => {
  const view = read("../src/features/memory/components/ArtifactMemoryView.tsx");
  const review = read("../src/features/memory/components/MemoryEditReview.tsx");
  const css = read("../src/design/memory.css");

  expect(review).toContain("MOTION.focus");
  expect(review).toContain("POSE_SHEET_IN");
  expect(review).toContain("POSE_SHEET_OUT");
  expect(review).toContain("SHEET_CLEANUP_ENTER_TRANSITION");
  expect(review).toContain("SHEET_CLEANUP_EXIT_TRANSITION");
  expect(review).toContain("const REVISION_LABEL_LENGTH = 8");
  expect(review).toContain("slice(0, REVISION_LABEL_LENGTH)");
  expect(review).toContain("scrimMotionProps");
  expect(review).toContain("sheetMotionProps");
  expect(review).toContain('className="memory-edit-review__scrim"');
  expect(view).toContain("requestReviewExit");
  expect(view).toContain("review-closing");
  expect(view).toContain("diff-closing");
  expect(view).toContain("onOpenChange={(open) => setDiffClosing(!open)}");
  expect(view).toContain("onExitComplete={finishReviewExit}");
  expect(css).toContain(".memory-ws.review-open.review-closing");
  expect(css).toContain("body.memory-diff-stage #app");
  expect(css).toContain("body.memory-diff-receded #app");
  expect(view).toContain('className="memory-focus-cache"');
  expect(css).toContain(".memory-focus-plane");
  const recededCache = css.match(/\.memory-surface \.memory-ws\.review-open \.memory-focus-cache\s*\{([^}]*)\}/)?.[1] ?? "";
  expect(recededCache).toContain("will-change: transform");
  expect(recededCache).toContain("transform: scale(.982)");
  expect(recededCache).not.toContain("filter:");
  expect(css).toContain(".memory-surface .memory-ws.review-open .memory-focus-plane");
  expect(css).toContain("filter: blur(2px)");
  const focusPlane = css.match(/\.memory-surface \.memory-focus-plane\s*\{([^}]*)\}/)?.[1] ?? "";
  expect(focusPlane).not.toContain("will-change");
  expect(css).toContain("transform var(--motion-focus) var(--smooth-out)");
});

test("activity history diff reuses the mock's tier-three review sheet, read-only", () => {
  const css = read("../src/design/memory.css");
  const overlay = read("../src/features/memory/components/MemoryDiffOverlay.tsx");
  const sheet = css.match(/\.memory-diff-overlay \.mw-dv\s*\{([\s\S]*?)\n\}/)?.[1] ?? "";

  expect(sheet).toContain("left: calc(50% - 23.75rem)");
  expect(sheet).toContain("bottom: 0");
  expect(sheet).toContain("width: 47.5rem");
  expect(sheet).toContain("max-height: 78%");
  expect(sheet).toContain("background: var(--paper)");
  expect(sheet).toContain("box-shadow: var(--shadow-6)");
  const compact = css.slice(
    css.indexOf("@media (max-width: 740px)"),
    css.indexOf("@media (max-width: 620px)"),
  );
  expect(compact).not.toContain(".memory-diff-overlay .mw-dv { left: 0; width: 100%; }");
  expect(css).toContain("padding: var(--space-3) 18px");
  expect(css).toContain(".memory-diff-overlay .mw-dv-body");
  expect(css).toContain("padding: 0");
  expect(css).toContain("grid-template-columns: 3rem 3rem 1.5rem max-content");
  expect(css).toContain("white-space: pre");
  expect(css).toContain(".memory-diff-overlay .mw-dv-foot");
  expect(css).toContain("background: color-mix(in oklab, var(--ink) 5%, transparent)");
  expect(css).toContain(".mw-dv-scrim:not(:disabled):active { transform: none; }");
  expect(overlay).toContain("getBoardMotion()");
  expect(overlay).toContain("boardMotion.surface.show");
  expect(overlay).toContain("boardMotion.surface.hide");
  expect(overlay).toContain("spring: boardMotion.spring.sheet");
  expect(overlay).toContain("motionIntentRef.current === open");
  expect(overlay).toContain('beforeOpen: () => panel.classList.add("show")');
  expect(overlay).toContain('panel.classList.remove("show")');
  expect(overlay).not.toContain("<motion.div");
  expect(overlay).toContain('className="sheet3 mw-dv"');
  expect(overlay).toContain('className={`dl ${line.kind}`}');
  expect(overlay).toContain('className="mw-dv-line-number mw-dv-line-old"');
  expect(overlay).toContain('className="mw-dv-line-number mw-dv-line-new"');
  expect(overlay).toContain('className="mw-dv-marker"');
  expect(css).toContain("overscroll-behavior: contain");
  expect(css).toContain("contain: layout paint style");
  expect(overlay).toContain('document.body.classList.add("memory-diff-stage")');
  expect(overlay).toContain('document.body.classList.toggle("memory-diff-receded", open)');
  expect(overlay).toContain("document.body,");
  expect(css).toContain("body.memory-diff-stage #app");
  expect(css).toContain("body.memory-diff-receded #app");
  const diffRecession = css.match(/body\.memory-diff-receded #app\s*\{([^}]*)\}/)?.[1] ?? "";
  expect(diffRecession).toContain("transform: scale(.982)");
  expect(diffRecession).not.toContain("filter:");
  const diffScrim = css.match(/\.mw-dv-scrim\s*\{([^}]*)\}/)?.[1] ?? "";
  expect(diffScrim).toContain("backdrop-filter: blur(2px)");
  expect(diffScrim).toContain("-webkit-backdrop-filter: blur(2px)");
  expect(css).toContain("will-change: scroll-position");
  expect(overlay).not.toContain("capturePage");
  expect(overlay).not.toContain("mw-dv-backdrop");
  const diffRow = css.match(/\.memory-diff-overlay \.mw-dv-body > \.dl\s*\{([^}]*)\}/)?.[1] ?? "";
  expect(diffRow).not.toContain("contain: paint");
  expect(css).toContain(".memory-diff-overlay .mw-dv-body .ctx { color: var(--muted); }");
  expect(css).not.toContain("content-visibility: auto");
  expect(css).not.toContain("contain-intrinsic-size");
  expect(css).toContain(".memory-diff-overlay .mw-dv-word-add");
  expect(css).toContain(".memory-diff-overlay .mw-dv-word-del");
  expect(overlay).toContain("className=\"mw-dv-foot\"");
  expect(overlay).toContain('variant="quiet" size="md" onClick={requestClose}>Close</Button>');
  expect(overlay).toContain("{when} · {event.actor} / <b>{title}</b>");
  expect(overlay).toContain("<span className=\"s3d mw-dv-meta\">{patchStat(event.patch)}</span>");
  expect(overlay).not.toContain("Split");
  expect(overlay).not.toContain("Unified");
});

test("memory link groups keep the mock's square transparent rows", () => {
  const css = read("../src/design/memory.css");
  const linkRow = css.match(/\.memory-surface \.mw-lk-row\s*\{([^}]*)\}/)?.[1] ?? "";

  expect(linkRow).toContain("padding: 0");
  expect(linkRow).toContain("border-radius: 0");
  expect(css).not.toContain(".mw-lk-row:hover,\n.memory-surface .mw-rec > button:hover");
  expect(css).toContain(".mw-lk-row:hover .mw-lk-title");
});

test("memory instrument collapse matches the mock's clip and delayed fade choreography", () => {
  const css = read("../src/design/memory.css");

  expect(css).toMatch(/\.memory-surface \.mw-instrument-items\s*\{[\s\S]*?clip-path var\(--motion-instrument\) var\(--motion-drawer-ease\),\s*opacity var\(--motion-instant\) linear;/);
  expect(css).toMatch(/\.memory-surface \.mw-instruments\.collapsed \.mw-instrument-items\s*\{[\s\S]*?opacity var\(--motion-instant\) linear var\(--motion-instrument-hide-delay\);/);
});

test("compact memory peek preserves the mock's 16px side and 48px floor", () => {
  const css = read("../src/design/memory.css");

  expect(css).toMatch(/@media \(max-width: 740px\)[\s\S]*?\.memory-surface \.mw-context\s*\{[\s\S]*?right:\s*1rem;[\s\S]*?bottom:\s*3rem;[\s\S]*?width:\s*min\(var\(--mw-ctx-w\), calc\(100vw - 2rem\)\);/);
});

test("memory never transitions layout properties", () => {
  const css = read("../src/design/memory.css");
  const declarations = css.matchAll(/transition(?:-property)?\s*:\s*([^;}]*)/g);
  for (const [, value] of declarations) {
    expect(value).not.toMatch(/\b(?:width|max-width|min-width|height|max-height|min-height|grid|inset|top|right|bottom|left|margin|padding|gap)\b/);
  }
});

test("Memory is a route with its own rail, not a dialog", () => {
  const surface = read("../src/features/memory/components/MemorySurface.tsx");
  const view = read("../src/features/memory/components/ArtifactMemoryView.tsx");

  // The vault has internal navigation and its own left column, so it replaces
  // the stage rather than covering it. Leaving walks history back to wherever
  // it was opened from — it never evicts the user to Home.
  expect(surface).not.toContain('role="dialog"');
  expect(surface).not.toContain("aria-modal");
  expect(surface).not.toContain("useFocusTrap");
  expect(surface).not.toContain("useOverlayLayer");
  expect(surface).not.toContain("ShellBackButton");
  expect(surface).not.toContain("goToNewSessionHome");
  expect(view).toContain("<SidebarToggle");
  expect(view).toContain("hidden={railHidden}");
  expect(view).toContain("onToggle={() => setRailHidden((hidden) => !hidden)}");
  expect(view).not.toContain("PanelLeftClose");
  expect(view).not.toContain("PanelLeftOpen");
});

test("memory note typography and properties copy match the active mockup", () => {
  const memoryCss = read("../src/design/memory.css");
  const memoryProperties = read("../src/features/memory/components/MemoryProperties.tsx");
  expect(memoryCss).toMatch(/\.memory-surface \.mw-prose\s*\{[^}]*line-height:\s*var\(--leading-reading\);/s);
  expect(memoryProperties).toContain('properties <span className="mw-props-count">');
});

test("memory prose links never inherit the legacy rounded hover or focus box", () => {
  const memoryCss = read("../src/design/memory.css");
  const sharedCss = read("../src/styles.css");
  const wikilink = sharedCss.match(/\.md a\.wikilink\s*\{([^}]*)\}/)?.[1] ?? "";
  const wikilinkHover = sharedCss.match(/\.md a\.wikilink:hover\s*\{([^}]*)\}/)?.[1] ?? "";
  const proseFocus =
    memoryCss.match(
      /\.memory-surface \.mw-prose :is\(a, \[data-wikilink\]\):focus-visible\s*\{([^}]*)\}/,
    )?.[1] ?? "";

  expect(wikilink).not.toContain("border-radius");
  expect(wikilinkHover).not.toContain("background");
  expect(proseFocus).toContain("outline: 0");
  expect(proseFocus).toContain("border-bottom-color: var(--accent-ink)");
});
