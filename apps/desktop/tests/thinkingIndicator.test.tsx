import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { act } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createRoot, type Root } from "react-dom/client";
import { AppearanceTab } from "@/features/settings/components/AppearanceTab";
import {
  THINKING_INTENSITIES,
  THINKING_TREATMENTS,
} from "@/lib/thinkingIndicator";
import { MOTION } from "@/lib/tokens/motion";
import {
  THINKING_ANIMATION_IDS,
  THINKING_INTENSITY_IDS,
  useStore,
} from "@/stores";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

function mount(): { el: HTMLElement; root: Root; restore: () => void } {
  const el = document.createElement("div");
  document.body.append(el);
  return { el, root: createRoot(el), restore: () => el.remove() };
}

test("Appearance renders one accessible card for every canonical thinking treatment", () => {
  const previous = useStore.getState().prefs;
  useStore.setState({
    prefs: { ...previous, thinkingAnimation: "breath", thinkingIntensity: "strong" },
  });
  try {
    const html = renderToStaticMarkup(<AppearanceTab />);
    expect(THINKING_TREATMENTS.map((treatment) => treatment.id)).toEqual(THINKING_ANIMATION_IDS);
    expect(THINKING_INTENSITIES.map((intensity) => intensity.id)).toEqual(THINKING_INTENSITY_IDS);
    expect(html).toContain('aria-label="Thinking indicator treatment"');
    expect((html.match(/role="radio"/g) ?? []).length).toBeGreaterThanOrEqual(4);
    expect(html).toContain('data-thinking-animation="breath"');
    expect(html).toContain('aria-checked="true"');
    expect(html).toContain("Single arc travels around the rim");
    expect(html).toContain("Border drifts toward accent — no motion");
    expect(html).toContain("Spinner around the send button only");
    expect(html).toContain('class="settings-shortcut-keys"');
    // Mock settings has one Enter hint plus Command–Shift–Space.
    expect((html.match(/class="arden-kbd"/g) ?? [])).toHaveLength(4);
  } finally {
    useStore.setState({ prefs: previous });
  }
});

test("thinking treatment cards use shared roving-radio keyboard selection", async () => {
  const previous = useStore.getState().prefs;
  const { el, root, restore } = mount();
  try {
    useStore.setState({
      prefs: { ...previous, thinkingAnimation: "comet", thinkingIntensity: "normal" },
    });
    await act(async () => {
      root.render(<AppearanceTab />);
    });
    const comet = el.querySelector<HTMLElement>('[data-thinking-animation="comet"]')!;
    await act(async () => {
      comet.focus();
      comet.dispatchEvent(new KeyboardEvent("keydown", {
        key: "ArrowRight",
        bubbles: true,
        cancelable: true,
      }));
    });

    expect(useStore.getState().prefs.thinkingAnimation).toBe("breath");
    expect(document.activeElement?.getAttribute("data-thinking-animation")).toBe("breath");
  } finally {
    await act(async () => root.unmount());
    restore();
    useStore.setState({ prefs: previous });
  }
});

test("composer routes each selected treatment through shared tokens and reduced-motion styles", () => {
  const composer = read("../src/features/chat/components/Composer.tsx");
  const toolbar = read("../src/features/chat/components/ComposerToolbar.tsx");
  const css = read("../src/design/chat.css");
  const tokens = read("../src/design/tokens.css");

  expect(composer).toContain('data-thinking-state={showWorking ? "waiting" : "idle"}');
  expect(composer).toContain("thinkingAnimation={thinkingAnimation}");
  expect(composer).toContain('className="board-composer__comet"');
  expect(composer).toContain('pathLength="100"');
  expect(toolbar).toContain('data-working={working ? "true" : "false"}');
  expect(css).toContain(".board-composer__comet-path");
  expect(css).toContain('data-thinking-animation="breath"]');
  expect(css).toContain('data-thinking-animation="border"]');
  expect(css).toContain('data-thinking-animation="orbit"]::before');
  expect(css).toContain("@media (prefers-reduced-motion: reduce)");
  const cometRule = css.match(/\.board-composer__comet-path\s*\{([\s\S]*?)\n\}/)?.[1] ?? "";
  const cometKeyframes =
    css.match(/@keyframes arden-thinking-comet\s*\{([\s\S]*?)\n\}/)?.[1] ?? "";
  expect(cometRule).toContain("stroke-dasharray: 2px 98px");
  expect(cometRule).not.toContain("transform");
  expect(cometKeyframes).toContain("stroke-dashoffset: -100px");
  expect(cometKeyframes).not.toContain("transform");
  expect(css).not.toContain("--thinking-comet-angle");
  expect(MOTION.thinkingComet).toBe(1.8);
  expect(MOTION.thinkingBreath).toBe(2.4);
  expect(MOTION.thinkingOrbit).toBe(0.7);
  expect(tokens).toContain("--motion-thinking-comet: 1800ms;");
  expect(tokens).toContain("--motion-thinking-breath: 2400ms;");
  expect(tokens).toContain("--motion-thinking-orbit: 700ms;");
});
