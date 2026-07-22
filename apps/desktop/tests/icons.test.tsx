import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { Copy, House, PanelRightOpen } from "../src/components/icons";

test("desktop semantic exports render Hugeicons", () => {
  const markup = renderToStaticMarkup(
    <>
      <Copy aria-label="copy" />
      <House aria-label="home" />
      <PanelRightOpen aria-label="open right panel" />
    </>,
  );

  expect((markup.match(/viewBox="0 0 24 24"/g) ?? []).length).toBe(3);
  expect((markup.match(/fill="none"/g) ?? []).length).toBe(3);
});

test("shared mockup icon sprite uses Hugeicons geometry", () => {
  const source = readFileSync(
    new URL("../../../docs/mockups/board-icons.js", import.meta.url),
    "utf8",
  ).replaceAll('\\"', '"');

  const symbols = source.match(/<symbol id="dp-[\s\S]*?<\/symbol>/g) ?? [];
  expect(symbols.length).toBeGreaterThan(30);
  expect(symbols.every((symbol) => symbol.includes('viewBox="0 0 24 24"'))).toBeTrue();
  expect(symbols.some((symbol) => symbol.includes('viewBox="0 0 256 256"'))).toBeFalse();
});

test("all icon-bearing Desk and Paper mockups use Hugeicons geometry", () => {
  const sharedIcons = readFileSync(
    new URL("../../../docs/mockups/board-icons.js", import.meta.url),
    "utf8",
  ).replaceAll('\\"', '"');
  const mockups = [
    "board-chat.html",
    "board-language.html",
    "board-motion.html",
    "board-memory.html",
    "board-settings.html",
    "memory-annotated-page.html",
    "memory-workspace-draft.html",
  ].map((name) =>
    readFileSync(new URL(`../../../docs/mockups/${name}`, import.meta.url), "utf8"),
  );

  for (const source of mockups) {
    const iconSource = source.includes("board-icons.js") ? sharedIcons : source;
    expect(iconSource).toContain("Hugeicons Stroke Rounded");
    expect(iconSource).not.toContain("Phosphor");
    expect(iconSource).not.toContain('viewBox="0 0 256 256"');
    expect(iconSource).not.toContain('d="M216');
    const symbols = iconSource.match(/<symbol [\s\S]*?<\/symbol>/g) ?? [];
    expect(symbols.every((symbol) => symbol.includes('viewBox="0 0 24 24"'))).toBeTrue();
  }
});

test("legacy Memory mockups contain no hand-drawn control icons", () => {
  for (const name of ["memory-annotated-page.html", "memory-workspace-draft.html"]) {
    const source = readFileSync(
      new URL(`../../../docs/mockups/${name}`, import.meta.url),
      "utf8",
    ).replace(/<svg aria-hidden="true" width="0"[\s\S]*?<\/svg>/, "");

    expect(source).not.toMatch(/<svg[^>]*>\s*<(?:path|circle|rect)\b/);
  }
});
