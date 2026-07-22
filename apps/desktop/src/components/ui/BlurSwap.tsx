import type { ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import clsx from "clsx";
import { EASE_OUT, MOTION } from "@/lib/tokens/motion";

// Overlapping blur crossfade for spatial content. Two-icon state changes use
// IconSwap; in-place text states that must never coexist use FieldSwap.
interface BlurSwapProps {
  /** Identity of the current content. A change triggers the crossfade. */
  swapKey: string;
  children: ReactNode;
  /** Blur radius (px) of the bridge. Tiny glyphs need less. */
  blur?: number;
  /** Crossfade duration in seconds; defaults to MOTION.check. */
  duration?: number;
  className?: string;
}

/**
 * Overlapping blur crossfade for two states that share one slot — Emil
 * Kowalski's "use blur when nothing else works" (tip #7). The old and new
 * state co-exist mid-transition (sync mode) stacked in a single grid cell,
 * so they blur into each other in place. mode="wait" would finish the exit
 * before starting the enter, leaving an empty frame — the opposite of a
 * bridge. The grid stack lets both occupy cell 1/1 without reflowing
 * side-by-side, and sizes to the larger child so there's no width jump.
 */
export function BlurSwap({
  swapKey,
  children,
  blur = 4,
  duration = MOTION.check,
  className,
}: BlurSwapProps) {
  const hidden = {
    opacity: 0,
    filter: `blur(${blur}px)`,
  };
  const shown = {
    opacity: 1,
    filter: "blur(0px)",
  };
  return (
    <span className={clsx("inline-grid place-items-center", className)}>
      <AnimatePresence initial={false}>
        <motion.span
          key={swapKey}
          className="col-start-1 row-start-1 inline-flex"
          initial={hidden}
          animate={shown}
          exit={hidden}
          transition={{ duration, ease: EASE_OUT }}
        >
          {children}
        </motion.span>
      </AnimatePresence>
    </span>
  );
}
