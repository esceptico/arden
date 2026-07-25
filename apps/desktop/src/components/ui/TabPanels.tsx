import { useEffect, useRef, type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  CONTENT_ENTER_TRANSITION,
  CONTENT_EXIT_TRANSITION,
  CONTENT_SWAP_VARIANTS,
  CONTENT_SWAP_VERTICAL_VARIANTS,
  DISTANCE,
} from "@/lib/tokens/motion";

// Directional content swap: the new panel enters from the side you're moving
// toward, the old one leaves the opposite way. Keep this export for the
// command palette's own composition; TabPanels below adds the canonical blur
// and staged timings.
export const SLIDE_PAGE_VARIANTS = {
  enter: (direction: number) => ({ opacity: 0, x: direction * DISTANCE.contentSwap }),
  center: { opacity: 1, x: 0 },
  exit: (direction: number) => ({ opacity: 0, x: direction * -DISTANCE.contentSwap }),
};

const HORIZONTAL_TAB_PANEL_VARIANTS = {
  enter: CONTENT_SWAP_VARIANTS.enter,
  center: {
    ...CONTENT_SWAP_VARIANTS.center,
    transition: CONTENT_ENTER_TRANSITION,
  },
  exit: (direction: number) => ({
    ...CONTENT_SWAP_VARIANTS.exit(direction),
    transition: CONTENT_EXIT_TRANSITION,
  }),
};

const VERTICAL_SLIDE_PAGE_VARIANTS = {
  enter: CONTENT_SWAP_VERTICAL_VARIANTS.enter,
  center: {
    ...CONTENT_SWAP_VERTICAL_VARIANTS.center,
    transition: CONTENT_ENTER_TRANSITION,
  },
  exit: (direction: number) => ({
    ...CONTENT_SWAP_VERTICAL_VARIANTS.exit(direction),
    transition: CONTENT_EXIT_TRANSITION,
  }),
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
  axis = "x",
  className,
  children,
}: {
  value: string;
  direction: number;
  /** The content follows the surface's information order. */
  axis?: "x" | "y";
  className?: string;
  children: ReactNode;
}) {
  const reducedMotion = useReducedMotion() ?? false;
  const swapVariants = axis === "y" ? VERTICAL_SLIDE_PAGE_VARIANTS : HORIZONTAL_TAB_PANEL_VARIANTS;

  if (reducedMotion) return <div className={className}>{children}</div>;

  return (
    <AnimatePresence mode="wait" custom={direction} initial={false}>
      <motion.div
        key={value}
        custom={direction}
        variants={swapVariants}
        initial="enter"
        animate="center"
        exit="exit"
        className={className}
      >
        {children}
      </motion.div>
    </AnimatePresence>
  );
}
