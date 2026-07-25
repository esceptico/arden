import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { CopyGlyph } from "@/components/ui/CopyGlyph";

test("copy glyph keeps both icons mounted and changes only swap state", () => {
  const idle = renderToStaticMarkup(<CopyGlyph copied={false} size={14} />);
  const copied = renderToStaticMarkup(<CopyGlyph copied size={14} />);

  expect(idle).toContain('class="t-icon-swap"');
  expect(idle).toContain('data-state="a"');
  expect((idle.match(/class="t-icon"/g) ?? []).length).toBe(2);
  expect(copied).toContain('data-state="b"');
  expect((copied.match(/class="t-icon"/g) ?? []).length).toBe(2);
});

test("the board motion engine exposes the same icon-swap variables", () => {
  const source = readFileSync(
    new URL("../src/lib/boardMotionEngine.js", import.meta.url),
    "utf8",
  );

  expect(source).toContain('"--icon-swap-dur": `${duration.iconSwap}ms`');
  expect(source).toContain("blur(var(--icon-swap-blur))");
  expect(source).toContain("scale(var(--icon-swap-start-scale))");
});
