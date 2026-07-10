import { apiWithConfig, type AppConfig } from "@/api/core";

// ── Scope ─────────────────────────────────────────────────────────────────
export type ScopeKind = "global" | "area" | "session" | "integration" | "user";
export interface ScopeParams {
  scope_kind?: ScopeKind;
  scope_key?: string;
}
export interface MemoryScope {
  kind: ScopeKind;
  key: string | null;
}

// ── Shared value objects ────────────────────────────────────────────────────
export type MemoryKind = "directive" | "fact" | "source";
export type MemoryStatus = "active" | "superseded" | "archived" | "unresolved" | "retired";
export type MemoryFeedback = "none" | "confirmed" | "corrected";

export interface MemorySourceRef {
  kind: string;
  ref: string;
  captured_at: string;
}

export interface MemoryItem {
  id: string;
  content: string;
  kind: MemoryKind;
  canonical_subject: string;
  labels: string[];
  scope: MemoryScope;
  pinned: boolean;
  status: MemoryStatus;
  valid_from: string | null;
  invalid_at: string | null;
  source_refs: MemorySourceRef[];
  corroboration: number;
  last_relevant_at: string | null;
  feedback: MemoryFeedback;
  created_at: string;
  updated_at: string;
}

// ── Query encoding ──────────────────────────────────────────────────────────
export function queryString(params: Record<string, string | number | boolean | undefined>): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") qs.set(key, String(value));
  }
  const raw = qs.toString();
  return raw ? `?${raw}` : "";
}

// ── 1 — List records ──────────────────────────────────────────────────────
export interface MemoryItemsResponse {
  items: MemoryItem[];
  limit: number;
}
// A scope summary row (distinct from `MemoryScope` = a record's {kind,key}).
export interface MemoryScopeSummary {
  scope_kind: ScopeKind;
  scope_key: string | null;
  count: number;
}

export function listScopes(config: AppConfig) {
  return apiWithConfig<{ scopes: MemoryScopeSummary[] }>(config, `/admin/memory/scopes`);
}

export interface ListMemoryItemsParams extends ScopeParams {
  subject?: string;
  status?: MemoryStatus | ""; // "" => all statuses
  valid_at?: string;
  kind?: MemoryKind;
  limit?: number;
  offset?: number;
  q?: string;
}

export function listMemoryItems(config: AppConfig, params: ListMemoryItemsParams = {}) {
  return apiWithConfig<MemoryItemsResponse>(
    config,
    `/admin/memory/items${queryString({
      scope_kind: params.scope_kind,
      scope_key: params.scope_key,
      subject: params.subject,
      status: params.status,
      valid_at: params.valid_at,
      kind: params.kind,
      limit: params.limit,
      offset: params.offset,
      q: params.q,
    })}`,
  );
}

// ── 2 — Get one item ────────────────────────────────────────────────────────
export interface MemoryItemDetail {
  item: MemoryItem;
}

export function getMemoryItem(config: AppConfig, itemId: string) {
  return apiWithConfig<MemoryItemDetail>(config, `/admin/memory/items/${encodeURIComponent(itemId)}`);
}

// ── 2b — Pin / unpin a record ─────────────────────────────────────────────────
export function setRecordPinned(config: AppConfig, recordId: string, pinned: boolean) {
  return apiWithConfig<{ ok: boolean; pinned: boolean }>(
    config,
    `/admin/memory/record/${encodeURIComponent(recordId)}/pin`,
    { method: "POST", body: JSON.stringify({ pinned }) },
  );
}

// ── 3 — Search ──────────────────────────────────────────────────────────────
export interface MemorySearchFts {
  mode: "fts";
  items: MemoryItem[];
  degraded: boolean;
}
export interface RankedItem {
  item: MemoryItem;
  order_score: number;
  rrf: number;
  freshness: number;
  corroboration: number;
}
export interface MemorySearchRetrieve {
  mode: "retrieve";
  rendered: string;
  items: RankedItem[];
  degraded: boolean;
  diagnostics: Record<string, unknown>;
}
export type MemorySearchResponse = MemorySearchFts | MemorySearchRetrieve;
export interface MemorySearchParams extends ScopeParams {
  q: string;
  limit?: number;
  include_inactive?: boolean;
  mode?: "fts" | "retrieve";
  kind?: MemoryKind;
}

export function searchMemory(config: AppConfig, params: MemorySearchParams) {
  // Routed through apiWithConfig so it uses the Electron bridge (main-process
  // fetch, no renderer CSP/CORS) like every other memory call — a raw renderer
  // fetch breaks search in packaged builds / non-localhost servers.
  return apiWithConfig<MemorySearchResponse>(
    config,
    `/admin/memory/search${queryString({
      q: params.q,
      scope_kind: params.scope_kind,
      scope_key: params.scope_key,
      limit: params.limit,
      include_inactive: params.include_inactive,
      mode: params.mode,
      kind: params.kind,
    })}`,
    { timeout: 8_000 } as RequestInit & { timeout: number },
  );
}
