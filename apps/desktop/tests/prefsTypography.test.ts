import { expect, test } from "bun:test";
import { DEFAULT_PREFS, loadPrefs, PREFS_VERSION } from "@/stores/prefs";
import { useStore } from "@/stores";

const PREFS_KEY = "arden.desktop.prefs";

test("restored typography junk falls back to defaults", () => {
  localStorage.setItem(
    PREFS_KEY,
    JSON.stringify({
      prefsVersion: PREFS_VERSION,
      uiFontSize: "big",
      uiFont: 42,
    }),
  );

  expect(loadPrefs()).toMatchObject({
    uiFontSize: DEFAULT_PREFS.uiFontSize,
    uiFont: DEFAULT_PREFS.uiFont,
  });
});

test("valid restored typography values survive", () => {
  localStorage.setItem(
    PREFS_KEY,
    JSON.stringify({
      prefsVersion: PREFS_VERSION,
      uiFontSize: 18,
      uiFont: "Inter, sans-serif",
      fontSmoothing: false,
    }),
  );

  expect(loadPrefs()).toMatchObject({
    uiFontSize: 18,
    uiFont: "Inter, sans-serif",
    fontSmoothing: false,
  });
});

test("font size outside [10, 24] falls back to the default", () => {
  localStorage.setItem(
    PREFS_KEY,
    JSON.stringify({
      prefsVersion: PREFS_VERSION,
      uiFontSize: 9,
    }),
  );

  expect(loadPrefs()).toMatchObject({ uiFontSize: DEFAULT_PREFS.uiFontSize });
});

test("retired codeFont/codeFontSize/diffMarkers keys are stripped on load", () => {
  localStorage.setItem(
    PREFS_KEY,
    JSON.stringify({
      prefsVersion: PREFS_VERSION,
      codeFont: "JetBrains Mono, monospace",
      codeFontSize: 12,
      diffMarkers: "markers",
    }),
  );

  const restored = loadPrefs() as Record<string, unknown>;
  expect(restored.codeFont).toBeUndefined();
  expect(restored.codeFontSize).toBeUndefined();
  expect(restored.diffMarkers).toBeUndefined();
});

test("setPref refuses an out-of-range font size at the state boundary", () => {
  const previous = useStore.getState().prefs;
  try {
    useStore.getState().setPref("uiFontSize", 999);
    expect(useStore.getState().prefs.uiFontSize).toBe(previous.uiFontSize);
  } finally {
    useStore.setState({ prefs: previous });
  }
});
