export type SettingsSurface = "providers" | "integrations";

export function settingsErrorTitle(surface: SettingsSurface, hasLoadedData: boolean): string {
  if (hasLoadedData) {
    return surface === "providers" ? "Provider action failed" : "Integration action failed";
  }
  return surface === "providers" ? "Couldn't load providers" : "Couldn't load integrations";
}

export function shouldShowLoadedSettingsContent({
  loading,
  error,
  hasData,
}: {
  loading: boolean;
  error: string | null;
  hasData: boolean;
}): boolean {
  if (loading) return hasData;
  return !error || hasData;
}

export function settingsErrorMessage(error: string): string {
  if (error === "Failed to fetch") return "Couldn't reach Arden server.";
  return error;
}
