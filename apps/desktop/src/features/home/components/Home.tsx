import { useMemo } from "react";
import { motion } from "motion/react";
import { Settings, Sparkles } from "lucide-react";
import type { AreaSummary, AreasBrief } from "@/api/areas";
import type { Automation } from "@/api/types";
import { useStore } from "@/stores";
import { formatRelativePast } from "@/lib/format";
import { useAreasData } from "@/features/home/hooks/useAreasData";
import { HeroInput } from "@/features/home/components/HeroInput";
import { WorkBrief } from "@/features/home/components/WorkBrief";
import { AreasStrip } from "@/features/home/components/AreasStrip";
import { ScrollFadeTop, ScrollFadeBottom } from "@/components/ui/ScrollBlur";
import { Button } from "@/components/ui/Button";
import { ICON } from "@/lib/icons";
import { RISE_IN, RISE_SETTLED, DISSOLVE_OUT, withExit, EXIT_ROW, MOTION, EASE_DECELERATE, originFromEvent } from "@/lib/tokens/motion";

const DATE_FORMAT: Intl.DateTimeFormatOptions = {
  weekday: "long",
  month: "long",
  day: "numeric",
};

// Stable references for "not loaded yet" fallbacks — see HeroInput's
// NO_AREAS/NO_AUTOMATIONS for why an inline `?? []` is unsafe with zustand.
const NO_AREAS: AreaSummary[] = [];
const NO_BRIEF: AreasBrief = { done: [], in_progress: [], needs_you: [] };

function greeting(focusCount: number): string {
  if (focusCount === 0) return "All clear.";
  if (focusCount === 1) return "One thing needs you.";
  return `${focusCount} things need you.`;
}

/** The standing agents made legible: how many are watching, and when one
 *  last swept. Area agents are automations keyed `area:{key}`; the line
 *  reassures the focus set is a fresh read, not a stale to-do list. */
function agentWatchLine(automations: Automation[] | null): string | null {
  const agents = (automations ?? []).filter((a) => a.task_id.startsWith("area:"));
  if (agents.length === 0) return null;
  const running = agents.filter((a) => a.running_since != null).length;
  const runs = agents
    .map((a) => a.last_run_at)
    .filter((t): t is string => !!t)
    .sort();
  const noun = agents.length === 1 ? "agent" : "agents";
  if (running > 0) return `${agents.length} ${noun} watching · ${running} running now`;
  const last = runs.at(-1);
  return last
    ? `${agents.length} ${noun} watching · last swept ${formatRelativePast(last)} ago`
    : `${agents.length} ${noun} watching`;
}

/** Home entrypoint: centered 640px column, nothing else on the screen.
 *  date line → hero input (the composer, promoted) → greeting stating the
 *  focus count → FOCUS rows → AREAS strip. Replaces HomeHero as the empty-
 *  state surface Chat.tsx renders when the current session has no visible
 *  messages and nothing is running. */
export function Home() {
  const { overview } = useAreasData();
  const connected = useStore((s) => s.connected);
  const openSettings = useStore((s) => s.openSettings);
  const automations = useStore((s) => s.automations);
  const brief = overview?.brief ?? NO_BRIEF;
  const areas = overview?.areas ?? NO_AREAS;
  const dateLabel = useMemo(() => new Date().toLocaleDateString(undefined, DATE_FORMAT), []);
  const watchLine = agentWatchLine(automations);

  if (!connected) {
    // Mirrors the retired HomeHero's disconnected state: Home's hero input
    // is a dead end with no server behind it, so a connect CTA replaces it
    // entirely rather than showing an input that can't send.
    return (
      <motion.div
        initial={RISE_IN}
        animate={RISE_SETTLED}
        transition={{ duration: MOTION.trace, ease: EASE_DECELERATE }}
        className="mt-[14vh] mx-auto grid gap-5 justify-items-center text-center"
      >
        <span
          aria-hidden
          className="grid place-items-center w-12 h-12 rounded-2xl bg-accent-soft text-accent-strong"
        >
          <Sparkles size={ICON.HERO} strokeWidth={2} />
        </span>
        <div className="grid gap-1.5 max-w-[420px]">
          <h2 className="m-0 text-2xl font-semibold tracking-[-0.018em] text-ink">Connect to get started</h2>
          <p className="m-0 text-base text-muted leading-snug">Open settings to point ntrp at your server.</p>
        </div>
        <Button
          variant="secondary"
          size="md"
          leadingIcon={Settings}
          onClick={(e) => openSettings(originFromEvent(e.currentTarget), "connection")}
        >
          Open settings
        </Button>
      </motion.div>
    );
  }

  return (
    <motion.div
      initial={RISE_IN}
      animate={RISE_SETTLED}
      exit={withExit(DISSOLVE_OUT, EXIT_ROW)}
      transition={{ duration: MOTION.trace, ease: EASE_DECELERATE }}
      /* absolute inset-0 (not h-full): during the Home↔room crossfade both
         surfaces are mounted — absolute keeps them overlapping instead of
         stacking in flow, and preserves height for the inner h-full column. */
      className="absolute inset-0 overflow-hidden"
    >
      {/* One vertically-centered cluster — Home never scrolls as a whole, and
          it reads as a single composed instrument rather than fragments pinned
          to opposite edges. The focus list is the only shrinkable child: it
          caps at 48vh and scrolls internally when the asks outgrow the space,
          so the composer and the strip always stay in view and balanced. */}
      <div className="mx-auto flex h-full w-[640px] max-w-full flex-col justify-center gap-6 px-4 py-10">
        <div className="grid shrink-0 gap-3">
          <span className="text-2xs font-medium tracking-[0.08em] text-faint uppercase">{dateLabel}</span>
          <HeroInput />
        </div>
        <div className="grid shrink-0 gap-1">
          <h2 className="m-0 text-2xl font-medium tracking-[-0.01em] text-ink text-balance">{greeting(brief.needs_you.length)}</h2>
          {watchLine && <p className="m-0 text-xs text-faint">{watchLine}</p>}
        </div>
        <div className="relative min-h-0 max-h-[48vh] overflow-y-auto overflow-x-hidden scroll-thin">
          <ScrollFadeTop />
          <ScrollFadeBottom />
          <WorkBrief brief={brief} />
        </div>
        <div className="shrink-0">
          <AreasStrip areas={areas} suggested={overview?.suggested} />
        </div>
      </div>
    </motion.div>
  );
}
