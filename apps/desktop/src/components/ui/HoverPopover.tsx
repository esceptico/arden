import { useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import clsx from "clsx";
import {
  EXIT_FAST,
  MOTION,
  POPOVER_ENTER_TRANSITION,
  POSE_PORTALED_POPOVER_IN,
  POSE_PORTALED_POPOVER_OUT,
  POSE_PORTALED_POPOVER_VISIBLE,
} from "@/lib/tokens/motion";
import { useReanchor } from "@/lib/hooks";
import { useHasBlockingOverlay, useOverlayLayer } from "@/lib/overlayStack";

/** 120ms hover bridge — one value across the composer-toolbar popovers
 *  (BudgetDial, GoalStrip, LoopStatus) so the gap-crossing grace feels
 *  identical. */
const HIDE_DELAY_MS = MOTION.press * 1_000;

interface TriggerBind {
  ref: RefObject<HTMLButtonElement | null>;
  open: boolean;
  /** Click-to-pin for triggers that want it; hover alone works without. */
  toggle: () => void;
  hoverProps: {
    onMouseEnter: () => void;
    onMouseLeave: () => void;
    onFocus: () => void;
    onBlur: () => void;
  };
}

interface HoverPopoverProps {
  /** Renders the trigger; spread `hoverProps` and attach `ref` on it. */
  trigger: (bind: TriggerBind) => ReactNode;
  children: ReactNode;
  /** Trigger edge the panel aligns to. The panel always opens above. */
  anchor?: "left" | "right";
  /** Panel classes on top of surface-panel/surface-popover (width, padding). */
  className?: string;
  /** Accessible label for the portaled dialog panel. */
  label?: string;
  /** Close on mousedown outside trigger + panel. Cheap insurance for the
   *  case where hover misbehaves on a flaky trackpad and the popover gets
   *  stuck open. */
  dismissOnOutsideClick?: boolean;
}

/**
 * Hover-anchored popover for composer-toolbar pills. Owns the shared
 * scaffolding: trigger-rect measurement (re-measured on resize/scroll),
 * hover bridge between trigger and panel, hide-delay timer, portal to the
 * app overlay root, and the house popover entrance — rising from
 * `transformOrigin: bottom <anchor>` above the trigger.
 */
export function HoverPopover({
  trigger,
  children,
  anchor = "left",
  className,
  label = "Popover",
  dismissOnOutsideClick = false,
}: HoverPopoverProps) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const hideTimerRef = useRef<number | null>(null);
  const [open, setOpen] = useState(false);
  const [coords, setCoords] = useState<{ bottom: number; left?: number; right?: number } | null>(
    null,
  );
  const nested = useHasBlockingOverlay();

  const cancelHide = () => {
    if (hideTimerRef.current !== null) {
      window.clearTimeout(hideTimerRef.current);
      hideTimerRef.current = null;
    }
  };
  const show = () => {
    cancelHide();
    setOpen(true);
  };
  const scheduleHide = () => {
    cancelHide();
    hideTimerRef.current = window.setTimeout(() => setOpen(false), HIDE_DELAY_MS);
  };
  const toggle = () => setOpen((v) => !v);
  useOverlayLayer(popoverRef, open && coords !== null, () => setOpen(false), false, false);

  // useLayoutEffect so coords are committed before the popover paints —
  // an open=true / coords-stale frame would render the popover at the
  // wrong corner of the viewport for one tick (visible flash).
  useReanchor(open, () => {
    const t = triggerRef.current;
    if (!t) return;
    const r = t.getBoundingClientRect();
    setCoords({
      bottom: Math.max(8, window.innerHeight - r.top + 8),
      ...(anchor === "left"
        ? { left: Math.max(8, r.left) }
        : { right: Math.max(8, window.innerWidth - r.right - 8) }),
    });
  });

  useLayoutEffect(() => {
    if (!open || !dismissOnOutsideClick) return;
    const onClick = (e: MouseEvent) => {
      const target = e.target as Node;
      if (triggerRef.current?.contains(target)) return;
      if (popoverRef.current?.contains(target)) return;
      setOpen(false);
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, [open, dismissOnOutsideClick]);

  const root = document.querySelector("#app");

  return (
    <>
      {trigger({
        ref: triggerRef,
        open,
        toggle,
        hoverProps: {
          onMouseEnter: show,
          onMouseLeave: scheduleHide,
          onFocus: show,
          onBlur: scheduleHide,
        },
      })}
      {root && createPortal(
        <AnimatePresence>
          {open && coords && (
            <motion.div
              ref={popoverRef}
              role="dialog"
              aria-label={label}
              onMouseEnter={show}
              onMouseLeave={scheduleHide}
              initial={POSE_PORTALED_POPOVER_IN}
              animate={POSE_PORTALED_POPOVER_VISIBLE}
              exit={{ ...POSE_PORTALED_POPOVER_OUT, transition: EXIT_FAST }}
              transition={POPOVER_ENTER_TRANSITION}
              style={{
                position: "fixed",
                ...coords,
                zIndex: nested ? "var(--z-nested)" : "var(--z-popover)",
                transformOrigin: `bottom ${anchor}`,
              }}
              data-overlay-depth={nested ? "nested" : "page"}
              className={clsx("surface-panel surface-popover", className)}
            >
              {children}
            </motion.div>
          )}
        </AnimatePresence>,
        root,
      )}
    </>
  );
}
