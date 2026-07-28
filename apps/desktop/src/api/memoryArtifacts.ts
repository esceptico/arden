import { apiWithConfig, type AppConfig } from "@/api/core";
import { queryString } from "@/api/memoryItems";
import type { MemoryKind } from "@/api/memoryItems";

type MemoryTimePrecision = "millisecond" | "second" | "minute" | "day" | "unknown";

interface MemoryScope {
  kind: "global" | "user" | "area" | "integration" | "session";
  key: string | null;
}

interface MemorySourceRef {
  kind: string;
  ref: string;
  capturedAt: string | null;
  scope: MemoryScope | null;
  occurredAt: string | null;
  timePrecision: MemoryTimePrecision;
  role: string | null;
  excerptHash: string | null;
  metadata: Record<string, unknown>;
}

type NotebookMemoryKind = "directive" | "fact" | "source" | "changelog" | "lesson";

interface TextOperationFields {
  id: string;
  text: string;
  metaLabels: string[];
  entityLabels: string[];
  sources: MemorySourceRef[];
}

type MemoryOperation =
  | ({ kind: "ADD"; memoryKind: NotebookMemoryKind; scope: MemoryScope } & TextOperationFields)
  | ({
      kind: "SUPERSEDE" | "MERGE";
      memoryKind: NotebookMemoryKind | null;
      scope: MemoryScope | null;
      targetIds: string[];
    } & TextOperationFields)
  | { kind: "RETRACT"; id: string; targetIds: string[]; sources: MemorySourceRef[] }
  | { kind: "NOOP"; id: string; reason: string; sources: MemorySourceRef[] }
  | { kind: "ASK"; id: string; question: string; targetIds: string[] };

interface PageEditQuestion {
  id: string;
  operationIndex: number;
  question: string;
}

interface PageEditAnalysis {
  path: string;
  before: string[];
  after: string[];
  changedBefore: string[];
  changedAfter: string[];
  patch: string;
}

interface PageEditPreview {
  id: string;
  path: string;
  baseRevision: string;
  resultRevision: string;
  patch: string;
  operations: MemoryOperation[];
  questions: PageEditQuestion[];
  analysisPending: boolean;
}

interface PageEditEvent {
  eventType: "PAGE_EDIT" | "SYNTHESIS_MERGE";
  id: string;
  occurredAt: string;
  sequence: number;
  actor: string;
  origin: "desktop" | "external" | "agent" | "synthesis";
  path: string;
  baseRevision: string;
  resultRevision: string;
  patch: string;
  operations: MemoryOperation[];
  reconciliation: "applied" | "pending" | "needs_review";
  analysis: PageEditAnalysis | null;
  reconcilesEventId: string | null;
  reviewOperations: MemoryOperation[];
  questions: PageEditQuestion[];
  reviewEventId: string | null;
  observationId: string | null;
  sourceCanonicalRevision: string | null;
}

interface PageEditDecision {
  choice: "note_only" | "forget_memory";
  targetIds: string[];
}

interface PageEditHistory {
  events: PageEditEvent[];
  total: number;
  limit: number;
  nextBeforeSequence: number | null;
}

interface MemoryLink {
  sourcePath: string;
  target: string;
  display: string;
  alias: string | null;
  heading: string | null;
  context: string;
  line: number;
  column: number;
  status: "resolved" | "ambiguous" | "unresolved";
  resolvedPath: string | null;
  candidates: string[];
  sourceRevision: string;
}

interface UnlinkedMention {
  sourcePath: string;
  context: string;
}

interface PageLinks {
  path: string;
  revision: string;
  stale: boolean;
  outgoing: MemoryLink[];
  backlinks: MemoryLink[];
  unlinked: UnlinkedMention[];
  totalOutgoing: number;
  totalBacklinks: number;
  limit: number;
  offset: number;
}

export type MemoryArtifactKind = MemoryKind | "changelog" | "lesson" | "topic";

type MemoryFrontmatter = Record<string, string | number | boolean | null | Array<string | number | boolean | null>>;

export interface ArtifactScope {
  kind: string;
  key: string | null;
}

interface MemoryTimelineEntry {
  id: string;
  text: string;
  kind: string;
  date: string;
  src: string;
  pinned: boolean;
  superseded: boolean;
}

export interface MemoryArtifactSummary {
  path: string;
  title: string;
  kind: MemoryArtifactKind;
  type: "file";
  directory: string;
  scope: ArtifactScope;
  snippet: string | null;
  summary: string | null;
  revision: string | null;
  recordCount: number | null;
  generated: boolean;
  editable: boolean;
  readonlyReason: string | null;
  updatedAt: string | null;
  createdAt: string | null;
  labels: string[];
  source: string | null;
}

export interface MemoryArtifactDetail extends Omit<MemoryArtifactSummary, "revision"> {
  revision: string;
  content: string;
  editableContent: string | null;
  timeline: MemoryTimelineEntry[];
  frontmatter: MemoryFrontmatter;
}

export interface MemoryArtifactSummariesResponse {
  artifacts: MemoryArtifactSummary[];
  directories: string[];
}

export interface MemoryArtifactDetailResponse {
  artifact: MemoryArtifactDetail;
}

interface MemoryArtifactTransport {
  path: string;
  title: string;
  kind: MemoryArtifactKind;
  type: "file";
  directory: string;
  scope: RawScope;
  content: string;
  snippet: string | null;
  record_count: number | null;
  generated: boolean;
  editable: boolean;
  readonly_reason: string | null;
  updated_at: string | null;
  created_at?: string | null;
  labels: string[];
  source: string | null;
  timeline: MemoryTimelineEntry[];
  frontmatter?: MemoryFrontmatter;
  summary?: string | null;
  revision?: string | null;
  editable_content?: string | null;
}

interface MemoryArtifactsTransport {
  artifacts: MemoryArtifactTransport[];
  directories?: string[];
}

interface RawScope {
  kind: string;
  key: string | null;
}

interface RawSourceRef extends Record<string, unknown> {
  kind: string;
  ref: string;
  captured_at?: string | null;
  scope_kind?: string | null;
  scope_key?: string | null;
  occurred_at?: string | null;
  time_precision?: string | null;
  role?: string | null;
  excerpt_hash?: string | null;
}

type RawOperationKind = "ADD" | "SUPERSEDE" | "MERGE" | "RETRACT" | "NOOP" | "ASK";

interface RawOperation {
  op: RawOperationKind;
  text?: string | null;
  kind?: string | null;
  scope?: RawScope | null;
  target_ids?: string[];
  question?: string | null;
  meta_labels?: string[] | null;
  entity_labels?: string[] | null;
  sources?: RawSourceRef[];
}

interface RawQuestion {
  id: string;
  operation_index: number;
  question: string;
}

interface RawAnalysis {
  path: string;
  before: string[];
  after: string[];
  changed_before: string[];
  changed_after: string[];
  patch: string;
}

interface RawPreview {
  id: string;
  path: string;
  base_revision: string;
  result_revision: string;
  patch: string;
  operations: RawOperation[];
  questions: RawQuestion[];
  analysis_pending: boolean;
}

interface RawEvent {
  event_type: "PAGE_EDIT" | "SYNTHESIS_MERGE";
  id: string;
  occurred_at: string;
  sequence: number;
  actor: string;
  origin: "desktop" | "external" | "agent" | "synthesis";
  path: string;
  base_revision: string;
  result_revision: string;
  patch: string;
  operations: RawOperation[];
  reconciliation: "applied" | "pending" | "needs_review";
  analysis?: RawAnalysis | null;
  reconciles_event_id?: string | null;
  review_operations?: RawOperation[];
  questions?: RawQuestion[];
  review_event_id?: string | null;
  observation_id?: string | null;
  source_canonical_revision?: string | null;
}

interface RawLink {
  source_path: string;
  target: string;
  display: string;
  alias?: string | null;
  heading: string | null;
  context: string;
  line: number;
  column: number;
  status: "resolved" | "ambiguous" | "unresolved";
  resolved_path: string | null;
  candidates: string[];
  source_revision: string;
}

const SOURCE_FIELDS = new Set([
  "kind",
  "ref",
  "captured_at",
  "scope_kind",
  "scope_key",
  "occurred_at",
  "time_precision",
  "role",
  "excerpt_hash",
]);

function mapArtifactSummary(raw: MemoryArtifactTransport): MemoryArtifactSummary {
  return {
    path: raw.path,
    title: raw.title,
    kind: raw.kind,
    type: raw.type,
    directory: raw.directory,
    scope: { kind: raw.scope.kind, key: raw.scope.key },
    snippet: raw.snippet,
    summary: raw.summary ?? null,
    revision: raw.revision ?? null,
    recordCount: raw.record_count,
    generated: raw.generated,
    editable: raw.editable,
    readonlyReason: raw.readonly_reason,
    updatedAt: raw.updated_at,
    createdAt: raw.created_at ?? null,
    labels: raw.labels,
    source: raw.source,
  };
}

function mapArtifactDetail(raw: MemoryArtifactTransport): MemoryArtifactDetail {
  if (raw.revision == null) throw new Error("Memory artifact detail is missing its revision");
  const summary = mapArtifactSummary(raw);
  return {
    path: summary.path,
    title: summary.title,
    kind: summary.kind,
    type: summary.type,
    directory: summary.directory,
    scope: summary.scope,
    snippet: summary.snippet,
    summary: summary.summary,
    revision: raw.revision,
    recordCount: summary.recordCount,
    generated: summary.generated,
    editable: summary.editable,
    readonlyReason: summary.readonlyReason,
    updatedAt: summary.updatedAt,
    createdAt: summary.createdAt,
    labels: summary.labels,
    source: summary.source,
    content: raw.content,
    editableContent: raw.editable_content ?? null,
    timeline: raw.timeline,
    frontmatter: raw.frontmatter ?? {},
  };
}

function mapScope(raw: RawScope | null | undefined): MemoryScope | null {
  if (!raw) return null;
  if (!["global", "user", "area", "integration", "session"].includes(raw.kind)) {
    throw new Error(`Unsupported memory scope: ${raw.kind}`);
  }
  return raw as MemoryScope;
}

function mapMemoryKind(raw: string | null | undefined): NotebookMemoryKind | null {
  if (raw == null) return null;
  if (!["directive", "fact", "source", "changelog", "lesson"].includes(raw)) {
    throw new Error(`Unsupported memory kind: ${raw}`);
  }
  return raw as NotebookMemoryKind;
}

function mapSource(raw: RawSourceRef): MemorySourceRef {
  const metadata = Object.fromEntries(Object.entries(raw).filter(([key]) => !SOURCE_FIELDS.has(key)));
  const scope = raw.scope_kind == null
    ? null
    : mapScope({ kind: raw.scope_kind, key: raw.scope_key ?? null });
  const precision = raw.time_precision ?? "unknown";
  if (!["millisecond", "second", "minute", "day", "unknown"].includes(precision)) {
    throw new Error(`Unsupported source time precision: ${precision}`);
  }
  return {
    kind: raw.kind,
    ref: raw.ref,
    capturedAt: raw.captured_at ?? null,
    scope,
    occurredAt: raw.occurred_at ?? null,
    timePrecision: precision as MemoryTimePrecision,
    role: raw.role ?? null,
    excerptHash: raw.excerpt_hash ?? null,
    metadata,
  };
}

function requiredText(raw: RawOperation): string {
  if (raw.text == null) throw new Error(`${raw.op} operation is missing text`);
  return raw.text;
}

function mapOperation(raw: RawOperation, id: string): MemoryOperation {
  const sources = (raw.sources ?? []).map(mapSource);
  const textFields = () => ({
    id,
    text: requiredText(raw),
    metaLabels: raw.meta_labels ?? [],
    entityLabels: raw.entity_labels ?? [],
    sources,
  });
  switch (raw.op) {
    case "ADD": {
      const memoryKind = mapMemoryKind(raw.kind);
      const scope = mapScope(raw.scope);
      if (memoryKind == null || scope == null) throw new Error("ADD operation is missing kind or scope");
      return { kind: "ADD", ...textFields(), memoryKind, scope };
    }
    case "SUPERSEDE":
      return {
        kind: "SUPERSEDE",
        ...textFields(),
        memoryKind: mapMemoryKind(raw.kind),
        scope: mapScope(raw.scope),
        targetIds: raw.target_ids ?? [],
      };
    case "MERGE":
      return {
        kind: "MERGE",
        ...textFields(),
        memoryKind: mapMemoryKind(raw.kind),
        scope: mapScope(raw.scope),
        targetIds: raw.target_ids ?? [],
      };
    case "RETRACT":
      return { kind: "RETRACT", id, targetIds: raw.target_ids ?? [], sources };
    case "NOOP":
      return { kind: "NOOP", id, reason: raw.text ?? "No memory changes", sources };
    case "ASK":
      return { kind: "ASK", id, question: raw.question ?? "", targetIds: raw.target_ids ?? [] };
  }
}

function mapQuestion(raw: RawQuestion): PageEditQuestion {
  return { id: raw.id, operationIndex: raw.operation_index, question: raw.question };
}

function mapAnalysis(raw: RawAnalysis): PageEditAnalysis {
  return {
    path: raw.path,
    before: raw.before,
    after: raw.after,
    changedBefore: raw.changed_before,
    changedAfter: raw.changed_after,
    patch: raw.patch,
  };
}

function mapPreview(raw: RawPreview): PageEditPreview {
  return {
    id: raw.id,
    path: raw.path,
    baseRevision: raw.base_revision,
    resultRevision: raw.result_revision,
    patch: raw.patch,
    operations: raw.operations.map((operation, index) => mapOperation(operation, `${raw.id}:operation:${index}`)),
    questions: raw.questions.map(mapQuestion),
    analysisPending: raw.analysis_pending,
  };
}

function mapEvent(raw: RawEvent): PageEditEvent {
  return {
    eventType: raw.event_type,
    id: raw.id,
    occurredAt: raw.occurred_at,
    sequence: raw.sequence,
    actor: raw.actor,
    origin: raw.origin,
    path: raw.path,
    baseRevision: raw.base_revision,
    resultRevision: raw.result_revision,
    patch: raw.patch,
    operations: raw.operations.map((operation, index) => mapOperation(operation, `${raw.id}:operation:${index}`)),
    reconciliation: raw.reconciliation,
    analysis: raw.analysis ? mapAnalysis(raw.analysis) : null,
    reconcilesEventId: raw.reconciles_event_id ?? null,
    reviewOperations: (raw.review_operations ?? []).map((operation, index) =>
      mapOperation(operation, `${raw.id}:review-operation:${index}`)),
    questions: (raw.questions ?? []).map(mapQuestion),
    reviewEventId: raw.review_event_id ?? null,
    observationId: raw.observation_id ?? null,
    sourceCanonicalRevision: raw.source_canonical_revision ?? null,
  };
}

function mapLink(raw: RawLink): MemoryLink {
  return {
    sourcePath: raw.source_path,
    target: raw.target,
    display: raw.display,
    alias: raw.alias ?? null,
    heading: raw.heading,
    context: raw.context,
    line: raw.line,
    column: raw.column,
    status: raw.status,
    resolvedPath: raw.resolved_path,
    candidates: raw.candidates,
    sourceRevision: raw.source_revision,
  };
}

export function listMemoryArtifactSummaries(
  config: AppConfig,
  params: { kind?: MemoryArtifactKind; q?: string } = {},
  options: { signal?: AbortSignal } = {},
): Promise<MemoryArtifactSummariesResponse> {
  return apiWithConfig<MemoryArtifactsTransport>(
    config,
    `/admin/memory/artifacts${queryString({ kind: params.kind, q: params.q })}`,
    { signal: options.signal },
  ).then((response) => ({
    artifacts: response.artifacts.map(mapArtifactSummary),
    directories: response.directories ?? [],
  }));
}

function canonicalArtifactPath(path: string): string {
  const parts = path.split("/");
  if (
    path.length === 0
    || path.startsWith("/")
    || path.includes("\\")
    || /^[A-Za-z]:\//.test(path)
    || parts.some((part) => part === "" || part === "." || part === "..")
  ) {
    throw new Error("Invalid memory artifact path");
  }
  return path;
}

function encodeArtifactPath(path: string): string {
  return canonicalArtifactPath(path).split("/").map(encodeURIComponent).join("/");
}

export async function readMemoryArtifactDetail(
  config: AppConfig,
  path: string,
  options: { signal?: AbortSignal } = {},
): Promise<MemoryArtifactDetailResponse> {
  return apiWithConfig<{ artifact: MemoryArtifactTransport }>(
    config,
    `/admin/memory/artifacts/${encodeArtifactPath(path)}`,
    { signal: options.signal },
  ).then((response) => ({ artifact: mapArtifactDetail(response.artifact) }));
}

export function rebuildMemoryArtifactSummaries(
  config: AppConfig,
  options: { signal?: AbortSignal } = {},
): Promise<MemoryArtifactSummariesResponse> {
  return apiWithConfig<MemoryArtifactsTransport>(config, "/admin/memory/artifacts/rebuild", {
    method: "POST",
    signal: options.signal,
  })
    .then((response) => ({
      artifacts: response.artifacts.map(mapArtifactSummary),
      directories: response.directories ?? [],
    }));
}

export interface PreviewPageEditInput {
  path: string;
  baseRevision: string;
  content: string;
  actor?: string;
}

export async function previewPageEdit(
  config: AppConfig,
  input: PreviewPageEditInput,
  options: { signal?: AbortSignal } = {},
): Promise<PageEditPreview> {
  const path = canonicalArtifactPath(input.path);
  return apiWithConfig<{ preview: RawPreview }>(config, "/admin/memory/page-edits/preview", {
    method: "POST",
    signal: options.signal,
    body: JSON.stringify({
      path,
      base_revision: input.baseRevision,
      content: input.content,
      actor: input.actor ?? "user:desktop",
    }),
  }).then((response) => mapPreview(response.preview));
}

export interface ApplyPageEditInput {
  previewId: string;
  decisions: Record<string, PageEditDecision>;
  savePending?: boolean;
}

function serializePageEditDecisions(decisions: Record<string, PageEditDecision>) {
  return Object.fromEntries(
    Object.entries(decisions).map(([id, decision]) => [
      id,
      { choice: decision.choice, target_ids: decision.targetIds },
    ]),
  );
}

export function applyPageEdit(
  config: AppConfig,
  input: ApplyPageEditInput,
): Promise<{ event: PageEditEvent; revision: string }> {
  return apiWithConfig<{ event: RawEvent; revision: string }>(config, "/admin/memory/page-edits/apply", {
    method: "PUT",
    body: JSON.stringify({
      preview_id: input.previewId,
      decisions: serializePageEditDecisions(input.decisions),
      save_pending: input.savePending ?? false,
    }),
  }).then((response) => ({ event: mapEvent(response.event), revision: response.revision }));
}

export interface PageHistoryParams {
  path?: string;
  limit?: number;
  beforeSequence?: number;
}

export async function getPageHistory(
  config: AppConfig,
  params: PageHistoryParams = {},
  options: { signal?: AbortSignal } = {},
): Promise<PageEditHistory> {
  const path = params.path === undefined ? undefined : canonicalArtifactPath(params.path);
  return apiWithConfig<{
    events: RawEvent[];
    total: number;
    limit: number;
    next_before_sequence: number | null;
  }>(config, `/admin/memory/page-edits/history${queryString({
    path,
    limit: params.limit,
    before_sequence: params.beforeSequence,
  })}`, { signal: options.signal }).then((response) => ({
    events: response.events.map(mapEvent),
    total: response.total,
    limit: response.limit,
    nextBeforeSequence: response.next_before_sequence,
  }));
}

export interface PageLinksParams {
  path: string;
  limit?: number;
  offset?: number;
}

export async function getPageLinks(
  config: AppConfig,
  params: PageLinksParams,
  options: { signal?: AbortSignal } = {},
): Promise<PageLinks> {
  const path = canonicalArtifactPath(params.path);
  return apiWithConfig<{
    path: string;
    revision: string;
    stale: boolean;
    outgoing: RawLink[];
    backlinks: RawLink[];
    unlinked?: { source_path: string; context: string }[];
    total_outgoing: number;
    total_backlinks: number;
    limit: number;
    offset: number;
  }>(config, `/admin/memory/links${queryString({
    path,
    limit: params.limit,
    offset: params.offset,
  })}`, { signal: options.signal }).then((response) => ({
    path: response.path,
    revision: response.revision,
    stale: response.stale,
    outgoing: response.outgoing.map(mapLink),
    backlinks: response.backlinks.map(mapLink),
    unlinked: (response.unlinked ?? []).map((mention) => ({
      sourcePath: mention.source_path,
      context: mention.context,
    })),
    totalOutgoing: response.total_outgoing,
    totalBacklinks: response.total_backlinks,
    limit: response.limit,
    offset: response.offset,
  }));
}

export async function createMemoryNode(
  config: AppConfig,
  params: { path: string; kind: "note" | "folder" },
): Promise<{ artifact: MemoryArtifactSummary | null; path: string }> {
  const response = await apiWithConfig<{ artifact?: MemoryArtifactTransport; path?: string }>(
    config,
    "/admin/memory/notebook/create",
    { method: "POST", body: JSON.stringify({ path: canonicalArtifactPath(params.path), kind: params.kind }) },
  );
  return {
    artifact: response.artifact ? mapArtifactSummary(response.artifact) : null,
    path: response.artifact?.path ?? response.path ?? params.path,
  };
}
