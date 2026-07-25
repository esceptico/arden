import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { Takeover } from "@/components/workspace/Takeover";
import { hasBlockingOverlay } from "@/lib/overlayStack";

let root: Root | null = null;

afterEach(async () => {
  if (root) {
    await act(async () => root?.unmount());
    root = null;
  }
  document.body.replaceChildren();
});

function Scene({ open }: { open: boolean }) {
  return (
    <>
      <button id="takeover-trigger" type="button">Open</button>
      <Takeover open={open} onClose={() => {}} ariaLabel="Test takeover">
        <button id="takeover-control" type="button">Inside</button>
      </Takeover>
    </>
  );
}

async function mountScene(open: boolean) {
  const app = document.createElement("div");
  app.id = "app";
  const scene = document.createElement("div");
  scene.id = "scene";
  app.append(scene);
  document.body.append(app);
  root = createRoot(scene);
  await renderScene(open);
  return { app, scene };
}

async function renderScene(open: boolean) {
  await act(async () => {
    root?.render(<Scene open={open} />);
  });
}

async function wait(ms: number) {
  await act(async () => {
    await Bun.sleep(ms);
  });
}

test("retains blocking, inert, and focus state until the sheet exit finishes", async () => {
  const { app, scene } = await mountScene(false);
  const trigger = scene.querySelector<HTMLButtonElement>("#takeover-trigger")!;
  trigger.focus();

  await renderScene(true);
  const control = app.querySelector<HTMLButtonElement>("#takeover-control")!;
  expect(document.activeElement).toBe(control);
  expect(scene.inert).toBe(true);
  expect(hasBlockingOverlay()).toBe(true);

  await renderScene(false);
  expect(app.querySelector('[data-overlay-state="closing"]')).not.toBeNull();
  expect(document.activeElement).toBe(control);
  expect(scene.inert).toBe(true);
  expect(hasBlockingOverlay()).toBe(true);

  await wait(80);
  expect(app.querySelector('[data-overlay-state="closing"]')).not.toBeNull();
  expect(scene.inert).toBe(true);
  expect(hasBlockingOverlay()).toBe(true);

  await wait(100);
  expect(app.querySelector('[data-overlay-layer="takeover"]')).toBeNull();
  expect(scene.inert).toBe(false);
  expect(hasBlockingOverlay()).toBe(false);
  expect(document.activeElement).toBe(trigger);
});

test("reopening cancels a stale close without releasing the layer", async () => {
  const { app, scene } = await mountScene(false);
  const trigger = scene.querySelector<HTMLButtonElement>("#takeover-trigger")!;
  trigger.focus();

  await renderScene(true);
  await renderScene(false);
  await wait(80);
  await renderScene(true);

  // Cross the first close's original deadline. Its cleanup must stay canceled.
  await wait(100);
  expect(app.querySelector('[data-overlay-state="open"]')).not.toBeNull();
  expect(scene.inert).toBe(true);
  expect(hasBlockingOverlay()).toBe(true);

  await renderScene(false);
  await wait(180);
  expect(app.querySelector('[data-overlay-layer="takeover"]')).toBeNull();
  expect(scene.inert).toBe(false);
  expect(hasBlockingOverlay()).toBe(false);
  expect(document.activeElement).toBe(trigger);
});
