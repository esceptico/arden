import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { SwitchControl } from "@/components/ui/SwitchControl";

function mount(): { el: HTMLElement; root: Root; restore: () => void } {
  const el = document.createElement("div");
  document.body.append(el);
  return { el, root: createRoot(el), restore: () => el.remove() };
}
const click = (el: HTMLElement) =>
  el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

test("SwitchControl renders role=switch with aria-checked + accessible name", () => {
  const on = renderToStaticMarkup(<SwitchControl checked onChange={() => {}} aria-label="Auto-Approve" />);
  expect(on).toContain('role="switch"');
  expect(on).toContain('aria-checked="true"');
  expect(on).toContain('aria-label="Auto-Approve"');
  const off = renderToStaticMarkup(<SwitchControl checked={false} onChange={() => {}} aria-label="Auto-Approve" />);
  expect(off).toContain('aria-checked="false"');
});

test("clicking a switch toggles (onChange with the negated value)", async () => {
  const { el, root, restore } = mount();
  let next: boolean | null = null;
  await act(async () => {
    root.render(<SwitchControl checked={false} onChange={(v) => (next = v)} aria-label="T" />);
  });
  const btn = el.querySelector<HTMLElement>('[role="switch"]')!;
  expect(btn).not.toBeNull();
  await act(async () => click(btn));
  expect(next).toBe(true);
  await act(async () => root.unmount());
  restore();
});

test("a disabled switch does not toggle on click", async () => {
  const { el, root, restore } = mount();
  let next: boolean | null = null;
  await act(async () => {
    root.render(<SwitchControl checked={false} disabled onChange={(v) => (next = v)} aria-label="T" />);
  });
  const btn = el.querySelector<HTMLElement>('[role="switch"]')!;
  await act(async () => click(btn));
  expect(next).toBeNull();
  await act(async () => root.unmount());
  restore();
});

test("all call-site sizes preserve the canonical switch geometry", () => {
  for (const size of ["sm", "md"] as const) {
    const html = renderToStaticMarkup(<SwitchControl size={size} checked={false} onChange={() => {}} aria-label={size} />);
    expect(html).toContain('role="switch"');
  }

  const component = read("../src/components/ui/SwitchControl.tsx");
  const css = read("../src/styles.css");
  expect(component).not.toContain("onPointerMove");
  expect(component).not.toContain("SPRING_LAYOUT");
  expect(css).toMatch(/\.switch-control\s*\{[\s\S]*?width:\s*2rem;[\s\S]*?height:\s*1\.125rem;/);
  expect(css).toMatch(/\.switch-control-knob\s*\{[\s\S]*?top:\s*0\.125rem;[\s\S]*?left:\s*0\.125rem;[\s\S]*?width:\s*0\.875rem;[\s\S]*?height:\s*0\.875rem;/);
  expect(css).toContain("transform: translateX(0.875rem)");
  expect(css).toContain("inset: -0.25rem -0.1875rem");
  expect(css).toContain("transform var(--motion-feedback) var(--smooth-out)");
});
