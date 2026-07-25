import {
  createAreaPage as createAreaPageApi,
  createAreaOutcome as createAreaOutcomeApi,
  detachAreaPage as detachAreaPageApi,
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
  AreaAskState,
  AreaAttention,
  AreaInterrupts,
  AreaOutcomeStatus,
  AreaWorkItem,
  AreaWorkStatus,
} from "@/api/areas";
import { getState } from "@/stores";

export async function fetchAreasOverview(): Promise<boolean> {
  const s = getState();
  s.setAreasOverviewPhase("loading");
  try {
    const overview = await fetchAreasOverviewApi(s.config);
    s.areasOverviewLoaded(overview);
    return true;
  } catch {
    s.setAreasOverviewPhase("error");
    return false;
  }
}

export async function fetchAreaDetail(key: string): Promise<boolean> {
  const s = getState();
  s.setAreaDetailPhase(key, "loading");
  try {
    const detail = await fetchAreaDetailApi(s.config, key);
    s.areaDetailLoaded(detail);
    return true;
  } catch {
    s.setAreaDetailPhase(key, "error");
    return false;
  }
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
  askId: string,
  state: AreaAskState,
  snoozedUntil?: string,
  resolution?: string,
): Promise<void> {
  const s = getState();
  await resolveAskApi(s.config, askId, state, snoozedUntil, resolution);
  if (state === "active") {
    // Reopening needs the canonical overview row back, not a local splice.
    await fetchAreasOverview();
  } else {
    s.areaAskResolved(askId);
  }
}

export async function replyToAsk(askId: string, message: string): Promise<void> {
  const s = getState();
  await replyToAskApi(s.config, askId, message);
  s.areaAskResolved(askId);
}

export async function updateAreaAutonomy(key: string, autonomy: "observe" | "act" | null): Promise<void> {
  const s = getState();
  const record = await updateAreaAutonomyApi(s.config, key, autonomy);
  s.upsertAreaRecord(record);
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
