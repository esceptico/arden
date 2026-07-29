import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { AppearanceTab } from "@/features/settings/components/AppearanceTab";
import { THINKING_INTENSITIES } from "@/lib/thinkingIndicator";
import { THINKING_INTENSITY_IDS, useStore } from "@/stores";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

test("Appearance exposes intensity as the working strip's only knob", () => {
  const previous = useStore.getState().prefs;
  useStore.setState({ prefs: { ...previous, thinkingIntensity: "strong" } });
  try {
    const html = renderToStaticMarkup(<AppearanceTab />);
    expect(THINKING_INTENSITIES.map((intensity) => intensity.id)).toEqual(THINKING_INTENSITY_IDS);
    expect(html).toContain('aria-label="Working strip intensity"');
    // The four-treatment picker is gone: one indicator, no treatment choice.
    expect(html).not.toContain("Thinking indicator treatment");
    expect(html).not.toContain("settings-thinking-card");
    expect(html).not.toContain("data-thinking-animation");
  } finally {
    useStore.setState({ prefs: previous });
  }
});

test("the strip is the composer's single working locus", () => {
  const composer = read("../src/features/chat/components/Composer.tsx");
  const toolbar = read("../src/features/chat/components/ComposerToolbar.tsx");
  const css = read("../src/design/chat.css");

  expect(composer).toContain('data-thinking-state={showWorking ? "waiting" : "idle"}');
  expect(composer).toContain("<WorkingStrip active={showWorking} label={stripLabel} />");
  expect(toolbar).toContain('data-working={working ? "true" : "false"}');
  // The toolbar's duplicate "working" word retired with the treatments —
  // two indicators for one state was the thing being removed.
  expect(toolbar).not.toContain("board-composer__status");
  for (const dead of ["comet", "breath", "orbit"]) {
    expect(composer).not.toContain(dead);
    expect(css).not.toContain(`data-thinking-animation="${dead}"`);
  }
  expect(css).toContain(".board-composer__strip");
  expect(css).toContain("@media (prefers-reduced-motion: reduce)");
});

test("only the strip's height animates, and intensity drives the field alone", () => {
  const css = read("../src/design/chat.css");

  const openRule = css.match(/\.board-composer__strip\s*\{([\s\S]*?)\n\}/)?.[1] ?? "";
  expect(openRule).toContain("height: 0");
  expect(openRule).toContain("transition: height");
  // Padding and the rule live on the inner element; animating the outer box's
  // spacing would slide the type as the row opens.
  expect(openRule).not.toContain("padding");

  for (const [intensity, strength] of [["subtle", ".55"], ["strong", "1"]] as const) {
    const rule = css.match(
      new RegExp(`\\.board-composer\\[data-thinking-intensity="${intensity}"\\]\\s*\\{([\\s\\S]*?)\\n\\}`),
    )?.[1] ?? "";
    expect(rule).toContain(`--strip-field-strength: ${strength}`);
  }
});
