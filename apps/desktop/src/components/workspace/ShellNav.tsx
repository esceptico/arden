import { useSyncExternalStore } from "react";
import { motion, useReducedMotion } from "motion/react";
import { ArrowLeft02, ArrowRight02 } from "@/components/icons";
import { IconButton } from "@/components/ui/IconButton";
import {
  getHistorySnapshot,
  goBack,
  goForward,
  readHistoryAvailability,
  subscribeToHistory,
} from "@/actions/navigation";
import { ICON } from "@/lib/icons";
import { DISTANCE, EASE_OUT, MOTION } from "@/lib/tokens/motion";

export function useHistoryAvailability() {
  return readHistoryAvailability(
    useSyncExternalStore(subscribeToHistory, getHistorySnapshot, getHistorySnapshot),
  );
}

/** Shell-level route history: back and forward over Home, chats, areas,
 *  Memory and Automations. Settings is a Takeover, not a route, so it never
 *  appears here — see currentDestination.
 *
 *  Both controls stay mounted and disable at the ends rather than
 *  appearing and disappearing: a control that vanishes takes its slot with
 *  it, and the sidebar toggle beside it would shift every time you navigate. */
export function ShellNav() {
  const { canBack, canForward } = useHistoryAvailability();
  const reducedMotion = useReducedMotion() ?? false;

  return (
    <motion.div
      className="shell-nav"
      initial={reducedMotion ? { opacity: 0 } : { opacity: 0, x: -DISTANCE.subtle }}
      animate={{ opacity: 1, x: 0 }}
      exit={reducedMotion ? { opacity: 0 } : { opacity: 0, x: -DISTANCE.subtle }}
      transition={{ duration: reducedMotion ? MOTION.reduced : MOTION.feedback, ease: EASE_OUT }}
    >
      <IconButton
        size="xs"
        shape="circle"
        className="shell-control"
        onClick={goBack}
        disabled={!canBack}
        aria-label="Back"
        title="Back (⌘[)"
      >
        <ArrowLeft02 size={ICON.MD} />
      </IconButton>
      <IconButton
        size="xs"
        shape="circle"
        className="shell-control"
        onClick={goForward}
        disabled={!canForward}
        aria-label="Forward"
        title="Forward (⌘])"
      >
        <ArrowRight02 size={ICON.MD} />
      </IconButton>
    </motion.div>
  );
}
