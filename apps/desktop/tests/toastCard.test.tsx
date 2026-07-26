import { afterEach, beforeEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { Toaster } from "@/components/ui/Toaster";
import { getState, setState } from "@/stores";
import { TOAST_CEILING_MS, TOAST_LIFETIME_MS, type Toast } from "@/lib/taskToast";

let root: Root | null = null;

const offer = (destination: { kind: "area"; area_id: string }): Toast => ({
  id: "nav:s-1:1",
  title: "Open the Ops area",
  status: "info",
  target: { kind: "destination", destination },
});

async function renderToaster() {
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root?.render(<Toaster />));
}

beforeEach(() => setState({ toasts: [] }));

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  setState({ toasts: [] });
  getState().setAreaRecords([]);
  getState().openArea(null);
  document.body.replaceChildren();
});

test("an agent's offer reads as a status word, not the raw enum", async () => {
  setState({ toasts: [offer({ kind: "area", area_id: "ops" })] });
  await renderToaster();

  const status = document.body.querySelector(".arden-toast__status");
  expect(status?.textContent).toBe("suggested");
  expect(document.body.textContent).not.toContain("info");
});

test("clicking an offer whose destination is gone says why instead of vanishing", async () => {
  setState({ toasts: [offer({ kind: "area", area_id: "gone" })] });
  await renderToaster();

  const card = document.body.querySelector<HTMLButtonElement>(".arden-toast__body");
  await act(async () => card?.click());

  expect(getState().toasts).toEqual([
    {
      id: "nav:s-1:1:failed",
      title: "Open the Ops area",
      detail: "That area is no longer available.",
      status: "failed",
      target: { kind: "destination", destination: { kind: "area", area_id: "gone" } },
    },
  ]);
});

test("the close button dismisses the card without taking its offer", async () => {
  setState({ toasts: [offer({ kind: "area", area_id: "ops" })] });
  // A real destination: taking the offer would open it, so a still-null open
  // area proves the close button dismissed instead of navigating.
  getState().setAreaRecords([
    {
      area_id: "ops",
      name: "Ops",
      created_at: "2026-07-25T00:00:00Z",
      updated_at: "2026-07-25T00:00:00Z",
    } as never,
  ]);
  await renderToaster();

  const close = document.body.querySelector<HTMLButtonElement>(
    '[aria-label="Dismiss notification"]',
  );
  expect(close).not.toBeNull();
  await act(async () => close?.click());

  expect(getState().toasts).toEqual([]);
  expect(getState().areas.openAreaKey).toBeNull();
});

test("a card arms both its lifetime and its hard ceiling on mount", async () => {
  const armed: number[] = [];
  const real = globalThis.setTimeout;
  globalThis.setTimeout = ((fn: () => void, ms?: number, ...rest: unknown[]) => {
    if (ms !== undefined) armed.push(ms);
    return real(fn, ms, ...(rest as []));
  }) as typeof globalThis.setTimeout;
  try {
    setState({ toasts: [offer({ kind: "area", area_id: "ops" })] });
    await renderToaster();
  } finally {
    globalThis.setTimeout = real;
  }

  // The lifetime can be cleared and re-armed by holds; the ceiling cannot.
  expect(armed).toContain(TOAST_LIFETIME_MS);
  expect(armed).toContain(TOAST_CEILING_MS);
});
