import {
  cloneElement,
  isValidElement,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import clsx from "clsx";
import { MOTION } from "@/lib/tokens/motion";
import { calculateTooltipPlacement, type TooltipPlacement, type TooltipSide } from "@/components/ui/tooltipPlacement";

const GAP = 6;
/** Min distance the tooltip should keep from the viewport edge before flipping
 *  to the opposite side. */
const SAFE_MARGIN = 8;
/** Canonical first-hover delay; keyboard focus bypasses it. */
const OPEN_DELAY_MS = MOTION.tooltipDelay * 1_000;
/** Once any tooltip has just been open, the next one within this window opens
 *  instantly — no delay, no entrance animation. Moving across a toolbar then
 *  feels immediate instead of re-paying the 350ms intent delay every hover
 *  (Emil Kowalski's "skip the delay on subsequent hovers"). */
const INSTANT_GRACE_MS = MOTION.tooltipWarm * 1_000;
let warmUntil = 0;

interface TooltipProps {
  label: ReactNode;
  /** A single focusable element. Handlers + ref are merged onto it directly —
   *  no wrapper, so the tooltip never perturbs the trigger's layout. */
  children: ReactElement;
  side?: TooltipSide;
  className?: string;
}

/**
 * Passive hover/focus hint. It deliberately has no entrance transform:
 * tooltips are spatial labels, not panels competing for attention.
 * Clones its child to attach listeners and a measuring ref in place (no
 * wrapper span). Shares HoverPopover's house entrance and portal/re-measure
 * scaffolding. Give the child its own aria-label; the tooltip is supplementary.
 */
export function Tooltip({ label, children, side = "bottom", className }: TooltipProps) {
  const triggerRef = useRef<HTMLElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  const showTimer = useRef<number | null>(null);
  const [open, setOpen] = useState(false);
  const [placement, setPlacement] = useState<TooltipPlacement | null>(null);
  // Portal mounts client-side only — keeps the component SSR/renderToStaticMarkup
  // safe (createPortal + document.body aren't touched until after mount).
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const tipId = useId();

  const clear = useCallback(() => {
    if (showTimer.current !== null) window.clearTimeout(showTimer.current);
    showTimer.current = null;
  }, []);
  const show = useCallback(() => {
    clear();
    setPlacement(null);
    if (performance.now() < warmUntil) {
      setOpen(true);
    } else {
      showTimer.current = window.setTimeout(() => setOpen(true), OPEN_DELAY_MS);
    }
  }, [clear]);
  const hide = useCallback(() => {
    clear();
    setOpen(false);
    if (open) warmUntil = performance.now() + INSTANT_GRACE_MS;
  }, [clear, open]);
  // Cancel any pending show/hide on unmount — a stray hide timer would
  // otherwise fire later and stamp `lastTooltipClosedAt`, making the next
  // unrelated hover open with no delay.
  useLayoutEffect(() => clear, [clear]);

  useLayoutEffect(() => {
    if (!open) return;
    const reposition = () => {
      const t = triggerRef.current;
      const tooltip = tipRef.current?.getBoundingClientRect();
      if (!t || !tooltip) return;
      setPlacement(
        calculateTooltipPlacement({
          preferredSide: side,
          trigger: t.getBoundingClientRect(),
          tooltip,
          viewport: { width: window.innerWidth, height: window.innerHeight },
          gap: GAP,
          safeMargin: SAFE_MARGIN,
        }),
      );
    };
    const dismissOnScroll = () => hide();
    reposition();
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", dismissOnScroll, true);
    const observer = tipRef.current && typeof ResizeObserver !== "undefined"
      ? new ResizeObserver(reposition)
      : null;
    if (tipRef.current) observer?.observe(tipRef.current);
    return () => {
      window.removeEventListener("resize", reposition);
      window.removeEventListener("scroll", dismissOnScroll, true);
      observer?.disconnect();
    };
  }, [hide, open, side]);

  // Merge our listeners + ref onto the child without a wrapper element.
  const childProps = children.props as Record<string, unknown>;
  const compose =
    (orig: unknown, ours: (event: unknown) => void) =>
    (e: unknown) => {
      if (typeof orig === "function") (orig as (ev: unknown) => void)(e);
      ours(e);
  };
  const showFromFocus = (event: unknown) => {
    const trigger = (event as { currentTarget?: unknown }).currentTarget;
    if (!(trigger instanceof HTMLElement) || !trigger.matches(":focus-visible")) return;
    clear();
    setPlacement(null);
    setOpen(true);
  };
  const setRef = (node: HTMLElement | null) => {
    triggerRef.current = node;
    const orig = (childProps as { ref?: unknown }).ref;
    if (typeof orig === "function") (orig as (n: HTMLElement | null) => void)(node);
    else if (orig && typeof orig === "object") (orig as { current: unknown }).current = node;
  };
  const trigger = isValidElement(children)
    ? cloneElement(children as ReactElement<Record<string, unknown>>, {
        ref: setRef,
        onMouseEnter: compose(childProps.onMouseEnter, show),
        onMouseLeave: compose(childProps.onMouseLeave, hide),
        onFocus: compose(childProps.onFocus, showFromFocus),
        onBlur: compose(childProps.onBlur, hide),
        "aria-describedby": open ? tipId : childProps["aria-describedby"],
      })
    : children;

  return (
    <>
      {trigger}
      {mounted &&
        createPortal(
          open ? (
            <div
              ref={tipRef}
              id={tipId}
              role="tooltip"
              style={{
                top: placement?.top ?? 0,
                left: placement?.left ?? 0,
                visibility: placement ? undefined : "hidden",
              }}
              className={clsx("arden-tooltip", className)}
            >
              {label}
            </div>
          ) : null,
          document.body,
        )}
    </>
  );
}
