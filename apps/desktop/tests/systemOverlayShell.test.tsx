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

test("native Quick Capture mirrors the canonical sheet geometry and motion", () => {
  const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
  const capture = read("../src/app/QuickCapture.tsx");
  const electron = read("../electron/main.cjs");

  expect(capture).toContain("initial={POSE_SHEET_IN}");
  expect(capture).toContain("transition={exiting ? SHEET_EXIT_TRANSITION : SHEET_ENTER_TRANSITION}");
  expect(capture).toContain("Quick capture");
  expect(capture).toContain("What needs attention?");
  expect(capture).toContain("Inbox · new chat");
  expect(capture).toContain("Cmd/Ctrl+Enter");
  expect(capture).toContain("const PICKER_OVERHEAD = 19;");
  expect(capture).toContain("rounded-[var(--r-panel)]");
  expect(electron).toContain("const QUICK_WIDTH = 656;");
  expect(electron).toContain("const QUICK_BASE_HEIGHT = 324;");
  expect(electron).toContain("const QUICK_VISIBLE_TOP_GUTTER = 8;");
  expect(electron).toContain("dh * 0.18");
  expect(electron).toContain("2.5 * 16");
  expect(electron).toContain("4.5 * 16");
});
