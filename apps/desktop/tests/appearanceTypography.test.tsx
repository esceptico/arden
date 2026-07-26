import { expect, test } from "bun:test";
import { act } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createRoot, type Root } from "react-dom/client";
import { AppearanceTab } from "@/features/settings/components/AppearanceTab";
import { DEFAULT_PREFS, useStore } from "@/stores";

function mount(): { el: HTMLElement; root: Root; restore: () => void } {
  const el = document.createElement("div");
  document.body.append(el);
  return { el, root: createRoot(el), restore: () => el.remove() };
}

test("Appearance renders the Typography section with its three rows", () => {
  const html = renderToStaticMarkup(<AppearanceTab />);
  expect(html).toContain(">Typography<");
  expect(html).toContain(">Font<");
  expect(html).toContain(">Font size<");
  expect(html).toContain(">Font smoothing<");
  expect(html).toContain('aria-label="Font"');
  expect(html).toContain('aria-label="Font size"');
  expect(html).toContain('aria-label="Font smoothing"');
  expect(html).not.toContain(">Code font<");
  expect(html).not.toContain(">Diff markers<");
});

test("Font size input clamps out-of-range typed values on commit", async () => {
  const previous = useStore.getState().prefs;
  const { el, root, restore } = mount();
  try {
    useStore.setState({ prefs: { ...previous, uiFontSize: DEFAULT_PREFS.uiFontSize } });
    await act(async () => {
      root.render(<AppearanceTab />);
    });
    const input = el.querySelector<HTMLInputElement>('input[aria-label="Font size"]')!;
    const nativeValueSetter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      "value",
    )!.set!;

    await act(async () => {
      input.focus();
      nativeValueSetter.call(input, "999");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      input.blur();
    });

    expect(useStore.getState().prefs.uiFontSize).toBe(24);
  } finally {
    await act(async () => root.unmount());
    restore();
    useStore.setState({ prefs: previous });
  }
});

test("Font smoothing switch toggles the prefs boolean", async () => {
  const previous = useStore.getState().prefs;
  const { el, root, restore } = mount();
  try {
    useStore.setState({ prefs: { ...previous, fontSmoothing: true } });
    await act(async () => {
      root.render(<AppearanceTab />);
    });
    const toggle = el.querySelector<HTMLButtonElement>('button[aria-label="Font smoothing"]')!;

    await act(async () => {
      toggle.click();
    });

    expect(useStore.getState().prefs.fontSmoothing).toBe(false);
  } finally {
    await act(async () => root.unmount());
    restore();
    useStore.setState({ prefs: previous });
  }
});
