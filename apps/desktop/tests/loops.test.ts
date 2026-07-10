import { expect, test } from "bun:test";
import { formatLoopCountdown } from "@/features/chat/lib/loops";
import { truncatePrompt } from "@/actions/_shared";

test("formats loop countdown", () => {
  expect(formatLoopCountdown(1_060_000, 1_000_000)).toBe("60s");
  // Under a minute renders as live seconds so the user can watch the tick.
  expect(formatLoopCountdown(1_045_000, 1_000_000)).toBe("45s");
  expect(formatLoopCountdown(1_001_000, 1_000_000)).toBe("1s");
  expect(formatLoopCountdown(1_000_000, 1_000_000)).toBe("0s");
  expect(formatLoopCountdown(1_000_000 + 90 * 60_000, 1_000_000)).toBe("1h 30m");
  expect(formatLoopCountdown(1_000_000 + 25 * 60 * 60_000, 1_000_000)).toBe("1d");
});

test("truncatePrompt collapses whitespace and trims", () => {
  expect(truncatePrompt("short prompt")).toBe("short prompt");
  expect(truncatePrompt("  spaced   out\n\nprompt  ")).toBe("spaced out prompt");
});

test("truncatePrompt under the limit returns as-is", () => {
  const text = "a".repeat(80);
  expect(truncatePrompt(text)).toBe(text);
  expect(truncatePrompt(text).length).toBe(80);
});

test("truncatePrompt exactly at the limit returns as-is", () => {
  const text = "a".repeat(80);
  expect(truncatePrompt(text, 80)).toBe(text);
});

test("truncatePrompt over the limit areas and ellipsizes", () => {
  const text = "a".repeat(120);
  const out = truncatePrompt(text);
  expect(out.length).toBe(80);
  expect(out.endsWith("…")).toBe(true);
  expect(out.slice(0, 79)).toBe("a".repeat(79));
});

test("truncatePrompt handles multi-paragraph input", () => {
  const text = "para one with quite a bit of text\n\npara two also goes on for a while and should push past the limit";
  const out = truncatePrompt(text);
  expect(out.length).toBeLessThanOrEqual(80);
  expect(out.includes("\n")).toBe(false);
  expect(out.endsWith("…")).toBe(true);
});
