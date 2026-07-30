import { useRef, type ReactNode } from "react";
import { motion, useReducedMotion } from "motion/react";
import { useFocusTrap } from "@/lib/hooks";
import { useOverlayLayer } from "@/lib/overlayStack";
import {
  EASE_OUT,
  MOTION,
  POSE_SHEET_IN,
  POSE_SHEET_OUT,
  POSE_SHEET_VISIBLE,
  SHEET_ENTER_TRANSITION,
  SHEET_EXIT_TRANSITION,
} from "@/lib/tokens/motion";
import { useRetainedPresence } from "@/components/workspace/useRetainedPresence";

/** Shared contract for dialogs nested inside a takeover workspace. */
export function DialogLayer({
  open,
  onClose,
  className,
  ariaLabel,
  labelledBy,
  describedBy,
  alert = false,
  children,
}: {
  open: boolean;
  onClose: () => void;
  className?: string;
  ariaLabel?: string;
  labelledBy?: string;
  describedBy?: string;
  alert?: boolean;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const reducedMotion = useReducedMotion() ?? false;
  const present = useRetainedPresence(open, (reducedMotion ? MOTION.reduced : MOTION.focus) * 1_000);
  // The dialog remains topmost and keeps its parent inert while the scrim
  // fades. Focus restores only after that release, matching Takeover.
  useOverlayLayer(ref, present, onClose);
  useFocusTrap(ref, present);

  if (!present) return null;

  return (
    <motion.div
      ref={ref}
      data-overlay-layer="nested-dialog"
      data-overlay-state={open ? "open" : "closing"}
      role={alert ? "alertdialog" : "dialog"}
      aria-modal="true"
      aria-label={ariaLabel}
      aria-labelledby={labelledBy}
      aria-describedby={describedBy}
      tabIndex={-1}
      className={className}
      initial={{ opacity: 0 }}
      animate={{ opacity: open ? 1 : 0 }}
      transition={{ duration: reducedMotion ? MOTION.reduced : MOTION.focus, ease: EASE_OUT }}
      style={{ pointerEvents: open ? undefined : "none" }}
      onKeyDownCapture={
        open
          ? undefined
          : (event) => {
              if (event.key !== "Tab") event.preventDefault();
            }
      }
    >
      <motion.div
        initial={reducedMotion ? { opacity: 0 } : POSE_SHEET_IN}
        animate={
          open
            ? reducedMotion
              ? { opacity: 1 }
              : POSE_SHEET_VISIBLE
            : reducedMotion
              ? { opacity: 0 }
              : POSE_SHEET_OUT
        }
        transition={reducedMotion ? { duration: MOTION.reduced } : open ? SHEET_ENTER_TRANSITION : SHEET_EXIT_TRANSITION}
      >
        {children}
      </motion.div>
    </motion.div>
  );
}
