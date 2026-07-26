import { memo, useLayoutEffect, useRef, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import clsx from "clsx";
import {
  COUNTER_SPIN_NEW,
  COUNTER_SPIN_OLD,
  COUNTER_SPIN_OLD_EXIT,
  COUNTER_SPIN_TRANSITION,
  COUNTER_SPIN_VISIBLE,
  counterSpinDelay,
  counterSpinSettleDelay,
} from "@/lib/tokens/motion";

export interface RollingTokenCell {
  oldChar: string;
  nextChar: string;
  index: number;
}

/** Align counter values from the right so only changed characters spin. */
export function rollingTokenCells(previous: string, next: string): RollingTokenCell[] {
  const length = Math.max(previous.length, next.length);
  const oldChars = previous.padStart(length, " ").split("");
  const nextChars = next.padStart(length, " ").split("");
  return oldChars.map((oldChar, index) => ({ oldChar, nextChar: nextChars[index], index }));
}

function visibleCharacter(character: string): string {
  return character === " " ? "\u00a0" : character;
}

/** Per-character counter from board-motion. Width changes commit immediately;
 * changed cells roll while matching characters remain still. */
export const RollingToken = memo(function RollingToken({
  value,
  mono = false,
  motionDisabled = false,
}: {
  value: string;
  mono?: boolean;
  motionDisabled?: boolean;
}) {
  const previousValue = useRef(value);
  const settleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const settleRun = useRef(0);
  const [transition, setTransition] = useState(() => ({ from: value, to: value }));
  const reducedMotion = useReducedMotion() ?? false;

  useLayoutEffect(() => {
    const previous = previousValue.current;
    const changed = previous !== value;
    const run = ++settleRun.current;
    previousValue.current = value;
    if (settleTimer.current !== null) {
      clearTimeout(settleTimer.current);
      settleTimer.current = null;
    }
    if (motionDisabled || reducedMotion || !changed) {
      setTransition((current) => (
        current.from === value && current.to === value ? current : { from: value, to: value }
      ));
      return;
    }

    setTransition({ from: previous, to: value });

    settleTimer.current = setTimeout(() => {
      if (settleRun.current !== run) return;
      settleTimer.current = null;
      setTransition({ from: value, to: value });
    }, counterSpinSettleDelay(Math.max(previous.length, value.length)) * 1_000);

    return () => {
      settleRun.current += 1;
      if (settleTimer.current !== null) {
        clearTimeout(settleTimer.current);
        settleTimer.current = null;
      }
    };
  }, [motionDisabled, reducedMotion, value]);

  const rootClassName = clsx("t-spin-counter relative inline-flex items-baseline", mono && "tabular-nums");
  if (motionDisabled || reducedMotion) return <span className={rootClassName}>{value}</span>;

  const cells = rollingTokenCells(transition.from, transition.to);

  return (
    <span className={rootClassName} aria-label={value}>
      {cells.map(({ oldChar, nextChar, index }) => {
        const unchanged = oldChar === nextChar;
        const delay = counterSpinDelay(cells.length, index);
        const transition = { ...COUNTER_SPIN_TRANSITION, delay };
        // Fixed slots keep rolling digits in rhythm; letters take their
        // natural width — padding them apart reads as broken kerning.
        const digitSlot = /\d/.test(oldChar) || /\d/.test(nextChar);
        return (
          <span
            key={`${index}:${oldChar}:${nextChar}`}
            aria-hidden="true"
            className={clsx(
              "t-spin-cell relative inline-grid h-[1em] overflow-hidden leading-none",
              digitSlot && "min-w-[.58em] text-center",
              !unchanged && "is-spinning",
            )}
          >
            {unchanged ? (
              visibleCharacter(nextChar)
            ) : (
              <>
                <motion.span
                  className="t-spin-old col-start-1 row-start-1 block"
                  style={{ willChange: "transform, opacity, filter" }}
                  initial={COUNTER_SPIN_OLD}
                  animate={COUNTER_SPIN_OLD_EXIT}
                  transition={transition}
                >
                  {visibleCharacter(oldChar)}
                </motion.span>
                <motion.span
                  className="t-spin-new col-start-1 row-start-1 block"
                  style={{ willChange: "transform, opacity, filter" }}
                  initial={COUNTER_SPIN_NEW}
                  animate={COUNTER_SPIN_VISIBLE}
                  transition={transition}
                >
                  {visibleCharacter(nextChar)}
                </motion.span>
              </>
            )}
          </span>
        );
      })}
    </span>
  );
});
