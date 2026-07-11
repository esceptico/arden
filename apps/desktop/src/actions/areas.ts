import {
  createArea as createAreaApi,
  createAreaPage as createAreaPageApi,
  createAreaOutcome as createAreaOutcomeApi,
  detachAreaPage as detachAreaPageApi,
  dismissAreaSuggestion as dismissAreaSuggestionApi,
  fetchAreasOverview as fetchAreasOverviewApi,
  fetchAreaDetail as fetchAreaDetailApi,
  resolveAsk as resolveAskApi,
  replyToAsk as replyToAskApi,
  updateAreaAutonomy as updateAreaAutonomyApi,
  updateAreaOutcome as updateAreaOutcomeApi,
  updateAreaSettings as updateAreaSettingsApi,
  updateAreaWorkItem as updateAreaWorkItemApi,
} from "@/api/areas";
import type {
  AreaAsk,
  AreaAttention,
  AreaInterrupts,
  AreaOutcomeStatus,
  AreaWorkItem,
  AreaWorkStatus,
} from "@/api/areas";
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

async function refreshAreaWork(key: string): Promise<void> {
  await Promise.all([fetchAreaDetail(key), fetchAreasOverview()]);
}

export async function createAreaOutcome(
  key: string,
  body: { key: string; title: string; success_criteria: string; priority: number },
): Promise<void> {
  const s = getState();
  await createAreaOutcomeApi(s.config, key, body);
  await refreshAreaWork(key);
}

export async function updateAreaOutcome(
  key: string,
  outcomeKey: string,
  body: {
    expected_updated_at: string;
    title?: string;
    success_criteria?: string;
    priority?: number;
    status?: AreaOutcomeStatus;
  },
): Promise<void> {
  const s = getState();
  await updateAreaOutcomeApi(s.config, key, outcomeKey, body);
  await refreshAreaWork(key);
}

export async function updateAreaWorkItem(
  key: string,
  workKey: string,
  body: {
    expected_updated_at: string;
    text?: string;
    status?: AreaWorkStatus;
    owner?: AreaWorkItem["owner"];
    due_at?: string | null;
    next_attempt_at?: string | null;
  },
): Promise<void> {
  const s = getState();
  await updateAreaWorkItemApi(s.config, key, workKey, body);
  await refreshAreaWork(key);
}

export async function resolveAsk(
  key: string,
  askId: string,
  state: string,
  snoozedUntil?: string,
  resolution?: string,
): Promise<void> {
  const s = getState();
  await resolveAskApi(s.config, key, askId, state, snoozedUntil, resolution);
  s.areaAskResolved(key, askId);
}

export async function replyToAsk(key: string, askId: string, message: string): Promise<void> {
  const s = getState();
  await replyToAskApi(s.config, key, askId, message);
  s.areaAskResolved(key, askId);
}

export async function updateAreaAutonomy(key: string, autonomy: "observe" | "act" | null): Promise<void> {
  const s = getState();
  const record = await updateAreaAutonomyApi(s.config, key, autonomy);
  s.upsertAreaRecord(record);
}

export async function promoteSuggestedArea(title: string, pagePath: string): Promise<void> {
  const s = getState();
  const record = await createAreaApi(s.config, title, pagePath);
  s.upsertAreaRecord(record);
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
  const record = await updateAreaSettingsApi(s.config, key, patch);
  s.upsertAreaRecord(record);
  // The room shows these live; refetch is the source of truth (the row
  // PATCH response lacks the agent block).
  await fetchAreaDetail(key);
}

/** Contextual tuning ("fewer like this"): the feedback lands on the area's
 *  standing instructions, which the agent reads every turn via the AREA
 *  block — the ask itself is dismissed. */
export async function fewerLikeThis(ask: AreaAsk): Promise<void> {
  const s = getState();
  const record = s.areas.recordsById[ask.area_key];
  const line = `- Fewer asks like: "${ask.text.slice(0, 120)}"`;
  const instructions = record?.instructions ? `${record.instructions}\n${line}` : line;
  const updated = await updateAreaSettingsApi(s.config, ask.area_key, { instructions });
  s.upsertAreaRecord(updated);
  await resolveAsk(ask.area_key, ask.id, "dismissed", undefined, "dismissed");
}

export async function createAreaPage(key: string): Promise<void> {
  const s = getState();
  const record = await createAreaPageApi(s.config, key);
  s.upsertAreaRecord(record);
  await Promise.all([fetchAreaDetail(key), fetchAreasOverview()]);
}

export async function detachAreaPage(key: string): Promise<void> {
  const s = getState();
  const record = await detachAreaPageApi(s.config, key);
  s.upsertAreaRecord(record);
  await Promise.all([fetchAreaDetail(key), fetchAreasOverview()]);
}
