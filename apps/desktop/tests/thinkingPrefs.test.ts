import { expect, test } from "bun:test";
import { DEFAULT_PREFS, loadPrefs, persistPrefs, PREFS_VERSION } from "@/stores/prefs";
import { useStore } from "@/stores";

const PREFS_KEY = "arden.desktop.prefs";

test("previous prefs migrate while retaining valid thinking selections", () => {
  localStorage.setItem(
    PREFS_KEY,
    JSON.stringify({
      prefsVersion: PREFS_VERSION - 1,
      sidebarWidth: 272,
      thinkingAnimation: "orbit",
      thinkingIntensity: "subtle",
    }),
  );

  expect(loadPrefs()).toMatchObject({
    sidebarWidth: DEFAULT_PREFS.sidebarWidth,
    thinkingAnimation: "orbit",
    thinkingIntensity: "subtle",
  });
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

test("stale thinking IDs fall back to the canonical defaults", () => {
  localStorage.setItem(
    PREFS_KEY,
    JSON.stringify({
      prefsVersion: PREFS_VERSION,
      thinkingAnimation: "legacy-pulse",
      thinkingIntensity: "maximum",
    }),
  );

  expect(loadPrefs()).toMatchObject({
    thinkingAnimation: DEFAULT_PREFS.thinkingAnimation,
    thinkingIntensity: DEFAULT_PREFS.thinkingIntensity,
  });
});

test("persistPrefs cannot write arbitrary thinking IDs through an unsafe caller", () => {
  persistPrefs({
    ...DEFAULT_PREFS,
    thinkingAnimation: "legacy-pulse" as never,
    thinkingIntensity: "maximum" as never,
  });

  expect(JSON.parse(localStorage.getItem(PREFS_KEY) ?? "{}"))
    .toMatchObject({
      prefsVersion: PREFS_VERSION,
      thinkingAnimation: DEFAULT_PREFS.thinkingAnimation,
      thinkingIntensity: DEFAULT_PREFS.thinkingIntensity,
    });
});

test("setPref refuses arbitrary thinking IDs at the state boundary", () => {
  const previous = useStore.getState().prefs;
  try {
    useStore.getState().setPref("thinkingAnimation", "legacy-pulse" as never);
    useStore.getState().setPref("thinkingIntensity", "maximum" as never);

    expect(useStore.getState().prefs).toMatchObject({
      thinkingAnimation: previous.thinkingAnimation,
      thinkingIntensity: previous.thinkingIntensity,
    });
  } finally {
    useStore.setState({ prefs: previous });
  }
});
