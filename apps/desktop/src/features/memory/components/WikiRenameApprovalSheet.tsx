import { useEffect, useRef, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { Button } from "@/components/ui/Button";
import { useFocusTrap } from "@/lib/hooks";
import { useOverlayLayer } from "@/lib/overlayStack";
import {
  EASE_OUT,
  MOTION,
  POSE_SHEET_IN,
  POSE_SHEET_VISIBLE,
  SHEET_CLEANUP_ENTER_TRANSITION,
} from "@/lib/tokens/motion";
import type { WikiRenameApproval } from "@/api/wiki";

type RenameDraft = {
  pageId: string;
  oldPath: string;
  oldTitle: string;
};

type Props = {
  draft?: RenameDraft;
  approval?: WikiRenameApproval;
  pending: boolean;
  reconciliationRequired: boolean;
  error: string | null;
  onCancel: () => void;
  onRequest: (input: { pageId: string; newPath: string; newTitle: string }) => void;
  onReconcile: () => void;
  onAccept: (approvalId: string) => void;
  onReject: (approvalId: string) => void;
};

function Arrow() {
  return <span className="wiki-rename-sheet__arrow" aria-hidden>→</span>;
}

export function WikiRenameApprovalSheet({
  draft,
  approval,
  pending,
  reconciliationRequired,
  error,
  onCancel,
  onRequest,
  onReconcile,
  onAccept,
  onReject,
}: Props) {
  const overlayRef = useRef<HTMLElement>(null);
  const titleRef = useRef<HTMLInputElement>(null);
  const approvalActionRef = useRef<HTMLButtonElement>(null);
  const reconcileActionRef = useRef<HTMLButtonElement>(null);
  const [validationError, setValidationError] = useState<string | null>(null);
  const reduce = useReducedMotion() ?? false;
  const isApproval = approval != null;
  useOverlayLayer(overlayRef, true, onCancel, isApproval || pending || reconciliationRequired);
  useFocusTrap(overlayRef, true);

  useEffect(() => {
    if (pending) return;
    const target = reconciliationRequired
      ? reconcileActionRef.current
      : draft
        ? titleRef.current
        : approvalActionRef.current;
    target?.focus({ preventScroll: true });
  }, [approval?.approvalId, approval?.generation, approval?.status, draft?.pageId, pending, reconciliationRequired]);

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

  if (approval) {
    return (
      <section
        ref={overlayRef}
        data-wiki-rename-approval
        data-reduced-motion-ready="true"
        data-long-content-ready="true"
        role="dialog"
        aria-modal="true"
        aria-label={`Review rename from ${approval.oldPath} to ${approval.newPath}`}
        tabIndex={-1}
        className="memory-edit-review wiki-rename-sheet min-w-0"
      >
        <motion.div
          className="memory-edit-review__scrim"
          aria-hidden="true"
          {...scrimMotionProps}
        />
        <motion.div className="memory-edit-review__sheet flex min-h-0 min-w-0 flex-col" {...motionProps}>
          <p role="status" aria-live="polite" className="sr-only">
            Rename approval {approval.status}: {approval.oldPath} to {approval.newPath}
          </p>
          <header className="memory-edit-review__header">
            <div className="min-w-0">
              <p className="memory-edit-review__crumb">Rename approval · generation {approval.generation}</p>
              <h1 className="wiki-rename-sheet__title">Review page rename</h1>
            </div>
          </header>
          <div className="memory-edit-review__body wiki-rename-sheet__body scroll-fade min-h-0 min-w-0 flex-1">
            <dl className="wiki-rename-sheet__changes">
              <div><dt>Title</dt><dd>{approval.oldTitle} <Arrow /> {approval.newTitle}</dd></div>
              <div><dt>Path</dt><dd><code>{approval.oldPath}</code> <Arrow /> <code>{approval.newPath}</code></dd></div>
              <div><dt>Affected</dt><dd>{approval.linkCount} links across {approval.pageCount} pages</dd></div>
              <div><dt>Generation</dt><dd>{approval.generation}</dd></div>
              <div><dt>Status</dt><dd>{approval.status}</dd></div>
              <div><dt>Created</dt><dd>{approval.createdAt}</dd></div>
              <div><dt>Reason</dt><dd>{approval.resolution ?? "No reason recorded"}</dd></div>
            </dl>
            <p className="wiki-rename-sheet__explanation">Accepting moves the page and updates the reviewed links. Rejecting keeps the current page unchanged.</p>
          </div>
          {error && <p role="alert" className="memory-edit-review__error">{error}</p>}
          <footer className="wiki-rename-sheet__footer">
            {approval.status !== "applying" && (
              <Button ref={approvalActionRef} variant="danger" disabled={pending} onClick={() => onReject(approval.approvalId)}>Reject rename</Button>
            )}
            <Button
              ref={approval.status === "applying" ? approvalActionRef : undefined}
              variant="primary"
              disabled={pending}
              onClick={() => onAccept(approval.approvalId)}
            >
              {pending ? "Applying…" : approval.status === "applying" ? "Retry rename" : "Accept rename"}
            </Button>
          </footer>
        </motion.div>
      </section>
    );
  }

  if (!draft) return null;
  return (
    <section
      ref={overlayRef}
      data-wiki-rename-draft
      data-reduced-motion-ready="true"
      role="dialog"
      aria-modal="true"
      aria-label={`Rename ${draft.oldTitle}`}
      tabIndex={-1}
      className="memory-edit-review wiki-rename-sheet min-w-0"
    >
      <motion.div className="memory-edit-review__scrim" aria-hidden="true" {...scrimMotionProps} />
      <motion.form
        className="memory-edit-review__sheet wiki-rename-sheet__form flex min-h-0 min-w-0 flex-col"
        {...motionProps}
        onSubmit={(event) => {
          event.preventDefault();
          const values = new FormData(event.currentTarget);
          const newTitle = String(values.get("title") ?? "").trim();
          const newPath = String(values.get("path") ?? "").trim();
          if (newPath === draft.oldPath) {
            setValidationError("Choose a different path to request a rename. Title-only editing is not yet available in this view.");
            return;
          }
          setValidationError(null);
          onRequest({ pageId: draft.pageId, newPath, newTitle });
        }}
      >
        <header className="memory-edit-review__header">
          <div className="min-w-0">
            <p className="memory-edit-review__crumb">Page actions</p>
            <h1 className="wiki-rename-sheet__title">Rename page</h1>
          </div>
        </header>
        <div className="memory-edit-review__body wiki-rename-sheet__body scroll-fade min-h-0 min-w-0 flex-1">
          <p className="wiki-rename-sheet__explanation">A rename moves this page and requires a new path. Title-only editing is not yet available in this view.</p>
          <label className="wiki-rename-sheet__field">
            <span>New title</span>
            <input ref={titleRef} name="title" defaultValue={draft.oldTitle} disabled={pending || reconciliationRequired} required />
          </label>
          <label className="wiki-rename-sheet__field">
            <span>New path</span>
            <input
              name="path"
              defaultValue={draft.oldPath}
              spellCheck={false}
              disabled={pending || reconciliationRequired}
              required
              aria-invalid={validationError ? true : undefined}
              aria-describedby={validationError ? "wiki-rename-validation" : undefined}
              onInput={() => setValidationError(null)}
            />
          </label>
          <p className="wiki-rename-sheet__current">Current path: <code>{draft.oldPath}</code></p>
        </div>
        {(validationError ?? error) && <p id="wiki-rename-validation" role="alert" className="memory-edit-review__error">{validationError ?? error}</p>}
        <footer className="wiki-rename-sheet__footer">
          <Button variant="quiet" disabled={pending || reconciliationRequired} onClick={onCancel}>Cancel</Button>
          {reconciliationRequired ? (
            <Button ref={reconcileActionRef} variant="primary" disabled={pending} onClick={onReconcile}>
              {pending ? "Checking…" : "Check request status"}
            </Button>
          ) : (
            <Button variant="primary" type="submit" disabled={pending}>{pending ? "Requesting…" : "Request rename"}</Button>
          )}
        </footer>
      </motion.form>
    </section>
  );
}
