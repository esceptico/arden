import { useEffect, useRef, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { useOverlayLayer } from "@/lib/overlayStack";
import { Button } from "@/components/ui/Button";
import { DiffReview } from "@/components/ui/DiffReview";
import { useFocusTrap } from "@/lib/hooks";
import {
  EASE_OUT,
  MOTION,
  POSE_SHEET_IN,
  POSE_SHEET_OUT,
  POSE_SHEET_VISIBLE,
  SHEET_CLEANUP_ENTER_TRANSITION,
  SHEET_CLEANUP_EXIT_TRANSITION,
} from "@/lib/tokens/motion";
import type { DiffReviewDecision, DiffReviewOperation } from "@/components/ui/diffReviewTypes";

export interface MemoryConflict {
  currentRevision: string;
  currentContent: string;
}

export function MemoryEditReview({
  kind,
  reviewId,
  path,
  baseContent,
  draftContent,
  operations = [],
  decisions,
  analysisPending = false,
  conflict,
  pending,
  error,
  onDecision,
  onApply,
  onCancel,
  onRebase,
  closing = false,
  onExitComplete,
}: {
  kind: "preview" | "conflict";
  reviewId: string;
  path: string;
  baseContent: string;
  draftContent: string;
  operations?: readonly DiffReviewOperation[];
  decisions: Readonly<Record<string, DiffReviewDecision>>;
  analysisPending?: boolean;
  conflict?: MemoryConflict;
  pending: boolean;
  error: string | null;
  onDecision: (operationId: string, decision: DiffReviewDecision) => void;
  onApply: () => void;
  onCancel: () => void;
  onRebase?: () => void;
  /** Parent retains the review until this exact exit completes. */
  closing?: boolean;
  onExitComplete?: () => void;
}) {
  const overlayRef = useRef<HTMLElement>(null);
  const sheetRef = useRef<HTMLDivElement>(null);
  const [stacked, setStacked] = useState(true);
  const reduce = useReducedMotion() ?? false;
  useOverlayLayer(overlayRef, true, onCancel, pending, false);
  useFocusTrap(overlayRef, true);

  useEffect(() => {
    const container = sheetRef.current;
    if (!container || typeof ResizeObserver === "undefined") return;
    const measure = () => setStacked(container.clientWidth < 860);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    overlayRef.current?.focus({ preventScroll: true });
  }, [conflict?.currentRevision, kind, path, reviewId]);

  const scrimMotionProps = {
    initial: reduce ? false : { opacity: 0 },
    animate: { opacity: closing ? 0 : 1 },
    transition: {
      duration: reduce ? MOTION.reduced : MOTION.focus,
      ease: EASE_OUT,
    },
  };
  const sheetMotionProps = {
    initial: reduce ? false : POSE_SHEET_IN,
    animate: closing ? (reduce ? { opacity: 0 } : POSE_SHEET_OUT) : POSE_SHEET_VISIBLE,
    transition: reduce
      ? { duration: MOTION.reduced }
      : closing
        ? SHEET_CLEANUP_EXIT_TRANSITION
        : SHEET_CLEANUP_ENTER_TRANSITION,
    onAnimationComplete: closing ? onExitComplete : undefined,
  };

  if (kind === "conflict" && conflict) {
    return (
      <section
        ref={overlayRef}
        data-memory-edit-review
        data-reduced-motion-ready="true"
        data-long-content-ready="true"
        role="dialog"
        aria-modal="true"
        aria-label={`Three-way conflict for ${path}`}
        tabIndex={-1}
        className="memory-edit-review min-w-0"
        data-review-closing={closing || undefined}
      >
        <motion.div className="memory-edit-review__scrim" aria-hidden="true" {...scrimMotionProps} />
        <motion.div
          ref={sheetRef}
          className="memory-edit-review__sheet flex min-h-0 min-w-0 flex-col outline-none"
          {...sheetMotionProps}
        >
          <p role="status" aria-live="polite" className="sr-only">Conflict {reviewId} detected for {path}. Your draft is preserved.</p>
          <header className="memory-edit-review__header mb-3 flex flex-wrap items-center gap-2 sm:gap-3">
            <div className="min-w-0 flex-1 basis-56">
              <p className="memory-edit-review__crumb">Review changes / {path}</p>
              <h1 className="text-lg font-semibold text-ink">Page changed elsewhere</h1>
              <p className="break-words text-xs text-muted">Compare both changes from your saved base. Your draft was not changed.</p>
            </div>
            <Button className="ml-auto" variant="secondary" size="sm" aria-label="Back to draft" disabled={pending} onClick={onCancel}>Back to draft</Button>
            <Button size="sm" aria-label="Continue with current page" disabled={pending} onClick={onRebase}>Continue with current</Button>
          </header>
          <div className="memory-edit-review__conflict grid min-h-0 min-w-0 flex-1 gap-3 overflow-auto lg:grid-cols-2">
            <section aria-label="Current page" className="flex min-h-[280px] min-w-0 flex-col">
              <h2 className="mb-1 text-xs font-semibold text-muted">Current page · rev {conflict.currentRevision.slice(0, 12)}</h2>
              <DiffReview
                before={{ path, content: baseContent }}
                after={{ path, content: conflict.currentContent }}
                layout="stacked"
                hideFooter
              />
            </section>
            <section aria-label="Your draft" className="flex min-h-[280px] min-w-0 flex-col">
              <h2 className="mb-1 text-xs font-semibold text-muted">Your draft</h2>
              <DiffReview
                before={{ path, content: baseContent }}
                after={{ path, content: draftContent }}
                layout="stacked"
                hideFooter
              />
            </section>
          </div>
        </motion.div>
      </section>
    );
  }

  return (
    <section
      ref={overlayRef}
      data-memory-edit-review
      data-reduced-motion-ready="true"
      data-long-content-ready="true"
      role="dialog"
      aria-modal="true"
      aria-label={`Review memory edit for ${path}`}
      tabIndex={-1}
      className="memory-edit-review min-w-0"
      data-review-closing={closing || undefined}
    >
      <motion.div className="memory-edit-review__scrim" aria-hidden="true" {...scrimMotionProps} />
      <motion.div
        ref={sheetRef}
        className="memory-edit-review__sheet flex min-h-0 min-w-0 flex-col outline-none"
        {...sheetMotionProps}
      >
        <p role="status" aria-live="polite" className="sr-only">
          {`Reviewing changes to ${path}, review ${reviewId}`}
        </p>
        <header className="memory-edit-review__header">
          <p className="memory-edit-review__crumb">Review changes / {path}</p>
        </header>
        {analysisPending && (
          <div role="status" className="memory-edit-review__notice">
            Memory analysis is unavailable. Saving now records this edit as pending for later reconciliation.
          </div>
        )}
        <div className="memory-edit-review__body scroll-fade min-h-0 min-w-0 flex-1">
          <DiffReview
            before={{ path, content: baseContent }}
            after={{ path, content: draftContent }}
            operations={operations}
            decisions={decisions}
            onDecision={onDecision}
            onApply={onApply}
            onCancel={onCancel}
            applyLabel={analysisPending ? "Save as pending" : "Apply changes"}
            applyDisabled={pending}
            interactionDisabled={pending}
            cancelLabel="Back to edit"
            layout={stacked ? "stacked" : "split"}
          />
        </div>
        {error && <p role="alert" className="memory-edit-review__error">{error}</p>}
      </motion.div>
    </section>
  );
}
