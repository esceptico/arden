import type { ReactNode } from "react";
import clsx from "clsx";

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
