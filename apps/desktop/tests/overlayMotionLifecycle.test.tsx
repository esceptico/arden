import { afterEach, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { DialogLayer } from "@/components/workspace/DialogLayer";
import { CommandPalette } from "@/features/command-palette/components/CommandPalette";
import { ModalScrim } from "@/components/ui/ModalScrim";
import { hasBlockingOverlay } from "@/lib/overlayStack";
import {
  DISTANCE,
  MOTION,
  SHEET_EXIT_TRANSITION,
  SPRING_SHEET,
} from "@/lib/tokens/motion";
import { getState, setState } from "@/stores";

let root: Root | null = null;

afterEach(async () => {
  if (root) {
    await act(async () => root?.unmount());
    root = null;
  }
  setState({ paletteOpen: false });
  document.body.replaceChildren();
});

async function wait(ms: number) {
  await act(async () => {
    await Bun.sleep(ms);
  });
}

function PaletteScene() {
  return (
    <>
      <button id="palette-trigger" type="button">Open palette</button>
      <CommandPalette />
      <ModalScrim />
    </>
  );
}

function DialogScene({ open }: { open: boolean }) {
  return (
    <>
      <button id="dialog-trigger" type="button">Open dialog</button>
      <DialogLayer open={open} onClose={() => {}} ariaLabel="Test dialog">
        <button id="dialog-control" type="button">Inside dialog</button>
      </DialogLayer>
    </>
  );
}

async function mount(element: ReactNode) {
  const app = document.createElement("div");
  app.id = "app";
  const scene = document.createElement("div");
  app.append(scene);
  document.body.append(app);
  root = createRoot(scene);
  await act(async () => root?.render(element));
  return { app, scene };
}

test("overlay tokens preserve the mockup sheet and scrim contract", () => {
  expect(SPRING_SHEET).toEqual({ type: "spring", stiffness: 360, damping: 32, mass: 0.75 });
  expect(DISTANCE.sheetEnter).toBe(24);
  expect(DISTANCE.sheetExit).toBe(28);
  expect(SHEET_EXIT_TRANSITION.duration).toBe(0.16);
  expect(MOTION.focus).toBe(0.18);

  const styles = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
  const tokens = readFileSync(new URL("../src/design/tokens.css", import.meta.url), "utf8");
  expect(tokens).toContain("--modal-scrim-bg: color-mix(in oklab, var(--ink) 9%, transparent)");
  expect(styles).toContain("background: var(--modal-scrim-bg)");
  expect(styles).toContain("z-index: var(--z-scrim)");
  expect(styles).toContain("position: fixed");
  expect(styles).toContain("backdrop-filter: blur(var(--modal-scrim-blur))");
});

test("command palette retains focus, inertness, and its scrim through the sheet exit", async () => {
  const { app, scene } = await mount(<PaletteScene />);
  const trigger = scene.querySelector<HTMLButtonElement>("#palette-trigger")!;
  trigger.focus();

  await act(async () => setState({ paletteOpen: true }));
  await wait(20);
  expect(app.querySelector('[data-overlay-layer="command-palette"]')).not.toBeNull();
  expect(scene.inert).toBe(true);
  expect(hasBlockingOverlay()).toBe(true);

  await act(async () => getState().closePalette());
  expect(app.querySelector('[data-overlay-state="closing"]')).not.toBeNull();
  expect(scene.inert).toBe(true);
  expect(hasBlockingOverlay()).toBe(true);

  await wait(100);
  expect(app.querySelector('[data-overlay-state="closing"]')).not.toBeNull();
  expect(scene.inert).toBe(true);

  await wait(100);
  expect(app.querySelector('[data-overlay-layer="command-palette"]')).toBeNull();
  expect(scene.inert).toBe(false);
  expect(hasBlockingOverlay()).toBe(false);
  expect(document.activeElement?.id).toBe("palette-trigger");
});

test("Cmd+K reverses a retained palette exit without releasing its layer", async () => {
  const { app, scene } = await mount(<PaletteScene />);
  const trigger = scene.querySelector<HTMLButtonElement>("#palette-trigger")!;
  trigger.focus();

  await act(async () => setState({ paletteOpen: true }));
  await wait(20);
  await act(async () => getState().closePalette());
  await wait(60);

  await act(async () => {
    window.dispatchEvent(new KeyboardEvent("keydown", {
      key: "k",
      metaKey: true,
      bubbles: true,
      cancelable: true,
    }));
  });
  expect(getState().paletteOpen).toBe(true);
  expect(app.querySelector('[data-overlay-state="open"]')).not.toBeNull();

  // Cross the original exit deadline: its cancelled timer must not tear down
  // the re-opened layer.
  await wait(140);
  expect(app.querySelector('[data-overlay-layer="command-palette"]')).not.toBeNull();
  expect(scene.inert).toBe(true);
  expect(hasBlockingOverlay()).toBe(true);
});

test("nested dialogs retain their stack entry until the fade completes", async () => {
  const { app, scene } = await mount(<DialogScene open={false} />);
  const trigger = scene.querySelector<HTMLButtonElement>("#dialog-trigger")!;
  trigger.focus();

  await act(async () => root?.render(<DialogScene open />));
  const control = scene.querySelector<HTMLButtonElement>("#dialog-control")!;
  expect(document.activeElement).toBe(control);
  expect(trigger.inert).toBe(true);
  expect(hasBlockingOverlay()).toBe(true);

  await act(async () => root?.render(<DialogScene open={false} />));
  expect(app.querySelector('[data-overlay-layer="nested-dialog"][data-overlay-state="closing"]')).not.toBeNull();
  expect(trigger.inert).toBe(true);
  expect(hasBlockingOverlay()).toBe(true);

  await wait(100);
  expect(app.querySelector('[data-overlay-layer="nested-dialog"]')).not.toBeNull();
  expect(trigger.inert).toBe(true);

  await wait(100);
  expect(app.querySelector('[data-overlay-layer="nested-dialog"]')).toBeNull();
  expect(trigger.inert).toBe(false);
  expect(hasBlockingOverlay()).toBe(false);
  expect(document.activeElement).toBe(trigger);
});
