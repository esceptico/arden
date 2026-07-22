import { useEffect, useRef, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import { EASE_EMPHASIZED, MOTION } from "@/lib/tokens/motion";

// Directional content swap: the new panel enters from the side you're moving
// toward, the old one leaves the opposite way. Small offset — restraint over
// theatrics, and no blur on directional slides. Single source for page-level
// slides (tab panels, palette hierarchy pages).
export const SLIDE_PAGE_VARIANTS = {
  enter: (dir: number) => ({ opacity: 0, x: dir * 16 }),
  center: { opacity: 1, x: 0 },
  exit: (dir: number) => ({ opacity: 0, x: dir * -16 }),
};

/** Tracks travel direction between ordered tab values so panel content slides
 *  the way you're moving (+1 forward, -1 back). */
export function useTabDirection(order: readonly string[], current: string): number {
  const index = order.indexOf(current);
  const prev = useRef(index);
  const direction = index >= prev.current ? 1 : -1;
  useEffect(() => {
    prev.current = index;
  }, [index]);
  return direction;
}

/** Directional slide + fade swap for tab panel content. `value` is the active
 *  tab key (drives the swap); `direction` comes from `useTabDirection`. */
export function TabPanels({
  value,
  direction,
  className,
  children,
}: {
  value: string;
  direction: number;
  className?: string;
  children: ReactNode;
}) {
  return (
    <AnimatePresence mode="popLayout" custom={direction} initial={false}>
      <motion.div
        key={value}
        custom={direction}
        variants={SLIDE_PAGE_VARIANTS}
        initial="enter"
        animate="center"
        exit="exit"
        transition={{ duration: MOTION.palette, ease: EASE_EMPHASIZED }}
        className={className}
      >
        {children}
      </motion.div>
    </AnimatePresence>
  );
}
