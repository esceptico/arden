import { AnimatePresence, motion } from "motion/react";
import { Check, CircleDashed } from "lucide-react";
import type { AreaBriefItem, AreasBrief } from "@/api/areas";
import { useStore } from "@/stores";
import { FocusRow } from "@/features/home/components/FocusRow";
import { RISE_IN, RISE_SETTLED, ROW_EXIT, SPRING_ROW_ENTRY, MOTION, EASE_OUT } from "@/lib/tokens/motion";

function BriefRow({ item, done }: { item: AreaBriefItem; done?: boolean }) {
  const openArea = useStore((state) => state.openArea);
  return (
    <motion.button
      layout
      type="button"
      data-area-id={item.area_id}
      onClick={() => openArea(item.area_id)}
      initial={RISE_IN}
      animate={RISE_SETTLED}
      exit={{ ...ROW_EXIT, transition: { duration: MOTION.row, ease: EASE_OUT } }}
      transition={SPRING_ROW_ENTRY}
      className="app-row group/row flex w-full min-w-0 items-start gap-3 rounded-xl bg-surface-soft px-4 py-3 text-left focus-visible:shadow-[0_0_0_2px_var(--color-accent-soft)] focus-visible:outline-none"
    >
      <span className={done ? "mt-0.5 text-accent" : "mt-0.5 text-faint"} aria-hidden>
        {done ? <Check size={14} /> : <CircleDashed size={14} />}
      </span>
      <span className="grid min-w-0 flex-1 gap-0.5">
        <span className="flex min-w-0 items-center gap-2">
          <span className="min-w-0 truncate text-xs font-medium text-ink-soft group-hover/row:text-ink">{item.area_title}</span>
          {item.outcome_title && <span className="min-w-0 truncate text-2xs text-faint">{item.outcome_title}</span>}
        </span>
        <span className="min-w-0 text-sm leading-snug text-ink line-clamp-2 [overflow-wrap:anywhere]">{item.text}</span>
      </span>
    </motion.button>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="grid gap-1.5">
      <h3 className="m-0 px-1 text-2xs font-semibold uppercase tracking-wide text-faint">{title}</h3>
      <div className="grid gap-1.5">{children}</div>
    </section>
  );
}

export function WorkBrief({ brief }: { brief: AreasBrief }) {
  return (
    <div className="grid gap-4">
      <AnimatePresence initial={false}>
        {brief.done.length > 0 && (
          <Section key="done" title="Done for you">
            {brief.done.map((item) => <BriefRow key={`${item.area_id}:${item.stable_key}`} item={item} done />)}
          </Section>
        )}
        {brief.in_progress.length > 0 && (
          <Section key="in-progress" title="In progress">
            {brief.in_progress.map((item) => <BriefRow key={`${item.area_id}:${item.stable_key}`} item={item} />)}
          </Section>
        )}
        {brief.needs_you.length > 0 && (
          <Section key="needs-you" title="Needs you">
            {brief.needs_you.map((ask) => (
              <FocusRow key={ask.id} ask={ask} areaTitle={ask.area_title ?? ask.area_key} />
            ))}
          </Section>
        )}
      </AnimatePresence>
      <p className="m-0 px-1 text-2xs text-whisper select-none">That’s it for today.</p>
    </div>
  );
}
