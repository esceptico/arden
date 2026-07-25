import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import {
  SliderComfortable,
  sliderMarkerOpacity,
  sliderPipStopCenter,
  snapSliderInput,
} from "@/components/ui/Slider";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
const foundation = read("../src/design/foundation.css");

function mount(): { el: HTMLElement; root: Root; restore: () => void } {
  const el = document.createElement("div");
  document.body.append(el);
  return { el, root: createRoot(el), restore: () => el.remove() };
}

function setInputValue(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")!.set!;
  setter.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

const fireKey = (element: HTMLElement, key: string) =>
  element.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));

test("renders one native, accessible range with the formatted label and value", () => {
  const html = renderToStaticMarkup(
    <SliderComfortable
      value={512}
      onChange={() => {}}
      min={0}
      max={8000}
      step={64}
      label="Tokens"
      aria-label="Tokens"
      formatValue={(value) => `${value} tokens`}
    />,
  );
  expect(html).toContain('type="range"');
  expect(html).toContain('role="slider"');
  expect(html).toContain('aria-valuemin="0"');
  expect(html).toContain('aria-valuemax="8000"');
  expect(html).toContain('aria-valuenow="512"');
  expect(html).toContain('aria-label="Tokens"');
  expect(html).toContain('step="64"');
  expect(html).toContain("512 tokens");

  const offGrid = renderToStaticMarkup(
    <SliderComfortable value={1500} onChange={() => {}} min={256} max={8000} step={64} />,
  );
  expect(offGrid).toContain('step="1"');
});

test("readout keeps the mock's intrinsic height so the slider centers it", () => {
  const rule = foundation.match(/:where\(\.arden-slider__value\)\s*\{([^}]*)\}/)?.[1] ?? "";
  expect(rule).not.toContain("height:");
});

test("shared geometry aligns marker fade and pip stop centers", () => {
  expect(sliderMarkerOpacity(40, 60, 240, 8)).toBe(0);
  expect(sliderMarkerOpacity(250, 60, 240, 8)).toBe(0);
  expect(sliderMarkerOpacity(140, 60, 240, 8)).toBe(1);
  expect(sliderMarkerOpacity(64, 60, 240, 8)).toBe(0.5);
  expect(sliderMarkerOpacity(236, 60, 240, 8)).toBe(0.5);
  expect(sliderPipStopCenter(0, 390, 14.5)).toBe(14.5);
  expect(sliderPipStopCenter(0.5, 390, 14.5)).toBe(195);
  expect(sliderPipStopCenter(1, 390, 14.5)).toBe(375.5);
});

test("out-of-range controlled values keep the readout and ARIA on the same bounded value", () => {
  const html = renderToStaticMarkup(
    <SliderComfortable
      value={20}
      onChange={() => {}}
      min={1}
      max={16}
      label="Depth"
      aria-label="Depth"
    />,
  );
  expect(html).toContain('aria-valuenow="16"');
  expect(html).toContain('data-value="16"');
  expect(html).toContain(">16</span>");
  expect(html).not.toContain('aria-valuenow="20"');
});

test("pointer values snap to the min-anchored grid and keep max reachable", () => {
  expect(snapSliderInput(643, 10, 1000, 10)).toBe(640);
  expect(snapSliderInput(30, 1, 500, 5)).toBe(31);
  expect(snapSliderInput(500, 1, 500, 5)).toBe(500);
});

async function withComfortable(
  props: { value: number; onChange: (value: number) => void } & Partial<{
    min: number;
    max: number;
    step: number;
    disabled: boolean;
  }>,
  run: (slider: HTMLInputElement) => void,
) {
  const { el, root, restore } = mount();
  await act(async () => {
    root.render(<SliderComfortable aria-label="Test" {...props} />);
  });
  const slider = el.querySelector<HTMLInputElement>('input[type="range"]')!;
  expect(slider).not.toBeNull();
  await act(async () => run(slider));
  await act(async () => root.unmount());
  restore();
}

test("keyboard steps from the current value and Home/End reach the endpoints", async () => {
  let next: number | null = null;
  await withComfortable(
    { value: 6, min: 1, max: 16, step: 1, onChange: (value) => (next = value) },
    (slider) => fireKey(slider, "ArrowRight"),
  );
  expect(next).toBe(7);
  next = null;
  await withComfortable(
    { value: 1500, min: 256, max: 8000, step: 64, onChange: (value) => (next = value) },
    (slider) => fireKey(slider, "ArrowRight"),
  );
  expect(next).toBe(1564);
  next = null;
  await withComfortable(
    { value: 6, min: 1, max: 16, onChange: (value) => (next = value) },
    (slider) => fireKey(slider, "Home"),
  );
  expect(next).toBe(1);
  next = null;
  await withComfortable(
    { value: 6, min: 1, max: 16, onChange: (value) => (next = value) },
    (slider) => fireKey(slider, "End"),
  );
  expect(next).toBe(16);
});

test("disabled sliders are inert and expose no editable value target", async () => {
  let next: number | null = null;
  const { el, root, restore } = mount();
  await act(async () => {
    root.render(
      <SliderComfortable
        aria-label="Test"
        value={6}
        min={1}
        max={16}
        disabled
        onChange={(value) => (next = value)}
      />,
    );
  });
  const slider = el.querySelector<HTMLInputElement>('input[type="range"]')!;
  expect(slider.disabled).toBe(true);
  await act(async () => el.querySelector<HTMLElement>(".arden-slider__value")!.click());
  expect(el.querySelector('input[type="number"]')).toBeNull();
  await act(async () => fireKey(slider, "ArrowRight"));
  expect(next).toBeNull();
  await act(async () => root.unmount());
  restore();
});

async function edit(
  initialValue: number,
  props: Partial<{ min: number; max: number; step: number }>,
  typed: string,
  commitKey: "Enter" | "Escape",
): Promise<number | null> {
  let next: number | null = null;
  const { el, root, restore } = mount();
  await act(async () => {
    root.render(
      <SliderComfortable
        aria-label="Test"
        value={initialValue}
        onChange={(value) => (next = value)}
        {...props}
      />,
    );
  });
  const valueTarget = el.querySelector<HTMLElement>(".arden-slider__value")!;
  await act(async () => valueTarget.click());
  const input = el.querySelector<HTMLInputElement>('input[type="number"]')!;
  await act(async () => setInputValue(input, typed));
  await act(async () => {
    input.dispatchEvent(
      new KeyboardEvent("keydown", { key: commitKey, bubbles: true, cancelable: true }),
    );
  });
  await act(async () => root.unmount());
  restore();
  return next;
}

test("exact entry clamps but does not snap to the pointer grid", async () => {
  expect(await edit(200, { min: 10, max: 1000, step: 10 }, "643", "Enter")).toBe(643);
  expect(await edit(200, { min: 10, max: 1000, step: 10 }, "99999", "Enter")).toBe(1000);
});

test("exact off-grid entry updates the native step after the controlled rerender", async () => {
  const { el, root, restore } = mount();
  function Harness() {
    const [value, setValue] = useState(120);
    return (
      <SliderComfortable
        aria-label="Test"
        value={value}
        min={10}
        max={1000}
        step={10}
        onChange={setValue}
      />
    );
  }

  await act(async () => root.render(<Harness />));
  expect(el.querySelector<HTMLInputElement>('input[type="range"]')!.step).toBe("10");
  await act(async () => el.querySelector<HTMLElement>(".arden-slider__value")!.click());
  const editor = el.querySelector<HTMLInputElement>('input[type="number"]')!;
  await act(async () => setInputValue(editor, "123"));
  await act(async () => fireKey(editor, "Enter"));
  expect(el.querySelector<HTMLInputElement>('input[type="range"]')!.step).toBe("1");

  await act(async () => root.unmount());
  restore();
});

test("exact entry preserves valid off-grid defaults and Escape cancels", async () => {
  expect(await edit(1500, { min: 256, max: 8000, step: 64 }, "1500", "Enter")).toBeNull();
  expect(await edit(30, { min: 1, max: 500, step: 5 }, "30", "Enter")).toBeNull();
  expect(await edit(8192, { min: 1, max: 100000, step: 1024 }, "8192", "Enter")).toBeNull();
  expect(await edit(200, { min: 10, max: 1000, step: 10 }, "500", "Escape")).toBeNull();
});

test("input deduplicates unchanged values", async () => {
  let calls = 0;
  await withComfortable(
    { value: 6, min: 1, max: 16, onChange: () => calls++ },
    (slider) => setInputValue(slider, "6"),
  );
  expect(calls).toBe(0);
});

test("pointer cancellation and lost capture clear the drag state", async () => {
  const { el, root, restore } = mount();
  await act(async () => {
    root.render(<SliderComfortable aria-label="Test" value={6} min={1} max={16} onChange={() => {}} />);
  });
  const control = el.querySelector<HTMLElement>(".arden-slider")!;
  const slider = el.querySelector<HTMLInputElement>('input[type="range"]')!;
  control.style.setProperty("--range-drag-threshold", "3px");

  await act(async () => {
    slider.dispatchEvent(
      new PointerEvent("pointerdown", {
        bubbles: true,
        pointerType: "mouse",
        button: 0,
        clientX: 20,
      }),
    );
    slider.dispatchEvent(
      new PointerEvent("pointermove", {
        bubbles: true,
        pointerType: "mouse",
        clientX: 24,
      }),
    );
  });
  expect(control.classList.contains("is-dragging")).toBe(true);
  await act(async () => slider.dispatchEvent(new PointerEvent("pointercancel", { bubbles: true })));
  expect(control.classList.contains("is-dragging")).toBe(false);

  await act(async () => {
    slider.dispatchEvent(
      new PointerEvent("pointerdown", {
        bubbles: true,
        pointerType: "mouse",
        button: 0,
        clientX: 20,
      }),
    );
    slider.dispatchEvent(
      new PointerEvent("pointermove", {
        bubbles: true,
        pointerType: "mouse",
        clientX: 24,
      }),
    );
    slider.dispatchEvent(new PointerEvent("lostpointercapture", { bubbles: true }));
  });
  expect(control.classList.contains("is-dragging")).toBe(false);
  await act(async () => root.unmount());
  restore();
});

test("measured pips and scrubber share the mock endpoint geometry", async () => {
  const run = async (variant: "pips" | "scrubber", initial: number, next: number) => {
    const { el, root, restore } = mount();
    await act(async () => {
      root.render(
        <SliderComfortable
          aria-label="Test"
          label="Test"
          variant={variant}
          value={initial}
          min={variant === "pips" ? 1 : 0}
          max={variant === "pips" ? 16 : 100}
          onChange={() => {}}
        />,
      );
    });
    const control = el.querySelector<HTMLElement>(".arden-slider")!;
    const slider = el.querySelector<HTMLInputElement>('input[type="range"]')!;
    control.style.setProperty("--range-edge-inset", "14.5px");
    control.style.setProperty("--range-marker-width", "2px");
    control.style.setProperty("--range-text-clearance", "4px");
    control.style.setProperty("--range-opacity-distance", "8px");
    control.getBoundingClientRect = () =>
      ({ left: 0, right: 390, top: 0, bottom: 32, width: 390, height: 32, x: 0, y: 0, toJSON() {} }) as DOMRect;
    await act(async () => setInputValue(slider, String(next)));
    const geometry = {
      fill: control.style.getPropertyValue("--slider-fill-x"),
      marker: control.style.getPropertyValue("--slider-marker-x"),
    };
    await act(async () => root.unmount());
    restore();
    return geometry;
  };

  expect(await run("pips", 1, 16)).toEqual({ fill: "375.5px", marker: "374.5px" });
  expect(await run("scrubber", 0, 100)).toEqual({ fill: "390px", marker: "389px" });
});

test("shared CSS owns the exact mock shape, material, focus, and motion", () => {
  const source = read("../src/components/ui/Slider.tsx");
  const foundation = read("../src/design/foundation.css");
  const tokens = read("../src/design/tokens.css");

  expect(source).toContain('type="range"');
  expect(source).toContain("onPointerCancel={finishPointer}");
  expect(source).toContain("onLostPointerCapture={finishPointer}");
  expect(source).not.toContain("motion/react");
  expect(source).not.toContain("hover-tooltip");
  expect(foundation).toMatch(/\.arden-slider\)\s*\{[\s\S]*?height:\s*2rem;/);
  expect(foundation).toMatch(/\.arden-slider\)\s*\{[\s\S]*?border:\s*0;/);
  expect(foundation).toMatch(/\.arden-slider\)\s*\{[\s\S]*?border-radius:\s*var\(--r-control\);/);
  expect(foundation).toMatch(/\.arden-slider\)\s*\{[\s\S]*?box-shadow:\s*var\(--shadow-2\);/);
  expect(foundation).toContain("--slider-fill-x var(--motion-tab-shift) var(--smooth-out)");
  expect(foundation).toContain("inset 0 0 0 1px color-mix(in oklab, var(--ink) 16%, transparent)");
  for (const token of [
    "--range-edge-inset: .90625rem;",
    "--range-marker-width: .125rem;",
    "--range-text-clearance: .25rem;",
    "--range-opacity-distance: .5rem;",
    "--range-drag-threshold: .1875rem;",
  ]) {
    expect(tokens).toContain(token);
  }
});
