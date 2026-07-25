import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
const css = read("../src/design/area.css");

function mediaBlock(source: string, start: string, end: string): string {
  const startAt = source.indexOf(start);
  const endAt = source.indexOf(end, startAt + start.length);
  return source.slice(startAt, endAt < 0 ? undefined : endAt);
}

test("Area room preserves the mock's regular-short request rhythm", () => {
  const compact = mediaBlock(css, "@media (max-height: 47rem)", "@media (max-height: 42rem)");
  const short = mediaBlock(css, "@media (max-height: 42rem)", "@media (prefers-reduced-motion: reduce)");

  // The active mock uses the 47rem threshold only for the smaller type step.
  // Its reason and verb spacing remain intact until the 42rem pressure mode.
  expect(compact).toContain(".board-area-deck__card h2 { margin-top: .5rem; font-size: var(--text-xl); }");
  expect(compact).not.toContain(".board-area-deck__reason {");
  expect(compact).not.toContain(".board-area-deck__actions {");
  expect(short).toContain(".board-area-deck__reason {");
  expect(short).toContain("-webkit-line-clamp: 1;");
  expect(short).toContain(".board-area-deck__actions { margin-top: var(--space-3); }");
});

test("Area peer rows retain the mock's vertical text rail", () => {
  const row = css.match(/\.board-area-deck__rest-row\s*\{([^}]*)\}/)?.[1] ?? "";
  const title = css.match(/\.board-area-deck__rest-row strong\s*\{([^}]*)\}/)?.[1] ?? "";

  // Mock buttons keep their one-pixel vertical inset and inherit the shared
  // unitless line-height. Tailwind's reset removes both unless Area restores
  // them explicitly, which visibly lifts peer-row labels.
  expect(row).toContain("padding-block: 1px;");
  expect(title).toContain("font-size: var(--text-sm);");
  expect(title).toContain("font-weight: 560;");
  expect(title).not.toContain("line-height:");
});
