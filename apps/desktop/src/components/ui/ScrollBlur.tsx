import { useEffect, useRef, useState, type RefObject } from "react";
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

/**
 * Mask-based scroll-edge fade for surface panels (modals, sidebars, command
 * palette). backdrop-filter breaks inside composited .surface-panel layers;
 * a painted overlay bleeds past rounded panel corners — masking the scroller
 * keeps the fade inside its box.
 */
export function ScrollFadeTop() {
  const sentinelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const scroller = sentinelRef.current?.parentElement;
    if (!scroller) return;

    const onScroll = () => {
      scroller.dataset.scrollFadeTop = scroller.scrollTop > 0 ? "true" : "false";
    };
    onScroll();
    scroller.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      scroller.removeEventListener("scroll", onScroll);
      delete scroller.dataset.scrollFadeTop;
    };
  }, []);

  return <div ref={sentinelRef} aria-hidden className="hidden" />;
}

/**
 * Edge-aware bottom fade — shown only while there IS more content below
 * (overflow and not scrolled to the end), so the mask never softens the
 * last row of a short or fully-scrolled list. Unlike the top edge, the
 * bottom state changes without scroll events (async list loads, rows
 * added/removed, pane resize), so it also watches size and mutations.
 */
export function ScrollFadeBottom() {
  const sentinelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const scroller = sentinelRef.current?.parentElement;
    if (!scroller) return;

    const update = () => {
      const overflows = scroller.scrollHeight - scroller.clientHeight > 1;
      const moreBelow = scroller.scrollTop + scroller.clientHeight < scroller.scrollHeight - 1;
      scroller.dataset.scrollFadeBottom = overflows && moreBelow ? "true" : "false";
    };

    let raf = 0;
    const scheduleUpdate = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        update();
      });
    };

    update();
    // Fonts/images settling right after mount can change scrollHeight
    // without any observable mutation on this subtree.
    scheduleUpdate();
    scroller.addEventListener("scroll", update, { passive: true });
    const resizeObserver = new ResizeObserver(scheduleUpdate);
    resizeObserver.observe(scroller);
    const mutationObserver = new MutationObserver(scheduleUpdate);
    mutationObserver.observe(scroller, { childList: true, subtree: true, characterData: true });
    return () => {
      scroller.removeEventListener("scroll", update);
      resizeObserver.disconnect();
      mutationObserver.disconnect();
      if (raf) cancelAnimationFrame(raf);
      delete scroller.dataset.scrollFadeBottom;
    };
  }, []);

  return <div ref={sentinelRef} aria-hidden className="hidden" />;
}
