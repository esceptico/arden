import { apiWithConfig, type AppConfig } from "@/api/core";

const ROW_LIMIT = 50;
const TEXT_LIMIT = 4096;

export interface InspectorContextItem {
  id: string;
  contentType: string;
  source: string;
  ref: string;
  freshness: string;
  selectionReason: string;
  sizeBytes: number;
}

export interface InspectorSource {
  provider: string;
  kind: string;
  ref: string;
  title: string;
  url?: string;
}

export interface InspectorApproval {
  toolCallId: string;
  toolName: string;
  status: string;
  feedback?: string;
}

export interface InspectorEffect {
  toolCallId: string;
  operation: string;
  target: string;
  beforeRef?: string;
  afterRef?: string;
}

export interface InspectorReceipt {
  toolCallId: string;
  receipt: string;
}

export interface InspectorCheck {
  toolCallId: string;
  postcondition: string;
  observed: string;
  confidence?: number;
}

export interface InspectorLimitation {
  toolCallId: string;
  status: string;
  code: string;
  recoveryAction?: string;
}

export interface TurnInspector {
  runId: string;
  sessionId: string;
  updatedAt: string;
  context: InspectorContextItem[];
  evidence: {
    sources: InspectorSource[];
    approvals: InspectorApproval[];
    effects: InspectorEffect[];
    receipts: InspectorReceipt[];
    checks: InspectorCheck[];
    limitations: InspectorLimitation[];
  };
}

export async function getTurnInspector(
  config: AppConfig,
  sessionId: string,
  turnId: string,
  signal?: AbortSignal,
): Promise<TurnInspector | null> {
  const raw = await apiWithConfig<unknown>(
    config,
    `/sessions/${encodeURIComponent(sessionId)}/turns/${encodeURIComponent(turnId)}/inspector`,
    { signal },
  );
  return normalizeTurnInspector(raw);
}

export function normalizeTurnInspector(raw: unknown): TurnInspector | null {
  if (!isRecord(raw)) return null;
  const runId = requiredString(raw.run_id);
  const sessionId = requiredString(raw.session_id);
  const updatedAt = requiredString(raw.updated_at);
  if (!runId || !sessionId || !updatedAt) return null;
  const evidence = isRecord(raw.evidence) ? raw.evidence : {};
  return {
    runId,
    sessionId,
    updatedAt,
    context: normalizeRows(raw.context_manifest, normalizeContext),
    evidence: {
      sources: normalizeRows(evidence.sources, normalizeSource),
      approvals: normalizeRows(evidence.approvals, normalizeApproval),
      effects: normalizeRows(evidence.effects, normalizeEffect),
      receipts: normalizeRows(evidence.receipts, normalizeReceipt),
      checks: normalizeRows(evidence.checks, normalizeCheck),
      limitations: normalizeRows(evidence.limitations, normalizeLimitation),
    },
  };
}

function normalizeContext(raw: unknown): InspectorContextItem | null {
  if (!isRecord(raw)) return null;
  const id = requiredString(raw.context_id);
  const contentType = requiredString(raw.content_type);
  const source = requiredString(raw.source);
  const ref = requiredString(raw.ref);
  const freshness = requiredString(raw.freshness);
  const selectionReason = requiredString(raw.selection_reason);
  const sizeBytes = finiteNumber(raw.size_bytes);
  if (!id || !contentType || !source || !ref || !freshness || !selectionReason || sizeBytes === null || sizeBytes < 0) {
    return null;
  }
  return { id, contentType, source, ref, freshness, selectionReason, sizeBytes };
}

function normalizeSource(raw: unknown): InspectorSource | null {
  if (!isRecord(raw)) return null;
  const provider = requiredString(raw.provider);
  const kind = requiredString(raw.kind);
  const ref = requiredString(raw.ref);
  const title = requiredString(raw.title);
  if (!provider || !kind || !ref || !title) return null;
  const url = optionalString(raw.url);
  return { provider, kind, ref, title, ...(url ? { url } : {}) };
}

function normalizeApproval(raw: unknown): InspectorApproval | null {
  if (!isRecord(raw)) return null;
  const toolCallId = requiredString(raw.tool_call_id);
  const toolName = requiredString(raw.tool_name);
  const status = requiredString(raw.status);
  if (!toolCallId || !toolName || !status) return null;
  const feedback = optionalString(raw.feedback);
  return { toolCallId, toolName, status, ...(feedback ? { feedback } : {}) };
}

function normalizeEffect(raw: unknown): InspectorEffect | null {
  if (!isRecord(raw)) return null;
  const toolCallId = requiredString(raw.tool_call_id);
  const operation = requiredString(raw.operation);
  const target = requiredString(raw.target);
  if (!toolCallId || !operation || !target) return null;
  const beforeRef = optionalString(raw.before_ref);
  const afterRef = optionalString(raw.after_ref);
  return {
    toolCallId,
    operation,
    target,
    ...(beforeRef ? { beforeRef } : {}),
    ...(afterRef ? { afterRef } : {}),
  };
}

function normalizeReceipt(raw: unknown): InspectorReceipt | null {
  if (!isRecord(raw)) return null;
  const toolCallId = requiredString(raw.tool_call_id);
  const receipt = requiredString(raw.receipt);
  return toolCallId && receipt ? { toolCallId, receipt } : null;
}

function normalizeCheck(raw: unknown): InspectorCheck | null {
  if (!isRecord(raw)) return null;
  const toolCallId = requiredString(raw.tool_call_id);
  const postcondition = requiredString(raw.postcondition);
  const observed = requiredString(raw.observed);
  if (!toolCallId || !postcondition || !observed) return null;
  const confidence = finiteNumber(raw.confidence);
  return { toolCallId, postcondition, observed, ...(confidence === null ? {} : { confidence }) };
}

function normalizeLimitation(raw: unknown): InspectorLimitation | null {
  if (!isRecord(raw)) return null;
  const toolCallId = requiredString(raw.tool_call_id);
  const status = requiredString(raw.status);
  const code = requiredString(raw.code);
  if (!toolCallId || !status || !code) return null;
  const recoveryAction = optionalString(raw.recovery_action);
  return { toolCallId, status, code, ...(recoveryAction ? { recoveryAction } : {}) };
}

function normalizeRows<T>(raw: unknown, normalize: (value: unknown) => T | null): T[] {
  if (!Array.isArray(raw)) return [];
  const rows: T[] = [];
  for (const value of raw) {
    const row = normalize(value);
    if (row) rows.push(row);
    if (rows.length === ROW_LIMIT) break;
  }
  return rows;
}

function requiredString(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const value = raw.trim();
  return value && value.length <= TEXT_LIMIT ? value : null;
}

function optionalString(raw: unknown): string | undefined {
  return requiredString(raw) ?? undefined;
}

function finiteNumber(raw: unknown): number | null {
  return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
}

function isRecord(raw: unknown): raw is Record<string, unknown> {
  return Boolean(raw && typeof raw === "object" && !Array.isArray(raw));
}
