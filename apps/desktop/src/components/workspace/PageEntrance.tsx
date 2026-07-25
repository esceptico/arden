import { useLayoutEffect, useRef, type ReactNode } from "react";
import { getBoardMotion } from "@/lib/boardMotion";

/**
 * React lifecycle adapter for the canonical pageEntrance controller.
 * Choreography stays in lib/boardMotionEngine.js (see that file's header).
 */
export function PageEntrance({
  routeKey,
  children,
  className = "contents",
}: {
  routeKey: string;
  children: ReactNode;
  className?: string;
}) {
  const boundaryRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const boundary = boundaryRef.current;
    const host = boundary?.querySelector<HTMLElement>("[data-page-enter]");
    if (!boundary || !host) return;

    delete host.dataset.pageEnterBound;
    const run = getBoardMotion()?.pageEntrance.bind(boundary);

    return () => {
      run?.animations.forEach((animation) => animation.cancel());
      // Only time-based animations belong to the entrance. CSS scroll-driven
      // animations (scroll-fade edge masks) share this subtree, and cancelling
      // a CSS animation kills it until its animation-name is re-applied.
      host.getAnimations?.({ subtree: true })
        .filter((animation) => animation.timeline === document.timeline)
        .forEach((animation) => animation.cancel());
      host.querySelectorAll(".dp-page-skeleton").forEach((node) => node.remove());
      delete host.dataset.pageEnterBound;
    };
  }, [routeKey]);

  return (
    <div ref={boundaryRef} className="contents">
      <div data-page-enter className={className}>
        {children}
      </div>
    </div>
  );
}
