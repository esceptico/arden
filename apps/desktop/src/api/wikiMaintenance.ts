import { apiWithConfig, type AppConfig } from "@/api/core";

export type WikiMaintenanceReviewStatus =
  | "needs_review"
  | "accepted"
  | "rejected"
  | "resolved_manual"
  | "cleared";

export interface WikiMaintenanceUpdateProposal {
  kind: "maintenance_updates";
  summary: string;
  updates: Array<{
    pageId: string;
    title: string;
    aliases: string[];
    body: string;
  }>;
}

export interface WikiMaintenanceEvidenceProposal {
  kind: "manual_evidence_review";
  section: string;
  actualBytes: number;
  actualBytesAtLeast: boolean;
  limitBytes: number;
}

export interface WikiMaintenancePageMergeProposal {
  kind: "page_merge";
  summary: string;
  canonicalPageId: string;
  canonicalTitle: string;
  loserPageId: string;
  loserTitle: string;
  linkCount: number;
  pageCount: number;
  redirectCount: 0;
}

export type WikiMaintenanceProposal =
  | WikiMaintenanceUpdateProposal
  | WikiMaintenancePageMergeProposal
  | WikiMaintenanceEvidenceProposal;

export interface WikiMaintenanceReview {
  reviewId: string;
  blockingCommitId: string;
  generation: number;
  status: WikiMaintenanceReviewStatus;
  summary: string;
  proposal: WikiMaintenanceProposal | null;
  createdAt: string;
  updatedAt: string;
  resolvedAt: string | null;
  decisionNote: string | null;
}

export interface WikiMaintenanceEvidenceCursor {
  changeIndex: number;
  diffOffset: number;
}

export interface WikiMaintenanceEvidence {
  reviewId: string;
  generation: number;
  actor: string;
  origin: string;
  reason: string;
  occurredAt: string;
  changeIndex: number;
  changeCount: number;
  diffOffset: number;
  diffEndOffset: number;
  moreInChange: boolean;
  previousCursor: WikiMaintenanceEvidenceCursor | null;
  nextCursor: WikiMaintenanceEvidenceCursor | null;
  change: {
    resourceId: string;
    path: string;
    action: string;
    unifiedDiff: string;
    displayLossy: boolean;
  };
}

interface RawReview {
  review_id: string;
  blocking_commit_id: string;
  generation: number;
  status: WikiMaintenanceReviewStatus;
  summary: string;
  proposal: WikiMaintenanceProposal | null;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
  decision_note: string | null;
}

interface RawEvidence {
  review_id: string;
  generation: number;
  actor: string;
  origin: string;
  reason: string;
  occurred_at: string;
  changeIndex: number;
  changeCount: number;
  diffOffset: number;
  diffEndOffset: number;
  moreInChange: boolean;
  previousCursor: WikiMaintenanceEvidenceCursor | null;
  nextCursor: WikiMaintenanceEvidenceCursor | null;
  change: WikiMaintenanceEvidence["change"];
}

function review(raw: RawReview): WikiMaintenanceReview {
  return {
    reviewId: raw.review_id,
    blockingCommitId: raw.blocking_commit_id,
    generation: raw.generation,
    status: raw.status,
    summary: raw.summary,
    proposal: raw.proposal,
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
    resolvedAt: raw.resolved_at,
    decisionNote: raw.decision_note,
  };
}

export function listWikiMaintenanceReviews(
  config: AppConfig,
  options: { signal?: AbortSignal } = {},
): Promise<WikiMaintenanceReview[]> {
  return apiWithConfig<{ reviews: RawReview[] }>(
    config,
    "/admin/wiki/maintenance-reviews",
    { signal: options.signal },
  ).then((response) => response.reviews.map(review));
}

export function resolveWikiMaintenanceReview(
  config: AppConfig,
  item: Pick<WikiMaintenanceReview, "reviewId" | "generation">,
  decision:
    | { action: "accept" | "reject" }
    | { action: "resolve-manually"; note: string },
): Promise<WikiMaintenanceReview> {
  const path = `/admin/wiki/maintenance-reviews/${encodeURIComponent(item.reviewId)}/${decision.action}`;
  const body = decision.action === "resolve-manually"
    ? { generation: item.generation, note: decision.note }
    : { generation: item.generation };
  return apiWithConfig<RawReview>(config, path, {
    method: "POST",
    body: JSON.stringify(body),
  }).then(review);
}

export function readWikiMaintenanceReviewEvidence(
  config: AppConfig,
  item: Pick<WikiMaintenanceReview, "reviewId" | "generation">,
  options: {
    changeIndex?: number;
    diffOffset?: number;
    signal?: AbortSignal;
  } = {},
): Promise<WikiMaintenanceEvidence> {
  const query = new URLSearchParams({
    generation: String(item.generation),
    change_index: String(options.changeIndex ?? 0),
    diff_offset: String(options.diffOffset ?? 0),
  });
  return apiWithConfig<RawEvidence>(
    config,
    `/admin/wiki/maintenance-reviews/${encodeURIComponent(item.reviewId)}/evidence?${query}`,
    { signal: options.signal },
  ).then((raw) => ({
    reviewId: raw.review_id,
    generation: raw.generation,
    actor: raw.actor,
    origin: raw.origin,
    reason: raw.reason,
    occurredAt: raw.occurred_at,
    changeIndex: raw.changeIndex,
    changeCount: raw.changeCount,
    diffOffset: raw.diffOffset,
    diffEndOffset: raw.diffEndOffset,
    moreInChange: raw.moreInChange,
    previousCursor: raw.previousCursor,
    nextCursor: raw.nextCursor,
    change: raw.change,
  }));
}
