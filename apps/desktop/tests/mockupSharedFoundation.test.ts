import { expect, test } from "bun:test";
import { existsSync, readFileSync } from "node:fs";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
const system = read("../../../docs/mockups/board-system.css");
const surfaces = read("../../../docs/mockups/board-surfaces.css");
const motion = read("../../../docs/mockups/board-motion.js");
const sonnerUrl = new URL("../../../docs/mockups/board-sonner.js", import.meta.url);
const sonner = existsSync(sonnerUrl) ? readFileSync(sonnerUrl, "utf8") : "";
const icons = read("../../../docs/mockups/board-icons.js");
const chatCss = read("../../../docs/mockups/board-chat.css");
const chatHtml = read("../../../docs/mockups/board-chat.html");
const chatJs = read("../../../docs/mockups/board-chat.js");
const appRailCss = read("../../../docs/mockups/board-app-rail.css");
const attentionCss = read("../../../docs/mockups/board-attention.css");
const home = read("../../../docs/mockups/board-home.html");
const homeCss = read("../../../docs/mockups/board-home.css");
const memory = read("../../../docs/mockups/board-memory.html");
const memoryCss = memory.match(/<style>([\s\S]*?)<\/style>/)?.[1] || "";
const settings = read("../../../docs/mockups/board-settings.html");
const settingsCompact = settings.replace(/\s+/g, "");
const settingsCss = settings.match(/<style>([\s\S]*?)<\/style>/)?.[1] || "";
const automationsCss = read("../../../docs/mockups/board-automations.css");
const automationsHtml = read("../../../docs/mockups/board-automations.html");
const automationsJs = read("../../../docs/mockups/board-automations.js");
const overlaysHtml = read("../../../docs/mockups/board-system-overlays.html");
const overlaysCss = read("../../../docs/mockups/board-system-overlays.css");
const areaHtml = read("../../../docs/mockups/board-area-room.html");
const areaCss = read("../../../docs/mockups/board-area-room.css");
const areaJs = read("../../../docs/mockups/board-area-room.js");
const localSources = [chatCss, chatHtml, chatJs, memory, settings, automationsCss, automationsHtml, automationsJs];
const activeStyleSources = new Map([
  ["app rail", appRailCss],
  ["home", homeCss],
  ["chat", chatCss],
  ["automations", automationsCss],
  ["memory", memoryCss],
  ["settings", settingsCss],
  ["area room", areaCss],
  ["overlays", overlaysCss],
]);
const styleAuthority = read("../../../docs/mockups/BOARD_STYLE_AUTHORITY.md");

test("the Board style authority is explicit and test-enforced", () => {
  expect(styleAuthority).toContain("board-surfaces.css");
  expect(styleAuthority).toContain("board-system.css");
  expect(styleAuthority).toContain("Page files: layout and content composition only");
  expect(styleAuthority).toContain("Allowed page-local exceptions");
});

test("all three mockups consume one shared token and component foundation", () => {
  expect(chatCss.startsWith('@import "./board-system.css')).toBe(true);
  expect(memory).toContain('href="./board-system.css');
  expect(settings).toContain('href="./board-system.css');
  expect(memory.indexOf('href="./board-system.css')).toBeLessThan(memory.indexOf("<style>"));
  expect(settings.indexOf('href="./board-system.css')).toBeLessThan(settings.indexOf("<style>"));

  for (const token of [
    "--icon-size",
    "--control-size",
    "--sidebar-width",
    "--sidebar-min-width",
    "--sidebar-max-width",
    "--sidebar-edge",
    "--sidebar-resizer-width",
    "--content-max-width",
    "--content-gutter",
    "--floating-edge",
    "--chrome-control-inset",
    "--r-shell",
    "--r-control",
    "--r-row",
    "--surface-radius",
    "--press-scale",
    "--press-scale-subtle",
  ]) {
    expect(system).toContain(`${token}:`);
    for (const source of localSources) expect(source).not.toMatch(new RegExp(`${token}:\\s*[^v]`));
  }
  const legacyAliases = /--(?:sidebar|sidebar-lane|content-max|page-gutter|rail-width|rail-min|rail-max|rail-gap|rail-lane|rail-edge|lane|gutter)\s*:/;
  for (const source of localSources) expect(source).not.toMatch(legacyAliases);
});

test("document defaults have one shared authority", () => {
  expect(system).toMatch(/html, body \{[^}]*width: 100%;[^}]*height: 100%;[^}]*margin: 0;/s);
  expect(system).toMatch(/body \{[^}]*color: var\(--ink\);[^}]*background: var\(--paper\);[^}]*font: var\(--text-base\)\/var\(--leading-body\) var\(--sans\);/s);
  for (const [name, source] of activeStyleSources) {
    expect(source, `${name} must not redeclare shared document typography`).not.toMatch(/body\s*\{[^}]*(?:font-family|font:\s|font-size:|background:\s*var\(--paper\)|color:\s*var\(--ink\))/s);
  }
});

test("active mockups keep layout out of inline styles", () => {
  for (const [name, source] of Object.entries({ Home: home, Chat: chatHtml, Automations: automationsHtml, Memory: memory, Settings: settings, "Area Room": areaHtml, Overlays: overlaysHtml })) {
    const inlineStyles = [...source.matchAll(/style="([^"]*)"/g)].map((match) => match[1]);
    for (const inlineStyle of inlineStyles) {
      expect(inlineStyle, `${name} inline styles are reserved for data-driven swatches`).toMatch(/^--swatch:\s*#[\da-f]{6}$/i);
    }
  }
});

test("the corner profile controls every interactive row through one radius token", () => {
  expect(system).toContain("--r-pill: 999px;");
  expect(system).toContain("--r-square: 9px;");
  expect(system).toContain("--r-circle: 50%;");
  expect(system).toContain("--r-control: var(--r-pill);");
  expect(system).toContain("--r-row: var(--r-control);");
  expect(system).not.toMatch(/--(?:row|pill|shell)-radius:/);
  expect(settingsCompact).toMatch(/style\.setProperty\(["']--r-control["'],square\?["']var\(--r-square\)["']:["']var\(--r-pill\)["'],?\)/);
  expect(settings).not.toContain("style.setProperty('--r-row'");
  for (const [name, source] of activeStyleSources) {
    expect(source, `${name} must consume named radius roles`).not.toMatch(/border-radius:\s*(?:\d|50%)/);
  }
});

test("selected pills use one raised surface that remains visible in dark mode", () => {
  expect(system).toContain("--control-selected-bg: var(--surface-4)");
  expect(system).toContain("--control-selected-shadow: var(--shadow-3)");
  const indicator = system.match(/\.dp-tab-indicator \{([^}]*)\}/)?.[1] ?? "";
  expect(indicator).toContain("background: var(--control-selected-bg)");
  expect(indicator).toContain("box-shadow: var(--control-selected-shadow)");
  expect(indicator).not.toContain("background: var(--paper)");
  expect(memory).not.toMatch(/\.ornament[^{}]*\.on\{[^}]*background:#fff/);
  expect(memory).toContain("background:var(--control-selected-bg)");
  expect(overlaysCss).toContain('button[aria-pressed="true"] { color: var(--ink); background: var(--control-selected-bg); }');
});

test("interactive row states keep shared breathing room between neighbors", () => {
  expect(system).toContain("--interactive-row-gap: var(--space-1);");
  expect(system).toContain("--interactive-row-inline-padding: var(--space-3);");
  expect(system).toContain("--state-hover-bg: rgb(0 0 0 / 4%);");
  expect(system).toContain("--state-selected-bg: rgb(0 0 0 / 7%);");
  expect(system).not.toContain("--interactive-row-state-inset:");
  expect(appRailCss).toMatch(/\.rail-nav\s*\{[^}]*gap:\s*var\(--interactive-row-gap\);/s);
  expect(appRailCss).toMatch(/\.area-list\s*\{[^}]*gap:\s*var\(--interactive-row-gap\);/s);
  expect(appRailCss).toMatch(/\.nav-row\s*\{[^}]*padding:\s*0 var\(--interactive-row-inline-padding\);/s);
  expect(system).toMatch(/\.dp-sidebar-nav-item\s*\{[^}]*padding:\s*0 var\(--interactive-row-inline-padding\);/s);
  expect(system).toMatch(/\.dp-menu-item:is\(:hover, :focus-visible\)[^}]*background: var\(--state-hover-bg\)/);
  expect(system).toMatch(/\.dp-peek-row:hover[^}]*background: var\(--state-hover-bg\)/);
  expect(system).not.toContain(".dp-disclosure-row");
  expect(overlaysCss).toMatch(/\[cmdk-item\]\[data-selected\][^}]*background: var\(--state-selected-bg\)/);
  for (const source of [appRailCss, chatCss, homeCss, automationsCss, memory]) {
    expect(source).toContain("var(--state-hover-bg)");
  }
  const legacyStateToken = /--(?:hover|hov|selected|sel|interactive-row-hover-bg|interactive-row-selected-bg)(?=\s*:|\))/;
  expect(system).not.toMatch(legacyStateToken);
  for (const [name, source] of activeStyleSources) {
    expect(source, `${name} must consume the canonical interaction state tokens`).not.toMatch(legacyStateToken);
  }
});

test("shared text buttons constrain leading and trailing icons", () => {
  expect(system).toMatch(/\.dp-button,\s*\n\.btn\s*\{[^}]*display:\s*inline-flex;[^}]*align-items:\s*center;[^}]*gap:\s*var\(--space-2\);/s);
  expect(system).toMatch(/:is\(\.dp-button, \.btn\)\s*>\s*svg\s*\{[^}]*width:\s*var\(--icon-size\);[^}]*height:\s*var\(--icon-size\);[^}]*flex:\s*none;/s);
});

test("active mockups consume the shared spacing scale", () => {
  for (const token of ["--space-none", "--space-0-5", "--space-1", "--space-1-5", "--space-2", "--space-2-5", "--space-3", "--space-4", "--space-5", "--space-6", "--space-8"]) {
    expect(system).toContain(`${token}:`);
  }
  const rawGap = /(?:^|[;{])\s*(?:gap|row-gap|column-gap):\s*(?:\d|\.\d)/m;
  for (const [name, source] of activeStyleSources) {
    expect(source, `${name} must consume shared spacing tokens for gaps`).not.toMatch(rawGap);
  }
});

test("review controls use one shared demo primitive", () => {
  expect(system).toContain(".dp-demo-toggle {");
  expect(system).toContain(".dp-demo-panel {");
  for (const [html, css] of [[home, homeCss], [chatHtml, chatCss]]) {
    expect(html).toContain("dp-demo-toggle");
    expect(html).toContain("dp-demo-panel");
    expect(css).not.toMatch(/\.lab-(?:toggle|panel)\s*\{/);
  }
});

test("all primary mockups share one review-only layout guide overlay", () => {
  expect(system).toContain(".dp-layout-guides {");
  expect(system).toContain("pointer-events: none");
  expect(system).toContain("body:not(.dp-guides-on) .dp-layout-guides");
  expect(system).toContain(".dp-layout-inspected-box");
  expect(system).toContain(".dp-layout-parent-box");
  expect(system).toContain(".dp-layout-measure");
  expect(system).toContain(".dp-layout-selection");
  expect(system).not.toContain("background-size: var(--space-2) var(--space-2)");
  expect(motion).toContain("const bindLayoutGuides = (root = document) =>");
  expect(motion).toContain("const guideQueryEnabled =");
  expect(motion).toContain('event.key.toLowerCase() !== "g"');
  expect(motion).toContain('root.addEventListener("pointerover"');
  expect(motion).toContain('root.addEventListener("click"');
  expect(motion).toContain("event.stopImmediatePropagation()");
  expect(motion).toContain('if (["Enter", " "].includes(event.key)');
  expect(motion).toMatch(/root\.addEventListener\("click",[\s\S]*?\}, true\);/);
  expect(motion).toContain("target.getBoundingClientRect()");
  expect(motion).toContain("window.getComputedStyle(parent)");
  expect(motion).toContain("const selectedTargets = new Map()");
  expect(motion).toContain("if (!event.shiftKey) clearSelections()");
  expect(motion).toContain("selectedTargets.has(target)");
  expect(motion).toContain('${selectedTargets.size} selected');
  expect(motion).toContain('measure.textContent = `${Math.round(rect.width)} × ${Math.round(rect.height)}');
  expect(motion).toContain('guideToggle.textContent = "Inspect layout"');
  expect(motion).toContain("bindLayoutGuides(document)");
});

test("every desktop sidebar consumes the same default width token", () => {
  expect(system).toContain("--sidebar-width: 18rem");
  expect(system).not.toContain("--automation-rail-width:");
  expect(automationsCss).toContain("width: var(--sidebar-width)");
  expect(automationsCss).toContain("padding-left: calc(var(--sidebar-width) + var(--sidebar-gap))");
  expect(memory).not.toContain("RAIL_WIDTH_KEY");
  expect(memory).not.toContain('localStorage.setItem("board.rail-width"');
});

test("workspace app rails have one styling authority", () => {
  for (const [name, source] of Object.entries({ Home: homeCss, Chat: chatCss, "Area Room": areaCss })) {
    expect(source, `${name} must import the shared app rail`).toContain('board-app-rail.css?v=20260722-5');
  }
  expect(chatCss).not.toMatch(/\.nav-row\s*\{/);
  expect(chatCss).not.toMatch(/\.rail-bottom\s*\{/);
  expect(chatCss).not.toMatch(/\.area-bar\s*\{/);
});

test("Home and Area Room share one attention composition", () => {
  for (const [name, source] of Object.entries({ Home: homeCss, "Area Room": areaCss })) {
    expect(source, `${name} must import the shared attention composition`).toContain('board-attention.css?v=20260722-10');
  }
  for (const selector of ["answer-line", "answer-sub", "card-eyebrow", "card-title", "card-reason", "verb-key", "deck-chrome", "deck-pos", "rest-row"]) {
    expect(attentionCss).toContain(`.${selector}`);
    expect(homeCss).not.toMatch(new RegExp(`\\.${selector}\\s*\\{[^}]*(?:font|margin|color:)`));
    expect(areaCss).not.toMatch(new RegExp(`\\.${selector}\\s*\\{[^}]*(?:font|margin|color:)`));
  }
});

test("Home attention rows share one content and interaction rail", () => {
  expect(attentionCss).toMatch(/\.rest-row,\s*\.strip-head\s*\{[^}]*width:\s*calc\(100% \+ var\(--space-5\)\)[^}]*margin-inline:\s*calc\(var\(--space-2-5\) \* -1\)[^}]*padding-inline:\s*var\(--space-3\)/s);
  expect(homeCss).toMatch(/\.strips\s*\{[^}]*padding:\s*5px 0 0/s);
  expect(homeCss).not.toMatch(/\.strip-head\s*\{[^}]*padding:/s);
});

test("Home attention rows share one vertical rhythm", () => {
  expect(attentionCss).toMatch(/\.rest,\s*\.strips\s*\{[^}]*display:\s*grid[^}]*gap:\s*var\(--interactive-row-gap\)/s);
  expect(attentionCss).toMatch(/\.rest-row,\s*\.strip-head\s*\{[^}]*min-height:\s*var\(--control-size\)/s);
  expect(homeCss).not.toMatch(/\.strip-head\s*\{[^}]*min-height:/s);
});

test("all primary pages consume the canonical primitive matrix", () => {
  const matrix = new Map([
    ["Home", [home + homeCss, ["dp-icon-button", "dp-sidebar"]]],
    ["Chat", [chatHtml + chatCss + chatJs, ["dp-button", "dp-icon-button", "dp-peek", "dp-popover", "dp-sidebar", "dp-tabs"]]],
    ["Automations", [automationsHtml + automationsCss + automationsJs, ["dp-button", "dp-icon-button", "dp-menu", "dp-peek", "dp-popover", "dp-search-shell", "dp-segmented", "dp-sidebar", "dp-switch", "dp-tabs"]]],
    ["Memory", [memory, ["dp-icon-button", "dp-peek", "dp-sidebar"]]],
    ["Settings", [settings, ["dp-field", "dp-icon-button", "dp-menu", "dp-popover", "dp-search-shell", "dp-segmented", "dp-sheet", "dp-sidebar", "dp-status", "dp-switch"]]],
    ["Area Room", [areaHtml + areaCss + areaJs, ["dp-button", "dp-icon-button", "dp-peek", "dp-sidebar", "dp-tabs"]]],
    ["Overlays", [overlaysHtml + overlaysCss, ["dp-button", "dp-empty-state", "dp-error-state", "dp-field", "dp-icon-button", "dp-scrim", "dp-search-shell", "dp-sheet", "dp-skeleton", "dp-status", "dp-tooltip"]]],
  ]);
  for (const [name, [source, primitives]] of matrix) {
    expect(source, `${name} must load the shared component CSS`).toContain("board-system.css");
    expect(source, `${name} must load the shared behavior engine`).toContain("board-motion.js");
    for (const primitive of primitives) expect(source, `${name} must consume ${primitive}`).toContain(primitive);
  }
});

test("all composed searches use one shared shell", () => {
  expect(system).toContain(".dp-search-shell {");
  expect(system).toContain(".dp-search-shell:focus-within");
  expect(system).toContain(".dp-search-shell-compact");
  expect(automationsHtml).toContain('class="rail-search dp-search-shell dp-search-shell-compact"');
  expect(settings.match(/dp-search-shell/g)?.length).toBeGreaterThanOrEqual(3);
  expect(overlaysHtml).toMatch(/class="[^"]*\bdp-search-shell\b/);
  expect(automationsCss).not.toMatch(/\.rail-search \{[^}]*background:/);
  expect(settingsCss).not.toMatch(/\.(?:searchbar|model-search-wrap)\{[^}]*background:/);
});

test("every rendered keyboard shortcut uses the shared Kbd primitives", () => {
  expect(system).toContain(".dp-kbd {");
  expect(system).toContain(".dp-kbd-group {");

  const activeMarkup = new Map([
    ["Home", home],
    ["Chat", chatHtml],
    ["Automations", automationsHtml],
    ["Memory", memory],
    ["Settings", settings],
    ["Area Room", areaHtml],
    ["Overlays", overlaysHtml],
  ]);
  for (const [name, source] of activeMarkup) {
    for (const match of source.matchAll(/<kbd\b[^>]*>/g)) {
      const isGroup = match[0].includes('data-slot="kbd-group"');
      expect(match[0], `${name} Kbd must consume the shared class`).toContain(isGroup ? "dp-kbd-group" : "dp-kbd");
      expect(match[0], `${name} Kbd must expose the shared slot`).toContain(isGroup ? 'data-slot="kbd-group"' : 'data-slot="kbd"');
    }
  }

  expect(system).toContain("width: fit-content;");
  expect(system).toContain("min-width: 1.25rem;");
  expect(system).toContain("height: 1.25rem;");
  expect(system).toContain("border-radius: var(--r-mark);");
  expect(system).toContain("font: 500 .75rem/1 var(--sans);");

  expect(home).toContain('class="dp-kbd-group" data-slot="kbd-group"');
  expect(automationsHtml).toContain('class="dp-kbd-group dp-search-trailing" data-slot="kbd-group"');
  expect(settings).toContain('class="dp-kbd-group" data-slot="kbd-group"');
  expect(overlaysHtml).toContain('class="dp-kbd-group" data-slot="kbd-group"');
  expect(motion).toContain("function createKbd(");
  expect(motion).toContain("function createKbdGroup(");
  expect(motion).toContain("const keyboard = Object.freeze({ key: createKbd, group: createKbdGroup });");
  expect(motion).toContain('const shortcut = keyboard.key("G")');
  expect(memory).toContain('MOTION.keyboard.group(["⌘", "S"]');

  for (const [name, source] of activeStyleSources) {
    expect(source, `${name} must not style raw kbd elements`).not.toMatch(/(?:^|[\s>+~,])kbd\s*(?:[,.:{]|$)/m);
    expect(source, `${name} must not define a local shortcut primitive`).not.toMatch(/\.command-shortcut\s*\{/);
  }

  expect(settings).not.toContain("<kbd\n            class=\"dp-search-trailing\"");
  expect(settings).not.toContain('<kbd class="dp-search-trailing">12 items</kbd>');
  expect(settings).not.toContain('<button class="btn">⌘⇧Space</button>');
  expect(settings).not.toContain("Enter creates a new session");
  expect(memory).not.toContain("⌘S to review · esc to stop");
  expect(overlaysHtml).not.toContain("⌘ Enter to send");
  expect(overlaysHtml).not.toContain("<kbd>⌘ Enter asks the navigation helper</kbd>");
});

test("Board uses the canonical Fluid Functionalism surface ladder", () => {
  expect(system).toMatch(/^@import "\.\/typeset\.css";\n@import "\.\/board-surfaces\.css(?:\?v=\d{8}-\d+)?";/);
  for (let level = 1; level <= 8; level += 1) {
    expect(surfaces).toContain(`--surface-${level}:`);
    expect(surfaces).toContain(`--shadow-light-${level}:`);
    expect(surfaces).toContain(`--shadow-dark-${level}:`);
    expect(surfaces).toContain(`--shadow-${level}:`);
    for (const source of localSources) expect(source).not.toContain(`--surface-${level}:`);
  }
  expect(surfaces).toContain("--surface-1: light-dark(#FAFAFA, #171717);");
  expect(surfaces).toContain("--surface-8: light-dark(#FFFFFF, #484848);");
  expect(surfaces).toContain("--shadow-dark-3: inset 0 1px 0 0 var(--dm-hi-mid)");
});

test("press depth is shared and shallow", () => {
  expect(system).toContain("--press-scale: .997;");
  expect(system).toContain("--press-scale-subtle: .999;");
  expect(system).toContain("button:not(:disabled):active { transform: scale(var(--press-scale)); }");
  for (const source of [system, chatCss, memory, settings]) {
    expect(source).not.toMatch(/:active[^{}]*\{[^{}]*scale\(\.[0-9]+\)/);
  }
});

test("icon swaps have one implementation and one icon-size contract", () => {
  expect(motion).toContain("function ensureIconSwap(target)");
  expect(motion).toContain("const iconSwap = Object.freeze");
  expect(motion).toContain("prepare: ensureIconSwap");
  expect(motion).toContain("function enhanceIconButton");
  expect(motion).toContain("motion.iconButton.bind(document)");
  expect(motion).toContain(".t-icon-swap .t-icon");
  expect(memory).toContain('id="o-collapse"');
  for (const source of [chatHtml, memory, settings]) {
    expect(source).not.toContain('class="t-icon-swap"');
    expect(source).not.toContain('data-icon="a"');
  }
  expect(memory).not.toContain("collapse-chevron");
  expect(memory).not.toContain("collapse-more");

  const localStyle = [chatCss, memory, settings].join("\n");
  expect(localStyle).not.toMatch(/svg[^{}]*\{[^{}]*(?:width|height):\s*\d+(?:\.\d+)?px/);
  for (const source of [chatHtml, memory, settings]) {
    expect(source).toContain("board-icons.js");
    expect(source).not.toContain("<symbol id=");
    expect(source).not.toMatch(/#[ip]-/);
  }
  expect(chatJs).not.toMatch(/#[ip]-/);
  expect(icons).toContain('id=\\"board-icon-sprite\\"');
  expect(icons).toContain('id=\\"dp-panel-left-close\\"');
  expect(icons).toContain('id=\\"dp-panel-left-open\\"');
  expect(icons.match(/dp-panel-left-close/g)?.length).toBe(1);
  expect(system).toMatch(/\.dp-icon-button:is\(\.dp-button, \.btn\)\s*\{[^}]*min-height:\s*0;[^}]*padding:\s*0;/s);
  expect(system).toMatch(/\.dp-icon-button > svg,\s*\.dp-icon-button > \.t-icon-swap\s*\{[^}]*top:\s*50%;[^}]*left:\s*50%;[^}]*translate:\s*-50% -50%;/s);
});

test("tabs and sidebar state use shared measured controllers", () => {
  expect(motion).toContain("function syncExpandingTabLayout");
  expect(system).toContain("--tab-control-size: var(--control-size)");
  expect(system).toContain("--tab-active-gap:");
  expect(system).toContain("--tab-inline-padding:");
  expect(motion).toContain('length(config.collapsedWidth, "--tab-control-size")');
  expect(memory).not.toContain("collapsedWidth: 28");
  expect(motion).not.toMatch(/--sidebar-(?:edge|min-width|max-width|resize-step)"\s*,\s*\{\s*fallback:/);
  expect(motion).toContain("function syncSidebarState");
  expect(motion).toContain("function sidebarWidthFromPointer");
  expect(motion).toContain("function syncShellControlGeometry");
  expect(motion).toContain("function bindSidebarResize");
  expect(motion).toContain("const shellControls = Object.freeze");
  expect(chatJs).toContain("motion.sidebar.sync");
  expect(memory).toContain("MOTION.sidebar.sync");
  expect(memory).toContain("MOTION.tabs.bind(railModes");
  expect(memory).toContain("MOTION.tabPanels.bind(peekTabs");
  expect(memory).toContain("MOTION.tabs.bind(tabStrip");
  expect(settings).toContain("BOARD_MOTION.sidebar.sync");
  expect(settings).toContain("BOARD_MOTION.tabs.bind(group");
  expect(chatJs).not.toContain("sidebarWidthFromPointer(");
  expect(settings).not.toContain("sidebarWidthFromPointer(");
  expect(memory).not.toContain("function syncTabIndicator");
  expect(memory).not.toContain("function syncRailModeIndicator");
  expect(chatJs).toContain("motion.sidebarResize.bind(railResize");
  expect(memory).toContain("MOTION.sidebarResize.bind(railResize");
  expect(settings).toContain("BOARD_MOTION.sidebarResize.bind(resizer");
  expect(chatJs).not.toContain("railResize?.addEventListener('pointerdown'");
  expect(memory).not.toContain('railResize.addEventListener("pointerdown"');
  for (const source of [chatHtml, memory, settings]) expect(source).toContain("dp-sidebar-resizer");
  expect(system).toContain(".dp-sidebar-resizer {");
  expect(system).toContain(".dp-shell-toggle:hover");
  expect(system).toContain(".dp-traffic-light:nth-child(3)");
});

test("Memory tab close actions stay above the shared tab hit target", () => {
  const closeRule = memory.match(/\.tab-close\{([^}]*)\}/)?.[1] ?? "";
  expect(closeRule).toContain("z-index:calc(var(--z-raised) + 1)");
});

test("content and text state changes use one shared transition engine", () => {
  expect(motion).toContain("async function swapContent");
  expect(motion).toContain("async function enterContent");
  expect(motion).toContain("const content = Object.freeze");
  expect(motion).toContain("function bindTabPanels");
  expect(motion).toContain("const tabPanels = Object.freeze({ bind: bindTabPanels })");
  expect(motion).toContain("tabPanels,");
  expect(chatJs).toContain("motion.content.swap([lane, chatTitle], swap)");
  expect(chatJs).not.toContain("function dissolve(");
  expect(chatJs).not.toContain("function dissolveGroup(");
  expect(memory.match(/MOTION\.content\.enter/g)?.length).toBeGreaterThanOrEqual(4);
  expect(memory).toContain("MOTION.textSwap.swap(peekTitle");
  expect(memory).not.toContain("function animatePeekSwap");
  expect(memory).not.toContain("function animateRailSwap");
  expect(settingsCompact).toContain("BOARD_MOTION.content.swap(plane,commit,{direction})");
  expect(settings).not.toMatch(/plane\.animate\(\[\{opacity:1/);
  expect(motion).toContain("function animateListChange");
  expect(motion).toContain("async function overlapContent");
  expect(motion).not.toContain("function animateWidth");
  expect(motion).toContain("function transitionCentered");
  expect(motion).toContain("function positionIndicator");
  for (const source of [chatJs, memory, settings]) expect(source).not.toContain(".animate(");
});

test("peek panels share one lifecycle and content structure", () => {
  expect(motion).toContain("function bindPeek");
  expect(motion).toContain("const peek = Object.freeze({ bind: bindPeek })");
  expect(motion).toContain("peek,");
  for (const name of ["dp-peek-body", "dp-peek-section", "dp-peek-section-head", "dp-peek-list", "dp-peek-row"]) {
    expect(system).toContain(`.${name}`);
  }
  expect(system).toMatch(/\.dp-peek-row\s*\{[^}]*border-radius:\s*var\(--r-dense-row\)/);
  expect(system).not.toMatch(/\.dp-peek-row\s*\{[^}]*border-radius:\s*var\(--r-row\)/);
});

test("Chat consumes the shared peek lifecycle and panel controller", () => {
  expect(chatJs).toContain("motion.peek.bind");
  expect(chatJs).toContain("motion.tabPanels.bind");
  expect(chatHtml).toContain("data-tab-panels");
  expect(chatHtml).toContain("dp-peek-body");
  expect(chatHtml).toContain("dp-peek-section");
  expect(chatHtml).toContain("dp-peek-section-head");
  expect(chatHtml).toContain("dp-peek-list");
  expect(chatHtml).toContain("dp-peek-row");
  for (const selector of [".peek-section", ".peek-section-head", ".peek-list", ".peek-row"]) {
    expect(chatCss).not.toContain(selector);
  }
});

test("Memory and Automations consume shared peek and panel controllers", () => {
  expect(memory).toContain("MOTION.peek.bind");
  expect(memory).toContain("MOTION.tabPanels.bind");
  expect(memory).toContain('class="peek-body dp-peek-body"');
  expect(automationsJs).toContain("motion.peek.bind");
  expect(automationsJs).toContain("motion.tabPanels.bind");
  expect(automationsHtml.match(/dp-peek-body/g)?.length).toBeGreaterThanOrEqual(2);
  expect(automationsCss).not.toContain(".schedule-tab-indicator");
});

test("menus and sheets use shared structure", () => {
  for (const name of ["dp-menu", "dp-menu-label", "dp-menu-item", "dp-sheet-header", "dp-sheet-body", "dp-sheet-footer"]) {
    expect(system).toContain(`.${name}`);
  }
  for (const name of ["dp-menu", "dp-menu-label", "dp-menu-item"]) {
    expect(automationsHtml + automationsJs).toContain(name);
    expect(settings).toContain(name);
  }
  for (const name of ["dp-sheet-header", "dp-sheet-body", "dp-sheet-footer"]) {
    expect(settings).toContain(name);
    expect(overlaysHtml).toContain(name);
  }
  for (const selector of [".model-option", ".effort-option", ".sheet-head", ".sheet-body", ".sheet-actions"]) {
    expect(settingsCss).not.toContain(selector);
  }
  expect(overlaysCss).not.toMatch(/^\.system-overlay-(?:head|body|foot)\s*\{/m);
});

test("appearance state uses one shared theme controller", () => {
  expect(motion).toContain("function bindThemeToggle");
  expect(motion).toContain("const theme = Object.freeze");
  expect(system).toMatch(/:root\[data-theme="light"\]\s*\{\s*color-scheme:\s*light;\s*\}/);
  expect(system).toMatch(/:root\[data-theme="dark"\]\s*\{[^}]*color-scheme:\s*dark;/);
  expect(chatJs).toContain("motion.theme.bindToggle(themeToggle");
  expect(chatJs).not.toContain("syncTheme");
  expect(settings).toContain("data-theme-control");
  expect(settings).toContain("BOARD_MOTION.theme.set(value)");
});

test("rich text uses the shared shadcn typeset preset", () => {
  expect(system.startsWith('@import "./typeset.css";')).toBe(true);
  expect(system).toContain(".typeset-notes {");
  expect(chatHtml.match(/typeset typeset-notes/g)?.length).toBe(3);
  expect(memory.match(/prose typeset typeset-notes/g)?.length).toBeGreaterThanOrEqual(4);
});

test("floating inspectors share one surface and adaptive width contract", () => {
  expect(system).toContain(".dp-floating-surface {");
  for (const source of [chatHtml, memory, settings]) expect(source).toContain("dp-floating-surface");
  expect(system).toContain(".dp-peek {");
  expect(system).toContain("width: var(--peek-width)");
  expect(chatHtml).toContain("dp-peek peek");
  expect(memory).toContain("dp-peek peek");
  expect(system).toContain("right: var(--floating-edge)");
  expect(settingsCompact).toContain("right:var(--floating-edge)");
});

test("shared interactive primitives replace local variants", () => {
  expect(system).toContain(".dp-icon-button {");
  expect(system).toContain(".dp-button,");
  expect(system).not.toContain(".dp-toast {");
  expect(system).toContain(".dp-popover {");
  expect(system).toContain(".dp-field,");
  expect(system).toContain(".dp-switch {");
  expect(system).toContain(".dp-segmented {");
  expect(motion).toContain("function syncPopover");
  expect(motion).toContain("function placePopover");
  expect(motion).toContain("const disclosure = Object.freeze");
  expect(chatJs).toContain("motion.popover.sync");
  expect(chatJs).toContain("motion.disclosure.set");
  expect(settings).toContain("BOARD_MOTION.popover.sync");
  expect(automationsJs).toContain("motion.popover.place");
  expect(settings).toContain("BOARD_MOTION.disclosure.sync");
  expect(settings).toContain("BOARD_TOAST.show");
  expect(settingsCompact).toContain("sharedMotion.spinner.start(icon)");
  expect(settingsCompact).toContain("sharedMotion.spinner.settle(icon,active)");
  expect(memory).not.toMatch(/\.btn\s*\{/);
  expect(settings).not.toMatch(/\.btn\s*\{/);
  expect(settings).not.toMatch(/\.switch\s*\{/);
  expect(settings).not.toMatch(/\.text-field\s*\{/);
  expect(settings).not.toMatch(/\.seg\s*\{/);
  expect(automationsCss).not.toMatch(/\.switch\s*\{/);
  expect(settings).toContain('class="dp-switch');
  expect(settings).toContain('class="seg dp-segmented');
  expect(settings).toContain('class="dp-field');
  expect(automationsHtml).toContain('class="dp-switch"');
  expect(automationsJs).toContain('.approval-row .dp-switch');
  expect(automationsJs).not.toContain('.approval-row .switch');
  for (const source of [chatHtml, memory, settings]) expect(source).toContain("dp-icon-button");
  expect(chatHtml).toContain("dp-popover");
  expect(settings).toContain("dp-popover");
});

test("all mockup notifications use one shared Sonner adapter", () => {
  expect(sonner).toContain('import("https://esm.sh/sonner@2');
  expect(sonner).toContain("toast.custom");
  expect(sonner).toContain("window.BOARD_TOAST");
  expect(sonner).toContain('position: "bottom-right"');
  expect(sonner).toContain("visibleToasts: 3");
  for (const page of [home, chatHtml, automationsHtml, memory, settings, overlaysHtml, areaHtml]) {
    expect(page).toContain("board-sonner.js?v=20260722-1");
    expect(page).not.toContain("dp-toast");
  }
  for (const source of [motion, chatJs, automationsJs, settings, home, overlaysHtml]) {
    expect(source).not.toContain("BOARD_MOTION.toast");
  }
  expect(memory).not.toContain("Toaster as Sonner");
});

test("shared geometry owns repeated dimensions and computes placement", () => {
  for (const token of ["--breakpoint-compact", "--conversation-rail-base-width", "--instrument-collapsed-width", "--memory-chrome-top", "--memory-peek-top", "--range-edge-inset", "--composer-max-input-height"]) {
    expect(system).toContain(token);
  }
  for (const source of localSources) {
    const localDimensions = [...source.matchAll(/(--[\w-]+):\s*-?\d*\.?\d+(?:px|rem)\b/g)].map(match => match[1]);
    expect(localDimensions.every(name => name === "--fill-x" || name === "--marker-x")).toBe(true);
  }
  expect(motion).toContain("function shouldPlaceAbove");
  expect(motion).toContain("function maxWidthQuery");
  expect(motion).toContain("function centeredStart");
  expect(motion).toContain("function placeAfter");
  expect(motion).toContain("function mirroredInset");
  expect(motion).toContain("return rect.left");
  expect(motion).toContain("button.style.right = `${mirroredInset(trafficRect)}px`");
  expect(motion).not.toContain("button.style.right = `${inset}px`");
  expect(chatJs).not.toContain("const RAIL_BASE_W = 12");
  expect(memory).not.toContain("const ORNAMENT_COLLAPSED_WIDTH = 36");
  expect(settings).not.toContain("innerWidth<=740");
  expect(settings).not.toContain("innerHeight-rect.bottom<280");
});

test("progressive blur and sheet motion use shared engines", () => {
  expect(chatJs).toContain("motion.progressiveBlur.mount");
  expect(memory).toContain("MOTION.progressiveBlur.mount");
  expect(memory).toContain("MOTION.surface.show(sheet3");
  expect(memory).toContain("MOTION.surface.hide(sheet3");
  expect(memory).not.toContain("sheetLayerAnimation");
});

test("context menus and surface geometry have one shared contract", () => {
  expect(system).toContain("--r-menu: 10px;");
  expect(system).toContain("--r-panel: var(--r-shell-square);");
  expect(system).toContain("--surface-radius: var(--r-panel);");
  expect(system).toMatch(/\.dp-context-menu\s*\{[^}]*position:\s*fixed;/s);
  expect(system).toMatch(/\.dp-context-menu\s*\{[^}]*border-radius:\s*var\(--r-menu\);/s);
  expect(motion).toContain("function bindContextMenus");
  expect(motion).toContain('dataset.contextActions');
  expect(motion).toContain('new CustomEvent("dp:context-action"');
  expect(motion).toContain('event.key === "ContextMenu"');
  expect(motion).toContain('event.shiftKey && event.key === "F10"');
  expect(motion).toContain("contextMenu.style.left");
  expect(motion).toContain("contextMenu.style.top");

  const contextTargets = new Map([
    ["Home", home],
    ["Chat", chatHtml],
    ["Automations", automationsJs],
    ["Memory", memory],
    ["Settings", settings],
    ["Area Room", areaHtml],
    ["Overlays", overlaysHtml],
  ]);
  for (const [name, source] of contextTargets) {
    expect(source, `${name} must opt meaningful entities into the shared context menu`).toContain("data-context-actions");
  }
  for (const [name, source] of activeStyleSources) {
    if (name === "app rail") continue;
    expect(source, `${name} must not define a page-local context menu`).not.toMatch(/\.context-menu\s*\{/);
  }
});
