import { afterEach, expect, test } from "bun:test";
import { ACCENT_PALETTES, DEFAULT_ACCENT, applyAccentPalette } from "@/lib/palettes";

afterEach(() => {
  document.getElementById("arden-accent")?.remove();
});

test("every palette has a name and 6-digit hex accents for both themes", () => {
  const hex = /^#[0-9a-f]{6}$/;
  for (const p of ACCENT_PALETTES) {
    expect(p.name.length).toBeGreaterThan(0);
    for (const shade of [p.light.accent, p.light.strong, p.dark.accent, p.dark.strong]) {
      expect(shade).toMatch(hex);
    }
  }
});

test("palette ids are unique and DEFAULT_ACCENT is one of them", () => {
  const ids = ACCENT_PALETTES.map((p) => p.id);
  expect(new Set(ids).size).toBe(ids.length);
  expect(ids).toContain(DEFAULT_ACCENT);
});

test("applyAccentPalette injects one #arden-accent <style> carrying both theme accents", () => {
  applyAccentPalette("ocean");
  const el = document.getElementById("arden-accent");
  expect(el).not.toBeNull();
  expect(el!.tagName).toBe("STYLE");
  const ocean = ACCENT_PALETTES.find((p) => p.id === "ocean")!;
  expect(el!.textContent).toContain(`--accent:${ocean.light.accent}`);
  expect(el!.textContent).toContain(`--accent:${ocean.dark.accent}`);
});

test("re-applying updates the same element instead of duplicating it", () => {
  applyAccentPalette("blue");
  applyAccentPalette("rose");
  expect(document.querySelectorAll("#arden-accent").length).toBe(1);
  const rose = ACCENT_PALETTES.find((p) => p.id === "rose")!;
  expect(document.getElementById("arden-accent")!.textContent).toContain(rose.light.accent);
});

test("an unknown id falls back to the default palette", () => {
  applyAccentPalette("does-not-exist");
  const def = ACCENT_PALETTES.find((p) => p.id === DEFAULT_ACCENT)!;
  expect(document.getElementById("arden-accent")!.textContent).toContain(
    `--accent:${def.light.accent}`,
  );
});
