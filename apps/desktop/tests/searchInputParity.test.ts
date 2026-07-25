import { expect, test } from "bun:test";

test("the shared search shell exclusively owns the rounded focus treatment", async () => {
  const css = await Bun.file(
    new URL("../src/design/foundation.css", import.meta.url),
  ).text();

  const input = css.match(
    /\.arden-search-shell > \.arden-search-input\s*\{([^}]*)\}/s,
  )?.[1] ?? "";
  const focused = css.match(
    /\.arden-search-shell > \.arden-search-input:focus,[^{]*\{([^}]*)\}/s,
  )?.[1] ?? "";

  expect(input).toContain("border: 0");
  expect(input).toContain("outline: 0");
  expect(input).toContain("box-shadow: none");
  expect(focused).toContain("border: 0");
  expect(focused).toContain("outline: 0");
  expect(focused).toContain("box-shadow: none");
});
