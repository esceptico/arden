import { expect, test } from "bun:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { Collapse } from "@/components/ui/Collapse";

const clipOf = (el: HTMLElement) =>
  (el.querySelector<HTMLElement>("div[style]")?.style.clipPath ?? "");

test("the open panel does not crop at its own border box", async () => {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => {
      root.render(<Collapse open><input className="arden-field" /></Collapse>);
    });
    // Settled clip must sit OUTSIDE the box, or a focused child's ring is
    // sheared flat where it crosses the edge.
    const clip = clipOf(host);
    expect(clip).toContain("-4px");
    expect(clip).not.toBe("inset(0)");
    // ...and must remain an inset(), never `none`, so the exit wipe has an
    // interpolable value to animate from instead of snapping.
    expect(clip.startsWith("inset(")).toBe(true);
  } finally {
    await act(async () => root.unmount());
    host.remove();
  }
});
