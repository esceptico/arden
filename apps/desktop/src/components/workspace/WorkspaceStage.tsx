import { useLayoutEffect, useRef, type ReactNode } from "react";
import { animate, motion, useMotionValue, useReducedMotion } from "motion/react";
import { FURNITURE_TRANSITION } from "@/lib/tokens/motion";

/**
 * Preserves the reading-lane center while shell furniture docks or leaves.
 * Geometry commits immediately; only this presentation wrapper is translated
 * back to rest, matching the mockup's transitionCentered FLIP controller.
 */
export function WorkspaceStage({
  geometryKey,
  children,
}: {
  geometryKey: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const previousCenter = useRef<number | null>(null);
  const running = useRef<ReturnType<typeof animate> | null>(null);
  const x = useMotionValue(0);
  const reducedMotion = useReducedMotion();

  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;
    // board-chat.js passes `.chat-lane` as transitionCentered's anchor. The
    // workspace remains the presentation layer, but its reading lane supplies
    // the before/after center so docking never snaps the prose.
    const anchor = node.querySelector<HTMLElement>(".board-chat__lane") ?? node;
    const rect = anchor.getBoundingClientRect();
    const nextCenter = rect.left + rect.width / 2;
    const before = previousCenter.current;
    previousCenter.current = nextCenter;
    running.current?.stop();
    if (before === null || reducedMotion) {
      x.set(0);
      return;
    }
    const offset = before - nextCenter;
    if (Math.abs(offset) < 0.5) {
      x.set(0);
      return;
    }
    x.set(offset);
    running.current = animate(x, 0, FURNITURE_TRANSITION);
    return () => running.current?.stop();
  }, [geometryKey, reducedMotion, x]);

  return (
    <motion.div
      ref={ref}
      style={{ x }}
      className="workspace-stage absolute inset-0 overflow-hidden"
    >
      {children}
    </motion.div>
  );
}
