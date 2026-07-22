import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

const routes = {
  Home: "./board-home.html",
  Chat: "./board-chat.html",
  Automations: "./board-automations.html",
  Memory: "./board-memory.html",
  Settings: "./board-settings.html",
  "Area Room": "./board-area-room.html",
  Overlays: "./board-system-overlays.html",
} as const;

const pages = {
  Home: read("../../../docs/mockups/board-home.html"),
  Chat: read("../../../docs/mockups/board-chat.html"),
  Automations: read("../../../docs/mockups/board-automations.html"),
  Memory: read("../../../docs/mockups/board-memory.html"),
  Settings: read("../../../docs/mockups/board-settings.html"),
  "Area Room": read("../../../docs/mockups/board-area-room.html"),
  Overlays: read("../../../docs/mockups/board-system-overlays.html"),
} as const;
const systemSource = read("../../../docs/mockups/board-system.css");

const escape = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

test("every primary mockup exposes the complete native review navigation", () => {
  for (const [current, source] of Object.entries(pages)) {
    const switcher = (source.match(/<details class="dp-review-nav">([\s\S]*?)<\/details>/)?.[1] ?? "").replace(/\s+/g, "");
    expect(switcher).not.toBe("");
    expect(switcher).not.toContain("<button");

    for (const [label, href] of Object.entries(routes)) {
      expect(switcher).toMatch(new RegExp(`<a[^>]*href="${escape(href)}"[^>]*>${escape(label.replace(/\s+/g, ""))}</a>`));
    }

    expect(switcher).toMatch(
      new RegExp(`<a[^>]*href="${escape(routes[current as keyof typeof routes])}"[^>]*aria-current="page"[^>]*>${escape(current.replace(/\s+/g, ""))}</a>`),
    );
  }
});

test("review navigation uses a menu surface rather than a giant pill", () => {
  const menuRule = systemSource.match(/\.dp-review-nav nav \{([^}]*)\}/)?.[1] ?? "";
  expect(menuRule).toContain("border-radius: var(--r-house)");
  expect(menuRule).not.toContain("border-radius: var(--r-cluster)");
});

test("existing shell destinations use native links", () => {
  expect(pages.Home).toMatch(/<a class="nav-row on" href="\.\/board-home\.html"[^>]* aria-current="page"[^>]*>/);
  expect(pages.Home).toMatch(/<a class="nav-row" href="\.\/board-settings\.html">[\s\S]*?<span>Settings<\/span><\/a>/);
  expect(pages.Home.match(/<a class="area-row" href="\.\/board-area-room\.html"[^>]*>/g)?.length).toBe(4);

  expect(pages.Chat).toMatch(/<a class="nav-row" href="\.\/board-home\.html">[\s\S]*?<span>Home<\/span><\/a>/);
  expect(pages.Chat).toMatch(/<a class="nav-row" href="\.\/board-settings\.html">[\s\S]*?<span>Settings<\/span><\/a>/);
  expect(pages.Chat.match(/<a class="session[^\"]*" href="\.\/board-chat\.html/gi)?.length).toBe(6);

  expect(pages.Automations).toMatch(/<a class="rail-utility" href="\.\/board-settings\.html">[\s\S]*?Automation settings[\s\S]*?<\/a>/);
  expect(pages["Area Room"]).not.toContain("Area settings");
  expect(pages.Overlays).toMatch(/<a class="command-row"[^>]*href="\.\/board-chat\.html"[^>]*>[\s\S]*?New chat[\s\S]*?<\/a>/);
  expect(pages.Overlays).toMatch(/<a class="command-row"[^>]*href="\.\/board-area-room\.html"[^>]*>[\s\S]*?Open folder[\s\S]*?<\/a>/);
  expect(pages.Overlays).toMatch(/<a class="command-row"[^>]*href="\.\/board-settings\.html"[^>]*>[\s\S]*?General[\s\S]*?<\/a>/);
});

test("workspace rails expose Chat as a primary destination", () => {
  for (const source of [pages.Home, pages["Area Room"]]) {
    const appNav = source.match(/<nav class="rail-nav" aria-label="App">([\s\S]*?)<\/nav>/)?.[1] ?? "";
    expect(appNav).toMatch(
      /Mission Control[\s\S]*?<a class="nav-row" href="\.\/board-chat\.html"[^>]*>[\s\S]*?#dp-chat[\s\S]*?<span>Chat<\/span><\/a>[\s\S]*?Automations[\s\S]*?Memory/,
    );
  }
});

test("ordinary Chat session clicks preserve scene switching", () => {
  const chatScript = read("../../../docs/mockups/board-chat.js");
  expect(chatScript).toContain("$$('.session[data-session]').forEach(button => button.addEventListener('click', event => {");
  expect(chatScript).toContain("if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;");
  expect(chatScript).toMatch(/\.session\[data-session\][^;]+[\s\S]*?event\.preventDefault\(\);[\s\S]*?setScene\(sceneForSession/);
});

test("secondary workspaces always expose a visible way back Home", () => {
  for (const source of [pages.Automations, pages.Memory, pages.Settings, pages["Area Room"]]) {
    expect(source.replace(/\s+/g, "")).toMatch(
      /<aclass="dp-shell-toggledp-shell-back"href="\.\/board-home\.html"aria-label="BacktoHome">[\s\S]*?#dp-arrow-left[\s\S]*?<\/a>/,
    );
    expect(source).not.toMatch(/<(?:header|div) class="(?:rail-head|rail-title)"[^>]*>[\s\S]{0,240}?dp-shell-back/);
  }

  const system = read("../../../docs/mockups/board-system.css");
  expect(system).toContain("--chrome-back-control-left:");
  expect(system).toContain(".dp-shell-back");
});
