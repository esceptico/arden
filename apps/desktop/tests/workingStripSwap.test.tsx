import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { WorkingStrip } from "@/features/chat/components/WorkingStrip";

test("the step uses the app's one text-swap primitive", () => {
  // BlurSwap is the single text-swap treatment across the app. A second swap
  // idiom for the same job is the thing to avoid here.
  const source = readFileSync(
    new URL("../src/features/chat/components/WorkingStrip.tsx", import.meta.url),
    "utf8",
  );
  expect(source).toContain('import { BlurSwap } from "@/components/ui/BlurSwap"');
});

test("a step change swaps the rendered label", async () => {
  const el = document.createElement("div");
  document.body.append(el);
  const root = createRoot(el);
  try {
    await act(async () => {
      root.render(<WorkingStrip active label={{ verb: "Reading", target: "areas.md" }} />);
    });
    expect(el.textContent).toContain("Reading");
    expect(el.textContent).toContain("areas.md");

    await act(async () => {
      root.render(<WorkingStrip active label={{ verb: "Editing", target: "chat.css" }} />);
    });
    // Sequential by design: the outgoing step commits before the next enters,
    // so the label never shows two tools at once.
    expect(el.querySelectorAll(".board-composer__strip-step").length).toBe(1);
  } finally {
    await act(async () => root.unmount());
    el.remove();
  }
});
