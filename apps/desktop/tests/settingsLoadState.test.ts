import { expect, test } from "bun:test";
import {
  settingsErrorMessage,
  settingsErrorTitle,
  shouldShowLoadedSettingsContent,
} from "@/features/settings/lib/settingsLoadState";

test("distinguishes initial load failures from action failures", () => {
  expect(settingsErrorTitle("providers", false)).toBe("Couldn't load providers");
  expect(settingsErrorTitle("integrations", false)).toBe("Couldn't load integrations");
  expect(settingsErrorTitle("providers", true)).toBe("Provider action failed");
});

test("does not show empty loaded content after initial load failed", () => {
  expect(shouldShowLoadedSettingsContent({ loading: false, error: "Failed to fetch", hasData: false })).toBe(false);
  expect(shouldShowLoadedSettingsContent({ loading: false, error: "Failed to fetch", hasData: true })).toBe(true);
  expect(shouldShowLoadedSettingsContent({ loading: false, error: null, hasData: false })).toBe(true);
});

test("uses human wording for browser network errors", () => {
  expect(settingsErrorMessage("Failed to fetch")).toBe("Couldn't reach Arden server.");
  expect(settingsErrorMessage("Missing API key. Include Authorization: Bearer <key> header.")).toBe(
    "Missing API key. Include Authorization: Bearer <key> header.",
  );
});
