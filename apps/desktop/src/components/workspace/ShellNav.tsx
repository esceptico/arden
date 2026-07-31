import { useSyncExternalStore } from "react";
import { motion, useReducedMotion } from "motion/react";
import { ArrowLeft02, ArrowRight02, House } from "@/components/icons";
import { IconButton } from "@/components/ui/IconButton";
import {
  getHistorySnapshot,
  goBack,
  goForward,
  navigateHome,
  readHistoryAvailability,
  subscribeToHistory,
} from "@/actions/navigation";
import { useStore } from "@/stores";
import { ICON } from "@/lib/icons";
import { DISTANCE, EASE_OUT, MOTION } from "@/lib/tokens/motion";

export function useHistoryAvailability() {
  return readHistoryAvailability(
    useSyncExternalStore(subscribeToHistory, getHistorySnapshot, getHistorySnapshot),
  );
}

/** Shell-level route navigation: back, forward, and Home over chats, areas,
 *  Memory and Automations. Settings is a Takeover, not a route, so it never
 *  appears here — see currentDestination.
 *
 *  Home earns its place beside the other two rather than living only in the
 *  sidebar: Memory and Automations hide that rail to show their own, and
 *  stepping back one entry at a time was the only way out of them.
 *
 *  Every control stays mounted and disables where it has nowhere to go. One
 *  that vanished would take its slot with it, shifting the chrome beside it
 *  every time you navigated. */
export function ShellNav() {
  const { canBack, canForward } = useHistoryAvailability();
  const atHome = useStore((s) => (
    !s.memoryOpen && !s.automationsOpen && !s.areas.openAreaKey && s.currentSessionId === null
  ));
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
      <IconButton
        size="xs"
        shape="circle"
        className="shell-control"
        onClick={navigateHome}
        disabled={atHome}
        aria-label="Home"
        title="Home (⇧⌘H)"
      >
        <House size={ICON.MD} />
      </IconButton>
    </motion.div>
  );
}
