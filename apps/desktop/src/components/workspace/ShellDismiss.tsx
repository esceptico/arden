import { motion, useReducedMotion } from "motion/react";
import { X } from "@/components/icons";
import { IconButton } from "@/components/ui/IconButton";
import { ICON } from "@/lib/icons";
import { DISTANCE, EASE_OUT, MOTION } from "@/lib/tokens/motion";

/** Leaves a Takeover, in the shell slot ShellNav occupies elsewhere.
 *
 *  An X, not an arrow: Settings is layered over the stage rather than being
 *  a place in the route trail, so closing it returns you to whatever you were
 *  already doing. An arrow would promise a step backwards through history
 *  that never happens. */
export function ShellDismiss({
  onClick,
  label,
  disabled = false,
}: {
  onClick: () => void;
  label: string;
  disabled?: boolean;
}) {
  const reducedMotion = useReducedMotion() ?? false;
  return (
    <motion.div
      className="shell-nav"
      initial={reducedMotion ? { opacity: 0 } : { opacity: 0, x: -DISTANCE.subtle }}
      animate={{ opacity: 1, x: 0 }}
      exit={reducedMotion ? { opacity: 0 } : { opacity: 0, x: -DISTANCE.subtle }}
      transition={{ duration: reducedMotion ? MOTION.reduced : MOTION.feedback, ease: EASE_OUT }}
    >
      <IconButton
        size="xs"
        shape="circle"
        className="shell-control"
        onClick={onClick}
        disabled={disabled}
        aria-label={label}
      >
        <X size={ICON.MD} />
      </IconButton>
    </motion.div>
  );
}
