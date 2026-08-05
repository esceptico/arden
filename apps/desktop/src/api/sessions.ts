import { apiWithConfig, type AppConfig } from "@/api/core";
import type { Area, ContextBudgetSnapshot, SessionListItem, SessionType } from "@/api/types";

const SESSION_PAGE_SIZE = 500;

export async function listPrimarySessionsApi(config: AppConfig): Promise<SessionListItem[]> {
  const all: SessionListItem[] = [];
  for (let offset = 0; ; offset += SESSION_PAGE_SIZE) {
    const { sessions } = await apiWithConfig<{ sessions: SessionListItem[] }>(
      config,
      `/sessions?limit=${SESSION_PAGE_SIZE}&offset=${offset}&include_agents=false`,
    );
    all.push(...sessions);
    if (sessions.length < SESSION_PAGE_SIZE) return all;
  }
}

export async function renameSessionApi(
  config: AppConfig,
  sessionId: string,
  name: string,
): Promise<void> {
  await apiWithConfig(config, `/sessions/${encodeURIComponent(sessionId)}`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });
}

export async function listAreasApi(config: AppConfig): Promise<Area[]> {
  const response = await apiWithConfig<{ areas: Area[] }>(config, "/areas");
  return response.areas;
}

export async function createAreaApi(
  config: AppConfig,
  payload: { name: string; default_cwd?: string | null; instructions?: string | null; page_path?: string },
): Promise<Area> {
  return apiWithConfig<Area>(config, "/areas", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateAreaApi(
  config: AppConfig,
  areaId: string,
  patch: Partial<Pick<Area, "name" | "default_cwd" | "instructions" | "knowledge_scope">>,
): Promise<Area> {
  return apiWithConfig<Area>(config, `/areas/${encodeURIComponent(areaId)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export async function archiveAreaApi(config: AppConfig, areaId: string): Promise<void> {
  await apiWithConfig(config, `/areas/${encodeURIComponent(areaId)}`, {
    method: "DELETE",
  });
}

export async function moveSessionToAreaApi(
  config: AppConfig,
  sessionId: string,
  areaId: string | null,
): Promise<void> {
  await apiWithConfig(config, `/sessions/${encodeURIComponent(sessionId)}/area`, {
    method: "POST",
    body: JSON.stringify({ area_id: areaId }),
  });
}

export interface TriageTarget {
  /** The container's area_id. */
  key: string;
  title: string;
}

export interface TriageDecision {
  decision: "move" | "create" | "none";
  target?: TriageTarget | null;
  new_title?: string | null;
  rationale: string;
}

/** Ask the server where this just-started chat belongs. Read-only — the
 *  caller runs the chosen action. */
export async function triageSessionApi(config: AppConfig, sessionId: string): Promise<TriageDecision> {
  return apiWithConfig<TriageDecision>(config, `/sessions/${encodeURIComponent(sessionId)}/triage`, {
    method: "POST",
  });
}

export async function updateSessionModelApi(
  config: AppConfig,
  sessionId: string,
  chatModel: string | null,
): Promise<{ session_id: string; chat_model: string | null; context_budget: ContextBudgetSnapshot }> {
  return apiWithConfig(config, `/sessions/${encodeURIComponent(sessionId)}/model`, {
    method: "PUT",
    body: JSON.stringify({ chat_model: chatModel }),
  });
}

export async function archiveSessionApi(config: AppConfig, sessionId: string): Promise<void> {
  await apiWithConfig(config, `/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  });
}

export interface ArchivedSession {
  session_id: string;
  started_at: string;
  last_activity: string;
  name: string | null;
  archived_at: string;
  message_count: number;
  area_id?: string | null;
  session_type?: SessionType;
  origin_automation_id?: string | null;
  parent_session_id?: string | null;
  parent_tool_call_id?: string | null;
  agent_type?: string | null;
  agent_status?: string | null;
  storage_state?: "hot" | "cold";
  cold_bundle_bytes?: number;
}

export async function listArchivedSessionsApi(config: AppConfig): Promise<ArchivedSession[]> {
  const r = await apiWithConfig<{ sessions: ArchivedSession[] }>(config, "/sessions/archived");
  return r.sessions;
}

export async function restoreSessionApi(config: AppConfig, sessionId: string): Promise<void> {
  await apiWithConfig(config, `/sessions/${encodeURIComponent(sessionId)}/restore`, {
    method: "POST",
  });
}

export async function permanentlyDeleteSessionApi(config: AppConfig, sessionId: string): Promise<void> {
  await apiWithConfig(config, `/sessions/${encodeURIComponent(sessionId)}/permanent`, {
    method: "DELETE",
  });
}

export async function branchSessionApi(
  config: AppConfig,
  sessionId: string,
  payload: { name?: string; up_to_message_id?: string },
): Promise<{ session_id: string; name: string | null; started_at: string; last_activity: string; area_id?: string | null }> {
  return apiWithConfig(config, `/sessions/${encodeURIComponent(sessionId)}/branch`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
