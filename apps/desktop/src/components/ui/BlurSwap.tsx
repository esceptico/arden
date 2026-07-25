import type { ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import clsx from "clsx";
import {
  BLUR,
  TEXT_SWAP_ENTER,
  TEXT_SWAP_EXIT,
  TEXT_SWAP_TRANSITION,
  TEXT_SWAP_VISIBLE,
} from "@/lib/tokens/motion";

interface BlurSwapProps {
  /** Identity of the current content. A change swaps after the prior label exits. */
  swapKey: string;
  children: ReactNode;
  /** Legacy visual override for icon-sized slots. Standard text uses 2px. */
  blur?: number;
  className?: string;
}

/** Sequential 4px text swap from board-motion: exit upward, commit, enter
 * from below. Width commits with the text rather than animating. */
export function BlurSwap({ swapKey, children, blur = BLUR.contentSwap, className }: BlurSwapProps) {
  const reducedMotion = useReducedMotion() ?? false;
  const enter = blur === BLUR.contentSwap
    ? TEXT_SWAP_ENTER
    : { ...TEXT_SWAP_ENTER, filter: `blur(${blur}px)` };
  const exit = blur === BLUR.contentSwap
    ? TEXT_SWAP_EXIT
    : { ...TEXT_SWAP_EXIT, filter: `blur(${blur}px)` };

  if (reducedMotion) return <span className={clsx("inline-block", className)}>{children}</span>;

  return (
    <span className={clsx("inline-block", className)}>
      <AnimatePresence mode="wait" initial={false}>
        <motion.span
          key={swapKey}
          className="inline-block"
          style={{ willChange: "transform, filter, opacity" }}
          initial={enter}
          animate={TEXT_SWAP_VISIBLE}
          exit={exit}
          transition={TEXT_SWAP_TRANSITION}
        >
          {children}
        </motion.span>
      </AnimatePresence>
    </span>
  );
}
