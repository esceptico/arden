import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { motion, useReducedMotion } from "motion/react";
import {
  dismissTopSharedScrimOverlay,
  useSharedScrimOpen,
} from "@/lib/overlayStack";
import { EASE_OUT, MOTION } from "@/lib/tokens/motion";

/**
 * One visual scrim for the complete modal stack. Individual sheets still own
 * focus, Escape, and exit retention; stacking a viewer over a review never
 * compounds the 5% material.
 */
export function ModalScrim() {
  const open = useSharedScrimOpen();
  const reducedMotion = useReducedMotion() ?? false;
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  if (!mounted) return null;
  return createPortal(
    <motion.div
      data-shared-modal-scrim
      aria-hidden="true"
      className="modal-scrim"
      initial={false}
      animate={{ opacity: open ? 1 : 0 }}
      transition={{
        duration: reducedMotion ? MOTION.reduced : MOTION.focus,
        ease: EASE_OUT,
      }}
      style={{ pointerEvents: open ? "auto" : "none" }}
      onClick={open ? dismissTopSharedScrimOverlay : undefined}
    />,
    document.body,
  );
}
