import { afterEach, expect, test } from "bun:test";
import { copyText } from "@/lib/clipboard";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const w = globalThis.window as any;

afterEach(() => {
  delete w.ardenDesktop;
});

test("copyText copies via the Electron bridge when it succeeds", async () => {
  const seen: string[] = [];
  w.ardenDesktop = { clipboard: { writeText: async (t: string) => (seen.push(t), true) } };
  expect(await copyText("hello")).toBe(true);
  expect(seen).toEqual(["hello"]);
});

test("copyText falls back to execCommand when the bridge is unavailable", async () => {
  delete w.ardenDesktop;
  const orig = document.execCommand;
  let called = false;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  document.execCommand = ((c: string) => (c === "copy" ? ((called = true), true) : false)) as any;
  try {
    expect(await copyText("x")).toBe(true);
    expect(called).toBe(true);
  } finally {
    document.execCommand = orig;
  }
});

// The bug this guards: navigator.clipboard resolves without writing in the
// Electron webview, so trusting it flashed "Copied" over an empty clipboard.
test("copyText returns false — never a misleading success — when every path fails", async () => {
  delete w.ardenDesktop;
  const origExec = document.execCommand;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  document.execCommand = (() => false) as any;
  const origClip = Object.getOwnPropertyDescriptor(navigator, "clipboard");
  Object.defineProperty(navigator, "clipboard", {
    value: {
      writeText: async () => {
        throw new Error("blocked");
      },
    },
    configurable: true,
  });
  try {
    expect(await copyText("x")).toBe(false);
  } finally {
    document.execCommand = origExec;
    if (origClip) Object.defineProperty(navigator, "clipboard", origClip);
  }
});
