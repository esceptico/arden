import {
  createArea as createAreaApi,
  dismissAreaSuggestion as dismissAreaSuggestionApi, fetchAreasOverview as fetchAreasOverviewApi, fetchAreaDetail as fetchAreaDetailApi, resolveAsk as resolveAskApi, updateAreaAutonomy as updateAreaAutonomyApi } from "@/api/areas";
import { getState } from "@/stores";

export async function fetchAreasOverview(): Promise<void> {
  const s = getState();
  try {
    const overview = await fetchAreasOverviewApi(s.config);
    s.areasOverviewLoaded(overview);
  } catch {
    /* leave previous overview in place */
  }
}

export async function fetchAreaDetail(key: string): Promise<void> {
  const s = getState();
  const detail = await fetchAreaDetailApi(s.config, key);
  s.areaDetailLoaded(detail);
}

export async function resolveAsk(key: string, askId: string, state: string, snoozedUntil?: string): Promise<void> {
  const s = getState();
  await resolveAskApi(s.config, key, askId, state, snoozedUntil);
  s.areaAskResolved(key, askId);
}

export async function updateAreaAutonomy(key: string, autonomy: "observe" | "act"): Promise<void> {
  const s = getState();
  const record = await updateAreaAutonomyApi(s.config, key, autonomy);
  s.areaAutonomyUpdated(key, record.autonomy ?? "observe");
}

export async function promoteSuggestedArea(title: string, pagePath: string): Promise<void> {
  const s = getState();
  await createAreaApi(s.config, title, pagePath);
  await fetchAreasOverview();
}

export async function dismissAreaSuggestion(key: string): Promise<void> {
  const s = getState();
  await dismissAreaSuggestionApi(s.config, key);
  await fetchAreasOverview();
}
