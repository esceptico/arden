import { apiWithConfig, type AppConfig } from "@/api/core";

export interface WikiPageSummary {
  pageId: string;
  path: string;
  resourceState: "active" | "archived";
  title: string;
  aliases: string[];
  lifecycle: string;
  redirectTo: string | null;
  metadata: Record<string, unknown>;
  version: string;
  repositoryHead: string | null;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface WikiPage extends WikiPageSummary {
  content: string;
}

export interface Fact {
  factId: string;
  text: string;
  kind: string;
  labels: string[];
  subjects: string[];
  scope: { kind: string; key: string | null };
  lifecycle: string;
  status: string;
  certainty: string;
  evidenceClass: string;
  sources: Array<{
    kind: string;
    ref: string;
    capturedAt: string | null;
  }>;
  createdAt: string;
  reviewedAt: string | null;
  reviewAt: string | null;
  expiresAt: string | null;
  version: string;
}

export interface WikiLink {
  sourcePageId: string;
  target: string;
  alias: string | null;
  heading: string | null;
  status: "resolved" | "ambiguous" | "unresolved";
  targetPageId: string | null;
  candidates: string[];
}

export interface WikiPageLinks {
  pageId: string;
  repositoryHead: string | null;
  outgoing: WikiLink[];
  backlinks: WikiLink[];
}

export interface WikiHistoryCommit {
  commitId: string;
  parentId: string | null;
  actor: string;
  origin: string;
  reason: string;
  timestamp: string;
  changes: WikiHistoryChange[];
}

export interface WikiHistoryChange {
  action: string;
  before: WikiResourceVersion | null;
  after: WikiResourceVersion | null;
}

export interface WikiResourceVersion {
  resourceId: string;
  path: string;
  state: "active" | "archived";
  versionId: string;
}

export interface WikiPageDiff {
  offset: number;
  endOffset: number;
  hasMore: boolean;
  unifiedDiff: string;
}

interface RawWikiPage {
  page_id: string;
  path: string;
  resource_state: "active" | "archived";
  title: string;
  aliases: string[];
  lifecycle: string;
  redirect_to: string | null;
  metadata: Record<string, unknown>;
  version: string;
  repository_head: string | null;
  created_at: string | null;
  updated_at: string | null;
  content?: string;
}

function mapWikiPage(raw: RawWikiPage): WikiPage {
  if (typeof raw.content !== "string") throw new Error("Wiki page detail is missing content");
  return {
    pageId: raw.page_id,
    path: raw.path,
    resourceState: raw.resource_state,
    title: raw.title,
    aliases: raw.aliases,
    lifecycle: raw.lifecycle,
    redirectTo: raw.redirect_to,
    metadata: raw.metadata,
    version: raw.version,
    repositoryHead: raw.repository_head,
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
    content: raw.content,
  };
}

function mapWikiPageSummary(raw: RawWikiPage): WikiPageSummary {
  const { content: _content, ...summary } = mapWikiPage({ ...raw, content: raw.content ?? "" });
  return summary;
}

export function listWikiPages(config: AppConfig, options: { signal?: AbortSignal } = {}): Promise<WikiPageSummary[]> {
  return apiWithConfig<{ pages: RawWikiPage[] }>(config, "/admin/wiki/pages", { signal: options.signal })
    .then((response) => response.pages.map(mapWikiPageSummary));
}

export function readWikiPage(config: AppConfig, pageId: string, options: { signal?: AbortSignal } = {}): Promise<WikiPage> {
  return apiWithConfig<RawWikiPage>(config, `/admin/wiki/pages/${encodeURIComponent(pageId)}`, { signal: options.signal }).then(mapWikiPage);
}

function mapLink(raw: {
  source_page_id: string;
  node: { target?: string; alias?: string | null; heading?: string | null };
  status: "resolved" | "ambiguous" | "unresolved";
  target_page_id?: string | null;
  candidates?: string[];
}): WikiLink {
  return {
    sourcePageId: raw.source_page_id,
    target: raw.node.target ?? "",
    alias: raw.node.alias ?? null,
    heading: raw.node.heading ?? null,
    status: raw.status,
    targetPageId: raw.target_page_id ?? null,
    candidates: raw.candidates ?? [],
  };
}

export function readWikiPageLinks(config: AppConfig, pageId: string, options: { signal?: AbortSignal } = {}): Promise<WikiPageLinks> {
  return apiWithConfig<{
    page_id: string;
    repository_head: string | null;
    outgoing: Parameters<typeof mapLink>[0][];
    backlinks: Parameters<typeof mapLink>[0][];
  }>(config, `/admin/wiki/pages/${encodeURIComponent(pageId)}/links`, { signal: options.signal }).then((response) => ({
    pageId: response.page_id,
    repositoryHead: response.repository_head,
    outgoing: response.outgoing.map(mapLink),
    backlinks: response.backlinks.map(mapLink),
  }));
}

function mapResourceVersion(raw: {
  resource_id: string;
  path: string;
  state: "active" | "archived";
  version_id: string;
} | null): WikiResourceVersion | null {
  return raw == null ? null : {
    resourceId: raw.resource_id,
    path: raw.path,
    state: raw.state,
    versionId: raw.version_id,
  };
}

export function readWikiPageHistory(config: AppConfig, pageId: string, options: { signal?: AbortSignal; limit?: number } = {}): Promise<WikiHistoryCommit[]> {
  const query = new URLSearchParams({ limit: String(options.limit ?? 50) });
  return apiWithConfig<{ commits: Array<{
    commit_id: string;
    parent_id: string | null;
    actor: string;
    origin: string;
    reason: string;
    timestamp: string;
    changes: Array<{
      action: string;
      before: Parameters<typeof mapResourceVersion>[0];
      after: Parameters<typeof mapResourceVersion>[0];
    }>;
  }> }>(
    config,
    `/admin/wiki/pages/${encodeURIComponent(pageId)}/history?${query}`,
    { signal: options.signal },
  ).then((response) => response.commits.map((commit) => ({
    commitId: commit.commit_id,
    parentId: commit.parent_id,
    actor: commit.actor,
    origin: commit.origin,
    reason: commit.reason,
    timestamp: commit.timestamp,
    changes: commit.changes.map((change) => ({
      action: change.action,
      before: mapResourceVersion(change.before),
      after: mapResourceVersion(change.after),
    })),
  })));
}

export function readWikiPageDiff(
  config: AppConfig,
  pageId: string,
  input: {
    base: string | null;
    target: string;
    offset?: number;
    limit?: number;
    signal?: AbortSignal;
  },
): Promise<WikiPageDiff> {
  const query = new URLSearchParams({
    target: input.target,
    offset: String(input.offset ?? 0),
    limit: String(input.limit ?? 65_536),
  });
  if (input.base != null) query.set("base", input.base);
  return apiWithConfig<{
    diff: {
      offset: number;
      end_offset: number;
      has_more: boolean;
      unified_diff: string;
    };
  }>(config, `/admin/wiki/pages/${encodeURIComponent(pageId)}/diff?${query}`, {
    signal: input.signal,
  }).then(({ diff }) => ({
    offset: diff.offset,
    endOffset: diff.end_offset,
    hasMore: diff.has_more,
    unifiedDiff: diff.unified_diff,
  }));
}

interface RawFact {
  fact_id: string; text: string; kind: string; labels: string[]; subjects: string[];
  scope: { kind: string; key: string | null };
  lifecycle: string; status: string; certainty: string; evidence_class: string;
  sources: Array<{ kind: string; ref: string; captured_at?: string | null }>;
  created_at: string; reviewed_at: string | null; review_at: string | null;
  expires_at: string | null; version: string;
}

interface RawFactPage {
  facts: RawFact[];
  has_more: boolean;
  next_after: { created_at: string; fact_id: string } | null;
}

function mapFact(fact: RawFact): Fact {
  return {
    factId: fact.fact_id,
    text: fact.text,
    kind: fact.kind,
    labels: fact.labels,
    subjects: fact.subjects,
    scope: fact.scope,
    lifecycle: fact.lifecycle,
    status: fact.status,
    certainty: fact.certainty,
    evidenceClass: fact.evidence_class,
    sources: fact.sources.map((source) => ({
      kind: source.kind,
      ref: source.ref,
      capturedAt: source.captured_at ?? null,
    })),
    createdAt: fact.created_at,
    reviewedAt: fact.reviewed_at,
    reviewAt: fact.review_at,
    expiresAt: fact.expires_at,
    version: fact.version,
  };
}

export async function listFacts(config: AppConfig, options: { signal?: AbortSignal } = {}): Promise<Fact[]> {
  const facts: Fact[] = [];
  let cursor: RawFactPage["next_after"] = null;
  do {
    const query = new URLSearchParams({ limit: "100" });
    if (cursor) {
      query.set("after_created_at", cursor.created_at);
      query.set("after_fact_id", cursor.fact_id);
    }
    const response: RawFactPage = await apiWithConfig<RawFactPage>(config, `/admin/facts?${query}`, {
      signal: options.signal,
    });
    facts.push(...response.facts.map(mapFact));
    cursor = response.has_more ? response.next_after : null;
    if (response.has_more && cursor == null) throw new Error("Facts pagination response omitted its cursor");
  } while (cursor);
  return facts;
}

export function readFact(config: AppConfig, factId: string, options: { signal?: AbortSignal } = {}): Promise<Fact> {
  return apiWithConfig<{ fact: RawFact }>(config, `/admin/facts/${encodeURIComponent(factId)}`, {
    signal: options.signal,
  }).then(({ fact }) => mapFact(fact));
}

export type WikiRenameApprovalStatus = "pending" | "applying" | "accepted" | "rejected" | "superseded";

export interface WikiRenameApproval {
  approvalId: string;
  oldPath: string;
  newPath: string;
  oldTitle: string;
  newTitle: string;
  linkCount: number;
  pageCount: number;
  generation: number;
  status: WikiRenameApprovalStatus;
  createdAt: string;
  resolvedAt: string | null;
  commitId: string | null;
  resolution: string | null;
  replacementApprovalId: string | null;
}

export interface WikiRenameApprovalResult {
  status: WikiRenameApprovalStatus;
  approval: WikiRenameApproval | null;
  commitId: string | null;
  replacementApprovalId: string | null;
}

export interface UpdateWikiPageInput {
  pageId: string;
  content: string;
  expectedVersion: string;
  expectedHead: string;
}

export interface ArchiveWikiPageInput {
  pageId: string;
  expectedVersion: string;
  expectedHead: string;
}

interface RawWikiRenameApproval {
  approval_id: string;
  old_path: string;
  new_path: string;
  old_title: string;
  new_title: string;
  link_count: number;
  page_count: number;
  generation: number;
  status: WikiRenameApprovalStatus;
  created_at: string;
  resolved_at: string | null;
  commit_id: string | null;
  resolution: string | null;
  replacement_approval_id: string | null;
}

interface RawWikiRenameApprovalResult {
  status: WikiRenameApprovalStatus;
  approval: RawWikiRenameApproval | null;
  commit_id: string | null;
  replacement_approval_id: string | null;
}

function mapApproval(raw: RawWikiRenameApproval): WikiRenameApproval {
  return {
    approvalId: raw.approval_id,
    oldPath: raw.old_path,
    newPath: raw.new_path,
    oldTitle: raw.old_title,
    newTitle: raw.new_title,
    linkCount: raw.link_count,
    pageCount: raw.page_count,
    generation: raw.generation,
    status: raw.status,
    createdAt: raw.created_at,
    resolvedAt: raw.resolved_at,
    commitId: raw.commit_id,
    resolution: raw.resolution,
    replacementApprovalId: raw.replacement_approval_id,
  };
}

function mapResult(raw: RawWikiRenameApprovalResult): WikiRenameApprovalResult {
  return {
    status: raw.status,
    approval: raw.approval ? mapApproval(raw.approval) : null,
    commitId: raw.commit_id,
    replacementApprovalId: raw.replacement_approval_id,
  };
}

export function listWikiRenameApprovals(
  config: AppConfig,
  options: { signal?: AbortSignal } = {},
): Promise<WikiRenameApproval[]> {
  return apiWithConfig<{ approvals: RawWikiRenameApproval[] }>(config, "/admin/wiki/rename-approvals", {
    signal: options.signal,
  }).then((response) => response.approvals.map(mapApproval));
}

export function requestWikiRenameApproval(
  config: AppConfig,
  input: { pageId: string; newPath: string; newTitle: string },
): Promise<WikiRenameApprovalResult> {
  return apiWithConfig<RawWikiRenameApprovalResult>(config, "/admin/wiki/rename-approvals", {
    method: "POST",
    body: JSON.stringify({
      page_id: input.pageId,
      new_path: input.newPath,
      new_title: input.newTitle,
    }),
  }).then(mapResult);
}

export function acceptWikiRenameApproval(config: AppConfig, approvalId: string): Promise<WikiRenameApprovalResult> {
  return apiWithConfig<RawWikiRenameApprovalResult>(
    config,
    `/admin/wiki/rename-approvals/${encodeURIComponent(approvalId)}/accept`,
    { method: "POST" },
  ).then(mapResult);
}

export function rejectWikiRenameApproval(
  config: AppConfig,
  approvalId: string,
  resolution?: string,
): Promise<WikiRenameApprovalResult> {
  return apiWithConfig<RawWikiRenameApprovalResult>(
    config,
    `/admin/wiki/rename-approvals/${encodeURIComponent(approvalId)}/reject`,
    { method: "POST", body: JSON.stringify(resolution == null ? {} : { resolution }) },
  ).then(mapResult);
}

/** Writes a canonical wiki page against the revision the editor opened. */
export function updateWikiPage(config: AppConfig, input: UpdateWikiPageInput): Promise<WikiPage> {
  return apiWithConfig<RawWikiPage>(config, `/admin/wiki/pages/${encodeURIComponent(input.pageId)}`, {
    method: "PUT",
    body: JSON.stringify({
      content: input.content,
      expected_version: input.expectedVersion,
      expected_head: input.expectedHead,
    }),
  }).then(mapWikiPage);
}

/** Archives a canonical wiki page. Archive remains recoverable in page history. */
export function archiveWikiPage(config: AppConfig, input: ArchiveWikiPageInput): Promise<void> {
  return apiWithConfig<void>(config, `/admin/wiki/pages/${encodeURIComponent(input.pageId)}/archive`, {
    method: "POST",
    body: JSON.stringify({ expected_version: input.expectedVersion, expected_head: input.expectedHead }),
  }).then(() => undefined);
}
