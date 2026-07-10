import { apiWithConfig, type AppConfig } from "@/api/core";

export interface AreaSummary {
  /** The area's id. */
  key: string;
  title: string;
  autonomy: "observe" | "act" | null;
  page_path: string | null;
  live: boolean;
  updated: string;
  ask_count: number;
}

export interface AreaAsk {
  id: string;
  area_key: string;
  text: string;
  kind: "review" | "decide" | "act" | "drift";
  source: string;
  actions: { verb: string; ref: string }[];
  state: string;
  created_at: string;
  snoozed_until: string | null;
  provenance?: string | null;
}

export interface AreaSuggestion {
  id: string;
  key: string;
  title: string;
  page_path: string;
  rationale: string;
  created_at: string;
}

export interface AreasOverview {
  areas: AreaSummary[];
  focus: AreaAsk[];
  suggested?: AreaSuggestion[];
}

export interface AreaDetail {
  key: string;
  title: string;
  autonomy: "observe" | "act" | null;
  page_path: string | null;
  related: string[];
  open_loops: string[];
  updated: string;
  asks: AreaAsk[];
  sessions: { session_id: string; name: string }[];
  automations: unknown[];
}

export async function fetchAreasOverview(config: AppConfig): Promise<AreasOverview> {
  return apiWithConfig<AreasOverview>(config, "/areas/overview");
}

export async function fetchAreaDetail(config: AppConfig, key: string): Promise<AreaDetail> {
  return apiWithConfig<AreaDetail>(config, `/areas/${encodeURIComponent(key)}`);
}

export async function resolveAsk(
  config: AppConfig,
  key: string,
  askId: string,
  state: string,
  snoozedUntil?: string,
): Promise<AreaAsk> {
  const body: { state: string; snoozed_until?: string } = { state };
  if (snoozedUntil) body.snoozed_until = snoozedUntil;
  return apiWithConfig<AreaAsk>(config, `/areas/${encodeURIComponent(key)}/asks/${encodeURIComponent(askId)}/resolve`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// Server returns the updated area row — not a full AreaDetail (no
// asks/sessions/automations). The action layer merges just the autonomy
// field into any cached detail.
export async function updateAreaAutonomy(
  config: AppConfig,
  key: string,
  autonomy: "observe" | "act",
): Promise<{ area_id: string; name: string; autonomy: "observe" | "act" | null }> {
  return apiWithConfig(config, `/areas/${encodeURIComponent(key)}/autonomy`, {
    method: "PUT",
    body: JSON.stringify({ autonomy }),
  });
}

/** Attach capabilities: mint a new container (name) or grow an existing one
 *  (area_id) with a page + observing agent. */
export async function createArea(
  config: AppConfig,
  title: string,
  pagePath: string,
): Promise<void> {
  await apiWithConfig(config, "/areas", {
    method: "POST",
    body: JSON.stringify({ name: title, page_path: pagePath }),
  });
}

export async function dismissAreaSuggestion(config: AppConfig, key: string): Promise<void> {
  await apiWithConfig(config, `/areas/suggestions/${encodeURIComponent(key)}/dismiss`, {
    method: "POST",
  });
}
