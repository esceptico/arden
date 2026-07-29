import { expect, test } from "bun:test";
import { DEFAULT_PREFS, loadPrefs, persistPrefs, PREFS_VERSION } from "@/stores/prefs";
import { useStore } from "@/stores";

const PREFS_KEY = "arden.desktop.prefs";

test("previous prefs migrate while retaining a valid intensity", () => {
  localStorage.setItem(
    PREFS_KEY,
    JSON.stringify({
      prefsVersion: PREFS_VERSION - 1,
      sidebarWidth: 272,
      thinkingAnimation: "orbit",
      thinkingIntensity: "subtle",
    }),
  );

  const loaded = loadPrefs();
  expect(loaded).toMatchObject({
    sidebarWidth: DEFAULT_PREFS.sidebarWidth,
    thinkingIntensity: "subtle",
  });
  // The four treatments collapsed into one strip, so a saved treatment is
  // meaningless rather than merely invalid — it is dropped, not defaulted.
  expect(loaded).not.toHaveProperty("thinkingAnimation");
});

test("redesign defaults migrate Chat open and Area closed", () => {
  localStorage.setItem(
    PREFS_KEY,
    JSON.stringify({
      prefsVersion: PREFS_VERSION - 1,
      rightPanelCollapsed: true,
      areaHubCollapsed: false,
    }),
  );

  expect(loadPrefs()).toMatchObject({
    rightPanelCollapsed: false,
    areaHubCollapsed: true,
  });
});

test("stale intensity IDs fall back to the canonical default", () => {
  localStorage.setItem(
    PREFS_KEY,
    JSON.stringify({
      prefsVersion: PREFS_VERSION,
      thinkingAnimation: "legacy-pulse",
      thinkingIntensity: "maximum",
    }),
  );

  const loaded = loadPrefs();
  expect(loaded).toMatchObject({ thinkingIntensity: DEFAULT_PREFS.thinkingIntensity });
  expect(loaded).not.toHaveProperty("thinkingAnimation");
});

test("persistPrefs cannot write an arbitrary intensity through an unsafe caller", () => {
  persistPrefs({ ...DEFAULT_PREFS, thinkingIntensity: "maximum" as never });

  expect(JSON.parse(localStorage.getItem(PREFS_KEY) ?? "{}"))
    .toMatchObject({
      prefsVersion: PREFS_VERSION,
      thinkingIntensity: DEFAULT_PREFS.thinkingIntensity,
    });
});

test("setPref refuses an arbitrary intensity at the state boundary", () => {
  const previous = useStore.getState().prefs;
  try {
    useStore.getState().setPref("thinkingIntensity", "maximum" as never);

    expect(useStore.getState().prefs).toMatchObject({
      thinkingIntensity: previous.thinkingIntensity,
    });
  } finally {
    useStore.setState({ prefs: previous });
  }
});
