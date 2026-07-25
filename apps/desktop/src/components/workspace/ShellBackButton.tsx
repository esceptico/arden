import { motion, useReducedMotion } from "motion/react";
import { ArrowLeft02 } from "@/components/icons";
import { IconButton } from "@/components/ui/IconButton";
import { ICON } from "@/lib/icons";
import { DISTANCE, EASE_OUT, MOTION } from "@/lib/tokens/motion";

/** Fixed shell-level return control shared by route rooms and takeovers. */
export function ShellBackButton({
  onClick,
  disabled = false,
}: {
  onClick: () => void;
  disabled?: boolean;
}) {
  const reducedMotion = useReducedMotion() ?? false;
  return (
    <motion.div
      className="shell-back"
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
        aria-label="Back to Home"
      >
        <ArrowLeft02 size={ICON.MD} />
      </IconButton>
    </motion.div>
  );
}
