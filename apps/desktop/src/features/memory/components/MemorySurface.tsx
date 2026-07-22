import { motion } from "motion/react";
import { useStore } from "@/stores";
import { useEscapeKey } from "@/lib/hooks";
import { DISSOLVE_OUT, EASE_DECELERATE, EXIT_ROW, MOTION, RISE_IN, RISE_SETTLED, withExit } from "@/lib/tokens/motion";
import { MemoryPane } from "@/features/memory/components/MemoryPane";

/** Memory as a full-window vault view: the memory rail is the only left
 *  column, layered above the app shell and its fixed sidebar toggles. Esc
 *  closes; overlays inside (quick switcher) stop propagation first. The
 *  header bar lives in ArtifactMemoryView so breadcrumb and note actions
 *  share one row. */
export function MemorySurface() {
  const close = useStore((s) => s.closeMemory);
  useEscapeKey(close, true);

  return (
    <motion.div
      initial={RISE_IN}
      animate={{ ...RISE_SETTLED, transitionEnd: { filter: "none" } }}
      exit={withExit(DISSOLVE_OUT, EXIT_ROW)}
      transition={{ duration: MOTION.trace, ease: EASE_DECELERATE }}
      className="absolute inset-0 z-[var(--z-modal)] flex flex-col bg-bg-main"
    >
      <MemoryPane />
    </motion.div>
  );
}
