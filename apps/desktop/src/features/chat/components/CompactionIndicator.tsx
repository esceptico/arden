import { AnimatePresence, motion } from "motion/react";
import { Loader2 } from "@/components/icons";
import { useStore } from "@/stores";
import { MOTION, EASE_OUT } from "@/lib/tokens/motion";
import { Marker, MarkerContent, MarkerIcon } from "@/components/ui/Marker";

export function CompactionIndicator() {
  const compacting = useStore((s) => s.compacting);

  return <CompactionIndicatorContent compacting={compacting} />;
}

export function CompactionIndicatorContent({ compacting }: { compacting: boolean }) {
  return (
    <AnimatePresence>
      {compacting ? (
        <motion.div
          key="compacting"
          role="status"
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -2, transition: { duration: MOTION.fast, ease: EASE_OUT } }}
          transition={{ duration: MOTION.palette, ease: EASE_OUT }}
          className="my-1"
        >
          <Marker>
            <MarkerIcon>
              <Loader2 strokeWidth={2} className="animate-spin" />
            </MarkerIcon>
            <MarkerContent>Compacting conversation…</MarkerContent>
          </Marker>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
