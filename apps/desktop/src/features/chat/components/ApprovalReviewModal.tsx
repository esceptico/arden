import { useEffect, useState } from "react";
import { Check, X } from "@/components/icons";
import { useStore } from "@/stores";
import { respondToApproval } from "@/actions/approvals";
import { IconButton } from "@/components/ui/IconButton";
import { Button } from "@/components/ui/Button";
import { PageModal } from "@/components/ui/PageModal";
import { ICON } from "@/lib/icons";
import { ScrollFadeTop } from "@/components/ui/ScrollBlur";
import { DiffReview } from "@/components/ui/DiffReview";
import { Input } from "@/components/ui/Input";
import { getApprovalFeedbackDraft, setApprovalFeedbackDraft } from "@/lib/approvalFeedbackDraft";

/** Diff/preview review for a pending approval. Opens when the banner's
 *  Review button is clicked. Approve/Reject actions live here too so the
 *  user doesn't have to dismiss the modal first. */
export function ApprovalReviewModal() {
  const reviewing = useStore((s) => s.reviewingApprovalToolId);
  const approval = useStore((s) =>
    reviewing ? s.pendingApprovals.find((a) => a.toolId === reviewing) ?? null : null,
  );
  const close = useStore((s) => s.setReviewingApproval);
  const origin = useStore((s) => s.modalOrigin);
  const [denyReason, setDenyReason] = useState("");
  useEffect(() => {
    setDenyReason(reviewing ? getApprovalFeedbackDraft(reviewing) : "");
  }, [reviewing]);

  return (
    <PageModal
      open={!!approval}
      onClose={() => close(null)}
      origin={origin}
      size="w-[min(720px,calc(100vw-80px))] max-h-[calc(100vh-80px)]"
      grid="grid-rows-[auto_minmax(0,1fr)_auto]"
      ariaLabel={approval ? `Review ${approval.toolName}` : "Review approval"}
    >
      {approval && (
        <>
          <header className="modal-header flex items-center gap-2 min-w-0">
            <span className="font-mono text-base font-medium text-ink truncate">
              {approval.toolName}
            </span>
            {approval.path && (
              <span className="font-mono text-sm text-faint truncate">{approval.path}</span>
            )}
            <IconButton onClick={() => close(null)} aria-label="Close" className="ml-auto shrink-0">
              <X size={ICON.SM} strokeWidth={2} />
            </IconButton>
          </header>

          <div className="overflow-y-auto scroll-thin">
            <ScrollFadeTop />
            {approval.diff ? (
              <div className="min-h-0 p-3">
                <DiffReview
                  before={{ path: approval.path ?? "before", content: "" }}
                  after={{ path: approval.path ?? "after", content: "" }}
                  rawPatch={approval.diff}
                  modes={["raw"]}
                  initialMode="raw"
                  layout="stacked"
                  hideFooter
                />
              </div>
            ) : approval.preview ? (
              <pre className="m-0 px-5 py-4 font-mono text-sm leading-[1.55] text-ink-soft whitespace-pre-wrap">
                {approval.preview}
              </pre>
            ) : (
              <div className="px-5 py-6 text-sm text-muted italic">
                No diff or preview available.
              </div>
            )}
          </div>

          <footer className="flex items-center gap-2 px-5 py-3 bg-surface-soft/40">
            <span className="text-xs text-faint font-mono">{approval.toolId.slice(0, 8)}</span>
            <Input
              size="sm"
              aria-label="Rejection reason"
              placeholder="Optional rejection reason"
              value={denyReason}
              onChange={(event) => {
                setDenyReason(event.target.value);
                setApprovalFeedbackDraft(approval.toolId, event.target.value);
              }}
              className="ml-auto min-w-0 max-w-72"
            />
            <Button
              variant="secondary"
              leadingIcon={X}
              onClick={() => void respondToApproval(approval.toolId, false, denyReason.trim())}
            >
              Reject
            </Button>
            <Button
              variant="primary"
              leadingIcon={Check}
              onClick={() => void respondToApproval(approval.toolId, true)}
            >
              Approve
            </Button>
          </footer>
        </>
      )}
    </PageModal>
  );
}
