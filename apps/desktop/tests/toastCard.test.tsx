import { afterEach, beforeEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { Toaster } from "@/components/ui/Toaster";
import { getState, setState } from "@/stores";
import type { Toast } from "@/lib/taskToast";

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

  const card = document.body.querySelector<HTMLButtonElement>(".arden-toast");
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
