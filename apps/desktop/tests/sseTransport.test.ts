import { describe, expect, test } from "bun:test";
import { classifySseOpen, parseSseFrame, sseReconnectDelayMs } from "@/lib/sseTransport";

describe("sseReconnectDelayMs", () => {
  const fixed = { jitterRatio: 0, random: () => 0.5 };

  test("grows exponentially and caps at maxMs", () => {
    expect(sseReconnectDelayMs(0, { baseMs: 500, maxMs: 15_000, ...fixed })).toBe(500);
    expect(sseReconnectDelayMs(1, { baseMs: 500, maxMs: 15_000, ...fixed })).toBe(1000);
    expect(sseReconnectDelayMs(2, { baseMs: 500, maxMs: 15_000, ...fixed })).toBe(2000);
    expect(sseReconnectDelayMs(20, { baseMs: 500, maxMs: 15_000, ...fixed })).toBe(15_000);
  });

  test("never returns a negative delay even with full negative jitter", () => {
    expect(
      sseReconnectDelayMs(0, { baseMs: 100, jitterRatio: 1, random: () => 0 }),
    ).toBeGreaterThanOrEqual(0);
  });
});

describe("classifySseOpen", () => {
  test("ok only for a 2xx event-stream response", () => {
    expect(classifySseOpen(200, true, "text/event-stream; charset=utf-8")).toBe("ok");
    expect(classifySseOpen(200, true, "application/json")).toBe("retry");
  });

  test("4xx (except 429) is fatal — stale token / deleted resource", () => {
    expect(classifySseOpen(401, false, "")).toBe("fatal");
    expect(classifySseOpen(404, false, "")).toBe("fatal");
  });

  test("429 and 5xx are transient → retry", () => {
    expect(classifySseOpen(429, false, "")).toBe("retry");
    expect(classifySseOpen(503, false, "")).toBe("retry");
  });
});

describe("parseSseFrame", () => {
  test("marks malformed event data as fatal", () => {
    expect(() => parseSseFrame("bad", () => {
      throw new Error("broken payload");
    })).toThrow("invalid stream event: broken payload");
  });
});
