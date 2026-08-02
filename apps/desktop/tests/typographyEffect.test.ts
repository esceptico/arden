import { afterEach, expect, test } from "bun:test";
import { applyTypography } from "@/lib/typography";

const root = document.documentElement;

afterEach(() => {
  root.style.removeProperty("--sans");
  root.style.removeProperty("--ui-font-size");
  root.style.removeProperty("font-size");
  delete root.dataset.fontSmoothing;
});

test("default prefs clear the font-family override and leave the smoothing attr unset", () => {
  applyTypography({ uiFont: "", uiFontSize: 14, fontSmoothing: true });

  expect(root.style.getPropertyValue("--sans")).toBe("");
  expect(root.style.getPropertyValue("--ui-font-size")).toBe("14px");
  expect(root.style.fontSize).toBe("100%");
  expect(root.dataset.fontSmoothing).toBeUndefined();
});

test("custom prefs set the font-family override, size, and non-default smoothing attr", () => {
  applyTypography({ uiFont: "Inter, sans-serif", uiFontSize: 18, fontSmoothing: false });

  expect(root.style.getPropertyValue("--sans")).toBe("Inter, sans-serif");
  expect(root.style.getPropertyValue("--ui-font-size")).toBe("18px");
  expect(Number.parseFloat(root.style.fontSize)).toBeCloseTo((18 / 14) * 100, 5);
  expect(root.dataset.fontSmoothing).toBe("native");
});

test("small text preferences preserve a 24px minimum geometry scale", () => {
  applyTypography({ uiFont: "", uiFontSize: 10, fontSmoothing: true });

  expect(root.style.getPropertyValue("--ui-font-size")).toBe("10px");
  expect(Number.parseFloat(root.style.fontSize)).toBeCloseTo((12 / 14) * 100, 5);
});

test("reverting to defaults removes the font-family override and smoothing attr", () => {
  applyTypography({ uiFont: "Inter, sans-serif", uiFontSize: 18, fontSmoothing: false });
  applyTypography({ uiFont: "", uiFontSize: 14, fontSmoothing: true });

  expect(root.style.getPropertyValue("--sans")).toBe("");
  expect(root.style.fontSize).toBe("100%");
  expect(root.dataset.fontSmoothing).toBeUndefined();
});
