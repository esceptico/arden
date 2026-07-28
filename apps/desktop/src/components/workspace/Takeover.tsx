import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { motion, useReducedMotion } from "motion/react";
import { useOverlayLayer } from "@/lib/overlayStack";
import { useFocusTrap } from "@/lib/hooks";
import { PageEntrance } from "@/components/workspace/PageEntrance";
import {
  POSE_SHEET_IN,
  POSE_SHEET_OUT,
  POSE_SHEET_VISIBLE,
  SHEET_EXIT_TRANSITION,
  SHEET_ENTER_TRANSITION,
  MOTION,
} from "@/lib/tokens/motion";

export function Takeover({
  open,
  onClose,
  ariaLabel,
  className = "",
  disableEscape = false,
  children,
}: {
  open: boolean;
  onClose: () => void;
  ariaLabel: string;
  className?: string;
  disableEscape?: boolean;
  children: ReactNode;
}) {
  const ref = useRef<HTMLElement>(null);
  const openRef = useRef(open);
  const closeVersion = useRef(0);
  const [present, setPresent] = useState(open);
  const reducedMotion = useReducedMotion() ?? false;
  const exitMs = (reducedMotion ? MOTION.reduced : SHEET_EXIT_TRANSITION.duration) * 1_000;
  openRef.current = open;

  useLayoutEffect(() => {
    const version = ++closeVersion.current;
    if (open) {
      setPresent(true);
      return;
    }
    if (!present) return;

    const timer = window.setTimeout(() => {
      if (closeVersion.current === version && !openRef.current) {
        setPresent(false);
      }
    }, exitMs);
    return () => window.clearTimeout(timer);
  }, [exitMs, open, present]);

  // Keep the stack registered and the focus trap active until the canonical
  // 160ms exit is finished. Calling the overlay hook first also restores the
  // inert background before the focus-trap cleanup returns focus to it.
  useOverlayLayer(ref, present, onClose, disableEscape);
  useFocusTrap(ref, present);

  const root = document.querySelector("#app");
  if (!root) return null;

  return createPortal(
    present ? (
      <motion.section
        ref={ref}
        data-overlay-layer="takeover"
        data-overlay-state={open ? "open" : "closing"}
        role="dialog"
        aria-modal="true"
        aria-label={ariaLabel}
        tabIndex={-1}
        initial={reducedMotion ? { opacity: 0 } : POSE_SHEET_IN}
        animate={open ? POSE_SHEET_VISIBLE : reducedMotion ? { opacity: 0 } : POSE_SHEET_OUT}
        transition={
          reducedMotion
            ? { duration: MOTION.reduced }
            : open
              ? SHEET_ENTER_TRANSITION
              : SHEET_EXIT_TRANSITION
        }
        className={`fixed inset-0 z-[var(--z-takeover)] overflow-hidden bg-[var(--paper)] focus:outline-none ${className}`}
      >
        <PageEntrance routeKey={ariaLabel}>{children}</PageEntrance>
      </motion.section>
    ) : null,
    root,
  );
}
