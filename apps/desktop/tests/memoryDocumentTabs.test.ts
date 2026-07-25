import { expect, test } from "bun:test";
import { countFullyHiddenTabs } from "@/features/memory/components/MemoryDocumentTabs";

test("memory tab overflow counts only tabs fully outside the strip", () => {
  const strip = { left: 100, right: 400 };

  expect(countFullyHiddenTabs(strip, [
    { left: 80, right: 140 },
    { left: 140, right: 260 },
    { left: 360, right: 430 },
  ])).toBe(0);

  expect(countFullyHiddenTabs(strip, [
    { left: 20, right: 99.5 },
    { left: 140, right: 260 },
    { left: 399.4, right: 470 },
    { left: 405, right: 480 },
  ])).toBe(2);
});

test("memory tabs do not force the overflow label to +1", async () => {
  const source = await Bun.file(
    new URL("../src/features/memory/components/MemoryDocumentTabs.tsx", import.meta.url),
  ).text();

  expect(source).not.toContain("Math.max(1, hiddenCount)");
  expect(source).toContain("+{overflow.hiddenCount}");
  expect(source).toContain("{overflow.overflowing && (");
  expect(source).toContain("observer?.observe(list)");
  expect(source).not.toContain('observer?.observe(slot)');
});
