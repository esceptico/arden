import {
  createArea as createAreaApi,
  dismissAreaSuggestion as dismissAreaSuggestionApi, fetchAreasOverview as fetchAreasOverviewApi, fetchAreaDetail as fetchAreaDetailApi, resolveAsk as resolveAskApi, updateAreaAutonomy as updateAreaAutonomyApi, updateAreaSettings as updateAreaSettingsApi } from "@/api/areas";
import type { AreaAsk, AreaAttention, AreaInterrupts } from "@/api/areas";
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


export async function updateAreaSettings(
  key: string,
  patch: { attention?: AreaAttention; interrupts?: AreaInterrupts; paused?: boolean },
): Promise<void> {
  const s = getState();
  await updateAreaSettingsApi(s.config, key, patch);
  // The room shows these live; refetch is the source of truth (the row
  // PATCH response lacks the agent block).
  await fetchAreaDetail(key);
}

/** Contextual tuning ("fewer like this"): the feedback lands on the area's
 *  standing instructions, which the agent reads every turn via the AREA
 *  block — the ask itself is dismissed. */
export async function fewerLikeThis(ask: AreaAsk): Promise<void> {
  const s = getState();
  const record = s.areaRecords.find((r) => r.area_id === ask.area_key);
  const line = `- Fewer asks like: "${ask.text.slice(0, 120)}"`;
  const instructions = record?.instructions ? `${record.instructions}\n${line}` : line;
  await updateAreaSettingsApi(s.config, ask.area_key, { instructions });
  await resolveAsk(ask.area_key, ask.id, "dismissed");
}
