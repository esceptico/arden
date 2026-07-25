import type { ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import clsx from "clsx";
import { BLUR, EASE_IN_OUT, MOTION } from "@/lib/tokens/motion";

export type IconSwapState = "a" | "b";

export function IconSwap({
  state,
  iconA,
  iconB,
  className,
}: {
  state: IconSwapState;
  iconA: ReactNode;
  iconB: ReactNode;
  className?: string;
}) {
  return (
    <span className={clsx("t-icon-swap", className)} data-state={state} aria-hidden>
      <span className="t-icon" data-icon="a">{iconA}</span>
      <span className="t-icon" data-icon="b">{iconB}</span>
    </span>
  );
}

/** Arbitrary-glyph counterpart to IconSwap. The mock alternates two retained
 * slots, so a third semantic state still bridges through the same 250ms
 * blur/scale recipe instead of replacing an SVG in place. */
export function DynamicIconSwap({
  swapKey,
  children,
  className,
}: {
  swapKey: string;
  children: ReactNode;
  className?: string;
}) {
  const reducedMotion = useReducedMotion() ?? false;
  if (reducedMotion) {
    return <span className={clsx("inline-flex", className)} aria-hidden>{children}</span>;
  }

  const hidden = {
    opacity: 0,
    filter: `blur(${BLUR.iconSwap}px)`,
    scale: 0.25,
  } as const;

  return (
    <span className={clsx("relative inline-grid place-items-center leading-none", className)} aria-hidden>
      <AnimatePresence initial={false} mode="popLayout">
        <motion.span
          key={swapKey}
          className="col-start-1 row-start-1 inline-flex"
          initial={hidden}
          animate={{ opacity: 1, filter: "blur(0px)", scale: 1 }}
          exit={hidden}
          transition={{ duration: MOTION.iconSwap, ease: EASE_IN_OUT }}
        >
          {children}
        </motion.span>
      </AnimatePresence>
    </span>
  );
}
