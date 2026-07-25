import { expect, test } from "bun:test";

test("context save action uses the mockup's shared primary button", async () => {
  const source = await Bun.file(
    new URL("../src/features/settings/components/ContextTab.tsx", import.meta.url),
  ).text();

  expect(source).toContain('<Button type="submit" variant="primary"');
  expect(source).toContain('{saving ? "Saving" : "Save changes"}');
  expect(source).not.toContain("<SaveButton");
  expect(source).not.toContain("disabled={!dirty}");
});

test("shared buttons retain mockup typography on button and status elements", async () => {
  const css = await Bun.file(
    new URL("../src/design/foundation.css", import.meta.url),
  ).text();

  expect(css).toMatch(/\.arden-button\s*\{[^}]*font-weight:\s*520;/s);
  expect(css).not.toContain(":where(.arden-button)");
});

test("shared buttons emit the mockup's canonical button classes", async () => {
  const source = await Bun.file(
    new URL("../src/components/ui/Button.tsx", import.meta.url),
  ).text();

  expect(source).toContain('"btn arden-button"');
  expect(source).toContain('variant === "primary" && "primary"');
  expect(source).toContain('variant === "quiet" && "quiet"');
  expect(source).toContain('variant === "danger" && "danger"');
});
