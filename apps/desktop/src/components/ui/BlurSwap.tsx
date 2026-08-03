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

  // inline-flex, not inline-block: an inline-block box sits on the text
  // baseline, so an icon child reserves descender space below it (a 16px glyph
  // measures a 19px box) and every flex-centered parent then renders the glyph
  // 1.5px high. Flex wrappers hug their content and keep first-line baseline
  // alignment for text, so both content shapes center honestly.
  if (reducedMotion) return <span className={clsx("inline-flex items-center", className)}>{children}</span>;

  return (
    <span className={clsx("inline-flex items-center", className)}>
      <AnimatePresence mode="wait" initial={false}>
        <motion.span
          key={swapKey}
          className="inline-flex items-center"
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
