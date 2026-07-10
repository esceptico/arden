import { apiWithConfig, type AppConfig } from "@/api/core";
import type { Area } from "@/api/types";

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
  /** Three-verb taxonomy: notify = FYI (expires quietly), question = the
   *  agent is blocked on the user's judgment, review = a proposed action
   *  awaiting approval. */
  kind: "notify" | "question" | "review";
  source: string;
  actions: { verb: string; ref: string }[];
  state: string;
  created_at: string;
  snoozed_until: string | null;
  provenance?: string | null;
  why_now?: string | null;
  what_next?: string | null;
  expires_at?: string | null;
  stable_key?: string | null;
  resolution?: string | null;
  resolved_at?: string | null;
}

export type AreaAttention = "dormant" | "ambient" | "active";
export type AreaInterrupts = "asks" | "all" | "none";

/** The custodian's liveness block — the room's "last checked / next check"
 *  line and run budget come from here. Null on agentless areas. */
export interface AreaAgentStatus {
  last_checked: string | null;
  next_check: string | null;
  next_check_reason: string | null;
  last_report: string | null;
  woken_by: string[];
  runs_today: number;
  runs_cap: number;
  running_since: string | null;
  availability: "ready" | "unavailable" | "error";
  last_error: string | null;
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
  attention: AreaAttention;
  interrupts: AreaInterrupts;
  paused: boolean;
  agent: AreaAgentStatus | null;
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
  resolution?: string,
): Promise<AreaAsk> {
  const body: { state: string; snoozed_until?: string; resolution?: string } = { state };
  if (snoozedUntil) body.snoozed_until = snoozedUntil;
  if (resolution) body.resolution = resolution;
  return apiWithConfig<AreaAsk>(config, `/areas/${encodeURIComponent(key)}/asks/${encodeURIComponent(askId)}/resolve`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function replyToAsk(config: AppConfig, key: string, askId: string, message: string): Promise<AreaAsk> {
  return apiWithConfig<AreaAsk>(
    config,
    `/areas/${encodeURIComponent(key)}/asks/${encodeURIComponent(askId)}/reply`,
    { method: "POST", body: JSON.stringify({ message }) },
  );
}

// Server returns the updated area row — not a full AreaDetail (no
// asks/sessions/automations). The action layer merges just the autonomy
// field into any cached detail.
export async function updateAreaAutonomy(
  config: AppConfig,
  key: string,
  autonomy: "observe" | "act" | null,
): Promise<Area> {
  return apiWithConfig(config, `/areas/${encodeURIComponent(key)}/autonomy`, {
    method: "PUT",
    body: JSON.stringify({ autonomy }),
  });
}

/** Attach a page capability without implicitly delegating a Custodian. */
export async function createArea(
  config: AppConfig,
  title: string,
  pagePath: string,
): Promise<Area> {
  return apiWithConfig(config, "/areas", {
    method: "POST",
    body: JSON.stringify({ name: title, page_path: pagePath }),
  });
}

/** PATCH the custodian's operator settings (attention / interrupts / pause /
 *  instructions). Returns the updated area row, not a full detail. */
export async function updateAreaSettings(
  config: AppConfig,
  key: string,
  patch: {
    attention?: AreaAttention;
    interrupts?: AreaInterrupts;
    paused?: boolean;
    instructions?: string | null;
  },
): Promise<Area> {
  return apiWithConfig(config, `/areas/${encodeURIComponent(key)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export async function createAreaPage(config: AppConfig, key: string): Promise<Area> {
  return apiWithConfig(config, `/areas/${encodeURIComponent(key)}/page`, { method: "POST" });
}

export async function detachAreaPage(config: AppConfig, key: string): Promise<Area> {
  return apiWithConfig(config, `/areas/${encodeURIComponent(key)}/page`, { method: "DELETE" });
}

export async function restoreArea(config: AppConfig, key: string): Promise<Area> {
  return apiWithConfig(config, `/areas/${encodeURIComponent(key)}/restore`, { method: "POST" });
}

export async function dismissAreaSuggestion(config: AppConfig, key: string): Promise<void> {
  await apiWithConfig(config, `/areas/suggestions/${encodeURIComponent(key)}/dismiss`, {
    method: "POST",
  });
}
