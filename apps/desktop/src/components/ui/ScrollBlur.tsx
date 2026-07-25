import { useEffect, useState, type RefObject } from "react";
import { ProgressiveBlur } from "@/components/ui/ProgressiveBlur";

/**
 * Progressive blur for scroll panes without a transformed surface-panel
 * ancestor (main chat). Rendered outside the scrolled content flow so the
 * band stays pinned to the pane top even when the history is short.
 */
export function ScrollBlurTop({ scrollerRef }: { scrollerRef: RefObject<HTMLElement | null> }) {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const scroller = scrollerRef.current;
    if (!scroller) return;

    const onScroll = () => {
      const next = scroller.scrollTop > 0;
      setScrolled((prev) => (prev === next ? prev : next));
    };
    onScroll();
    scroller.addEventListener("scroll", onScroll, { passive: true });
    return () => scroller.removeEventListener("scroll", onScroll);
  }, [scrollerRef]);

  return (
    <div
      aria-hidden
      className="scroll-progressive-blur-top"
      data-scrolled={scrolled ? "true" : "false"}
    >
      <ProgressiveBlur
        className="scroll-progressive-blur-layer"
        direction="top"
      />
    </div>
  );
}
