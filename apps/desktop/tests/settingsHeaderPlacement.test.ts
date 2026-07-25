import { expect, test } from "bun:test";

test("settings headers return to the mockup top position on every page change", async () => {
  const modal = await Bun.file(
    new URL("../src/features/settings/components/SettingsModal.tsx", import.meta.url),
  ).text();
  const css = await Bun.file(
    new URL("../src/design/settings.css", import.meta.url),
  ).text();

  expect(css).toMatch(/\.settings-page\s*\{[^}]*padding:\s*102px var\(--content-gutter\) 116px;/s);
  expect(modal).toContain("scrollRef.current.scrollTop = 0");
  expect(modal).toContain("[active, open]");
  expect(modal).toContain("viewportRef={scrollRef}");
  expect(modal).toContain('viewportClassName="settings-scroll scroll-fade"');
});
