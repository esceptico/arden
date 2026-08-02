import { AnimatePresence, motion } from "motion/react";
import { EASE_OUT, MOTION, originFromEvent, RISE_IN, RISE_SETTLED } from "@/lib/tokens/motion";
import { respondToApproval } from "@/actions/approvals";
import { Button } from "@/components/ui/Button";
import { useStore, type ApprovalState } from "@/stores";

type ApprovalRowProps = {
  /** Supplying approvals makes the Area Hub's session boundary explicit. */
  approvals?: readonly ApprovalState[];
  variant?: "compact" | "hub";
};

function HubApprovalCard({
  approval,
  onReview,
}: {
  approval: ApprovalState;
  onReview: (toolId: string, origin: { x: number; y: number } | null) => void;
}) {
  const detail = approval.preview?.trim()
    || (approval.diff ? "Review the exact diff." : "Review this action before it runs.");

  return (
    <article className="board-hub-approval-card" aria-label={`Approval required: ${approval.toolName}`}>
      <header>
        <h3>{approval.toolName}</h3>
        <span>Awaiting approval</span>
      </header>
      <p>{detail}</p>
      <footer>
        <Button
          size="sm"
          onClick={() => void respondToApproval(approval.toolId, false)}
        >
          Deny
        </Button>
        <Button
          variant="primary"
          size="sm"
          onClick={(event) => onReview(approval.toolId, originFromEvent(event.currentTarget))}
        >
          Review
        </Button>
      </footer>
    </article>
  );
}

/** The load-bearing "needs you" signal: a run is paused waiting on approval.
 * Chat keeps the compact row; the Area Hub gets the full review card. */
export function ApprovalsRow({ approvals: scopedApprovals, variant = "compact" }: ApprovalRowProps) {
  const pendingApprovals = useStore((s) => s.pendingApprovals);
  const review = useStore((s) => s.setReviewingApproval);
  const approvals = scopedApprovals ?? pendingApprovals;
  const first = approvals[0];

  if (variant === "hub") {
    if (approvals.length === 0) return null;
    return <div className="board-hub-approval-list">
      {approvals.map((approval) => (
        <HubApprovalCard key={approval.toolId} approval={approval} onReview={review} />
      ))}
    </div>;
  }

  return (
    <AnimatePresence initial={false}>
      {approvals.length > 0 && first && (
        <motion.div
          key="approvals"
          initial={{ ...RISE_IN, y: -4 }}
          animate={RISE_SETTLED}
          exit={{ opacity: 0, scale: 0.97, transition: { duration: MOTION.fast, ease: EASE_OUT } }}
          transition={{ duration: MOTION.row, ease: EASE_OUT }}
        >
          <button
            type="button"
            onClick={(e) => review(first.toolId, originFromEvent(e.currentTarget))}
            className="flex w-full items-center gap-2 rounded-[8px] bg-warn/10 px-2.5 py-2 text-left transition-[background-color,scale] duration-row ease-out hover:bg-warn/15 active:scale-[0.985]"
          >
            <span className="flex-1 text-xs text-ink-soft tabular-nums">
              {approvals.length} awaiting approval
            </span>
            <span className="shrink-0 text-2xs text-warn">Review →</span>
          </button>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
