import { AnimatePresence, motion } from "motion/react";
import { Check, Loader2 } from "@/components/icons";
import clsx from "clsx";
import { ICON } from "@/lib/icons";
import { EASE_OUT, MOTION } from "@/lib/tokens/motion";

/** Transient save confirmation for fire-on-change settings controls.
 *  Fed by `useMutationState` ({ busy, saved }): renders nothing at rest,
 *  "Saving…" with a spinner while in flight, then a "Saved" check that
 *  holds briefly and fades. The wrapper is a persistent `aria-live` region
 *  so screen readers announce the transition. */
export function SaveStatus({
  busy,
  saved,
  className,
}: {
  busy: boolean;
  saved: boolean;
  className?: string;
}) {
  const state = busy ? "saving" : saved ? "saved" : "idle";
  return (
    <div
      aria-live="polite"
      className={clsx("flex h-5 items-center text-xs text-muted", className)}
    >
      <AnimatePresence mode="wait" initial={false}>
        {state !== "idle" && (
          <motion.span
            key={state}
            className="inline-flex items-center gap-1 whitespace-nowrap tabular-nums"
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: MOTION.fast, ease: EASE_OUT }}
          >
            {state === "saving" ? (
              <>
                <Loader2 size={ICON.XS} strokeWidth={2} className="animate-spin" />
                Saving…
              </>
            ) : (
              <>
                <Check size={ICON.XS} strokeWidth={2.5} className="text-ok" />
                Saved
              </>
            )}
          </motion.span>
        )}
      </AnimatePresence>
    </div>
  );
}
