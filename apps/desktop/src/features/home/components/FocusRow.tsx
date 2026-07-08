import { motion } from "motion/react";
import type { SliceAsk } from "@/api/slices";
import { useStore } from "@/stores";
import { formatRelativePast } from "@/lib/format";
import { ASK_KIND } from "@/lib/sliceKind";
import { RISE_IN, RISE_SETTLED, ROW_EXIT, SPRING_ROW_ENTRY, MOTION, EASE_OUT } from "@/lib/tokens/motion";

/** A single focus-set row: a slice's one ask, made legible and actionable
 *  by the row itself. The whole card opens the slice — there's no per-row
 *  button, because for an agent ask "the button" only ever meant "open the
 *  slice" (the kind was a label masquerading as an action). Genuinely
 *  distinct actions (retry a failed run, approve a pending tool) live on
 *  the ask card inside the room, one click away. Eyebrow = slice title +
 *  severity dot + the kind as a text tag; the ask reads in full (two
 *  lines). Enters RISE_IN/SPRING_ROW_ENTRY, retires ROW_EXIT via the
 *  caller's AnimatePresence (keyed by ask.id). */
export function FocusRow({ ask, sliceTitle }: { ask: SliceAsk; sliceTitle: string }) {
  const openSlice = useStore((s) => s.openSlice);
  const kind = ASK_KIND[ask.kind];
  const age = formatRelativePast(ask.created_at);

  return (
    <motion.button
      layout
      type="button"
      onClick={() => openSlice(ask.slice_key)}
      initial={RISE_IN}
      animate={RISE_SETTLED}
      exit={{ ...ROW_EXIT, transition: { duration: MOTION.row, ease: EASE_OUT } }}
      transition={SPRING_ROW_ENTRY}
      className="app-row group/row grid w-full min-w-0 gap-1 rounded-xl bg-surface-soft px-4 py-3 text-left focus-visible:shadow-[0_0_0_2px_var(--color-accent-soft)] focus-visible:outline-none"
    >
      {/* Eyebrow reads left→right as identity · kind · freshness: the slice
          leads (what area), the kind qualifies (what's being asked), and the
          right-aligned age gives the list a measured spine — the one number
          that says whether this is fresh or has been sitting. */}
      <div className="flex min-w-0 items-center gap-2">
        <span className="min-w-0 truncate text-xs font-medium text-ink-soft group-hover/row:text-ink">
          {sliceTitle}
        </span>
        <span className="shrink-0 text-2xs font-medium uppercase tracking-wide text-faint">
          {kind.label}
        </span>
        {age && <span className="ml-auto shrink-0 text-2xs text-whisper tabular-nums">{age}</span>}
      </div>
      <p className="m-0 min-w-0 text-sm leading-snug text-pretty text-ink line-clamp-2 [overflow-wrap:anywhere]">
        {ask.text}
      </p>
    </motion.button>
  );
}
