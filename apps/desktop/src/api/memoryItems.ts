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
export interface ListMemoryItemsParams extends ScopeParams {
  status?: MemoryStatus | ""; // "" => all statuses
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
      status: params.status,
      kind: params.kind,
      limit: params.limit,
      offset: params.offset,
      q: params.q,
    })}`,
  );
}
