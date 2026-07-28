import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { useRef } from "react";
import { CommandPalette } from "@/features/command-palette/components/CommandPalette";
import { hasBlockingOverlay, useOverlayLayer } from "@/lib/overlayStack";
import { getState, setState } from "@/stores/index";
import { AnchoredPopover } from "@/components/ui/AnchoredPopover";
import { Tooltip } from "@/components/ui/Tooltip";

let root: Root | null = null;

afterEach(async () => {
  if (root) {
    await act(async () => root?.unmount());
    root = null;
  }
  setState({ paletteOpen: false });
  document.body.replaceChildren();
});

function Layer({
  name,
  open,
  blocksInput,
  dismissDisabled = false,
  onDismiss,
}: {
  name: string;
  open: boolean;
  blocksInput: boolean;
  dismissDisabled?: boolean;
  onDismiss: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useOverlayLayer(ref, open, onDismiss, dismissDisabled, blocksInput);
  return open ? <div ref={ref} data-layer={name} /> : null;
}

function BlockingSurfaceWithPeek() {
  const surfaceRef = useRef<HTMLDivElement>(null);
  const peekRef = useRef<HTMLDivElement>(null);
  useOverlayLayer(surfaceRef, true, () => {}, false, true);
  useOverlayLayer(peekRef, true, () => {}, false, false);
  return (
    <div ref={surfaceRef} data-layer="surface">
      <button type="button" data-surface-control>Surface control</button>
      <div ref={peekRef} data-layer="peek">Peek</div>
    </div>
  );
}

function BlockingSurfaceWithTooltip({ onDismiss }: { onDismiss: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useOverlayLayer(ref, true, onDismiss);
  return (
    <div ref={ref}>
      <Tooltip label="Passive hint">
        <button type="button">Hint trigger</button>
      </Tooltip>
    </div>
  );
}

test("a blocking takeover synchronously hides and dismisses older transient layers", async () => {
  const app = document.createElement("div");
  app.id = "app";
  document.body.append(app);
  root = createRoot(app);
  let dismissals = 0;

  await act(async () => {
    root?.render(
      <>
        <Layer name="popover" open blocksInput={false} onDismiss={() => { dismissals += 1; }} />
        <Layer name="takeover" open={false} blocksInput onDismiss={() => {}} />
      </>,
    );
  });

  expect(hasBlockingOverlay()).toBe(false);

  await act(async () => {
    root?.render(
      <>
        <Layer name="popover" open blocksInput={false} onDismiss={() => { dismissals += 1; }} />
        <Layer name="takeover" open blocksInput onDismiss={() => {}} />
      </>,
    );
  });

  const popover = app.querySelector<HTMLElement>('[data-layer="popover"]');
  expect(dismissals).toBe(1);
  expect(popover?.style.visibility).toBe("hidden");
  expect(popover?.getAttribute("aria-hidden")).toBe("true");
  expect(hasBlockingOverlay()).toBe(true);
});

test("Cmd+K dismisses the top blocking layer and replaces it with the palette", async () => {
  const app = document.createElement("div");
  app.id = "app";
  document.body.append(app);
  root = createRoot(app);
  setState({ paletteOpen: false });

  let takeoverDismissals = 0;
  await act(async () => {
    root?.render(
      <>
        <Layer name="takeover" open blocksInput onDismiss={() => { takeoverDismissals += 1; }} />
        <CommandPalette />
      </>,
    );
  });

  await act(async () => {
    window.dispatchEvent(new KeyboardEvent("keydown", {
      key: "k",
      metaKey: true,
      bubbles: true,
      cancelable: true,
    }));
  });

  expect(takeoverDismissals).toBe(1);
  expect(getState().paletteOpen).toBe(true);
  expect(app.querySelector('[aria-label="Command palette"]')).not.toBeNull();
});

test("Cmd+K cannot bypass a blocking question that requires an answer", async () => {
  const app = document.createElement("div");
  app.id = "app";
  document.body.append(app);
  root = createRoot(app);
  setState({ paletteOpen: false });

  await act(async () => {
    root?.render(
      <>
        <Layer name="question" open blocksInput dismissDisabled onDismiss={() => {}} />
        <CommandPalette />
      </>,
    );
  });

  await act(async () => {
    window.dispatchEvent(new KeyboardEvent("keydown", {
      key: "k",
      metaKey: true,
      bubbles: true,
      cancelable: true,
    }));
  });

  expect(getState().paletteOpen).toBe(false);
  expect(app.querySelector('[aria-label="Command palette"]')).toBeNull();
});

test("a shared popover derives page versus nested elevation from overlay state", async () => {
  const app = document.createElement("div");
  app.id = "app";
  const scene = document.createElement("div");
  app.append(scene);
  document.body.append(app);
  root = createRoot(scene);

  const render = async (blocking: boolean) => {
    await act(async () => {
      root?.render(
        <>
          <Layer name="sheet" open={blocking} blocksInput onDismiss={() => {}} />
          <AnchoredPopover open onClose={() => {}} anchor={{ x: 24, y: 24 }}>
            <button type="button">Popover action</button>
          </AnchoredPopover>
        </>,
      );
    });
  };

  await render(true);
  await act(async () => {});
  let popover = app.querySelector<HTMLElement>("[data-overlay-depth]")!;
  expect(popover.dataset.overlayDepth).toBe("nested");
  expect(popover.style.zIndex).toBe("var(--z-nested)");

  await render(false);
  await act(async () => {});
  popover = app.querySelector<HTMLElement>("[data-overlay-depth]")!;
  expect(popover.dataset.overlayDepth).toBe("page");
  expect(popover.style.zIndex).toBe("var(--z-popover)");
});

test("a nonblocking descendant does not inert siblings inside its blocking surface", async () => {
  const app = document.createElement("div");
  app.id = "app";
  document.body.append(app);
  root = createRoot(app);

  await act(async () => {
    root?.render(<BlockingSurfaceWithPeek />);
  });

  expect(app.querySelector<HTMLElement>("[data-surface-control]")?.inert).toBe(false);
  expect(app.querySelector<HTMLElement>('[data-layer="peek"]')?.inert).toBe(false);
  expect(hasBlockingOverlay()).toBe(true);
});

test("a visible tooltip stays passive: Escape closes the blocking surface and scroll hides the hint", async () => {
  const app = document.createElement("div");
  app.id = "app";
  document.body.append(app);
  root = createRoot(app);
  let dismissals = 0;

  await act(async () => {
    root?.render(<BlockingSurfaceWithTooltip onDismiss={() => { dismissals += 1; }} />);
  });
  const trigger = app.querySelector<HTMLButtonElement>("button")!;

  await act(async () => {
    trigger.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    await Bun.sleep(520);
  });
  expect(document.body.querySelector('[role="tooltip"]')).not.toBeNull();

  await act(async () => {
    window.dispatchEvent(new KeyboardEvent("keydown", {
      key: "Escape",
      bubbles: true,
      cancelable: true,
    }));
  });
  expect(dismissals).toBe(1);

  await act(async () => {
    window.dispatchEvent(new Event("scroll"));
  });
  expect(document.body.querySelector('[role="tooltip"]')).toBeNull();
});
