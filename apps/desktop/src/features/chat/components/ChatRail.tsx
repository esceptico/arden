import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";
import { useReducedMotion } from "motion/react";
import { CONVERSATION_RAIL } from "@/lib/tokens/motion";

type RailAnchor = {
  key: number;
  node: HTMLElement;
};

function anchorLabel(anchor: HTMLElement, index: number, total: number): string {
  const label = anchor.dataset.chatRailLabel ?? anchor.textContent;
  const normalized = label?.trim().replace(/\s+/g, " ");
  return normalized || `Conversation position ${index + 1} of ${total}`;
}

/**
 * Conversation outline with the mockup's physical proximity field. Pointer
 * travel is written directly to compositor-friendly transforms in one RAF; React
 * remains responsible only for scroll-spy state and accessible labels.
 */
export function ChatRail({
  turnIds,
  scrollRef,
}: {
  turnIds: string[];
  scrollRef: { current: HTMLElement | null };
}) {
  const reducedMotion = useReducedMotion() ?? false;
  const [anchors, setAnchors] = useState<RailAnchor[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);
  const [visibleKey, setVisibleKey] = useState("");
  const activeRef = useRef<HTMLButtonElement | null>(null);
  const bandRef = useRef<HTMLDivElement | null>(null);
  const labelRef = useRef<HTMLDivElement | null>(null);
  const markRefs = useRef<Array<HTMLSpanElement | null>>([]);
  const engagedRef = useRef(false);
  const labelOnRef = useRef(false);
  const activeIndexRef = useRef(0);
  const anchorKeysRef = useRef(new WeakMap<HTMLElement, number>());
  const nextAnchorKeyRef = useRef(1);

  useLayoutEffect(() => {
    const root = scrollRef.current;
    if (!root) return;
    let raf = 0;
    const sync = () => {
      raf = 0;
      const nodes = Array.from(
        root.querySelectorAll<HTMLElement>("[data-chat-rail-anchor]"),
      ).filter((node) => node.getClientRects().length > 0);
      setAnchors((current) => {
        if (
          current.length === nodes.length
          && current.every((anchor, index) => anchor.node === nodes[index])
        ) {
          return current;
        }
        return nodes.map((node) => {
          let key = anchorKeysRef.current.get(node);
          if (key == null) {
            key = nextAnchorKeyRef.current;
            nextAnchorKeyRef.current += 1;
            anchorKeysRef.current.set(node, key);
          }
          return { key, node };
        });
      });
    };
    const schedule = () => {
      if (!raf) raf = requestAnimationFrame(sync);
    };
    const observer = new MutationObserver(schedule);
    observer.observe(root, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["aria-expanded", "class", "hidden"],
    });
    sync();
    return () => {
      observer.disconnect();
      if (raf) cancelAnimationFrame(raf);
    };
  }, [scrollRef, turnIds]);

  useEffect(() => {
    const root = scrollRef.current;
    if (!root || anchors.length === 0) return;
    let raf = 0;
    const update = () => {
      raf = 0;
      const rootRect = root.getBoundingClientRect();
      const readLine = rootRect.top + CONVERSATION_RAIL.readLine;
      let active = 0;
      const visible: number[] = [];
      anchors.forEach((anchor, index) => {
        const rect = anchor.node.getBoundingClientRect();
        if (rect.top <= readLine) active = index;
        if (rect.bottom > rootRect.top && rect.top < rootRect.bottom) visible.push(index);
      });
      if (
        root.scrollHeight - root.clientHeight - root.scrollTop
        < CONVERSATION_RAIL.bottomThreshold
      ) {
        active = anchors.length - 1;
      }
      setActiveIndex(active);
      setVisibleKey(visible.join("\n"));
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(update);
    };
    update();
    root.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      root.removeEventListener("scroll", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [anchors, scrollRef]);

  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "nearest" });
  }, [activeIndex]);

  useEffect(() => {
    markRefs.current.length = anchors.length;
  }, [anchors.length]);

  const visibleSet = useMemo(
    () => new Set(visibleKey.split("\n").filter(Boolean).map(Number)),
    [visibleKey],
  );
  activeIndexRef.current = activeIndex;

  const scrollTo = (index: number) => {
    anchors[index]?.node.scrollIntoView({
      block: "start",
      behavior: reducedMotion ? "auto" : "smooth",
    });
  };

  const restField = () => {
    engagedRef.current = false;
    labelOnRef.current = false;
    markRefs.current.forEach((mark) => {
      if (!mark) return;
      mark.style.removeProperty("transition");
      mark.style.removeProperty("transform");
    });
    const label = labelRef.current;
    if (label) {
      label.style.opacity = "0";
      label.style.filter = `blur(${CONVERSATION_RAIL.labelBlur}px)`;
    }
  };

  const showDirectLabel = (index: number, target: HTMLButtonElement) => {
    const band = bandRef.current;
    const label = labelRef.current;
    if (!band || !label) return;
    const bandRect = band.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    const anchor = anchors[index]?.node;
    label.textContent = anchor
      ? anchorLabel(anchor, index, anchors.length)
      : "Conversation position";
    label.style.setProperty(
      "--conversation-rail-label-y",
      `${targetRect.top + targetRect.height / 2 - bandRect.top}px`,
    );
    label.style.opacity = "1";
    label.style.filter = "blur(0px)";
    labelOnRef.current = true;
  };

  useEffect(() => {
    if (!engagedRef.current) restField();
  }, [activeIndex]);

  useEffect(() => {
    let pointerRaf = 0;
    const smoothstep = (value: number) => {
      const unit = Math.max(0, Math.min(1, value));
      return unit * unit * (3 - 2 * unit);
    };
    const updateField = (cursorX: number, cursorY: number) => {
      const band = bandRef.current;
      const label = labelRef.current;
      const root = scrollRef.current;
      const marks = markRefs.current;
      if (!band || !label || !root || marks.length === 0) return;
      const bandRect = band.getBoundingClientRect();
      const lane = root.querySelector<HTMLElement>(".board-chat__lane");
      const contentLeft = lane?.getBoundingClientRect().left ?? Number.POSITIVE_INFINITY;
      const limit = Math.min(bandRect.left + CONVERSATION_RAIL.maxDx, contentLeft);
      const xStart = bandRect.left - CONVERSATION_RAIL.pointerLead;
      const yStart = bandRect.top - CONVERSATION_RAIL.maxDy;
      const yEnd = bandRect.bottom + CONVERSATION_RAIL.maxDy;
      if (
        cursorX < xStart
        || cursorX > limit
        || cursorY < yStart
        || cursorY > yEnd
      ) {
        restField();
        return;
      }

      const envX = cursorX > limit - CONVERSATION_RAIL.fadeX
        ? smoothstep((limit - cursorX) / CONVERSATION_RAIL.fadeX)
        : 1;
      const envY = cursorY < bandRect.top
        ? smoothstep((cursorY - yStart) / CONVERSATION_RAIL.maxDy)
        : cursorY > bandRect.bottom
          ? smoothstep((yEnd - cursorY) / CONVERSATION_RAIL.maxDy)
          : 1;
      const envelope = envX * envY;
      let nearest = 0;
      let nearestDistance = Number.POSITIVE_INFINITY;
      let nearestStrength = 0;

      marks.forEach((mark, index) => {
        if (!mark) return;
        const rect = mark.getBoundingClientRect();
        const dy = cursorY - (rect.top + rect.height / 2);
        const dx = Math.max(0, cursorX - (rect.left + CONVERSATION_RAIL.fullX));
        const field = envelope * Math.exp(
          -(dy * dy) / (2 * CONVERSATION_RAIL.sigmaY ** 2)
          - (dx * dx) / (2 * CONVERSATION_RAIL.sigmaX ** 2),
        );
        const base = index === activeIndexRef.current
          ? CONVERSATION_RAIL.activeScale
          : 1;
        mark.style.transition = "none";
        mark.style.transform = `scaleX(${base + (CONVERSATION_RAIL.hoverScale - base) * field})`;
        if (Math.abs(dy) < nearestDistance) {
          nearestDistance = Math.abs(dy);
          nearest = index;
          nearestStrength = field;
        }
      });

      engagedRef.current = true;
      const show = labelOnRef.current
        ? nearestStrength > CONVERSATION_RAIL.labelOff
        : nearestStrength > CONVERSATION_RAIL.labelOn;
      labelOnRef.current = show;
      if (!show) {
        label.style.opacity = "0";
        label.style.filter = `blur(${CONVERSATION_RAIL.labelBlur}px)`;
        return;
      }

      const mark = marks[nearest];
      const tick = mark?.parentElement;
      if (!tick) return;
      const tickRect = tick.getBoundingClientRect();
      const anchor = anchors[nearest]?.node;
      label.textContent = anchor
        ? anchorLabel(anchor, nearest, anchors.length)
        : "Conversation position";
      label.style.setProperty(
        "--conversation-rail-label-y",
        `${tickRect.top + tickRect.height / 2 - bandRect.top}px`,
      );
      label.style.opacity = "1";
      label.style.filter = "blur(0px)";
    };
    const onPointerMove = (event: PointerEvent) => {
      if (pointerRaf) cancelAnimationFrame(pointerRaf);
      pointerRaf = requestAnimationFrame(() => {
        pointerRaf = 0;
        updateField(event.clientX, event.clientY);
      });
    };
    document.addEventListener("pointermove", onPointerMove, { passive: true });
    return () => {
      document.removeEventListener("pointermove", onPointerMove);
      if (pointerRaf) cancelAnimationFrame(pointerRaf);
    };
  }, [anchors, scrollRef]);

  if (anchors.length < 2) return null;

  return (
    <nav
      aria-label="Conversation"
      className="board-chat-rail scroll-fade absolute z-[var(--z-raised)] hidden @[55rem]:flex flex-col overflow-y-auto overflow-x-hidden pointer-events-none [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
    >
      <div
        ref={bandRef}
        className="board-chat-rail__band relative my-auto flex shrink-0 flex-col items-start py-1"
      >
        {anchors.map((anchor, index) => {
          const active = index === activeIndex;
          const visible = visibleSet.has(index);
          return (
            <button
              key={anchor.key}
              ref={active ? activeRef : undefined}
              type="button"
              onClick={() => scrollTo(index)}
              onFocus={(event) => showDirectLabel(index, event.currentTarget)}
              onBlur={restField}
              aria-current={active ? "true" : undefined}
              aria-label={anchorLabel(anchor.node, index, anchors.length)}
              className={clsx(
                "board-chat-rail__tick pointer-events-auto relative flex h-[9px] scroll-mt-[52px] scroll-mb-[30px] items-center after:absolute after:content-[''] after:-inset-y-[7px] after:-left-2 after:-right-8",
                active && "is-active",
                visible && "is-visible",
              )}
            >
              <span
                ref={(node) => {
                  markRefs.current[index] = node;
                }}
                aria-hidden
                className="board-chat-rail__mark"
              />
            </button>
          );
        })}
        <div
          ref={labelRef}
          aria-hidden="true"
          className="conversation-rail-label board-chat-rail__label"
        />
      </div>
    </nav>
  );
}
