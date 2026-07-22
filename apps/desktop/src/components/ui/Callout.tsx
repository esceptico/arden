import { motion } from "motion/react";
import clsx from "clsx";
import { type ArdenIcon } from "@/components/icons";
import type { ReactNode } from "react";
import { RISE_IN, RISE_SETTLED, DISSOLVE_OUT, MOTION, EASE_OUT, EASE_DECELERATE } from "@/lib/tokens/motion";
import { ICON } from "@/lib/icons";

export type CalloutTone = "bad" | "warn" | "ok" | "neutral";

const TONE: Record<CalloutTone, { box: string; fg: string }> = {
  bad: { box: "bg-bad-soft border-bad/15", fg: "text-bad" },
  warn: { box: "bg-warn-soft border-warn/20", fg: "text-warn" },
  ok: { box: "bg-ok-soft border-ok/20", fg: "text-ok" },
  neutral: { box: "bg-surface-soft border-line-soft", fg: "text-muted" },
};

// Errors/warnings are assertive (interrupt the screen reader); ok/neutral are
// passive info and must NOT announce as "alert" — they're a polite status region.
const TONE_ROLE: Record<CalloutTone, "alert" | "status"> = {
  bad: "alert",
  warn: "alert",
  ok: "status",
  neutral: "status",
};

interface CalloutProps {
  tone?: CalloutTone;
  /** Optional leading icon (e.g. TriangleAlert for warnings). */
  icon?: ArdenIcon;
  /** Bold first line. */
  title?: ReactNode;
  /** Body copy. */
  children?: ReactNode;
  /** Optional trailing control (e.g. a Retry button). */
  action?: ReactNode;
  className?: string;
}

/**
 * Inline alert / notice box — one shared shape for the ~10 hand-rolled
 * `rounded-[10px] bg-{tone}-soft border px-3 py-2.5` callouts (settings errors,
 * automation-editor warnings, memory notices). `role="alert"` with the standard
 * rise-in entrance baked in; it also exits cleanly when rendered inside an
 * AnimatePresence (the exit is ignored otherwise).
 */
export function Callout({ tone = "neutral", icon: Icon, title, children, action, className }: CalloutProps) {
  const t = TONE[tone];
  return (
    <motion.div
      role={TONE_ROLE[tone]}
      initial={RISE_IN}
      animate={RISE_SETTLED}
      exit={{ ...DISSOLVE_OUT, transition: { duration: MOTION.row, ease: EASE_OUT } }}
      transition={{ duration: MOTION.panel, ease: EASE_DECELERATE }}
      className={clsx("flex items-start gap-2 rounded-[10px] border px-3 py-2.5", t.box, className)}
    >
      {Icon && <Icon size={ICON.SM} strokeWidth={2} className={clsx("mt-0.5 shrink-0", t.fg)} />}
      <div className="grid min-w-0 flex-1 gap-0.5">
        {title && <strong className={clsx("text-sm font-semibold", t.fg)}>{title}</strong>}
        {children && <span className={clsx("text-sm leading-[1.4]", t.fg)}>{children}</span>}
      </div>
      {action}
    </motion.div>
  );
}
