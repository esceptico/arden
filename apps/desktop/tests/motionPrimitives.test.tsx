import { expect, test } from "bun:test";
import React, { act } from "react";
import { readFileSync } from "node:fs";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { BlurSwap } from "@/components/ui/BlurSwap";
import { RollingToken, rollingTokenCells } from "@/components/ui/RollingToken";
import {
  COUNTER_SPIN_NEW,
  COUNTER_SPIN_OLD_EXIT,
  COUNTER_SPIN_TRANSITION,
  DECK_ENTER_TRANSITION,
  DECK_EXIT_TRANSITION,
  DECK_VARIANTS,
  TEXT_SWAP_ENTER,
  TEXT_SWAP_EXIT,
  TEXT_SWAP_TRANSITION,
  TRACE_ROW_ENTER,
  TRACE_ROW_EXIT,
  TRACE_ROW_TRANSITION,
  counterSpinDelay,
  counterSpinSettleDelay,
} from "@/lib/tokens/motion";

test("BlurSwap follows the canonical staged text swap", () => {
  const source = readFileSync(new URL("../src/components/ui/BlurSwap.tsx", import.meta.url), "utf8");
  const html = renderToStaticMarkup(<BlurSwap swapKey="ready">Ready</BlurSwap>);

  expect(TEXT_SWAP_ENTER).toEqual({ opacity: 0, y: 4, filter: "blur(2px)" });
  expect(TEXT_SWAP_EXIT).toEqual({ opacity: 0, y: -4, filter: "blur(2px)" });
  expect(TEXT_SWAP_TRANSITION).toEqual({ duration: 0.15, ease: [0.42, 0, 0.58, 1] });
  expect(source).toContain('<AnimatePresence mode="wait" initial={false}>');
  expect(source).toContain("useReducedMotion");
  expect(html).toContain("Ready");
});

test("RollingToken aligns values and rolls changed cells with canonical timing", () => {
  expect(rollingTokenCells("99", "100")).toEqual([
    { oldChar: " ", nextChar: "1", index: 0 },
    { oldChar: "9", nextChar: "0", index: 1 },
    { oldChar: "9", nextChar: "0", index: 2 },
  ]);
  expect(COUNTER_SPIN_OLD_EXIT).toEqual({ opacity: 0, y: "-105%", filter: "blur(2px)" });
  expect(COUNTER_SPIN_NEW).toEqual({ opacity: 0, y: "105%", filter: "blur(2px)" });
  expect(COUNTER_SPIN_TRANSITION.duration).toBe(0.42);
  expect(COUNTER_SPIN_TRANSITION.ease(0.5)).toBeCloseTo(0.85132, 5);
  expect(counterSpinDelay(4, 0)).toBe(0.096);
  expect(counterSpinDelay(4, 3)).toBe(0);
  expect(counterSpinSettleDelay(4)).toBe(0.516);

  const animated = renderToStaticMarkup(<RollingToken value="42" mono />);
  const instant = renderToStaticMarkup(<RollingToken value="42" motionDisabled />);
  expect(animated).toContain('aria-label="42"');
  expect((animated.match(/t-spin-cell/g) ?? []).length).toBe(2);
  expect(instant).not.toContain("t-spin-cell");
});

test("RollingToken settles empty values and cancels stale settle timers", async () => {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  const settle = (milliseconds: number) => new Promise((resolve) => setTimeout(resolve, milliseconds));
  const TokenProbe = ({ value, revision }: { value: string; revision: number }) => (
    <div data-revision={revision}><RollingToken value={value} mono /></div>
  );
  const render = (value: string, revision = 0) => root.render(<TokenProbe value={value} revision={revision} />);

  try {
    await act(async () => render("1234"));
    await act(async () => render(""));
    expect(host.querySelectorAll(".t-spin-cell")).toHaveLength(4);
    await act(async () => render("", 1));
    expect(host.querySelectorAll(".t-spin-cell")).toHaveLength(4);
    await act(async () => { await settle(540); });
    expect(host.querySelectorAll(".t-spin-cell")).toHaveLength(0);

    await act(async () => render("1234"));
    await act(async () => render(""));
    await act(async () => render("7"));
    await act(async () => { await settle(540); });
    expect(host.querySelector('[aria-label="7"]')?.textContent).toBe("7");
    expect(host.querySelectorAll(".t-spin-cell")).toHaveLength(1);
  } finally {
    await act(async () => root.unmount());
    host.remove();
  }
});

test("deck and trace-row recipes mirror their shared mockup controllers", () => {
  expect(DECK_VARIANTS.exit(-1)).toEqual({ opacity: 0, x: -32, y: 0, scale: 1, filter: "blur(2px)" });
  expect(DECK_VARIANTS.exit(0)).toEqual({ opacity: 0, x: 0, y: 4, scale: 0.96, filter: "blur(2px)" });
  expect(DECK_VARIANTS.enter(1)).toEqual({ opacity: 0, x: -16, y: 0, scale: 1, filter: "blur(2px)" });
  expect(DECK_VARIANTS.enter(0)).toEqual({ opacity: 0, x: 0, y: 8, scale: 1, filter: "blur(2px)" });
  expect(DECK_EXIT_TRANSITION).toEqual({ duration: 0.16, ease: [0.4, 0, 1, 1] });
  expect(DECK_ENTER_TRANSITION.duration).toBe(0.45);
  expect(DECK_ENTER_TRANSITION.delay).toBe(0.06);
  expect(TRACE_ROW_ENTER).toEqual({ opacity: 0, y: 4, filter: "blur(2px)" });
  expect(TRACE_ROW_EXIT).toEqual({ opacity: 0, y: -4, filter: "blur(2px)" });
  expect(TRACE_ROW_TRANSITION.duration).toBe(0.36);
  expect(TRACE_ROW_TRANSITION.ease(0.5)).toBeCloseTo(0.8158, 5);
});
