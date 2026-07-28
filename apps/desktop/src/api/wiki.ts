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
  lifecycle: string;
  status: string;
  certainty: string;
  evidenceClass: string;
  createdAt: string;
  reviewAt: string | null;
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
  actor: string;
  origin: string;
  reason: string;
  timestamp: string;
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

export function readWikiPageHistory(config: AppConfig, pageId: string, options: { signal?: AbortSignal } = {}): Promise<WikiHistoryCommit[]> {
  return apiWithConfig<{ commits: Array<{ commit_id: string; actor: string; origin: string; reason: string; timestamp: string }> }>(
    config,
    `/admin/wiki/pages/${encodeURIComponent(pageId)}/history`,
    { signal: options.signal },
  ).then((response) => response.commits.map((commit) => ({
    commitId: commit.commit_id,
    actor: commit.actor,
    origin: commit.origin,
    reason: commit.reason,
    timestamp: commit.timestamp,
  })));
}

interface RawFactPage {
  facts: Array<{
    fact_id: string; text: string; kind: string; labels: string[]; subjects: string[];
    lifecycle: string; status: string; certainty: string; evidence_class: string;
    created_at: string; review_at: string | null; version: string;
  }>;
  has_more: boolean;
  next_after: { created_at: string; fact_id: string } | null;
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
    facts.push(...response.facts.map((fact) => ({
      factId: fact.fact_id,
      text: fact.text,
      kind: fact.kind,
      labels: fact.labels,
      subjects: fact.subjects,
      lifecycle: fact.lifecycle,
      status: fact.status,
      certainty: fact.certainty,
      evidenceClass: fact.evidence_class,
      createdAt: fact.created_at,
      reviewAt: fact.review_at,
      version: fact.version,
    })));
    cursor = response.has_more ? response.next_after : null;
    if (response.has_more && cursor == null) throw new Error("Facts pagination response omitted its cursor");
  } while (cursor);
  return facts;
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
