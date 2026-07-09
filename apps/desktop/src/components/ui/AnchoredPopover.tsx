import {
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import clsx from "clsx";
import { EXIT_FAST, SPRING_POPOVER } from "@/lib/tokens/motion";

const MARGIN = 8;

/** Cursor point (context menu) or a trigger element to anchor below (popover). */
export type Anchor = { x: number; y: number } | DOMRect | RefObject<HTMLElement | null>;

interface AnchoredPopoverProps {
  open: boolean;
  onClose: () => void;
  /** Where the panel anchors: a cursor point opens at that point and grows
   *  from the clamped corner; an element rect/ref opens below it, right-aligned. */
  anchor: Anchor;
  /** Panel classes on top of `surface-panel surface-popover` (width, padding). */
  className?: string;
  /** "menu" adds role="menu" + roving Arrow/Home/End nav + focus-restore
   *  (WAI-ARIA APG menu pattern); "popover" is a plain portaled panel. */
  variant?: "menu" | "popover";
  ariaLabel?: string;
  /** Close when any ancestor scrolls (capture phase). Context menus pin to a
   *  cursor point, so a scroll invalidates them; trigger popovers reflow. */
  closeOnScroll?: boolean;
  children: ReactNode;
}

function isRefAnchor(anchor: Anchor): anchor is RefObject<HTMLElement | null> {
  return "current" in anchor;
}

function resolveRect(anchor: Anchor): { point: { x: number; y: number } | null; rect: DOMRect | null } {
  if (anchor instanceof DOMRect) return { point: null, rect: anchor };
  if (isRefAnchor(anchor)) {
    const el = anchor.current;
    return { point: null, rect: el ? el.getBoundingClientRect() : null };
  }
  return { point: { x: anchor.x, y: anchor.y }, rect: null };
}

/**
 * Portaled popover anchored to a cursor point or a trigger element. Owns the
 * shared machinery: portal to `#app`, the house popover motion panel
 * (`surface-panel surface-popover`, SPRING_POPOVER in / EXIT_FAST out), the
 * measure-before-paint clamp (`ready` flag avoids a wrong-corner flash), and
 * dismiss on outside-mousedown / Escape / (optionally) scroll. The `menu`
 * variant layers on role="menu", roving-tabindex keyboard nav, and focus
 * restore. Consumers keep only their item content and open-state wiring.
 */
export function AnchoredPopover({
  open,
  onClose,
  anchor,
  className,
  variant = "popover",
  ariaLabel,
  closeOnScroll = false,
  children,
}: AnchoredPopoverProps) {
  const ref = useRef<HTMLDivElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);
  const [pos, setPos] = useState({ left: 0, top: 0, transformOrigin: "top left", ready: false });

  const isMenu = variant === "menu";

  // A point anchor can move while the popover stays open (right-clicking a
  // different row without closing first); re-measure when it does. Rect/ref
  // anchors are measured once per open.
  const pointKey =
    !(anchor instanceof DOMRect) && !isRefAnchor(anchor) ? `${anchor.x},${anchor.y}` : null;

  // Snapshot the element that opened the menu and restore focus to it on
  // close — WAI-ARIA APG menu pattern.
  useEffect(() => {
    if (!isMenu || !open) return;
    restoreRef.current = document.activeElement as HTMLElement | null;
    return () => {
      const el = restoreRef.current;
      if (el && document.contains(el)) el.focus();
    };
  }, [isMenu, open]);

  // After mount, measure the panel and clamp to the viewport so it never hangs
  // off an edge. offsetWidth/offsetHeight read the layout box, unaffected by
  // the in-flight entrance transform (getBoundingClientRect would read scaled).
  useEffect(() => {
    if (!open) {
      setPos((p) => (p.ready ? { ...p, ready: false } : p));
      return;
    }
    const el = ref.current;
    if (!el) return;
    const { point, rect } = resolveRect(anchor);
    const width = el.offsetWidth;
    const height = el.offsetHeight;

    if (point) {
      const left = Math.max(MARGIN, Math.min(point.x, window.innerWidth - width - MARGIN));
      const top = Math.max(MARGIN, Math.min(point.y, window.innerHeight - height - MARGIN));
      // Grow from the clamped corner — if pushed left/up to fit, the cursor
      // sits at the panel's right/bottom edge instead.
      const originX = left < point.x ? "right" : "left";
      const originY = top < point.y ? "bottom" : "top";
      setPos({ left, top, transformOrigin: `${originY} ${originX}`, ready: true });
      return;
    }
    if (rect) {
      const left = Math.max(MARGIN, Math.min(rect.right - width, window.innerWidth - width - MARGIN));
      const top = Math.max(MARGIN, Math.min(rect.bottom + 4, window.innerHeight - height - MARGIN));
      setPos({ left, top, transformOrigin: "top right", ready: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, pointKey]);

  // Move focus into the menu once positioned so arrow keys work immediately.
  // pointKey: re-focus item 0 when the panel remounts at a new cursor point
  // (right-clicking another row without closing first).
  useEffect(() => {
    if (!isMenu || !open || !pos.ready) return;
    ref.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
  }, [isMenu, open, pos.ready, pointKey]);

  useEffect(() => {
    if (!open) return;
    // Exclude the trigger element (when the anchor is a ref) so its own click
    // toggles instead of closing-then-reopening.
    const triggerEl = isRefAnchor(anchor) ? anchor.current : null;
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (ref.current?.contains(target)) return;
      if (triggerEl?.contains(target)) return;
      onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    let scroll: (() => void) | null = null;
    if (closeOnScroll) {
      scroll = () => onClose();
      window.addEventListener("scroll", scroll, true);
    }
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
      if (scroll) window.removeEventListener("scroll", scroll, true);
    };
  }, [open, onClose, anchor, closeOnScroll]);

  const onMenuKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (!isMenu) return;
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
    const items = Array.from(ref.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
    if (items.length === 0) return;
    e.preventDefault();
    const idx = items.indexOf(document.activeElement as HTMLElement);
    const next =
      e.key === "Home" ? 0
      : e.key === "End" ? items.length - 1
      : e.key === "ArrowDown" ? (idx + 1) % items.length
      : idx <= 0 ? items.length - 1 : idx - 1;
    items[next]?.focus();
  };

  const root = document.querySelector("#app");
  if (!root) return null;

  return createPortal(
    <AnimatePresence>
      {open && (
        <motion.div
          key={pointKey ?? undefined}
          ref={ref}
          initial={{ opacity: 0, scale: 0.97, y: -4 }}
          animate={pos.ready ? { opacity: 1, scale: 1, y: 0 } : { opacity: 0, scale: 0.97, y: -4 }}
          exit={{ opacity: 0, scale: 0.97, transition: EXIT_FAST }}
          transition={SPRING_POPOVER}
          className={clsx("surface-panel surface-popover fixed z-[var(--z-popover)]", className)}
          style={{ left: pos.left, top: pos.top, transformOrigin: pos.transformOrigin }}
          onContextMenu={isMenu ? (e) => e.preventDefault() : undefined}
          role={isMenu ? "menu" : undefined}
          aria-label={ariaLabel}
          onKeyDown={isMenu ? onMenuKeyDown : undefined}
        >
          {children}
        </motion.div>
      )}
    </AnimatePresence>,
    root,
  );
}
