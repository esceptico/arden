import { ApiError, type AppConfig } from "@/api/core";
import {
  archiveWikiPage,
  FACT_BATCH_LIMIT,
  listWikiPages,
  readFactBatch,
  readWikiPage,
  readWikiPageDiff,
  readWikiPageHistory,
  readWikiPageLinks,
  restoreWikiMaintenanceChange,
  updateWikiPage,
  type Fact,
  type WikiHistoryCommit,
  type WikiLink,
  type WikiPage,
  type WikiPageSummary,
} from "@/api/wiki";

type MemoryTimePrecision = "millisecond" | "second" | "minute" | "day" | "unknown";

interface MemoryScope {
  kind: "global" | "user" | "area" | "project" | "integration" | "session";
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
  rawOrigin: string;
  reason: string;
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
  heading: string | null;
  context: string;
  line: number;
  column: number;
  status: "resolved" | "unresolved";
  resolvedPath: string | null;
  sourceRevision: string;
}

interface PageLinks {
  path: string;
  revision: string;
  stale: boolean;
  outgoing: MemoryLink[];
  backlinks: MemoryLink[];
  unlinked: Array<{ sourcePath: string; context: string }>;
  totalOutgoing: number;
  totalBacklinks: number;
  limit: number;
  offset: number;
}

export type MemoryArtifactKind = "directive" | "fact" | "source" | "changelog" | "lesson" | "topic";
export type MemoryFrontmatterValue =
  | string
  | number
  | boolean
  | null
  | MemoryFrontmatterValue[]
  | { [key: string]: MemoryFrontmatterValue };
type MemoryFrontmatter = Record<string, MemoryFrontmatterValue>;

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

export interface MemoryArtifactCitation {
  factId: string;
  version: string | null;
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
  pageId?: string;
  repositoryHead?: string | null;
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
  citations: MemoryArtifactCitation[];
}

interface StoredPreview {
  pageId: string;
  path: string;
  baseRevision: string;
  expectedHead: string;
  baseContent: string;
  candidateContent: string;
  actor: string;
  patch: string;
}

const pageByPath = new Map<string, WikiPageSummary>();
const pageById = new Map<string, WikiPageSummary>();
const previews = new Map<string, StoredPreview>();

function configKey(config: AppConfig): string {
  return `${config.serverUrl}\0${config.apiKey}`;
}

function pathKey(config: AppConfig, path: string): string {
  return `${configKey(config)}\0${path}`;
}

function idKey(config: AppConfig, pageId: string): string {
  return `${configKey(config)}\0${pageId}`;
}

function rememberPage(config: AppConfig, page: WikiPageSummary): void {
  const existing = pageById.get(idKey(config, page.pageId));
  if (existing && existing.path !== page.path) {
    pageByPath.delete(pathKey(config, existing.path));
  }
  pageByPath.set(pathKey(config, page.path), page);
  pageById.set(idKey(config, page.pageId), page);
}

function forgetPage(config: AppConfig, page: WikiPageSummary): void {
  const key = idKey(config, page.pageId);
  const current = pageById.get(key);
  pageByPath.delete(pathKey(config, page.path));
  if (current) pageByPath.delete(pathKey(config, current.path));
  pageById.delete(key);
}

function replacePages(config: AppConfig, pages: WikiPageSummary[]): void {
  const prefix = `${configKey(config)}\0`;
  for (const key of pageByPath.keys()) {
    if (key.startsWith(prefix)) pageByPath.delete(key);
  }
  for (const key of pageById.keys()) {
    if (key.startsWith(prefix)) pageById.delete(key);
  }
  pages.forEach((page) => rememberPage(config, page));
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

function directory(path: string): string {
  const parts = path.split("/");
  return parts.length > 1 ? parts.slice(0, -1).join("/") : "";
}

function stringArray(value: unknown, field: string): string[] {
  if (value === undefined) return [];
  if (!Array.isArray(value) || !value.every((item): item is string => typeof item === "string")) {
    throw new Error(`Wiki page metadata.${field} must be a string array`);
  }
  return value;
}

function citations(metadata: Record<string, unknown>): MemoryArtifactCitation[] {
  const value = metadata.fact_citations;
  if (value === undefined) return [];
  if (!Array.isArray(value)) throw new Error("Wiki page metadata.fact_citations must be an array");
  return value.map((item) => {
    if (!item || typeof item !== "object") {
      throw new Error("Wiki fact citation must be an object");
    }
    const factId = (item as { fact_id?: unknown }).fact_id;
    const version = (item as { version?: unknown }).version;
    if (typeof factId !== "string" || factId.length === 0) {
      throw new Error("Wiki fact citation is missing fact_id");
    }
    if (version !== undefined && version !== null && typeof version !== "string") {
      throw new Error("Wiki fact citation version must be a string or null");
    }
    return { factId, version: version ?? null };
  });
}

function frontmatterValue(value: unknown): MemoryFrontmatterValue {
  if (value === null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return value;
  }
  if (Array.isArray(value)) {
    return value.map(frontmatterValue);
  }
  if (typeof value === "object") {
    const result: Record<string, MemoryFrontmatterValue> = {};
    for (const [key, item] of Object.entries(value)) {
      result[key] = frontmatterValue(item);
    }
    return result;
  }
  throw new Error(`Unsupported wiki metadata value: ${typeof value}`);
}

function pageFrontmatter(page: WikiPageSummary): MemoryFrontmatter {
  const raw: Record<string, unknown> = {
    ...page.metadata,
    page_id: page.pageId,
    title: page.title,
    aliases: page.aliases,
    lifecycle: page.lifecycle,
    ...(page.redirectTo == null ? {} : { redirect_to: page.redirectTo }),
  };
  const result: MemoryFrontmatter = {};
  for (const [key, value] of Object.entries(raw)) {
    result[key] = frontmatterValue(value);
  }
  return result;
}

function bodyOnly(content: string): string {
  const match = /^---\r?\n[\s\S]*?\r?\n---\r?\n?/.exec(content);
  return match ? content.slice(match[0].length) : content;
}

function artifactSummary(page: WikiPageSummary): MemoryArtifactSummary {
  const pageCitations = citations(page.metadata);
  const summary = typeof page.metadata.summary === "string" ? page.metadata.summary : null;
  return {
    path: page.path,
    title: page.title,
    kind: "topic",
    type: "file",
    directory: directory(page.path),
    scope: { kind: "global", key: null },
    snippet: page.excerpt,
    summary,
    revision: page.version,
    recordCount: pageCitations.length,
    generated: typeof page.metadata.generated_from_revision === "string",
    editable: page.resourceState === "active" && page.path !== "health.md",
    readonlyReason: page.path === "health.md" ? "Generated health report" : null,
    updatedAt: page.updatedAt,
    createdAt: page.createdAt,
    labels: stringArray(page.metadata.labels, "labels"),
    source: "wiki",
    pageId: page.pageId,
    repositoryHead: page.repositoryHead,
  };
}

function timelineEntry(fact: Fact, citedVersion: string | null): MemoryTimelineEntry {
  return {
    id: fact.factId,
    text: fact.text,
    kind: fact.kind,
    date: fact.createdAt.slice(0, 10),
    src: citedVersion ?? fact.version,
    pinned: fact.labels.includes("pinned"),
    superseded: fact.status !== "active",
  };
}

function artifactDetail(page: WikiPage): MemoryArtifactDetailResponse {
  const refs = citations(page.metadata);
  return {
    artifact: {
      ...artifactSummary(page),
      revision: page.version,
      content: bodyOnly(page.content),
      editableContent: page.path === "health.md" ? null : page.content,
      timeline: [],
      frontmatter: pageFrontmatter(page),
    },
    citations: refs,
  };
}

export async function readMemoryArtifactTimeline(
  config: AppConfig,
  refs: MemoryArtifactCitation[],
  options: { signal?: AbortSignal } = {},
): Promise<MemoryTimelineEntry[]> {
  const factIds = [...new Set(refs.map((ref) => ref.factId))];
  if (factIds.length === 0) return [];
  const chunks: string[][] = [];
  for (let index = 0; index < factIds.length; index += FACT_BATCH_LIMIT) {
    chunks.push(factIds.slice(index, index + FACT_BATCH_LIMIT));
  }
  const facts = (await Promise.all(chunks.map((ids) => readFactBatch(config, ids, options)))).flat();
  const factsById = new Map(facts.map((fact) => [fact.factId, fact]));
  return refs.map((ref) => {
    const fact = factsById.get(ref.factId);
    if (!fact) throw new Error(`Fact batch response omitted cited fact: ${ref.factId}`);
    return timelineEntry(fact, ref.version);
  });
}

async function loadPages(config: AppConfig, signal?: AbortSignal): Promise<WikiPageSummary[]> {
  const pages = await listWikiPages(config, { signal });
  replacePages(config, pages);
  return pages;
}

async function pageForPath(config: AppConfig, rawPath: string, signal?: AbortSignal): Promise<WikiPageSummary> {
  const path = canonicalArtifactPath(rawPath);
  const cached = pageByPath.get(pathKey(config, path));
  if (cached) return cached;
  const pages = await loadPages(config, signal);
  const page = pages.find((item) => item.path === path);
  if (!page) throw new ApiError({ status: 404, data: { detail: `Unknown wiki page: ${path}` } });
  return page;
}

function conflict(page: WikiPage): ApiError {
  return new ApiError({
    status: 409,
    data: {
      detail: {
        error: "page_revision_conflict",
        current_revision: page.version,
        current_content: page.content,
        current_head: page.repositoryHead,
      },
    },
  });
}

function unifiedPatch(path: string, before: string, after: string): string {
  const beforeLines = before.split("\n");
  const afterLines = after.split("\n");
  return [
    `--- ${path}`,
    `+++ ${path}`,
    `@@ -1,${beforeLines.length} +1,${afterLines.length} @@`,
    ...beforeLines.map((line) => `-${line}`),
    ...afterLines.map((line) => `+${line}`),
    "",
  ].join("\n");
}

function origin(value: string): PageEditEvent["origin"] {
  if (value.includes("synthesis")) return "synthesis";
  if (value === "desktop") return "desktop";
  if (value.includes("agent") || value.includes("tool")) return "agent";
  return "external";
}

function commitChange(commit: WikiHistoryCommit, pageId: string) {
  return commit.changes.find((change) =>
    change.before?.resourceId === pageId || change.after?.resourceId === pageId);
}

async function fullCommitPatch(
  config: AppConfig,
  pageId: string,
  commit: WikiHistoryCommit,
  signal?: AbortSignal,
): Promise<string> {
  let offset = 0;
  let patch = "";
  do {
    const page = await readWikiPageDiff(config, pageId, {
      base: commit.parentId,
      target: commit.commitId,
      offset,
      signal,
    });
    patch += page.unifiedDiff;
    offset = page.endOffset;
    if (!page.hasMore) return patch;
  } while (true);
}

export async function listMemoryArtifactSummaries(
  config: AppConfig,
  params: { kind?: MemoryArtifactKind; q?: string } = {},
  options: { signal?: AbortSignal } = {},
): Promise<MemoryArtifactSummariesResponse> {
  const query = params.q?.trim().toLocaleLowerCase() ?? "";
  const pages = await loadPages(config, options.signal);
  const artifacts = pages
    .map(artifactSummary)
    .filter((artifact) => (params.kind == null || artifact.kind === params.kind)
      && (!query || artifact.path.toLocaleLowerCase().includes(query) || artifact.title.toLocaleLowerCase().includes(query)));
  return {
    artifacts,
    directories: [...new Set(artifacts.map((artifact) => artifact.directory).filter(Boolean))],
  };
}

export async function readMemoryArtifactDetail(
  config: AppConfig,
  path: string,
  options: { signal?: AbortSignal } = {},
): Promise<MemoryArtifactDetailResponse> {
  const summary = await pageForPath(config, path, options.signal);
  const page = await readWikiPage(config, summary.pageId, { signal: options.signal });
  rememberPage(config, page);
  return artifactDetail(page);
}

export function rebuildMemoryArtifactSummaries(
  config: AppConfig,
  options: { signal?: AbortSignal } = {},
): Promise<MemoryArtifactSummariesResponse> {
  return listMemoryArtifactSummaries(config, {}, options);
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
  const summary = await pageForPath(config, input.path, options.signal);
  const page = await readWikiPage(config, summary.pageId, { signal: options.signal });
  rememberPage(config, page);
  if (page.version !== input.baseRevision) throw conflict(page);
  if (page.repositoryHead == null) throw new Error("Wiki page has no repository head");
  const id = crypto.randomUUID();
  const patch = unifiedPatch(page.path, page.content, input.content);
  previews.set(id, {
    pageId: page.pageId,
    path: page.path,
    baseRevision: page.version,
    expectedHead: page.repositoryHead,
    baseContent: page.content,
    candidateContent: input.content,
    actor: input.actor ?? "user:desktop",
    patch,
  });
  return {
    id,
    path: page.path,
    baseRevision: page.version,
    resultRevision: page.version,
    patch,
    operations: [],
    questions: [],
    analysisPending: false,
  };
}

export interface ApplyPageEditInput {
  previewId: string;
  decisions: Record<string, PageEditDecision>;
  savePending?: boolean;
}

export async function applyPageEdit(
  config: AppConfig,
  input: ApplyPageEditInput,
): Promise<{ event: PageEditEvent; revision: string }> {
  const preview = previews.get(input.previewId);
  if (!preview) throw new Error("Edit preview expired; review the draft again");
  const page = await updateWikiPage(config, {
    pageId: preview.pageId,
    content: preview.candidateContent,
    expectedVersion: preview.baseRevision,
    expectedHead: preview.expectedHead,
  });
  previews.delete(input.previewId);
  rememberPage(config, page);
  return {
    revision: page.version,
    event: {
      eventType: "PAGE_EDIT",
      id: page.repositoryHead ?? page.version,
      occurredAt: page.updatedAt ?? new Date().toISOString(),
      sequence: 0,
      actor: preview.actor,
      origin: "desktop",
      rawOrigin: "desktop",
      reason: "edit wiki page",
      path: page.path,
      baseRevision: preview.baseRevision,
      resultRevision: page.version,
      patch: preview.patch,
      operations: [],
      reconciliation: "applied",
      analysis: null,
      reconcilesEventId: null,
      reviewOperations: [],
      questions: [],
      reviewEventId: null,
      observationId: null,
      sourceCanonicalRevision: page.repositoryHead,
    },
  };
}

export async function archiveMemoryArtifact(
  config: AppConfig,
  input: { path: string; baseRevision: string },
): Promise<void> {
  const summary = await pageForPath(config, input.path);
  const page = await readWikiPage(config, summary.pageId);
  if (page.version !== input.baseRevision) throw conflict(page);
  if (page.repositoryHead == null) throw new Error("Wiki page has no repository head");
  await archiveWikiPage(config, {
    pageId: page.pageId,
    expectedVersion: page.version,
    expectedHead: page.repositoryHead,
  });
  forgetPage(config, page);
}

export interface RestorePageMaintenanceChangeInput {
  path: string;
  commitId: string;
  expectedVersion: string;
  expectedHead: string;
}

export async function restorePageMaintenanceChange(
  config: AppConfig,
  input: RestorePageMaintenanceChangeInput,
): Promise<MemoryArtifactDetailResponse> {
  const summary = await pageForPath(config, input.path);
  const page = await restoreWikiMaintenanceChange(config, {
    pageId: summary.pageId,
    commitId: input.commitId,
    expectedVersion: input.expectedVersion,
    expectedHead: input.expectedHead,
  });
  rememberPage(config, page);
  return {
    artifact: {
      ...artifactSummary(page),
      revision: page.version,
      content: bodyOnly(page.content),
      editableContent: page.path === "health.md" ? null : page.content,
      timeline: [],
      frontmatter: pageFrontmatter(page),
    },
    citations: citations(page.metadata),
  };
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
  if (params.path == null) return { events: [], total: 0, limit: params.limit ?? 100, nextBeforeSequence: null };
  const page = await pageForPath(config, params.path, options.signal);
  const limit = params.limit ?? 100;
  const commits = await readWikiPageHistory(config, page.pageId, { signal: options.signal, limit });
  const patches = await Promise.all(commits.map((commit) => fullCommitPatch(config, page.pageId, commit, options.signal)));
  const events = commits.map((commit, index): PageEditEvent => {
    const change = commitChange(commit, page.pageId);
    return {
      eventType: commit.origin.includes("synthesis") ? "SYNTHESIS_MERGE" : "PAGE_EDIT",
      id: commit.commitId,
      occurredAt: commit.timestamp,
      sequence: commits.length - index,
      actor: commit.actor,
      origin: origin(commit.origin),
      rawOrigin: commit.origin,
      reason: commit.reason,
      path: change?.after?.path ?? change?.before?.path ?? page.path,
      baseRevision: change?.before?.versionId ?? "",
      resultRevision: change?.after?.versionId ?? "",
      patch: patches[index] ?? "",
      operations: [],
      reconciliation: "applied",
      analysis: null,
      reconcilesEventId: null,
      reviewOperations: [],
      questions: [],
      reviewEventId: null,
      observationId: null,
      sourceCanonicalRevision: commit.commitId,
    };
  });
  return { events, total: events.length, limit, nextBeforeSequence: null };
}

function mappedLink(
  config: AppConfig,
  link: WikiLink,
  current: WikiPageSummary,
  direction: "outgoing" | "backlink",
): MemoryLink {
  const source = direction === "outgoing"
    ? current
    : pageById.get(idKey(config, link.sourcePageId));
  const resolved = link.targetPageId == null ? null : pageById.get(idKey(config, link.targetPageId));
  return {
    sourcePath: source?.path ?? current.path,
    target: link.target,
    display: link.alias ?? link.target,
    heading: link.heading,
    context: `[[${link.target}${link.alias ? `|${link.alias}` : ""}]]`,
    line: 0,
    column: 0,
    status: link.status,
    resolvedPath: resolved?.path ?? null,
    sourceRevision: source?.version ?? current.version,
  };
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
  const current = await pageForPath(config, params.path, options.signal);
  const report = await readWikiPageLinks(config, current.pageId, { signal: options.signal });
  const limit = params.limit ?? 100;
  const offset = params.offset ?? 0;
  const outgoing = report.outgoing.map((link) => mappedLink(config, link, current, "outgoing"));
  const backlinks = report.backlinks.map((link) => mappedLink(config, link, current, "backlink"));
  return {
    path: current.path,
    revision: current.version,
    stale: false,
    outgoing: outgoing.slice(offset, offset + limit),
    backlinks: backlinks.slice(offset, offset + limit),
    unlinked: [],
    totalOutgoing: outgoing.length,
    totalBacklinks: backlinks.length,
    limit,
    offset,
  };
}
