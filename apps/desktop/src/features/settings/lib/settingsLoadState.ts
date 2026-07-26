export type SettingsSurface = "providers" | "integrations" | "notifiers";

const SURFACE_NOUNS: Record<SettingsSurface, { action: string; load: string }> = {
  providers: { action: "Provider action failed", load: "Couldn't load providers" },
  integrations: { action: "Integration action failed", load: "Couldn't load integrations" },
  notifiers: { action: "Notifier action failed", load: "Couldn't load notifiers" },
};

export function settingsErrorTitle(surface: SettingsSurface, hasLoadedData: boolean): string {
  return hasLoadedData ? SURFACE_NOUNS[surface].action : SURFACE_NOUNS[surface].load;
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
