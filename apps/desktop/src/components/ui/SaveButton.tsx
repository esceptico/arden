import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import clsx from "clsx";
import { Check, Loader2 } from "@/components/icons";
import { EASE_OUT, MOTION } from "@/lib/tokens/motion";

type SaveState = "idle" | "saving" | "saved";

interface SaveButtonProps {
  onSave: () => void | Promise<void>;
  idleLabel?: string;
  savingLabel?: string;
  savedLabel?: string;
  tone?: "accent" | "ink";
  disabled?: boolean;
  className?: string;
}

const SAVED_HOLD_MS = 1500;

/**
 * Save with state morph: idle → saving → saved, then auto-reverts. Icons
 * come from the shared registry; icon + word move as one state label.
 */
export function SaveButton({
  onSave,
  idleLabel = "Save",
  savingLabel = "Saving",
  savedLabel = "Saved",
  tone = "accent",
  disabled,
  className,
}: SaveButtonProps) {
  const [state, setState] = useState<SaveState>("idle");
  const mounted = useRef(true);
  const revertTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => {
    return () => {
      mounted.current = false;
      clearTimeout(revertTimer.current);
    };
  }, []);

  const handleClick = async () => {
    if (state === "saving") return;
    setState("saving");
    try {
      await onSave();
      if (!mounted.current) return;
      setState("saved");
      revertTimer.current = setTimeout(() => {
        if (mounted.current) setState("idle");
      }, SAVED_HOLD_MS);
    } catch {
      if (mounted.current) setState("idle");
    }
  };

  const label = state === "saving" ? savingLabel : state === "saved" ? savedLabel : idleLabel;

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={disabled || state === "saving"}
      aria-busy={state === "saving"}
      className={clsx(
        "relative inline-flex items-center justify-center h-8 px-3.5 rounded-[9px] text-sm font-medium text-on-ink overflow-hidden",
        tone === "ink" ? "bg-ink hover:bg-ink/90" : "bg-accent hover:bg-accent/90",
        "disabled:opacity-45 transition-[background-color,opacity] duration-check ease-out active:scale-[0.98]",
        className,
      )}
    >
      <AnimatePresence mode="wait" initial={false}>
        <motion.span
          key={state}
          className="inline-flex items-center gap-1.5 whitespace-nowrap"
          initial={{ opacity: 0, y: 7 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -7 }}
          transition={{ duration: MOTION.fast, ease: EASE_OUT }}
        >
          {state === "saving" && <Loader2 size={14} className="animate-spin" aria-hidden />}
          {state === "saved" && <Check size={14} aria-hidden />}
          {label}
        </motion.span>
      </AnimatePresence>
    </button>
  );
}
