import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { PageModal } from "@/components/ui/PageModal";
import { ModalScrim } from "@/components/ui/ModalScrim";
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
      <button id="modal-trigger" type="button">Open</button>
      <PageModal open={open} onClose={() => {}} ariaLabel="Test modal">
        <button id="modal-control" type="button">Inside</button>
      </PageModal>
      <ModalScrim />
    </>
  );
}

async function render(open: boolean) {
  await act(async () => {
    root?.render(<Scene open={open} />);
  });
}

async function wait(ms: number) {
  await act(async () => {
    await Bun.sleep(ms);
  });
}

test("PageModal retains inertness and focus through its scrim exit", async () => {
  const app = document.createElement("div");
  app.id = "app";
  const scene = document.createElement("div");
  app.append(scene);
  document.body.append(app);
  root = createRoot(scene);

  await render(false);
  const trigger = scene.querySelector<HTMLButtonElement>("#modal-trigger")!;
  trigger.focus();

  await render(true);
  const control = app.querySelector<HTMLButtonElement>("#modal-control")!;
  expect(document.activeElement).toBe(control);
  expect(scene.inert).toBe(true);
  expect(hasBlockingOverlay()).toBe(true);

  await render(false);
  expect(app.querySelector('[data-overlay-state="closing"]')).not.toBeNull();
  expect(scene.inert).toBe(true);
  expect(hasBlockingOverlay()).toBe(true);

  await wait(100);
  expect(app.querySelector('[data-overlay-state="closing"]')).not.toBeNull();
  expect(scene.inert).toBe(true);

  await wait(100);
  expect(app.querySelector('[data-overlay-layer="modal"]')).toBeNull();
  expect(scene.inert).toBe(false);
  expect(hasBlockingOverlay()).toBe(false);
  expect(document.activeElement).toBe(trigger);
});

test("PageModal reverses a close without releasing the overlay boundary", async () => {
  const app = document.createElement("div");
  app.id = "app";
  const scene = document.createElement("div");
  app.append(scene);
  document.body.append(app);
  root = createRoot(scene);

  await render(true);
  await render(false);
  await wait(80);
  await render(true);
  await wait(120);

  expect(app.querySelectorAll('[data-overlay-layer="modal"]')).toHaveLength(1);
  expect(app.querySelector('[data-overlay-state="open"]')).not.toBeNull();
  expect(scene.inert).toBe(true);
  expect(hasBlockingOverlay()).toBe(true);
});

test("stacked PageModals share one scrim and its click belongs to the top sheet", async () => {
  const app = document.createElement("div");
  app.id = "app";
  const scene = document.createElement("div");
  app.append(scene);
  document.body.append(app);
  root = createRoot(scene);
  let lowerDismissals = 0;
  let upperDismissals = 0;

  await act(async () => {
    root?.render(
      <>
        <PageModal open onClose={() => { lowerDismissals += 1; }} ariaLabel="Lower">
          <button type="button">Lower control</button>
        </PageModal>
        <PageModal open elevated onClose={() => { upperDismissals += 1; }} ariaLabel="Upper">
          <button type="button">Upper control</button>
        </PageModal>
        <ModalScrim />
      </>,
    );
  });

  const scrims = document.body.querySelectorAll<HTMLElement>("[data-shared-modal-scrim]");
  expect(scrims).toHaveLength(1);
  expect(app.querySelector(".modal-scrim")).toBeNull();

  await act(async () => {
    scrims[0].dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });

  expect(lowerDismissals).toBe(0);
  expect(upperDismissals).toBe(1);
});
