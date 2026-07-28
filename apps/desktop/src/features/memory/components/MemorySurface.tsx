import { useRef } from "react";
import { motion, useReducedMotion } from "motion/react";
import { useStore } from "@/stores";
import { useFocusTrap } from "@/lib/hooks";
import { useOverlayLayer } from "@/lib/overlayStack";
import {
  POSE_SHEET_IN,
  POSE_SHEET_OUT,
  POSE_SHEET_VISIBLE,
  MOTION,
  SHEET_ENTER_TRANSITION,
  SHEET_EXIT_TRANSITION,
} from "@/lib/tokens/motion";
import { MemoryPane } from "@/features/memory/components/MemoryPane";
import { PageEntrance } from "@/components/workspace/PageEntrance";
import { ShellBackButton } from "@/components/workspace/ShellBackButton";
import { goToNewSessionHome } from "@/actions/sessions";
import "@/design/memory.css";

/** Memory as a full-window vault view: the memory rail is the only left
 *  column, layered above the app shell and its fixed sidebar toggles. Esc
 *  closes; overlays inside (quick switcher) stop propagation first. The
 *  header bar lives in ArtifactMemoryView so breadcrumb and note actions
 *  share one row. */
export function MemorySurface() {
  const close = useStore((s) => s.closeMemory);
  const ref = useRef<HTMLDivElement>(null);
  const reducedMotion = useReducedMotion() ?? false;
  useOverlayLayer(ref, true, close);
  useFocusTrap(ref, true);

  return (
    <motion.div
      ref={ref}
      data-overlay-layer="memory"
      role="dialog"
      aria-modal="true"
      aria-label="Memory"
      tabIndex={-1}
      initial={reducedMotion ? { opacity: 0 } : POSE_SHEET_IN}
      animate={reducedMotion ? { opacity: 1 } : POSE_SHEET_VISIBLE}
      exit={
        reducedMotion
          ? { opacity: 0, transition: { duration: MOTION.reduced } }
          : { ...POSE_SHEET_OUT, transition: SHEET_EXIT_TRANSITION }
      }
      transition={reducedMotion ? { duration: MOTION.reduced } : SHEET_ENTER_TRANSITION}
      className="memory-surface absolute inset-0 z-[var(--z-takeover)] flex flex-col bg-bg-main"
    >
      <ShellBackButton
        onClick={() => {
          close();
          goToNewSessionHome();
        }}
      />
      <PageEntrance routeKey="memory" className="contents">
        <MemoryPane />
      </PageEntrance>
    </motion.div>
  );
}
