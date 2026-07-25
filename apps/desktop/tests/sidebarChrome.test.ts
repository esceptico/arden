import { expect, test } from "bun:test";

test("sidebar interactive rows opt out of the Electron drag region", async () => {
  const css = await Bun.file(new URL("../src/design/shell.css", import.meta.url)).text();

  expect(css).toContain(".workspace-rail {");
  expect(css).toContain("-webkit-app-region: drag;");
  expect(css).toContain(".workspace-rail :is(button, input, [role=\"button\"], [role=\"separator\"])");
  expect(css).toContain("-webkit-app-region: no-drag;");
});
