import { afterEach, expect, test } from "bun:test";
import { MotionConfig } from "motion/react";
import { act, StrictMode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { PeekSurface } from "@/components/workspace/PeekSurface";
import { hasBlockingOverlay } from "@/lib/overlayStack";
import {
  PEEK_ENTER_TRANSITION,
  POSE_PEEK_IN,
  POSE_PEEK_OUT,
  POSE_PEEK_VISIBLE,
} from "@/lib/tokens/motion";

let root: Root | null = null;

afterEach(async () => {
  if (root) {
    await act(async () => root?.unmount());
    root = null;
  }
  document.body.replaceChildren();
});

function Scene({
  open,
  reduced = false,
}: {
  open: boolean;
  reduced?: boolean;
}) {
  const content = (
    <>
      <button id="peek-trigger" type="button">
        Open peek
      </button>
      <PeekSurface
        open={open}
        onClose={() => {}}
        ariaLabel="Test peek"
        layer="test-peek"
        blocksInput
        focusOnOpen
      >
        <button id="peek-control" type="button">
          Inside peek
        </button>
      </PeekSurface>
    </>
  );

  return reduced ? (
    <MotionConfig reducedMotion="always">{content}</MotionConfig>
  ) : (
    content
  );
}

async function mountScene(open: boolean, reduced = false) {
  const app = document.createElement("div");
  app.id = "app";
  const scene = document.createElement("div");
  app.append(scene);
  document.body.append(app);
  root = createRoot(scene);
  await renderScene(open, reduced);
  return { app, scene };
}

async function renderScene(open: boolean, reduced = false) {
  await act(async () => {
    root?.render(
      <StrictMode>
        <Scene open={open} reduced={reduced} />
      </StrictMode>,
    );
  });
}

async function wait(ms: number) {
  await act(async () => {
    await Bun.sleep(ms);
  });
}

test("retains blocking, inert, and focus state until a peek exit finishes", async () => {
  const { app, scene } = await mountScene(false);
  const trigger = scene.querySelector<HTMLButtonElement>("#peek-trigger")!;
  trigger.focus();

  await renderScene(true);
  await wait(20);
  const control = app.querySelector<HTMLButtonElement>("#peek-control")!;
  expect(document.activeElement).toBe(control);
  expect(trigger.inert).toBe(true);
  expect(hasBlockingOverlay()).toBe(true);

  await renderScene(false);
  expect(app.querySelector('[data-peek-state="closing"]')).not.toBeNull();
  expect(trigger.inert).toBe(true);
  expect(hasBlockingOverlay()).toBe(true);

  await wait(80);
  expect(app.querySelector('[data-peek-state="closing"]')).not.toBeNull();
  expect(trigger.inert).toBe(true);
  expect(hasBlockingOverlay()).toBe(true);

  await wait(100);
  expect(app.querySelector('[data-overlay-layer="test-peek"]')).toBeNull();
  expect(trigger.inert).toBe(false);
  expect(hasBlockingOverlay()).toBe(false);
  expect(document.activeElement?.id).toBe("peek-trigger");
});

test("reopening a closing peek keeps one live overlay and cancels stale cleanup", async () => {
  const { app, scene } = await mountScene(false);
  const trigger = scene.querySelector<HTMLButtonElement>("#peek-trigger")!;
  trigger.focus();

  await renderScene(true);
  await wait(20);
  await renderScene(false);
  await wait(80);
  await renderScene(true);

  // Cross the original exit deadline. A stale exit must not release the layer.
  await wait(100);
  expect(app.querySelectorAll('[data-overlay-layer="test-peek"]')).toHaveLength(
    1,
  );
  expect(app.querySelector('[data-peek-state="open"]')).not.toBeNull();
  expect(trigger.inert).toBe(true);
  expect(hasBlockingOverlay()).toBe(true);

  await renderScene(false);
  await wait(180);
  expect(app.querySelector('[data-overlay-layer="test-peek"]')).toBeNull();
  expect(trigger.inert).toBe(false);
  expect(hasBlockingOverlay()).toBe(false);
  expect(document.activeElement?.id).toBe("peek-trigger");
});

test("reopening continues from the in-flight exit frame without flashing hidden", async () => {
  const { app } = await mountScene(true);
  await wait(600);
  await renderScene(false);
  await wait(60);

  const closing = app.querySelector<HTMLElement>('[data-overlay-layer="test-peek"]')!;
  const exitOpacity = Number.parseFloat(closing.style.opacity);
  expect(exitOpacity).toBeGreaterThan(0);
  expect(exitOpacity).toBeLessThan(1);

  await renderScene(true);
  const reopened = app.querySelector<HTMLElement>('[data-overlay-layer="test-peek"]')!;
  const reopenOpacity = Number.parseFloat(reopened.style.opacity);
  expect(reopened).toBe(closing);
  expect(reopenOpacity).toBeGreaterThan(0);

  await wait(40);
  expect(Number.parseFloat(reopened.style.opacity)).toBeGreaterThanOrEqual(reopenOpacity);
});

test("reduced motion finishes the peek without a translate offset", async () => {
  const { app } = await mountScene(true, true);
  await wait(80);
  const peek = app.querySelector<HTMLElement>(
    '[data-overlay-layer="test-peek"]',
  )!;

  expect(peek.style.transform).not.toContain("translate");
  expect(peek.style.opacity).toBe("1");

  await renderScene(false, true);
  await wait(80);
  expect(app.querySelector('[data-overlay-layer="test-peek"]')).toBeNull();
});

test("the peek keeps foreground text sharp through its canonical lifecycle", async () => {
  expect(POSE_PEEK_IN).toEqual({ opacity: 0, x: 16 });
  expect(POSE_PEEK_VISIBLE).toEqual({ opacity: 1, x: 0 });
  expect(POSE_PEEK_OUT).toEqual({ opacity: 0, x: 12 });
  expect(PEEK_ENTER_TRANSITION.duration).toBe(0.45);

  const { app } = await mountScene(true);
  await wait(600);

  const peek = app.querySelector<HTMLElement>('[data-overlay-layer="test-peek"]')!;
  expect(peek.style.filter).toBe("");
  expect(peek.style.willChange).toBe("opacity, transform");
});

test("an opted-in floating peek dismisses only from an outside pointer", async () => {
  let closeCount = 0;
  const app = document.createElement("div");
  document.body.append(app);
  root = createRoot(app);

  await act(async () => {
    root?.render(
      <>
        <button id="outside" type="button">Outside</button>
        <PeekSurface
          open
          onClose={() => { closeCount += 1; }}
          ariaLabel="Dismissible peek"
          layer="dismissible-peek"
          closeOnOutsidePointerDown
        >
          <button id="inside" type="button">Inside</button>
        </PeekSurface>
      </>,
    );
  });
  await wait(20);

  const inside = app.querySelector<HTMLButtonElement>("#inside")!;
  await act(async () => {
    inside.dispatchEvent(new Event("pointerdown", { bubbles: true, composed: true }));
  });
  expect(closeCount).toBe(0);

  const outside = app.querySelector<HTMLButtonElement>("#outside")!;
  await act(async () => {
    outside.dispatchEvent(new Event("pointerdown", { bubbles: true, composed: true }));
  });
  expect(closeCount).toBe(1);
});

test("an inspector-opening control is not treated as an outside dismissal", async () => {
  let closeCount = 0;
  const app = document.createElement("div");
  document.body.append(app);
  root = createRoot(app);

  await act(async () => {
    root?.render(
      <>
        <button id="source-trigger" type="button" data-inspector-trigger>
          Sources
        </button>
        <PeekSurface
          open
          onClose={() => { closeCount += 1; }}
          ariaLabel="Inspector"
          layer="inspector"
          closeOnOutsidePointerDown
          outsidePointerDownIgnoreSelector="[data-inspector-trigger]"
        >
          Activity
        </PeekSurface>
      </>,
    );
  });
  await wait(20);

  const trigger = app.querySelector<HTMLButtonElement>("#source-trigger")!;
  await act(async () => {
    trigger.dispatchEvent(new Event("pointerdown", { bubbles: true, composed: true }));
    trigger.click();
  });

  expect(closeCount).toBe(0);
  expect(app.querySelectorAll('[data-overlay-layer="inspector"]')).toHaveLength(1);
  expect(app.querySelector('[data-peek-state="open"]')).not.toBeNull();
});
