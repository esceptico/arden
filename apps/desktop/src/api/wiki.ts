import { apiWithConfig, type AppConfig } from "@/api/core";

export type WikiRenamePolicy = "always" | "ask" | "never";
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
  input: { pageId: string; newPath: string; newTitle: string; policy?: WikiRenamePolicy },
): Promise<WikiRenameApprovalResult> {
  return apiWithConfig<RawWikiRenameApprovalResult>(config, "/admin/wiki/rename-approvals", {
    method: "POST",
    body: JSON.stringify({
      page_id: input.pageId,
      new_path: input.newPath,
      new_title: input.newTitle,
      ...(input.policy ? { policy: input.policy } : {}),
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
