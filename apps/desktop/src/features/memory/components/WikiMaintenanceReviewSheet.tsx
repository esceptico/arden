import { useEffect, useRef, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import type { AppConfig } from "@/api/core";
import {
  readWikiMaintenanceReviewEvidence,
  type WikiMaintenanceEvidence,
  type WikiMaintenanceReview,
} from "@/api/wikiMaintenance";
import { Button } from "@/components/ui/Button";
import type { WikiMaintenanceDecision } from "@/features/memory/hooks/useWikiMaintenanceReviews";
import { useFocusTrap } from "@/lib/hooks";
import { useOverlayLayer } from "@/lib/overlayStack";
import {
  EASE_OUT,
  MOTION,
  POSE_SHEET_IN,
  POSE_SHEET_VISIBLE,
  SHEET_CLEANUP_ENTER_TRANSITION,
} from "@/lib/tokens/motion";

type Props = {
  config: AppConfig;
  review: WikiMaintenanceReview;
  position: number;
  total: number;
  pending: boolean;
  checking: boolean;
  reconciliationRequired: boolean;
  error: string | null;
  manual: boolean;
  note: string;
  onManualChange: (manual: boolean) => void;
  onNoteChange: (note: string) => void;
  onReconcile: () => void;
  onResolve: (review: WikiMaintenanceReview, decision: WikiMaintenanceDecision) => void;
};

function formatBytes(value: number): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(value);
}

export function WikiMaintenanceReviewStatusSheet({
  error,
  onRetry,
}: {
  error: string | null;
  onRetry: () => void;
}) {
  const overlayRef = useRef<HTMLElement>(null);
  const retryRef = useRef<HTMLButtonElement>(null);
  const reduce = useReducedMotion() ?? false;
  const failed = error != null;

  useOverlayLayer(overlayRef, true, () => {}, true);
  useFocusTrap(overlayRef, true);

  useEffect(() => {
    (failed ? retryRef.current : overlayRef.current)?.focus({ preventScroll: true });
  }, [failed]);

  const motionProps = {
    initial: reduce ? false : POSE_SHEET_IN,
    animate: POSE_SHEET_VISIBLE,
    transition: reduce ? { duration: MOTION.reduced } : SHEET_CLEANUP_ENTER_TRANSITION,
  };
  const scrimMotionProps = {
    initial: reduce ? false : { opacity: 0 },
    animate: { opacity: 1 },
    transition: { duration: reduce ? MOTION.reduced : MOTION.focus, ease: EASE_OUT },
  };

  return (
    <section
      ref={overlayRef}
      data-wiki-maintenance-status
      data-reduced-motion-ready="true"
      role="dialog"
      aria-modal="true"
      aria-labelledby="wiki-maintenance-status-title"
      aria-busy={!failed}
      tabIndex={-1}
      className="memory-edit-review wiki-maintenance-review min-w-0"
    >
      <motion.div className="memory-edit-review__scrim" aria-hidden="true" {...scrimMotionProps} />
      <motion.div className="memory-edit-review__sheet flex min-h-0 min-w-0 flex-col" {...motionProps}>
        <header className="memory-edit-review__header">
          <div className="min-w-0">
            <p className="memory-edit-review__crumb">Maintenance review</p>
            <h1 id="wiki-maintenance-status-title" className="wiki-maintenance-review__title">
              {failed ? "Couldn’t check maintenance questions" : "Checking maintenance questions"}
            </h1>
          </div>
        </header>
        <div className="memory-edit-review__body wiki-maintenance-review__status">
          {failed ? (
            <p role="alert">
              Arden can’t verify whether Wiki Maintenance is waiting for an answer. Memory stays paused until
              the review list can be checked.
            </p>
          ) : (
            <p role="status">Checking the durable review list before opening Memory.</p>
          )}
        </div>
        {failed && (
          <>
            <p className="memory-edit-review__error">{error}</p>
            <footer className="wiki-maintenance-review__footer">
              <Button ref={retryRef} variant="primary" onClick={onRetry}>
                Check again
              </Button>
            </footer>
          </>
        )}
      </motion.div>
    </section>
  );
}

export function WikiMaintenanceReviewSheet({
  config,
  review,
  position,
  total,
  pending,
  checking,
  reconciliationRequired,
  error,
  manual,
  note,
  onManualChange,
  onNoteChange,
  onReconcile,
  onResolve,
}: Props) {
  const overlayRef = useRef<HTMLElement>(null);
  const acceptRef = useRef<HTMLButtonElement>(null);
  const manualRef = useRef<HTMLButtonElement>(null);
  const reconcileRef = useRef<HTMLButtonElement>(null);
  const noteRef = useRef<HTMLTextAreaElement>(null);
  const evidencePageRef = useRef<HTMLDivElement>(null);
  const evidenceDiffRef = useRef<HTMLPreElement>(null);
  const evidenceRetryRef = useRef<HTMLButtonElement>(null);
  const evidenceFocusPending = useRef(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [evidencePosition, setEvidencePosition] = useState<{
    changeIndex: number;
    diffOffset: number;
  } | null>(null);
  const [evidenceAttempt, setEvidenceAttempt] = useState(0);
  const [evidence, setEvidence] = useState<WikiMaintenanceEvidence | null>(null);
  const [evidenceError, setEvidenceError] = useState<string | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const reduce = useReducedMotion() ?? false;
  const proposal = review.proposal;
  const hasUpdates = proposal?.kind === "maintenance_updates" && proposal.updates.length > 0;
  const canAccept = hasUpdates || proposal?.kind === "manual_evidence_review";
  const decisionsDisabled = pending || checking;

  useOverlayLayer(overlayRef, true, () => {}, true);
  useFocusTrap(overlayRef, true);

  useEffect(() => {
    if (decisionsDisabled) {
      overlayRef.current?.focus({ preventScroll: true });
      return;
    }
    if (reconciliationRequired) {
      reconcileRef.current?.focus({ preventScroll: true });
      return;
    }
    if (manual) {
      noteRef.current?.focus();
      noteRef.current?.scrollIntoView({ block: "nearest" });
      return;
    }
    (canAccept ? acceptRef.current : manualRef.current)?.focus({ preventScroll: true });
  }, [
    canAccept,
    decisionsDisabled,
    manual,
    reconciliationRequired,
    review.generation,
    review.reviewId,
  ]);

  useEffect(() => {
    if (evidencePosition == null) return;
    const controller = new AbortController();
    setEvidenceLoading(true);
    readWikiMaintenanceReviewEvidence(config, review, {
      changeIndex: evidencePosition.changeIndex,
      diffOffset: evidencePosition.diffOffset,
      signal: controller.signal,
    })
      .then((result) => {
        if (controller.signal.aborted) return;
        setEvidenceError(null);
        setEvidence(result);
      })
      .catch((reason) => {
        if (controller.signal.aborted) return;
        setEvidenceError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setEvidenceLoading(false);
      });
    return () => controller.abort();
  }, [
    config,
    evidenceAttempt,
    evidencePosition?.changeIndex,
    evidencePosition?.diffOffset,
    review.generation,
    review.reviewId,
  ]);

  useEffect(() => {
    if (!evidence || !evidenceFocusPending.current) return;
    const frame = window.requestAnimationFrame(() => {
      evidenceDiffRef.current?.scrollTo({ top: 0, left: 0 });
      evidencePageRef.current?.scrollIntoView({ block: "nearest" });
      evidencePageRef.current?.focus({ preventScroll: true });
      evidenceFocusPending.current = false;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [evidence]);

  useEffect(() => {
    if (!evidenceError || !evidenceFocusPending.current) return;
    evidenceRetryRef.current?.focus({ preventScroll: true });
  }, [evidenceError]);

  const requestEvidence = (position: { changeIndex: number; diffOffset: number }) => {
    evidenceFocusPending.current = true;
    setEvidencePosition(position);
  };

  const motionProps = {
    initial: reduce ? false : POSE_SHEET_IN,
    animate: POSE_SHEET_VISIBLE,
    transition: reduce ? { duration: MOTION.reduced } : SHEET_CLEANUP_ENTER_TRANSITION,
  };
  const scrimMotionProps = {
    initial: reduce ? false : { opacity: 0 },
    animate: { opacity: 1 },
    transition: { duration: reduce ? MOTION.reduced : MOTION.focus, ease: EASE_OUT },
  };

  return (
    <section
      ref={overlayRef}
      data-wiki-maintenance-review
      data-reduced-motion-ready="true"
      data-long-content-ready="true"
      role="dialog"
      aria-modal="true"
      aria-labelledby="wiki-maintenance-review-title"
      aria-busy={pending || checking || evidenceLoading}
      tabIndex={-1}
      className="memory-edit-review wiki-maintenance-review min-w-0"
    >
      <motion.div className="memory-edit-review__scrim" aria-hidden="true" {...scrimMotionProps} />
      <motion.div className="memory-edit-review__sheet flex min-h-0 min-w-0 flex-col" {...motionProps}>
        <p role="status" aria-live="polite" className="sr-only">
          Wiki Maintenance review {position} of {total}, generation {review.generation}
        </p>
        <header className="memory-edit-review__header">
          <div className="min-w-0">
            <p className="memory-edit-review__crumb">
              Maintenance review · {position} of {total} · generation {review.generation}
            </p>
            <h1 id="wiki-maintenance-review-title" className="wiki-maintenance-review__title">
              Wiki Maintenance needs a decision
            </h1>
          </div>
        </header>

        <div className="memory-edit-review__body wiki-maintenance-review__body scroll-fade min-h-0 min-w-0 flex-1">
          <section className="wiki-maintenance-review__summary" aria-labelledby="wiki-maintenance-summary-title">
            <h2 id="wiki-maintenance-summary-title">Question</h2>
            <p>{review.summary}</p>
          </section>

          {proposal?.kind === "maintenance_updates" && (
            <section className="wiki-maintenance-review__proposal" aria-labelledby="wiki-maintenance-proposal-title">
              <h2 id="wiki-maintenance-proposal-title">Proposed change</h2>
              <p>{proposal.summary}</p>
              <ol className="wiki-maintenance-review__updates">
                {proposal.updates.map((update) => (
                  <li key={update.pageId}>
                    <div className="wiki-maintenance-review__page-heading">
                      <h3>{update.title}</h3>
                      <code>{update.pageId}</code>
                    </div>
                    <p className="wiki-maintenance-review__aliases">
                      Aliases: {update.aliases.length > 0 ? update.aliases.join(", ") : "none"}
                    </p>
                    <pre>{update.body}</pre>
                  </li>
                ))}
              </ol>
            </section>
          )}

          {proposal?.kind === "manual_evidence_review" && (
            <section className="wiki-maintenance-review__proposal" aria-labelledby="wiki-maintenance-evidence-title">
              <h2 id="wiki-maintenance-evidence-title">Evidence needs manual review</h2>
              <p>
                The <strong>{proposal.section}</strong> evidence is{" "}
                {proposal.actualBytesAtLeast ? "at least " : ""}
                {formatBytes(proposal.actualBytes)} bytes;
                the automated review limit is {formatBytes(proposal.limitBytes)} bytes.
              </p>
              {evidence == null && evidenceError == null && (
                <Button
                  variant="secondary"
                  aria-disabled={evidenceLoading || undefined}
                  onClick={() => {
                    if (!evidenceLoading) requestEvidence({ changeIndex: 0, diffOffset: 0 });
                  }}
                >
                  {evidenceLoading ? "Loading reviewed changes…" : "Show reviewed changes"}
                </Button>
              )}
              {evidenceLoading && <p role="status">Loading the exact changed pages…</p>}
              {evidenceError && (
                <div className="wiki-maintenance-review__evidence-error">
                  <p role="alert">{evidenceError}</p>
                  <Button
                    ref={evidenceRetryRef}
                    variant="secondary"
                    aria-disabled={evidenceLoading || undefined}
                    onClick={() => {
                      if (evidenceLoading) return;
                      evidenceFocusPending.current = true;
                      setEvidenceAttempt((attempt) => attempt + 1);
                    }}
                  >
                    {evidenceLoading ? "Retrying evidence…" : "Retry evidence"}
                  </Button>
                </div>
              )}
              {evidence && (
                <div className="wiki-maintenance-review__evidence">
                  <p>
                    {evidence.actor} · {evidence.origin} ·{" "}
                    <time dateTime={evidence.occurredAt}>
                      {new Date(evidence.occurredAt).toLocaleString()}
                    </time>
                  </p>
                  <p>{evidence.reason}</p>
                  <div
                    ref={evidencePageRef}
                    className="wiki-maintenance-review__evidence-page"
                    role="region"
                    tabIndex={-1}
                    aria-labelledby="wiki-maintenance-evidence-page-title"
                    aria-busy={evidenceLoading}
                  >
                    <p className="wiki-maintenance-review__evidence-position" role="status" aria-live="polite">
                      Change {evidence.changeIndex + 1} of {evidence.changeCount}
                      {evidence.diffEndOffset > evidence.diffOffset && (
                        <>
                          {" "}
                          · characters {evidence.diffOffset + 1}–{evidence.diffEndOffset}
                          {!evidence.moreInChange && " · end of change"}
                        </>
                      )}
                    </p>
                    <ol className="wiki-maintenance-review__updates">
                      <li>
                        <div className="wiki-maintenance-review__page-heading">
                          <h3 id="wiki-maintenance-evidence-page-title">{evidence.change.path}</h3>
                          <code>{evidence.change.action}</code>
                        </div>
                        <pre ref={evidenceDiffRef}>
                          {evidence.change.unifiedDiff || "No textual diff; the page path or lifecycle changed."}
                        </pre>
                        {evidence.change.displayLossy && (
                          <p className="wiki-maintenance-review__evidence-warning">
                            Invalid UTF-8 bytes are shown as replacement characters. The stored revision is unchanged.
                          </p>
                        )}
                      </li>
                    </ol>
                  </div>
                  <div className="wiki-maintenance-review__evidence-navigation">
                    <Button
                      variant="quiet"
                      disabled={evidence.previousCursor == null}
                      aria-disabled={evidenceLoading || undefined}
                      onClick={() => {
                        if (!evidenceLoading && evidence.previousCursor) requestEvidence(evidence.previousCursor);
                      }}
                    >
                      Previous
                    </Button>
                    <Button
                      variant="quiet"
                      disabled={evidence.nextCursor == null}
                      aria-disabled={evidenceLoading || undefined}
                      onClick={() => {
                        if (!evidenceLoading && evidence.nextCursor) requestEvidence(evidence.nextCursor);
                      }}
                    >
                      Next
                    </Button>
                  </div>
                </div>
              )}
            </section>
          )}

          {(proposal == null || (proposal.kind === "maintenance_updates" && !hasUpdates)) && (
            <p className="wiki-maintenance-review__empty">
              No executable proposal is available. Resolve this manually or reject it.
            </p>
          )}

          <p className="wiki-maintenance-review__explanation">
            {hasUpdates
              ? "Accepting records your decision. Wiki Maintenance applies the proposed change on its next scheduled pass."
              : proposal?.kind === "manual_evidence_review"
                ? "Accept records approval; Reject records disapproval. Either leaves the wiki unchanged. Resolve manually adds your note."
              : "Resolve manually to record your judgment, or reject to leave the wiki unchanged."}
          </p>

          {manual && (
            <label className="wiki-maintenance-review__field">
              <span>What did you decide?</span>
              <textarea
                ref={noteRef}
                className="arden-field"
                value={note}
                disabled={decisionsDisabled}
                required
                rows={5}
                aria-invalid={validationError ? true : undefined}
                aria-describedby={validationError ? "wiki-maintenance-note-error" : undefined}
                onChange={(event) => {
                  onNoteChange(event.currentTarget.value);
                  setValidationError(null);
                }}
              />
              <small>Saved with this review so the decision remains auditable.</small>
            </label>
          )}
        </div>

        {(validationError ?? error) && (
          <p id="wiki-maintenance-note-error" role="alert" className="memory-edit-review__error">
            {validationError ?? error}
          </p>
        )}

        <footer className="wiki-maintenance-review__footer">
          {reconciliationRequired ? (
            <Button
              ref={reconcileRef}
              variant="primary"
              disabled={checking}
              onClick={onReconcile}
            >
              {checking ? "Checking…" : "Check status"}
            </Button>
          ) : manual ? (
            <>
              <Button
                variant="quiet"
                disabled={decisionsDisabled}
                onClick={() => {
                  if (decisionsDisabled) return;
                  onManualChange(false);
                  onNoteChange("");
                  setValidationError(null);
                }}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                disabled={decisionsDisabled}
                onClick={() => {
                  if (decisionsDisabled) return;
                  const decisionNote = note.trim();
                  if (!decisionNote) {
                    setValidationError("Describe the manual decision before resolving this review.");
                    noteRef.current?.focus();
                    return;
                  }
                  onResolve(review, { action: "resolve-manually", note: decisionNote });
                }}
              >
                {pending ? "Saving…" : "Resolve manually"}
              </Button>
            </>
          ) : (
            <>
              <Button
                variant="quiet"
                disabled={decisionsDisabled}
                onClick={() => {
                  if (!decisionsDisabled) onResolve(review, { action: "reject" });
                }}
              >
                Reject
              </Button>
              <Button
                ref={manualRef}
                variant={canAccept ? "secondary" : "primary"}
                disabled={decisionsDisabled}
                onClick={() => {
                  if (!decisionsDisabled) onManualChange(true);
                }}
              >
                Resolve manually
              </Button>
              {canAccept && (
                <Button
                  ref={acceptRef}
                  variant="primary"
                  disabled={decisionsDisabled}
                  onClick={() => {
                    if (!decisionsDisabled) onResolve(review, { action: "accept" });
                  }}
                >
                  {pending ? "Saving…" : hasUpdates ? "Accept change" : "Accept as-is"}
                </Button>
              )}
            </>
          )}
        </footer>
      </motion.div>
    </section>
  );
}
