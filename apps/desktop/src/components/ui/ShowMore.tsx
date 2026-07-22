import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { motion } from "motion/react";
import { ChevronDown } from "@/components/icons";
import clsx from "clsx";
import { EASE_EMPHASIZED, MOTION } from "@/lib/tokens/motion";

interface ShowMoreProps {
  children: ReactNode;
  /** Collapsed height in px. Content taller than this gets the toggle. */
  collapsedHeight?: number;
  moreLabel?: string;
  lessLabel?: string;
  className?: string;
}

/**
 * Clamp long content to `collapsedHeight` with a bottom fade-mask and a
 * Show more / Show less toggle. The height tween animates to a *measured*
 * px target (bounded — a ResizeObserver keeps it current), so this stays
 * smooth where the grid-rows `Collapse` primitive would jank on unbounded
 * content. Renders children plain (no toggle, no mask) when they already
 * fit.
 */
export function ShowMore({
  children,
  collapsedHeight = 120,
  moreLabel = "Show more",
  lessLabel = "Show less",
  className,
}: ShowMoreProps) {
  const innerRef = useRef<HTMLDivElement>(null);
  const [full, setFull] = useState(0);
  const [expanded, setExpanded] = useState(false);
  // Suppress the height transition until after the first paint, so the
  // initial measure-then-clamp doesn't animate the content open→shut.
  const canAnimate = useRef(false);

  useLayoutEffect(() => {
    const el = innerRef.current;
    if (!el) return;
    const measure = () => setFull(el.scrollHeight);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const id = requestAnimationFrame(() => {
      canAnimate.current = true;
    });
    return () => cancelAnimationFrame(id);
  }, []);

  const overflows = full > collapsedHeight + 1;
  const collapsed = overflows && !expanded;

  return (
    <div className={className}>
      <motion.div
        className="overflow-hidden"
        initial={false}
        animate={{ height: overflows ? (expanded ? full : collapsedHeight) : "auto" }}
        transition={{ duration: canAnimate.current ? MOTION.panel : 0, ease: EASE_EMPHASIZED }}
        style={{
          // Fade the clipped edge only while collapsed; the toggle sits below.
          maskImage: collapsed ? "linear-gradient(to bottom, #000 62%, transparent)" : undefined,
          WebkitMaskImage: collapsed
            ? "linear-gradient(to bottom, #000 62%, transparent)"
            : undefined,
        }}
      >
        <div ref={innerRef}>{children}</div>
      </motion.div>
      {overflows && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-muted transition-[color,scale] duration-check ease-out hover:text-ink active:scale-[0.97]"
        >
          {expanded ? lessLabel : moreLabel}
          <ChevronDown
            size={13}
            className={clsx("transition-transform duration-check ease-out", expanded && "rotate-180")}
          />
        </button>
      )}
    </div>
  );
}
