import { useCallback, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import clsx from "clsx";
import {
  CONTENT_ENTER_TRANSITION,
  CONTENT_EXIT_TRANSITION,
  DISTANCE,
  SPRING_DISCLOSURE,
} from "@/lib/tokens/motion";

interface CollapseProps {
  open: boolean;
  children: ReactNode;
  className?: string;
  /**
   * `reveal` (default) — the surrounding layout commits immediately and only
   * this presentation layer wipes in. `height` — the panel itself springs
   * between 0 and its measured height, so siblings travel with the reveal.
   */
  mode?: "reveal" | "height";
}

/** Disclosure reveal with snap-layout semantics. */
export function Collapse({ open, children, className, mode = "reveal" }: CollapseProps) {
  if (mode === "height") {
    return (
      <HeightCollapse open={open} className={className}>
        {children}
      </HeightCollapse>
    );
  }
  return (
    <AnimatePresence initial={false} mode="popLayout">
      {open && (
        <motion.div
          initial={{
            opacity: 0,
            y: -DISTANCE.contentSwap,
            clipPath: "inset(0 0 100% 0)",
            filter: "blur(2px)",
          }}
          animate={{
            opacity: 1,
            y: 0,
            clipPath: "inset(0)",
            filter: "blur(0px)",
            transition: CONTENT_ENTER_TRANSITION,
          }}
          exit={{
            opacity: 0,
            y: -DISTANCE.dissolve,
            clipPath: "inset(0 0 100% 0)",
            filter: "blur(2px)",
            transition: CONTENT_EXIT_TRANSITION,
          }}
          className={className}
        >
          {children}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

/**
 * Fluid Functionalism's accordion body, ported. The panel stays mounted so its
 * content can be measured, and animates to a LAYOUT pixel height rather than
 * `height: "auto"` — Motion resolves `auto` by measuring the element's visual
 * (transformed) box, which overshoots under any scaled ancestor. `offsetHeight`
 * and ResizeObserver are transform-immune.
 *
 * Only `height` animates — spacing a caller wants revealed with the panel
 * belongs INSIDE the children, where it is measured, never in margins on the
 * panel (unmeasured, and animating them forces extra per-frame style recalc).
 */
function HeightCollapse({ open, children, className }: Omit<CollapseProps, "mode">) {
  const innerRef = useRef<HTMLDivElement | null>(null);
  const observerRef = useRef<ResizeObserver | null>(null);
  const [contentHeight, setContentHeight] = useState<number | null>(null);
  // A panel mounted open receives its first pixel target a commit after the
  // initial render; that hand-off snaps, later toggles spring.
  const needsSnap = useRef(open);
  const [settledClosed, setSettledClosed] = useState(!open);
  if (open && settledClosed) setSettledClosed(false);

  const measureRef = useCallback((element: HTMLDivElement | null) => {
    observerRef.current?.disconnect();
    observerRef.current = null;
    innerRef.current = element;
    if (!element) return;
    if (element.offsetHeight > 0) setContentHeight(element.offsetHeight);
    const observer = new ResizeObserver(() => {
      // Ignore the 0 that fires while the panel is hidden.
      if (element.offsetHeight > 0) setContentHeight(element.offsetHeight);
    });
    observer.observe(element);
    observerRef.current = observer;
  }, []);

  // Re-measure pre-paint on open so the spring targets the fresh layout height
  // from its first frame.
  useLayoutEffect(() => {
    if (open && innerRef.current && innerRef.current.offsetHeight > 0) {
      setContentHeight(innerRef.current.offsetHeight);
    }
  }, [open]);

  if (contentHeight !== null) needsSnap.current = false;

  return (
    <motion.div
      hidden={!open && settledClosed}
      className={clsx("overflow-hidden", className)}
      initial={false}
      animate={{ height: open ? contentHeight ?? 0 : 0 }}
      transition={needsSnap.current ? { duration: 0 } : SPRING_DISCLOSURE}
      onAnimationComplete={() => {
        if (!open) setSettledClosed(true);
      }}
    >
      <div ref={measureRef}>{children}</div>
    </motion.div>
  );
}
