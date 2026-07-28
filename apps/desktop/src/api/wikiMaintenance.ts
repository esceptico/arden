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

export type WikiMaintenanceProposal =
  | WikiMaintenanceUpdateProposal
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

export interface WikiMaintenanceEvidenceCursor {
  changeIndex: number;
  diffOffset: number;
}

interface RawWikiMaintenanceReview {
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

interface RawWikiMaintenanceEvidence {
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

function mapReview(raw: RawWikiMaintenanceReview): WikiMaintenanceReview {
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
  return apiWithConfig<{ reviews: RawWikiMaintenanceReview[] }>(
    config,
    "/admin/wiki/maintenance-reviews",
    { signal: options.signal },
  ).then((response) => response.reviews.map(mapReview));
}

export function resolveWikiMaintenanceReview(
  config: AppConfig,
  review: Pick<WikiMaintenanceReview, "reviewId" | "generation">,
  decision:
    | { action: "accept" | "reject" }
    | { action: "resolve-manually"; note: string },
): Promise<WikiMaintenanceReview> {
  const path = `/admin/wiki/maintenance-reviews/${encodeURIComponent(review.reviewId)}/${decision.action}`;
  const body =
    decision.action === "resolve-manually"
      ? { generation: review.generation, note: decision.note }
      : { generation: review.generation };
  return apiWithConfig<RawWikiMaintenanceReview>(config, path, {
    method: "POST",
    body: JSON.stringify(body),
  }).then(mapReview);
}

export function readWikiMaintenanceReviewEvidence(
  config: AppConfig,
  review: Pick<WikiMaintenanceReview, "reviewId" | "generation">,
  options: { changeIndex?: number; diffOffset?: number; signal?: AbortSignal } = {},
): Promise<WikiMaintenanceEvidence> {
  const query = new URLSearchParams({
    generation: String(review.generation),
    change_index: String(options.changeIndex ?? 0),
    diff_offset: String(options.diffOffset ?? 0),
  });
  const path = `/admin/wiki/maintenance-reviews/${encodeURIComponent(review.reviewId)}/evidence?${query}`;
  return apiWithConfig<RawWikiMaintenanceEvidence>(config, path, { signal: options.signal }).then((response) => ({
    reviewId: response.review_id,
    generation: response.generation,
    actor: response.actor,
    origin: response.origin,
    reason: response.reason,
    occurredAt: response.occurred_at,
    changeIndex: response.changeIndex,
    changeCount: response.changeCount,
    diffOffset: response.diffOffset,
    diffEndOffset: response.diffEndOffset,
    moreInChange: response.moreInChange,
    previousCursor: response.previousCursor,
    nextCursor: response.nextCursor,
    change: response.change,
  }));
}
