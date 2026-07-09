import {
  createSlice as createSliceApi,
  dismissSliceSuggestion as dismissSliceSuggestionApi, fetchSlicesOverview as fetchSlicesOverviewApi, fetchSliceDetail as fetchSliceDetailApi, resolveAsk as resolveAskApi, updateSliceAutonomy as updateSliceAutonomyApi } from "@/api/slices";
import { getState } from "@/stores";

export async function fetchSlicesOverview(): Promise<void> {
  const s = getState();
  try {
    const overview = await fetchSlicesOverviewApi(s.config);
    s.slicesOverviewLoaded(overview);
  } catch {
    /* leave previous overview in place */
  }
}

export async function fetchSliceDetail(key: string): Promise<void> {
  const s = getState();
  const detail = await fetchSliceDetailApi(s.config, key);
  s.sliceDetailLoaded(detail);
}

export async function resolveAsk(key: string, askId: string, state: string, snoozedUntil?: string): Promise<void> {
  const s = getState();
  await resolveAskApi(s.config, key, askId, state, snoozedUntil);
  s.sliceAskResolved(key, askId);
}

export async function updateSliceAutonomy(key: string, autonomy: "observe" | "act"): Promise<void> {
  const s = getState();
  const record = await updateSliceAutonomyApi(s.config, key, autonomy);
  s.sliceAutonomyUpdated(key, record.autonomy ?? "observe");
}

export async function promoteSuggestedSlice(title: string, pagePath: string): Promise<void> {
  const s = getState();
  await createSliceApi(s.config, title, pagePath);
  await fetchSlicesOverview();
}

export async function dismissSliceSuggestion(key: string): Promise<void> {
  const s = getState();
  await dismissSliceSuggestionApi(s.config, key);
  await fetchSlicesOverview();
}
