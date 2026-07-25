import { motion } from "motion/react";
import { Check, Pencil, Target, X } from "@/components/icons";
import { acceptGoalProposal, cancelGoalProposal, editGoalProposal } from "@/actions/goals";
import { ICON } from "@/lib/icons";
import { RISE_IN, RISE_SETTLED } from "@/lib/tokens/motion";
import { SECTION_ENTER, SECTION_EXIT } from "@/features/chat/lib/composerMotion";
import { IconButton } from "@/components/ui/IconButton";

export function GoalProposalCard({ objective }: { objective: string }) {
  return (
    <motion.div
      initial={RISE_IN}
      animate={RISE_SETTLED}
      exit={SECTION_EXIT}
      transition={SECTION_ENTER}
      className="board-goal-proposal max-w-[760px] mx-auto mb-2"
    >
      <div className="board-goal-proposal__card surface-panel surface-radius-md flex items-start gap-2 px-3 py-2">
        <Target size={ICON.MD} strokeWidth={2} className="mt-0.5 shrink-0 text-accent" />
        <div className="min-w-0 flex-1">
          <div className="text-2xs font-medium text-muted">Proposed goal</div>
          <div className="max-h-10 overflow-hidden text-sm leading-5 text-ink-soft">{objective}</div>
        </div>
        <IconButton
          shape="circle"
          tone="primary"
          onClick={() => void acceptGoalProposal()}
          title="Accept goal"
          aria-label="Accept goal"
          className="shrink-0"
        >
          <Check size={ICON.SM} strokeWidth={2.4} />
        </IconButton>
        <IconButton
          shape="circle"
          onClick={editGoalProposal}
          title="Edit goal"
          aria-label="Edit goal"
          className="shrink-0"
        >
          <Pencil size={ICON.SM} strokeWidth={2} />
        </IconButton>
        <IconButton
          shape="circle"
          onClick={cancelGoalProposal}
          title="Cancel goal"
          aria-label="Cancel goal"
          className="shrink-0"
        >
          <X size={ICON.SM} strokeWidth={2} />
        </IconButton>
      </div>
    </motion.div>
  );
}
