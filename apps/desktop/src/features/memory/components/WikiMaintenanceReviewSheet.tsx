import { useCallback, useEffect, useRef, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Textarea";
import {
  readWikiMaintenanceReviewEvidence,
  type WikiMaintenanceEvidence,
  type WikiMaintenanceEvidenceCursor,
  type WikiMaintenanceReview,
} from "@/api/wikiMaintenance";
import type { AppConfig } from "@/api/core";
import { useFocusTrap } from "@/lib/hooks";
import { useOverlayLayer } from "@/lib/overlayStack";
import {
  EASE_OUT,
  MOTION,
  POSE_SHEET_IN,
  POSE_SHEET_VISIBLE,
  SHEET_CLEANUP_ENTER_TRANSITION,
} from "@/lib/tokens/motion";
import type { WikiMaintenanceDecision } from "../hooks/useWikiMaintenanceReviews";

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

type EvidenceState =
  | { state: "idle" | "loading"; data: WikiMaintenanceEvidence | null; error: null }
  | { state: "ready"; data: WikiMaintenanceEvidence; error: null }
  | { state: "error"; data: null; error: string; cursor: WikiMaintenanceEvidenceCursor };

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

/**
 * The durable answer surface for Wiki Maintenance. It deliberately has no
 * close path: a review remains visible until the server records an answer.
 */
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
  const primaryActionRef = useRef<HTMLButtonElement>(null);
  const manualActionRef = useRef<HTMLButtonElement>(null);
  const retryRef = useRef<HTMLButtonElement>(null);
  const noteRef = useRef<HTMLTextAreaElement>(null);
  const evidenceRequestId = useRef(0);
  const reduce = useReducedMotion() ?? false;
  const [noteError, setNoteError] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<EvidenceState>({
    state: "idle",
    data: null,
    error: null,
  });

  const evidenceReview = review.proposal?.kind === "manual_evidence_review" ? review : null;
  const hasUpdates = review.proposal?.kind === "maintenance_updates" && review.proposal.updates.length > 0;
  const canAccept = hasUpdates || evidenceReview !== null;
  const controlsDisabled = pending || checking || reconciliationRequired;

  // An unresolved question must not be dismissed with Escape or a backdrop.
  useOverlayLayer(overlayRef, true, () => {}, true);
  useFocusTrap(overlayRef, true);

  const loadEvidence = useCallback(async (cursor?: WikiMaintenanceEvidenceCursor) => {
    if (!evidenceReview) return;
    const requestId = ++evidenceRequestId.current;
    setEvidence({ state: "loading", data: null, error: null });
    try {
      const data = await readWikiMaintenanceReviewEvidence(config, evidenceReview, cursor);
      if (requestId !== evidenceRequestId.current) return;
      setEvidence({ state: "ready", data, error: null });
    } catch (reason) {
      if (requestId !== evidenceRequestId.current) return;
      setEvidence({ state: "error", data: null, error: errorMessage(reason), cursor: cursor ?? { changeIndex: 0, diffOffset: 0 } });
    }
  }, [config, evidenceReview]);

  useEffect(() => {
    setNoteError(null);
    if (!evidenceReview) {
      setEvidence({ state: "idle", data: null, error: null });
      return;
    }
    void loadEvidence();
    return () => { evidenceRequestId.current += 1; };
  }, [evidenceReview?.reviewId, evidenceReview?.generation, loadEvidence]);

  useEffect(() => {
    if (pending || checking) {
      overlayRef.current?.focus({ preventScroll: true });
      return;
    }
    const target = reconciliationRequired
      ? retryRef.current
      : manual
        ? noteRef.current
        : canAccept
          ? primaryActionRef.current
          : manualActionRef.current;
    target?.focus({ preventScroll: true });
  }, [canAccept, checking, manual, pending, reconciliationRequired, review.generation, review.reviewId]);

  const motionProps = {
    initial: reduce ? false : POSE_SHEET_IN,
    animate: POSE_SHEET_VISIBLE,
    transition: reduce
      ? { duration: MOTION.reduced }
      : SHEET_CLEANUP_ENTER_TRANSITION,
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
      role="alertdialog"
      aria-modal="true"
      aria-label="Wiki Maintenance needs an answer"
      tabIndex={-1}
      className="memory-edit-review wiki-maintenance-review min-w-0"
    >
      <motion.div className="memory-edit-review__scrim" aria-hidden="true" {...scrimMotionProps} />
      <motion.div className="memory-edit-review__sheet flex min-h-0 min-w-0 flex-col" {...motionProps}>
        <header className="memory-edit-review__header">
          <div className="min-w-0">
            <p className="memory-edit-review__crumb">
              Wiki Maintenance · question {position} of {total} · generation {review.generation}
            </p>
            <h1 className="wiki-maintenance-review__title">Wiki Maintenance needs an answer</h1>
          </div>
        </header>

        <ReviewBody
          review={review}
          evidence={evidence}
          manual={manual}
          note={note}
          noteError={noteError}
          noteRef={noteRef}
          disabled={controlsDisabled}
          onNoteChange={(value) => {
            onNoteChange(value);
            setNoteError(null);
          }}
          onEvidencePage={(cursor) => void loadEvidence(cursor)}
        />

        {(error || reconciliationRequired) && (
          <p role="alert" className="memory-edit-review__error">
            {error ?? "Answer status must be checked before answering again."}
          </p>
        )}
        {checking && <p role="status" className="memory-edit-review__notice">Checking the current durable review status…</p>}

        <footer className="wiki-maintenance-review__footer">
          {reconciliationRequired ? (
            <Button ref={retryRef} variant="primary" disabled={pending || checking} onClick={onReconcile}>
              {checking || pending ? "Checking…" : "Check request status"}
            </Button>
          ) : (
            manual ? (
              <>
                  <Button
                    variant="quiet"
                    disabled={controlsDisabled}
                    onClick={() => {
                      onManualChange(false);
                      onNoteChange("");
                      setNoteError(null);
                    }}
                  >
                    Cancel
                  </Button>
                  <Button
                    variant="primary"
                    disabled={controlsDisabled}
                    onClick={() => {
                      const trimmed = note.trim();
                      if (!trimmed) {
                        setNoteError("A manual resolution note is required.");
                        noteRef.current?.focus({ preventScroll: true });
                        return;
                      }
                      onResolve(review, { action: "resolve-manually", note: trimmed });
                    }}
                  >
                    {pending ? "Saving…" : "Resolve manually"}
                  </Button>
              </>
            ) : (
              <>
                <Button variant="quiet" disabled={controlsDisabled} onClick={() => onResolve(review, { action: "reject" })}>
                  Reject
                </Button>
                <Button ref={manualActionRef} variant="secondary" disabled={controlsDisabled} onClick={() => onManualChange(true)}>
                  Resolve manually
                </Button>
                {canAccept && (
                  <Button
                    ref={primaryActionRef}
                    variant="primary"
                    disabled={controlsDisabled}
                    onClick={() => onResolve(review, { action: "accept" })}
                  >
                    {pending ? "Saving…" : hasUpdates ? "Accept change" : "Accept as-is"}
                  </Button>
                )}
              </>
            )
          )}
        </footer>
      </motion.div>
    </section>
  );
}

function ReviewBody({
  review,
  evidence,
  manual,
  note,
  noteError,
  noteRef,
  disabled,
  onNoteChange,
  onEvidencePage,
}: {
  review: WikiMaintenanceReview;
  evidence: EvidenceState;
  manual: boolean;
  note: string;
  noteError: string | null;
  noteRef: React.RefObject<HTMLTextAreaElement | null>;
  disabled: boolean;
  onNoteChange: (value: string) => void;
  onEvidencePage: (cursor: WikiMaintenanceEvidenceCursor) => void;
}) {
  const proposal = review.proposal;
  return (
    <div className="memory-edit-review__body wiki-maintenance-review__body scroll-fade min-h-0 min-w-0 flex-1">
      <p className="wiki-rename-sheet__explanation">{review.summary}</p>
      {proposal?.kind === "maintenance_updates" && (
        <>
          <p className="wiki-rename-sheet__explanation">{proposal.summary}</p>
          <ol className="wiki-maintenance-review__updates">
            {proposal.updates.map((update) => (
              <li key={update.pageId}>
                <div className="wiki-maintenance-review__page-heading">
                  <h3>{update.title}</h3><code>{update.pageId}</code>
                </div>
                {update.aliases.length > 0 && <p className="wiki-maintenance-review__aliases">Aliases: {update.aliases.join(", ")}</p>}
                <pre>{update.body}</pre>
              </li>
            ))}
          </ol>
        </>
      )}
      {proposal?.kind === "manual_evidence_review" && (
        <>
          <p className="wiki-rename-sheet__explanation">
            {proposal.section} is {proposal.actualBytesAtLeast ? "at least " : ""}{proposal.actualBytes} bytes; the limit is {proposal.limitBytes} bytes. Review the evidence, then record what you did.
          </p>
          <Evidence evidence={evidence} onPage={onEvidencePage} />
        </>
      )}
      {!proposal && <p className="wiki-rename-sheet__explanation">This review has no proposal. Reject it or record a manual resolution.</p>}
      {manual && (
        <Textarea
          ref={noteRef}
          label="Manual resolution note"
          help="Required. Describe the manual follow-up or why this evidence is acceptable."
          error={noteError ?? undefined}
          value={note}
          disabled={disabled}
          onChange={(event) => onNoteChange(event.target.value)}
        />
      )}
    </div>
  );
}

export function WikiMaintenanceReviewStatusSheet({ error, onRetry }: { error: string | null; onRetry: () => void }) {
  const overlayRef = useRef<HTMLElement>(null);
  const retryRef = useRef<HTMLButtonElement>(null);
  const checking = error === null;
  useOverlayLayer(overlayRef, true, () => {}, true);
  useFocusTrap(overlayRef, true);
  useEffect(() => {
    if (!checking) retryRef.current?.focus({ preventScroll: true });
  }, [checking]);
  return (
    <section ref={overlayRef} data-wiki-maintenance-status role="alertdialog" aria-modal="true" aria-label={checking ? "Checking Wiki Maintenance" : "Couldn’t check Wiki Maintenance"} tabIndex={-1} className="memory-edit-review wiki-maintenance-review min-w-0">
      <div className="memory-edit-review__scrim" aria-hidden="true" />
      <div className="memory-edit-review__sheet flex min-h-0 min-w-0 flex-col">
        <header className="memory-edit-review__header"><div><p className="memory-edit-review__crumb">Durable review check</p><h1 className="wiki-maintenance-review__title">{checking ? "Checking Wiki Maintenance" : "Couldn’t check Wiki Maintenance"}</h1></div></header>
        <div className="memory-edit-review__body wiki-maintenance-review__body scroll-fade min-h-0 min-w-0 flex-1"><div className="wiki-maintenance-review__status" role={checking ? "status" : "alert"}><p>{checking ? "Checking for unresolved maintenance questions…" : error}</p></div></div>
        {!checking && <footer className="wiki-maintenance-review__footer"><Button ref={retryRef} variant="primary" onClick={onRetry}>Retry check</Button></footer>}
      </div>
    </section>
  );
}

function Evidence({ evidence, onPage }: { evidence: EvidenceState; onPage: (cursor: WikiMaintenanceEvidenceCursor) => void }) {
  if (evidence.state === "loading") return <p role="status" className="wiki-rename-sheet__explanation">Loading evidence…</p>;
  if (evidence.state === "error") {
    return (
      <div role="alert" className="wiki-maintenance-review__status">
        <p>Couldn’t load evidence: {evidence.error}</p>
        <Button variant="secondary" size="sm" onClick={() => onPage(evidence.cursor)}>Retry evidence</Button>
      </div>
    );
  }
  if (evidence.state !== "ready") return null;
  const item = evidence.data;
  return (
    <section aria-label="Maintenance evidence">
      <div className="wiki-maintenance-review__page-heading">
        <h3>{item.change.path}</h3><code>{item.change.action}</code>
      </div>
      <p className="wiki-maintenance-review__aliases">Change {item.changeIndex + 1} of {item.changeCount} · {item.actor} · {item.reason}</p>
      <pre>{item.change.unifiedDiff}</pre>
      <div className="mt-2 flex gap-2">
        {item.previousCursor && <Button size="sm" onClick={() => onPage(item.previousCursor!)}>Previous</Button>}
        {item.nextCursor && <Button size="sm" onClick={() => onPage(item.nextCursor!)}>More evidence</Button>}
      </div>
    </section>
  );
}
