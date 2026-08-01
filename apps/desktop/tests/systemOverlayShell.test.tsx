import { afterEach, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { SystemSheet } from "@/components/ui/SystemSheet";

let root: Root | null = null;

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = null;
  document.body.replaceChildren();
});

test("SystemSheet keeps viewer and review shells on the canonical bottom-sheet contract", async () => {
  const app = document.createElement("div");
  app.id = "app";
  const host = document.createElement("div");
  document.body.append(app, host);
  root = createRoot(host);

  await act(async () => {
    root?.render(
      <SystemSheet
        open
        onClose={() => {}}
        title="Review file changes"
        ariaLabel="Review file changes"
        status="Awaiting approval"
        statusTone="warning"
        footer={<button type="button">Approve changes</button>}
      >
        <p>Body</p>
      </SystemSheet>,
    );
  });

  const dialog = app.querySelector<HTMLElement>('[role="dialog"]')!;
  expect(dialog.className).toContain("surface-bottom-sheet");
  expect(dialog.parentElement?.className).toContain("page-modal-frame--bottom-sheet");
  expect(dialog.textContent).toContain("Review file changes");
  expect(dialog.textContent).toContain("Awaiting approval");
  expect(app.querySelector('[role="status"]')?.getAttribute("aria-live")).toBe("polite");
  expect(app.querySelector('[aria-label="Close"]')).not.toBeNull();
  expect(app.querySelector("footer")?.className).toContain("min-h-[3.25rem]");
});

test("native Quick Capture keeps the companion-bar geometry and motion contract", () => {
  const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
  const capture = read("../src/app/QuickCapture.tsx");
  const electron = read("../electron/main.cjs");

  // Shared sheet motion, bar-shaped card: bottom-anchored root inside a
  // fixed-height transparent window, so the card grows upward into the
  // invisible headroom and the input row stays fixed on screen.
  expect(capture).toContain("initial={POSE_SHEET_IN}");
  expect(capture).toContain("transition={exiting ? SHEET_EXIT_TRANSITION : SHEET_ENTER_TRANSITION}");
  expect(capture).toContain("What needs attention?");
  expect(capture).toContain("items-end");
  expect(capture).toContain("rounded-[var(--r-panel)]");
  // No renderer→main resize protocol: the fixed window can't clip the card.
  expect(capture).not.toContain("ResizeObserver");
  expect(capture).not.toContain("quickCapture?.resize");
  // Companion-bar placement: bottom-fraction anchor, fixed window height.
  expect(electron).toContain("const QUICK_WIDTH = 656;");
  expect(electron).toContain("const QUICK_HEIGHT = 560;");
  expect(electron).toContain("const QUICK_VISIBLE_BOTTOM_GUTTER = 36;");
  expect(electron).toContain("const QUICK_BOTTOM_FRACTION = 0.15;");
  expect(electron).not.toContain("quick:resize");
});
