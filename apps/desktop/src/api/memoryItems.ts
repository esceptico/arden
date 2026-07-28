import { listFacts, type Fact } from "@/api/wiki";
import type { AppConfig } from "@/api/core";

export type ScopeKind = "global" | "area" | "project" | "session" | "integration" | "user";
export interface ScopeParams {
  scope_kind?: ScopeKind;
  scope_key?: string;
}
export interface MemoryScope {
  kind: ScopeKind;
  key: string | null;
}

export type MemoryKind = string;
export type MemoryStatus = string;
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

export function queryString(params: Record<string, string | number | boolean | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") query.set(key, String(value));
  }
  const raw = query.toString();
  return raw ? `?${raw}` : "";
}

export interface MemoryItemsResponse {
  items: MemoryItem[];
  limit: number;
}

export interface ListMemoryItemsParams extends ScopeParams {
  status?: MemoryStatus | "";
  kind?: MemoryKind;
  limit?: number;
  offset?: number;
  q?: string;
}

function scope(fact: Fact): MemoryScope {
  const kind = fact.scope.kind;
  return {
    kind: (
      kind === "global"
      || kind === "area"
      || kind === "project"
      || kind === "session"
      || kind === "integration"
      || kind === "user"
    ) ? kind : "global",
    key: fact.scope.key,
  };
}

function memoryItem(fact: Fact): MemoryItem {
  return {
    id: fact.factId,
    content: fact.text,
    kind: fact.kind,
    canonical_subject: fact.subjects.join(", "),
    labels: fact.labels,
    scope: scope(fact),
    pinned: fact.labels.includes("pinned"),
    status: fact.status,
    valid_from: fact.createdAt,
    invalid_at: fact.expiresAt,
    source_refs: fact.sources.map((source) => ({
      kind: source.kind,
      ref: source.ref,
      captured_at: source.capturedAt ?? fact.createdAt,
    })),
    corroboration: fact.sources.length,
    last_relevant_at: fact.reviewedAt ?? fact.createdAt,
    feedback: "none",
    created_at: fact.createdAt,
    updated_at: fact.reviewedAt ?? fact.createdAt,
  };
}

export async function listMemoryItems(
  config: AppConfig,
  params: ListMemoryItemsParams = {},
): Promise<MemoryItemsResponse> {
  const query = params.q?.trim().toLocaleLowerCase() ?? "";
  const facts = await listFacts(config);
  const filtered = facts.map(memoryItem).filter((item) =>
    (params.status == null || params.status === "" || item.status === params.status)
    && (params.kind == null || item.kind === params.kind)
    && (params.scope_kind == null || item.scope.kind === params.scope_kind)
    && (params.scope_key == null || item.scope.key === params.scope_key)
    && (!query
      || item.content.toLocaleLowerCase().includes(query)
      || item.canonical_subject.toLocaleLowerCase().includes(query)));
  const offset = params.offset ?? 0;
  const limit = params.limit ?? 100;
  return { items: filtered.slice(offset, offset + limit), limit };
}
