import { expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import {
  SliderComfortable,
  sliderMarkerOpacity,
  sliderPipStopCenter,
} from "@/components/ui/Slider";

function mount(): { el: HTMLElement; root: Root; restore: () => void } {
  const el = document.createElement("div");
  document.body.append(el);
  return { el, root: createRoot(el), restore: () => el.remove() };
}
const fireKey = (el: HTMLElement, key: string) =>
  el.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));

// ── render + ARIA ───────────────────────────────────────────────────────────

test("SliderComfortable renders role=slider with aria-valuemin/max/now + accessible name", () => {
  const html = renderToStaticMarkup(
    <SliderComfortable value={6} onChange={() => {}} min={1} max={16} aria-label="Depth" />,
  );
  expect(html).toContain('role="slider"');
  expect(html).toContain('aria-valuemin="1"');
  expect(html).toContain('aria-valuemax="16"');
  expect(html).toContain('aria-valuenow="6"');
  expect(html).toContain('aria-label="Depth"');
});

test("SliderComfortable renders the formatted label + value", () => {
  const html = renderToStaticMarkup(
    <SliderComfortable
      value={512}
      onChange={() => {}}
      min={0}
      max={8000}
      step={64}
      label="Tokens"
      formatValue={(n) => `${n} tokens`}
    />,
  );
  expect(html).toContain("Tokens");
  expect(html).toContain("512 tokens");
});

test("slider marker yields to label and value lanes with a short fade", () => {
  expect(sliderMarkerOpacity(40, 60, 240)).toBe(0);
  expect(sliderMarkerOpacity(250, 60, 240)).toBe(0);
  expect(sliderMarkerOpacity(140, 60, 240)).toBe(1);
  expect(sliderMarkerOpacity(64, 60, 240)).toBe(0.5);
  expect(sliderMarkerOpacity(236, 60, 240)).toBe(0.5);
});

test("discrete slider uses one stop center for dots, fill, and marker", () => {
  expect(sliderPipStopCenter(0, 390)).toBe(14.5);
  expect(sliderPipStopCenter(0.5, 390)).toBe(195);
  expect(sliderPipStopCenter(1, 390)).toBe(375.5);
});

// ── keyboard stepping ───────────────────────────────────────────────────────

async function withComfortable(
  props: { value: number; onChange: (n: number) => void } & Partial<{
    min: number;
    max: number;
    step: number;
    disabled: boolean;
  }>,
  run: (slider: HTMLElement) => void,
) {
  const { el, root, restore } = mount();
  await act(async () => {
    root.render(<SliderComfortable aria-label="Test" {...props} />);
  });
  const slider = el.querySelector<HTMLElement>('[role="slider"]')!;
  expect(slider).not.toBeNull();
  await act(async () => run(slider));
  await act(async () => root.unmount());
  restore();
}

test("SliderComfortable ArrowRight/Left step; Home/End jump to min/max", async () => {
  let next: number | null = null;
  await withComfortable({ value: 6, min: 1, max: 16, step: 1, onChange: (n) => (next = n) }, (s) => fireKey(s, "ArrowRight"));
  expect(next).toBe(7);
  next = null;
  await withComfortable({ value: 6, min: 1, max: 16, step: 1, onChange: (n) => (next = n) }, (s) => fireKey(s, "ArrowLeft"));
  expect(next).toBe(5);
  next = null;
  await withComfortable({ value: 6, min: 1, max: 16, onChange: (n) => (next = n) }, (s) => fireKey(s, "Home"));
  expect(next).toBe(1);
  next = null;
  await withComfortable({ value: 6, min: 1, max: 16, onChange: (n) => (next = n) }, (s) => fireKey(s, "End"));
  expect(next).toBe(16);
});

test("SliderComfortable keyboard is inert when disabled", async () => {
  let next: number | null = null;
  await withComfortable({ value: 6, min: 1, max: 16, disabled: true, onChange: (n) => (next = n) }, (s) => fireKey(s, "ArrowRight"));
  expect(next).toBeNull();
});

// ── click-to-edit exact entry (regression coverage for the 3 fixed bugs) ─────

function setInputValue(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")!.set!;
  setter.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

async function edit(
  props: Partial<{ min: number; max: number; step: number }>,
  typed: string,
  commitKey: "Enter" | "Escape",
): Promise<number | null> {
  let next: number | null = null;
  const { el, root, restore } = mount();
  await act(async () => {
    root.render(<SliderComfortable aria-label="Test" value={200} onChange={(n) => (next = n)} {...props} />);
  });
  const valueSpan = el.querySelector<HTMLElement>("span.cursor-text");
  if (valueSpan) {
    await act(async () => valueSpan.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true })));
    const input = el.querySelector<HTMLInputElement>('input[type="number"]');
    if (input) {
      await act(async () => {
        setInputValue(input, typed);
        input.dispatchEvent(new KeyboardEvent("keydown", { key: commitKey, bubbles: true, cancelable: true }));
      });
    }
  }
  await act(async () => root.unmount());
  restore();
  return next;
}

test("click-to-edit commits a typed exact value on Enter", async () => {
  expect(await edit({ min: 10, max: 1000, step: 10 }, "500", "Enter")).toBe(500);
});

test("click-to-edit snaps to step and clamps to max", async () => {
  expect(await edit({ min: 10, max: 1000, step: 10 }, "643", "Enter")).toBe(640); // snap
  expect(await edit({ min: 10, max: 1000, step: 10 }, "99999", "Enter")).toBe(1000); // clamp
});

test("click-to-edit Escape cancels without committing", async () => {
  expect(await edit({ min: 10, max: 1000, step: 10 }, "500", "Escape")).toBeNull();
});

test("click-to-edit is a no-op when the typed value is unchanged (no redundant save)", async () => {
  // value is 200; typing 200 again must NOT fire onChange (the `!== value` guard).
  expect(await edit({ min: 10, max: 1000, step: 10 }, "200", "Enter")).toBeNull();
});

test("a disabled SliderComfortable exposes no click-to-edit target", async () => {
  const { el, root, restore } = mount();
  await act(async () => {
    root.render(<SliderComfortable aria-label="Test" value={6} min={1} max={16} disabled onChange={() => {}} />);
  });
  expect(el.querySelector("span.cursor-text")).toBeNull();
  await act(async () => root.unmount());
  restore();
});
