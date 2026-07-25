import type { ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import clsx from "clsx";
import { BLUR, EASE_BLUR, EASE_OUT, MOTION } from "@/lib/tokens/motion";

type OverlapSwapProps = {
  swapKey: string;
  children: ReactNode;
  className?: string;
};

/**
 * Simultaneous content bridge used by the mock's `content.overlap`: the old
 * value blurs out while its replacement settles in without directional travel.
 */
export function OverlapSwap({ swapKey, children, className }: OverlapSwapProps) {
  return (
    <span className={clsx("relative inline-grid", className)}>
      <AnimatePresence initial={false} mode="popLayout">
        <motion.span
          key={swapKey}
          className="col-start-1 row-start-1 inline-flex"
          initial={{ opacity: 0, filter: `blur(${BLUR.contentSwap}px)` }}
          animate={{
            opacity: 1,
            filter: "blur(0px)",
            transition: { duration: MOTION.dissolve, ease: EASE_OUT },
            transitionEnd: { filter: "none" },
          }}
          exit={{
            opacity: 0,
            filter: `blur(${BLUR.contentSwap}px)`,
            transition: { duration: MOTION.textSwap, ease: EASE_BLUR },
          }}
        >
          {children}
        </motion.span>
      </AnimatePresence>
    </span>
  );
}
