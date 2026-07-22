import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import * as Hugeicons from "@hugeicons/core-free-icons";

const chatIcons = {
  "i-panel-left": Hugeicons.PanelLeftCloseIcon,
  "i-panel-right": Hugeicons.PanelRightCloseIcon,
  "i-list-bullets": Hugeicons.LeftToRightListBulletIcon,
  "i-chat": Hugeicons.BubbleChatIcon,
  "i-grid": Hugeicons.DashboardSquare01Icon,
  "i-clock": Hugeicons.Clock01Icon,
  "i-search": Hugeicons.Search01Icon,
  "i-plus": Hugeicons.Add01Icon,
  "i-activity": Hugeicons.Activity01Icon,
  "i-sources": Hugeicons.BookOpen01Icon,
  "i-dots": Hugeicons.MoreHorizontalIcon,
  "i-x": Hugeicons.Cancel01Icon,
  "i-chevron": Hugeicons.ArrowDown01Icon,
  "i-files": Hugeicons.Folder01Icon,
  "i-terminal": Hugeicons.ComputerTerminal01Icon,
  "i-globe": Hugeicons.Globe02Icon,
  "i-robot": Hugeicons.AiChat02Icon,
  "i-shield-slash": Hugeicons.ShieldBanIcon,
  "i-file": Hugeicons.File01Icon,
  "i-pencil": Hugeicons.PencilEdit02Icon,
  "i-copy": Hugeicons.Copy01Icon,
  "i-git-branch": Hugeicons.GitBranchIcon,
  "i-attach": Hugeicons.ImageAdd01Icon,
  "i-arrow-up": Hugeicons.ArrowUp01Icon,
  "i-stop": Hugeicons.StopIcon,
  "i-stop-circle": Hugeicons.StopCircleIcon,
  "i-warning": Hugeicons.Alert02Icon,
  "i-check": Hugeicons.Tick02Icon,
  "i-arrow-left": Hugeicons.ArrowLeft02Icon,
  "i-arrow-right": Hugeicons.ArrowRight02Icon,
  "i-arrows-out": Hugeicons.Maximize02Icon,
  "i-arrows-in": Hugeicons.Minimize02Icon,
  "i-home": Hugeicons.Home01Icon,
  "i-zap": Hugeicons.ZapIcon,
  "i-brain": Hugeicons.Brain01Icon,
  "i-settings": Hugeicons.Settings01Icon,
  "i-sliders": Hugeicons.SlidersHorizontalIcon,
  "i-folder": Hugeicons.Folder01Icon,
  "i-inbox": Hugeicons.InboxIcon,
  "i-sun": Hugeicons.Sun01Icon,
  "i-moon": Hugeicons.Moon02Icon,
};

const memoryIcons = {
  "p-folder": Hugeicons.Folder01Icon,
  "p-notebook": Hugeicons.Notebook01Icon,
  "p-facts": Hugeicons.CheckListIcon,
  "p-caret-down": Hugeicons.ArrowDown01Icon,
  "p-sidebar-hide": Hugeicons.PanelLeftCloseIcon,
  "p-sidebar-show": Hugeicons.PanelLeftOpenIcon,
  "p-close": Hugeicons.Cancel01Icon,
  "p-edit": Hugeicons.PencilEdit02Icon,
  "p-caret-right": Hugeicons.ArrowRight01Icon,
  "p-more": Hugeicons.MoreHorizontalIcon,
};

const settingsIcons = {
  "i-connection": Hugeicons.Plug01Icon,
  "i-appearance": Hugeicons.PaintBoardIcon,
  "i-models": Hugeicons.AiMagicIcon,
  "i-agent": Hugeicons.AiBrain01Icon,
  "i-context": Hugeicons.Database01Icon,
  "i-providers": Hugeicons.Key01Icon,
  "i-integrations": Hugeicons.Plug02Icon,
  "i-tools": Hugeicons.Wrench01Icon,
  "i-mcp": Hugeicons.Layers01Icon,
  "i-archive": Hugeicons.Archive01Icon,
  "i-sidebar": Hugeicons.PanelLeftCloseIcon,
  "i-search": Hugeicons.Search01Icon,
  "i-refresh": Hugeicons.Refresh01Icon,
  "i-check": Hugeicons.Tick02Icon,
  "i-plus": Hugeicons.Add01Icon,
  "i-user-plus": Hugeicons.UserAdd01Icon,
  "i-caret-down": Hugeicons.ArrowDown01Icon,
  "i-policy-approve": Hugeicons.CheckmarkCircle02Icon,
  "i-policy-ask": Hugeicons.HelpCircleIcon,
  "i-policy-deny": Hugeicons.BanIcon,
};

const motionIcons = {
  ...memoryIcons,
  "p-refresh": Hugeicons.Refresh01Icon,
};

const workspaceIcons = {
  "mw-search": Hugeicons.Search01Icon,
  "mw-notebook": Hugeicons.Notebook01Icon,
  "mw-file-add": Hugeicons.FileAddIcon,
  "mw-folder-add": Hugeicons.FolderAddIcon,
  "mw-sort": Hugeicons.Sorting01Icon,
  "mw-panel-left": Hugeicons.PanelLeftCloseIcon,
  "mw-arrow-left": Hugeicons.ArrowLeft01Icon,
  "mw-arrow-right": Hugeicons.ArrowRight01Icon,
  "mw-edit": Hugeicons.PencilEdit02Icon,
  "mw-panel-right": Hugeicons.PanelRightCloseIcon,
  "mw-close": Hugeicons.Cancel01Icon,
  "mw-add": Hugeicons.Add01Icon,
  "mw-chevron-down": Hugeicons.ArrowDown01Icon,
  "mw-pin": Hugeicons.PinIcon,
  "mw-chevron-right": Hugeicons.ArrowRight01Icon,
  "mw-link": Hugeicons.Link01Icon,
  "mw-list": Hugeicons.LeftToRightListBulletIcon,
  "mw-clock": Hugeicons.Clock01Icon,
  "mw-calendar": Hugeicons.Calendar01Icon,
  "mw-tag": Hugeicons.Tag01Icon,
  "mw-hash": Hugeicons.HashtagIcon,
  "mw-checkbox": Hugeicons.CheckmarkSquare02Icon,
  "mw-text": Hugeicons.TextIcon,
  "mw-layers": Hugeicons.Layers01Icon,
};

const annotatedIcons = {
  "ma-theme": Hugeicons.Sun01Icon,
  "ma-close": Hugeicons.Cancel01Icon,
};

const attributeNames = {
  clipRule: "clip-rule",
  fillRule: "fill-rule",
  strokeDasharray: "stroke-dasharray",
  strokeLinecap: "stroke-linecap",
  strokeLinejoin: "stroke-linejoin",
  strokeMiterlimit: "stroke-miterlimit",
  strokeWidth: "stroke-width",
};

function escapeAttribute(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function renderNode([tag, attributes]) {
  const rendered = Object.entries(attributes)
    .filter(([name]) => name !== "key")
    .map(([name, value]) => `${attributeNames[name] ?? name}="${escapeAttribute(value)}"`)
    .join(" ");
  return `<${tag} ${rendered}/>`;
}

function renderDefinitions(icons) {
  return Object.entries(icons)
    .map(
      ([id, icon]) =>
        `  <symbol id="${id}" viewBox="0 0 24 24" fill="none">${icon.map(renderNode).join("")}</symbol>`,
    )
    .join("\n");
}

function renderSprite(icons) {
  return `<svg aria-hidden="true" width="0" height="0" style="position:absolute"><defs>\n  <!-- Hugeicons Stroke Rounded, generated from @hugeicons/core-free-icons. -->\n${renderDefinitions(icons)}\n</defs></svg>`;
}

const sharedDeskPaperAliases = {
  "i-panel-left": "dp-panel-left-close",
  "i-sidebar": "dp-panel-left-close",
  "p-sidebar-hide": "dp-panel-left-close",
  "i-sidebar-show": "dp-panel-left-open",
  "p-sidebar-show": "dp-panel-left-open",
  "i-chevron": "dp-chevron-down",
  "i-caret-down": "dp-chevron-down",
  "p-caret-down": "dp-chevron-down",
  "i-arrow-right": "dp-arrow-right",
  "p-caret-right": "dp-arrow-right",
  "i-folder": "dp-folder",
  "p-folder": "dp-folder",
  "i-x": "dp-close",
  "p-close": "dp-close",
  "i-pencil": "dp-edit",
  "p-edit": "dp-edit",
  "i-dots": "dp-more",
  "i-more": "dp-more",
  "p-more": "dp-more",
};

function sharedDeskPaperId(id) {
  return sharedDeskPaperAliases[id] ?? `dp-${id.replace(/^[ip]-/, "")}`;
}

function sharedDeskPaperIcons() {
  const icons = {};
  for (const source of [chatIcons, memoryIcons, settingsIcons]) {
    for (const [id, icon] of Object.entries(source)) {
      icons[sharedDeskPaperId(id)] ??= icon;
    }
  }
  return icons;
}

function syncSharedDeskPaperIcons() {
  const icons = sharedDeskPaperIcons();
  const sprite = renderSprite(icons);
  const outputPath = fileURLToPath(
    new URL("../../../docs/mockups/board-icons.js", import.meta.url),
  );
  writeFileSync(
    outputPath,
    `// Generated by apps/desktop/scripts/sync-mockup-icons.mjs. Do not hand-edit.\n` +
      `(() => {\n` +
      `  if (document.getElementById("board-icon-sprite")) return;\n` +
      `  const template = document.createElement("template");\n` +
      `  template.innerHTML = ${JSON.stringify(sprite.replace("<svg ", '<svg id="board-icon-sprite" '))};\n` +
      `  document.documentElement.prepend(template.content);\n` +
      `})();\n`,
  );

  for (const name of ["board-chat.html", "board-memory.html", "board-settings.html", "board-home.html"]) {
    const path = fileURLToPath(new URL(`../../../docs/mockups/${name}`, import.meta.url));
    let source = readFileSync(path, "utf8").replace(
      /<svg aria-hidden="true" width="0" height="0" style="position:absolute"><defs>[\s\S]*?<\/defs><\/svg>\s*/,
      "",
    );
    const sourceIds = [...new Set([...Object.keys(chatIcons), ...Object.keys(memoryIcons), ...Object.keys(settingsIcons)])]
      .sort((left, right) => right.length - left.length);
    for (const id of sourceIds) {
      source = source.replaceAll(`#${id}`, `#${sharedDeskPaperId(id)}`);
    }
    if (!source.includes("board-icons.js")) {
      source = source.replace(
        /(<script src="\.\/board-motion\.js\?v=[^"]+"><\/script>)/,
        `<script src="./board-icons.js?v=20260719-2"></script>\n$1`,
      );
    }
    writeFileSync(path, source);
  }
}

function syncSprite(name, icons) {
  const path = fileURLToPath(new URL(`../../../docs/mockups/${name}`, import.meta.url));
  const source = readFileSync(path, "utf8");
  const sprite = renderSprite(icons);
  const next = source
    .replace(
      /<svg(?: aria-hidden="true")? width="0" height="0" style="position:absolute"(?: aria-hidden="true")?><defs>[\s\S]*?<\/defs><\/svg>/,
      sprite,
    )
    .replace(
      /<svg width="0" height="0" style="position:absolute"><symbol[\s\S]*?<\/symbol><\/svg>/,
      sprite,
    )
    .replaceAll('viewBox="0 0 256 256"', 'viewBox="0 0 24 24"')
    .replaceAll("fill:currentColor", "fill:none");

  if (!next.includes("Hugeicons Stroke Rounded")) {
    throw new Error(`Mockup icon sprite was not found: ${name}`);
  }
  writeFileSync(path, next);
}

function syncLanguage() {
  const path = fileURLToPath(
    new URL("../../../docs/mockups/desk-paper-language.html", import.meta.url),
  );
  let source = readFileSync(path, "utf8");
  if (!source.includes("Hugeicons Stroke Rounded")) {
    const iconIds = ["p-folder", "p-notebook", "p-facts"];
    let index = 0;
    source = source.replace(
      /<svg viewBox="0 0 256 256" aria-hidden="true"><path [\s\S]*?<\/path><\/svg>/g,
      () => `<svg viewBox="0 0 24 24" aria-hidden="true"><use href="#${iconIds[index++]}"/></svg>`,
    );
    if (index !== iconIds.length) throw new Error("Language mockup icon set changed");
    source = source.replace(
      '<div class="page">',
      `${renderSprite({
        "p-folder": memoryIcons["p-folder"],
        "p-notebook": memoryIcons["p-notebook"],
        "p-facts": memoryIcons["p-facts"],
      })}\n\n<div class="page">`,
    );
  }
  source = source
    .replaceAll('viewBox="0 0 256 256"', 'viewBox="0 0 24 24"')
    .replaceAll("fill:currentColor", "fill:none");
  writeFileSync(path, source);
}

function compactGeometry(value) {
  return value.replace(/\s+/g, "");
}

function syncInlineIcons(name, icons, geometry, mount) {
  const path = fileURLToPath(new URL(`../../../docs/mockups/${name}`, import.meta.url));
  let source = readFileSync(path, "utf8");
  const sprite = renderSprite(icons);

  if (source.includes("Hugeicons Stroke Rounded")) {
    source = source.replace(
      /<svg aria-hidden="true" width="0" height="0" style="position:absolute"><defs>[\s\S]*?<\/defs><\/svg>/,
      sprite,
    );
  } else {
    source = source.replace(mount, `${sprite}\n\n${mount}`);
  }

  source = source.replace(/<svg([^>]*)>([\s\S]*?)<\/svg>/g, (svg, attributes, inner) => {
    const id = geometry.get(compactGeometry(inner));
    return id ? `<svg${attributes}><use href="#${id}"/></svg>` : svg;
  });
  writeFileSync(path, source);
}

const workspaceGeometry = new Map([
  ['<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>', "mw-search"],
  ['<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M15 3v18"/><path d="M6 8h5M6 12h5M6 16h5"/>', "mw-notebook"],
  ['<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M9 15h6"/><path d="M12 12v6"/>', "mw-file-add"],
  ['<path d="M12 10v6"/><path d="M9 13h6"/><path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>', "mw-folder-add"],
  ['<path d="m21 16-4 4-4-4"/><path d="M17 20V4"/><path d="m3 8 4-4 4 4"/><path d="M7 4v16"/>', "mw-sort"],
  ['<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>', "mw-panel-left"],
  ['<path d="M15 18l-6-6 6-6"/>', "mw-arrow-left"],
  ['<path d="M9 18l6-6-6-6"/>', "mw-arrow-right"],
  ['<path d="M17 3l4 4L8 20l-5 1 1-5L17 3z"/>', "mw-edit"],
  ['<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>', "mw-panel-right"],
  ['<path d="M6 6l12 12M18 6L6 18"/>', "mw-close"],
  ['<path d="M12 5v14M5 12h14"/>', "mw-add"],
  ['<path d="M6 9l6 6 6-6"/>', "mw-chevron-down"],
  ['<path d="M12 17v5"/><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1z"/>', "mw-pin"],
  ['<path d="M9 6l6 6-6 6"/>', "mw-chevron-right"],
  ['<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>', "mw-link"],
  ['<path d="M8 6h13M8 12h13M8 18h13"/><path d="M3.5 6h.01M3.5 12h.01M3.5 18h.01"/>', "mw-list"],
  ['<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>', "mw-clock"],
  ['<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>', "mw-calendar"],
  ['<path d="M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 .586 1.414l8.704 8.704a2.426 2.426 0 0 0 3.42 0l6.58-6.58a2.426 2.426 0 0 0 0-3.42z"/><circle cx="7.5" cy="7.5" r=".5"/>', "mw-tag"],
  ['<path d="M4 9h16M4 15h16M10 3 8 21M16 3l-2 18"/>', "mw-hash"],
  ['<rect x="3" y="3" width="18" height="18" rx="3"/><path d="m9 12 2 2 4-4"/>', "mw-checkbox"],
  ['<path d="M3 6h18M3 12h12M3 18h15"/>', "mw-text"],
  ['<path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83z"/><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/><path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/>', "mw-layers"],
].map(([geometry, id]) => [compactGeometry(geometry), id]));

function syncMotionInlineIcons() {
  const path = fileURLToPath(
    new URL("../../../docs/mockups/desk-paper-motion.html", import.meta.url),
  );
  let source = readFileSync(path, "utf8");
  const ids = ["p-folder", "p-notebook", "p-facts"];
  let index = 0;
  source = source.replace(
    /<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">(?!<use)[\s\S]*?<\/svg>/g,
    () => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><use href="#${ids[index++]}"/></svg>`,
  );
  if (index !== 0 && index !== ids.length) throw new Error("Motion mockup icon set changed");
  writeFileSync(path, source);
}

function syncAnnotated() {
  const path = fileURLToPath(
    new URL("../../../docs/mockups/memory-annotated-page.html", import.meta.url),
  );
  let source = readFileSync(path, "utf8")
    .replace("&#9680;", '<svg viewBox="0 0 24 24" aria-hidden="true"><use href="#ma-theme"/></svg>')
    .replace("&#10005;", '<svg viewBox="0 0 24 24" aria-hidden="true"><use href="#ma-close"/></svg>');
  if (!source.includes(".iconbtn svg {")) {
    source = source.replace(
      ".iconbtn:hover {",
      ".iconbtn svg { width: 14px; height: 14px; }\n  .iconbtn:hover {",
    );
  }
  writeFileSync(path, source);
  syncInlineIcons("memory-annotated-page.html", annotatedIcons, new Map(), '<div class="shell">');
}

if (process.argv.includes("--desk-paper-shared")) {
  syncSharedDeskPaperIcons();
} else {
  syncSharedDeskPaperIcons();
  syncSprite("desk-paper-motion.html", motionIcons);
  syncLanguage();
  syncMotionInlineIcons();
  syncInlineIcons("memory-workspace-draft.html", workspaceIcons, workspaceGeometry, '<div class="app">');
  syncAnnotated();
}
