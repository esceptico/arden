import { AnimatePresence, motion } from "motion/react";
import { ChevronRight } from "@/components/icons";
import { ICON } from "@/lib/icons";
import { EASE_EMPHASIZED, MOTION } from "@/lib/tokens/motion";
import type { Crumb } from "@/lib/commandEntries/types";

/** Breadcrumb trail rendered inline with the input. Each chip pops the
 *  stack back to that depth. Animates in with a tiny stagger and slides
 *  from -4px; popping reverses via AnimatePresence. */
export function Breadcrumbs({
  crumbs,
  onJump,
}: {
  crumbs: Crumb[];
  onJump: (depth: number) => void;
}) {
  if (crumbs.length === 0) return null;
  return (
    <div className="flex items-center gap-1 shrink-0">
      <AnimatePresence initial={false} mode="popLayout">
        {crumbs.map((crumb, i) => (
          <motion.div
            key={`${i}:${crumb.id}`}
            layout
            initial={{ opacity: 0, x: -4 }}
            animate={{
              opacity: 1,
              x: 0,
              // Stagger scoped to the entrance only — exits lead, not lag.
              transition: { duration: MOTION.check, ease: EASE_EMPHASIZED, delay: i * 0.02 },
            }}
            exit={{ opacity: 0, x: -4 }}
            transition={{ duration: MOTION.check, ease: EASE_EMPHASIZED }}
            className="flex items-center gap-1"
          >
            <button
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => onJump(i)}
              className="h-6 px-2 rounded-[var(--r-control)] bg-surface-soft text-xs text-ink-soft hover:text-ink hover:bg-surface-sunken transition-colors duration-check ease-out whitespace-nowrap"
            >
              {crumb.label}
            </button>
            <ChevronRight
              size={ICON.XS}
              strokeWidth={2}
              className="text-faint shrink-0"
              aria-hidden
            />
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
