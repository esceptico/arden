import { useEffect, useRef, type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { PageEntrance } from "@/components/workspace/PageEntrance";
import {
  CONTENT_EXIT_TRANSITION,
  CONTENT_SWAP_VARIANTS,
  MOTION,
} from "@/lib/tokens/motion";

const routeRank = (routeKey: string) =>
  routeKey === "home" ? 0 : routeKey.startsWith("chat:") ? 1 : 2;

const ROUTE_EXIT_VARIANTS = {
  exit: (direction: number) => ({
    ...CONTENT_SWAP_VARIANTS.exit(direction),
    transition: CONTENT_EXIT_TRANSITION,
  }),
};

/**
 * One route stage for Home, Chat, Area rooms, Memory and Automations. The
 * outgoing room completes the canonical 160ms content exit; only then does the
 * incoming room run its shared staged PageEntrance. This avoids
 * double-animating the new page while preserving the mock's sequential handoff.
 *
 * A route must never suspend. mode="wait" holds the outgoing room on screen
 * until the incoming one mounts, and a child that suspends never gets there:
 * the swap deadlocks, leaving the previous page rendered under the new route's
 * data-workspace. Import route components eagerly — lazy() belongs to
 * takeovers, which mount outside this host.
 */
export function WorkspaceRouteHost({
  routeKey,
  children,
}: {
  routeKey: string;
  children: ReactNode;
}) {
  const reducedMotion = useReducedMotion() ?? false;
  const rank = routeRank(routeKey);
  const previousRank = useRef(rank);
  const direction = rank >= previousRank.current ? 1 : -1;
  useEffect(() => {
    previousRank.current = rank;
  }, [rank]);

  return (
    <div className="workspace-route-host absolute inset-0 overflow-hidden">
      <AnimatePresence initial={false} mode="wait" custom={direction}>
        <motion.div
          key={routeKey}
          custom={direction}
          variants={ROUTE_EXIT_VARIANTS}
          initial={false}
          animate={{ opacity: 1, x: 0, filter: "none" }}
          exit={reducedMotion
            ? { opacity: 0, transition: { duration: MOTION.pageReduced } }
            : "exit"}
          className="absolute inset-0 overflow-hidden"
        >
          <PageEntrance routeKey={routeKey} className="absolute inset-0 overflow-hidden">
            {children}
          </PageEntrance>
        </motion.div>
      </AnimatePresence>
    </div>
  );
}
