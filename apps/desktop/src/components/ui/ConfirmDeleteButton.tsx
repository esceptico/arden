import { useCallback, useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import clsx from "clsx";
import { Check, Loader2, Trash2 } from "@/components/icons";
import { RollingToken } from "@/components/ui/RollingToken";

type State = "idle" | "counting" | "done";

interface ConfirmDeleteButtonProps {
  onConfirm: () => void;
  /** Accessible name on the idle (icon-only) button. */
  label?: string;
  /** Length of the cancellable countdown window, in seconds. */
  seconds?: number;
  /** Async-in-progress: shows a spinner and ignores clicks. */
  busy?: boolean;
  /** Fired when the countdown arms/disarms — lets a hover-revealed container
   *  stay visible while the countdown is running (so it can be cancelled). */
  onActiveChange?: (active: boolean) => void;
  size?: "xs" | "sm" | "md";
  className?: string;
  /** -1 removes the button from the Tab order (roving-focus hosts like the
   *  session rows expose the action via the row's context menu instead). */
  tabIndex?: number;
}

const SIZE = {
  // xs: idle footprint matches a 16px icon-action so it sits flush in a dense
  // action lane (AgentRunContent); grows to the same labelled countdown.
  xs: { idle: "w-4 h-4", grown: "h-[22px] px-2 text-xs", icon: 13 },
  sm: { idle: "w-[26px] h-[22px]", grown: "h-[22px] px-2 text-xs", icon: 14 },
  md: { idle: "w-7 h-7", grown: "h-7 px-2.5 text-xs", icon: 16 },
} as const;

/**
 * Destructive delete as a cancellable countdown (the Framer "Delete Button"
 * interaction): one click arms it — the icon button grows to show "Cancel"
 * plus an odometer counting down while a fill sweeps across — and unless you
 * click again to cancel, it fires `onConfirm` at zero. No accidental loss
 * (you get the window) and no second confirm click (it auto-commits).
 */
export function ConfirmDeleteButton({
  onConfirm,
  label = "Delete",
  seconds = 3,
  busy = false,
  onActiveChange,
  size = "sm",
  className,
  tabIndex,
}: ConfirmDeleteButtonProps) {
  const [state, setState] = useState<State>("idle");
  const [count, setCount] = useState(seconds);
  const tick = useRef<ReturnType<typeof setInterval> | null>(null);
  const doneTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sz = SIZE[size];

  useEffect(() => {
    onActiveChange?.(state !== "idle");
  }, [state, onActiveChange]);

  const clearTimers = useCallback(() => {
    if (tick.current) clearInterval(tick.current);
    if (doneTimer.current) clearTimeout(doneTimer.current);
    tick.current = null;
    doneTimer.current = null;
  }, []);

  useEffect(() => clearTimers, [clearTimers]);

  const cancel = useCallback(() => {
    clearTimers();
    setState("idle");
    setCount(seconds);
  }, [clearTimers, seconds]);

  const start = useCallback(() => {
    setState("counting");
    setCount(seconds);
    clearTimers();
    tick.current = setInterval(() => {
      setCount((c) => {
        if (c <= 1) {
          clearTimers();
          setState("done");
          onConfirm();
          doneTimer.current = setTimeout(() => setState("idle"), 1200);
          return 0;
        }
        return c - 1;
      });
    }, 1000);
  }, [seconds, clearTimers, onConfirm]);

  const onClick = () => {
    if (busy) return;
    if (state === "counting") cancel();
    else if (state === "idle") start();
  };

  const counting = state === "counting";
  const grown = counting || state === "done";

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      tabIndex={tabIndex}
      aria-label={counting ? "Cancel delete" : label}
      className={clsx(
        "relative inline-flex items-center justify-center overflow-hidden rounded-[6px] font-medium transition-[background-color,color] duration-check ease-out active:scale-[0.96]",
        grown ? sz.grown : sz.idle,
        state === "done"
          ? "text-ok bg-ok-soft"
          : counting
            ? "text-bad bg-bad-soft"
            : "text-faint hover:text-bad hover:bg-surface-soft/70",
        className,
      )}
    >
      {counting && (
        <motion.span
          aria-hidden
          className="absolute inset-0 origin-left bg-bad/15"
          initial={{ scaleX: 0 }}
          animate={{ scaleX: 1 }}
          transition={{ duration: seconds, ease: "linear" }}
        />
      )}
      <span className="relative inline-flex items-center gap-1 whitespace-nowrap">
        {busy ? (
          <Loader2 size={sz.icon} className="animate-spin" />
        ) : counting ? (
          <>
            <span>Cancel</span>
            <span className="tabular-nums opacity-80">
              <RollingToken value={String(count)} mono />
            </span>
          </>
        ) : state === "done" ? (
          <Check size={sz.icon} />
        ) : (
          <Trash2 size={sz.icon} />
        )}
      </span>
    </button>
  );
}
