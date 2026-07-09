import { apiWithConfig, type AppConfig } from "@/api/core";

export interface SliceSummary {
  /** The container's project_id (slices and projects are one concept). */
  key: string;
  title: string;
  autonomy: "observe" | "act" | null;
  page_path: string | null;
  live: boolean;
  updated: string;
  ask_count: number;
}

export interface SliceAsk {
  id: string;
  slice_key: string;
  text: string;
  kind: "review" | "decide" | "act" | "drift";
  source: string;
  actions: { verb: string; ref: string }[];
  state: string;
  created_at: string;
  snoozed_until: string | null;
  provenance?: string | null;
}

export interface SliceSuggestion {
  id: string;
  key: string;
  title: string;
  page_path: string;
  rationale: string;
  created_at: string;
}

export interface SlicesOverview {
  slices: SliceSummary[];
  focus: SliceAsk[];
  suggested?: SliceSuggestion[];
}

export interface SliceDetail {
  key: string;
  title: string;
  autonomy: "observe" | "act" | null;
  page_path: string | null;
  related: string[];
  open_loops: string[];
  updated: string;
  asks: SliceAsk[];
  sessions: { session_id: string; name: string }[];
  automations: unknown[];
}

export async function fetchSlicesOverview(config: AppConfig): Promise<SlicesOverview> {
  return apiWithConfig<SlicesOverview>(config, "/slices");
}

export async function fetchSliceDetail(config: AppConfig, key: string): Promise<SliceDetail> {
  return apiWithConfig<SliceDetail>(config, `/slices/${encodeURIComponent(key)}`);
}

export async function resolveAsk(
  config: AppConfig,
  key: string,
  askId: string,
  state: string,
  snoozedUntil?: string,
): Promise<SliceAsk> {
  const body: { state: string; snoozed_until?: string } = { state };
  if (snoozedUntil) body.snoozed_until = snoozedUntil;
  return apiWithConfig<SliceAsk>(config, `/slices/${encodeURIComponent(key)}/asks/${encodeURIComponent(askId)}/resolve`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// Server returns the updated project row — not a full SliceDetail (no
// asks/sessions/automations). The action layer merges just the autonomy
// field into any cached detail.
export async function updateSliceAutonomy(
  config: AppConfig,
  key: string,
  autonomy: "observe" | "act",
): Promise<{ project_id: string; name: string; autonomy: "observe" | "act" | null }> {
  return apiWithConfig(config, `/slices/${encodeURIComponent(key)}`, {
    method: "PUT",
    body: JSON.stringify({ autonomy }),
  });
}

/** Attach capabilities: mint a new container (name) or grow an existing one
 *  (project_id) with a page + observing agent. */
export async function createSlice(
  config: AppConfig,
  title: string,
  pagePath: string,
): Promise<void> {
  await apiWithConfig(config, "/slices", {
    method: "POST",
    body: JSON.stringify({ name: title, page_path: pagePath }),
  });
}

export async function dismissSliceSuggestion(config: AppConfig, key: string): Promise<void> {
  await apiWithConfig(config, `/slices/suggestions/${encodeURIComponent(key)}/dismiss`, {
    method: "POST",
  });
}
