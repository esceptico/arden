import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import clsx from "clsx";
import {
  MOTION,
  POPOVER_ENTER_TRANSITION,
  POSE_PORTALED_POPOVER_IN,
  POSE_PORTALED_POPOVER_OUT,
  POSE_PORTALED_POPOVER_VISIBLE,
} from "@/lib/tokens/motion";
import { useHasBlockingOverlay, useOverlayLayer } from "@/lib/overlayStack";
import { useFocusTrap } from "@/lib/hooks";

/** Viewport breathing room shared by every anchored desktop popup. */
export const ANCHORED_POPOVER_EDGE = 16;
/** Trigger-to-panel separation, matching the Board popover geometry. */
export const ANCHORED_POPOVER_GAP = 6;
/** Context menus are intentionally tighter to the viewport than popovers. */
export const CONTEXT_MENU_EDGE = 8;

type Viewport = { width: number; height: number };
type PopoverPosition = { left: number; top: number; transformOrigin: string };

/** Cursor point (context menu) or a trigger element to anchor below (popover). */
export type Anchor = { x: number; y: number } | DOMRect | RefObject<HTMLElement | null>;

interface AnchoredPopoverProps {
  open: boolean;
  onClose: () => void;
  /** Where the panel anchors: a cursor point grows from its clamped corner;
   *  an element rect/ref opens above or below from the trigger's centre. */
  anchor: Anchor;
  /** Panel classes on top of `surface-panel surface-popover` (width, padding). */
  className?: string;
  /** Horizontal edge shared with an element anchor. */
  align?: "start" | "end";
  /** Where an element anchor opens: stacked under (default) or beside it.
   *  `beside` suits a preview raised from a list row, which would otherwise
   *  cover the rows you are reading along. */
  side?: "block" | "inline";
  /** Element-anchor separation in pixels. */
  gap?: number;
  /** "menu" adds role="menu" + roving Arrow/Home/End nav + focus-restore
   *  (WAI-ARIA APG menu pattern); "context-menu" follows the Board cursor
   *  contract; "popover" is a plain portaled panel. */
  variant?: "menu" | "context-menu" | "popover";
  ariaLabel?: string;
  id?: string;
  role?: "dialog" | "tooltip";
  /** Close when any ancestor scrolls (capture phase). Context menus pin to a
   *  cursor point, so a scroll invalidates them; trigger popovers reflow. */
  closeOnScroll?: boolean;
  /** Context menus focus an action only when opened from the keyboard. */
  focusOnOpen?: boolean;
  /** Explicit target to restore on an Escape-dismissed context menu. */
  restoreFocusTarget?: HTMLElement | null;
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

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(value, max));
}

/**
 * Place a trigger popover inside the desktop's 16px viewport gutter. When the
 * preferred below placement would overflow, use the space above the trigger.
 * The origin stays on the trigger's horizontal centre even after edge clamping.
 */
export function placeAnchoredPopover(
  rect: Pick<DOMRect, "left" | "top" | "bottom" | "width">,
  width: number,
  height: number,
  viewport: Viewport,
  {
    align = "start",
    gap = ANCHORED_POPOVER_GAP,
  }: {
    align?: "start" | "end";
    gap?: number;
  } = {},
): PopoverPosition {
  const desiredLeft = align === "end" ? rect.left + rect.width - width : rect.left;
  const left = clamp(
    desiredLeft,
    ANCHORED_POPOVER_EDGE,
    Math.max(ANCHORED_POPOVER_EDGE, viewport.width - width - ANCHORED_POPOVER_EDGE),
  );
  const required = height + gap;
  const above = viewport.height - rect.bottom - ANCHORED_POPOVER_EDGE < required
    && rect.top - ANCHORED_POPOVER_EDGE >= required;
  const desiredTop = above
    ? rect.top - height - gap
    : rect.bottom + gap;
  const top = clamp(
    desiredTop,
    ANCHORED_POPOVER_EDGE,
    Math.max(ANCHORED_POPOVER_EDGE, viewport.height - height - ANCHORED_POPOVER_EDGE),
  );
  const originX = clamp(rect.left + rect.width / 2 - left, 0, width);

  return {
    left,
    top,
    transformOrigin: `${originX}px ${above ? "100%" : "0"}`,
  };
}

/**
 * Beside the trigger instead of under it — for a preview raised from a row in
 * a list, where the stacked placement would cover the rows you are reading
 * along. Prefers the right; flips left when the panel would not clear the
 * viewport there. Vertically it holds the trigger's top edge and clamps.
 */
export function placeSidePopover(
  rect: Pick<DOMRect, "left" | "right" | "top">,
  width: number,
  height: number,
  viewport: Viewport,
  gap: number = ANCHORED_POPOVER_GAP,
): PopoverPosition {
  const roomRight = viewport.width - rect.right - ANCHORED_POPOVER_EDGE;
  const flip = roomRight < width + gap && rect.left - ANCHORED_POPOVER_EDGE >= width + gap;
  const left = clamp(
    flip ? rect.left - width - gap : rect.right + gap,
    ANCHORED_POPOVER_EDGE,
    Math.max(ANCHORED_POPOVER_EDGE, viewport.width - width - ANCHORED_POPOVER_EDGE),
  );
  const top = clamp(
    rect.top,
    ANCHORED_POPOVER_EDGE,
    Math.max(ANCHORED_POPOVER_EDGE, viewport.height - height - ANCHORED_POPOVER_EDGE),
  );
  const originY = clamp(rect.top - top, 0, height);

  return { left, top, transformOrigin: `${flip ? "100%" : "0"} ${originY}px` };
}

function placePointPopover(
  point: { x: number; y: number },
  width: number,
  height: number,
  viewport: Viewport,
): PopoverPosition {
  const left = clamp(
    point.x,
    ANCHORED_POPOVER_EDGE,
    Math.max(ANCHORED_POPOVER_EDGE, viewport.width - width - ANCHORED_POPOVER_EDGE),
  );
  const top = clamp(
    point.y,
    ANCHORED_POPOVER_EDGE,
    Math.max(ANCHORED_POPOVER_EDGE, viewport.height - height - ANCHORED_POPOVER_EDGE),
  );
  const originX = left < point.x ? "right" : "left";
  const originY = top < point.y ? "bottom" : "top";

  return { left, top, transformOrigin: `${originY} ${originX}` };
}

/**
 * Board context menus stay pinned to the invocation point, clamp with an 8px
 * viewport edge, and always grow from their top-left corner. Unlike a trigger
 * popover they never flip or reinterpret their origin at the far edge.
 */
export function placeContextMenu(
  point: { x: number; y: number },
  width: number,
  height: number,
  viewport: Viewport,
): PopoverPosition {
  return {
    left: clamp(
      point.x,
      CONTEXT_MENU_EDGE,
      Math.max(CONTEXT_MENU_EDGE, viewport.width - width - CONTEXT_MENU_EDGE),
    ),
    top: clamp(
      point.y,
      CONTEXT_MENU_EDGE,
      Math.max(CONTEXT_MENU_EDGE, viewport.height - height - CONTEXT_MENU_EDGE),
    ),
    transformOrigin: "top left",
  };
}

/**
 * Portaled popover anchored to a cursor point or a trigger element. Owns the
 * shared machinery: portal to `#app`, the house popover motion panel
 * (`surface-panel surface-popover`, 180ms in/out), the
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
  align = "start",
  side = "block",
  gap = ANCHORED_POPOVER_GAP,
  variant = "popover",
  ariaLabel,
  id,
  role,
  closeOnScroll = false,
  focusOnOpen,
  restoreFocusTarget,
  children,
}: AnchoredPopoverProps) {
  const ref = useRef<HTMLDivElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);
  const restoreFocusOnClose = useRef(true);
  const [pos, setPos] = useState({ left: 0, top: 0, transformOrigin: "top left", ready: false });
  const isContextMenu = variant === "context-menu";
  const isMenu = variant === "menu" || isContextMenu;
  const dismiss = useCallback((restoreFocus = true) => {
    if (isContextMenu) restoreFocusOnClose.current = restoreFocus;
    onClose();
  }, [isContextMenu, onClose]);

  useOverlayLayer(ref, open, () => dismiss(true), false, false);
  useFocusTrap(ref, !isContextMenu && open && role !== "tooltip");
  // Blocking overlays are not the only surfaces that outrank --z-popover: a
  // peek stacks at --z-peek without blocking input, so a listbox opened from
  // inside one would paint behind its own opaque host. What matters is that
  // the anchor lives in a registered overlay layer, not that the layer blocks.
  const nested =
    useHasBlockingOverlay() ||
    (isRefAnchor(anchor) && Boolean(anchor.current?.closest("[data-overlay-layer]")));
  const reducedMotion = useReducedMotion() ?? false;

  // A point anchor can move while the popover stays open (right-clicking a
  // different row without closing first); re-measure when it does.
  const pointKey =
    !(anchor instanceof DOMRect) && !isRefAnchor(anchor) ? `${anchor.x},${anchor.y}` : null;

  // Generic menus use the standard APG focus restoration. Context menus have
  // distinct mock behavior: Escape restores the invoking target, while an
  // outside press/window loss deliberately does not steal focus back.
  useEffect(() => {
    if (!isMenu || isContextMenu || !open) return;
    restoreRef.current = document.activeElement as HTMLElement | null;
    return () => {
      const el = restoreRef.current;
      if (el && document.contains(el)) el.focus();
    };
  }, [isMenu, isContextMenu, open]);

  useLayoutEffect(() => {
    if (!isContextMenu || !open) return;
    restoreRef.current = restoreFocusTarget ?? (document.activeElement as HTMLElement | null);
    restoreFocusOnClose.current = true;
  }, [isContextMenu, open, pointKey, restoreFocusTarget]);

  useEffect(() => {
    if (!isContextMenu || !open) return;
    return () => {
      if (!restoreFocusOnClose.current) return;
      const el = restoreRef.current;
      if (el && document.contains(el)) el.focus();
    };
  }, [isContextMenu, open]);

  // After mount, measure the panel and clamp to the viewport so it never hangs
  // off an edge. offsetWidth/offsetHeight read the layout box, unaffected by
  // the in-flight entrance transform (getBoundingClientRect would read scaled).
  useEffect(() => {
    if (!open) {
      setPos((p) => (p.ready ? { ...p, ready: false } : p));
      return;
    }
    const place = () => {
      const el = ref.current;
      if (!el) return;
      const { point, rect } = resolveRect(anchor);
      const viewport = { width: window.innerWidth, height: window.innerHeight };
      const next = point
        ? isContextMenu
          ? placeContextMenu(point, el.offsetWidth, el.offsetHeight, viewport)
          : placePointPopover(point, el.offsetWidth, el.offsetHeight, viewport)
        : rect
          ? side === "inline"
            ? placeSidePopover(rect, el.offsetWidth, el.offsetHeight, viewport, gap)
            : placeAnchoredPopover(rect, el.offsetWidth, el.offsetHeight, viewport, { align, gap })
          : null;
      if (!next) return;
      setPos((current) => (
        current.ready
        && current.left === next.left
        && current.top === next.top
        && current.transformOrigin === next.transformOrigin
          ? current
          : { ...next, ready: true }
      ));
    };

    place();
    if (!isContextMenu) window.addEventListener("resize", place);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(place);
    if (ref.current) observer?.observe(ref.current);
    return () => {
      if (!isContextMenu) window.removeEventListener("resize", place);
      observer?.disconnect();
    };
  }, [align, anchor, gap, isContextMenu, open, pointKey, side]);

  // Move focus into the menu once positioned so arrow keys work immediately.
  // pointKey: re-focus item 0 when the panel remounts at a new cursor point
  // (right-clicking another row without closing first).
  useEffect(() => {
    const shouldFocus = focusOnOpen ?? (!isContextMenu && isMenu);
    if (!isMenu || !shouldFocus || !open || !pos.ready) return;
    ref.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
  }, [focusOnOpen, isContextMenu, isMenu, open, pos.ready, pointKey]);

  useEffect(() => {
    if (!open) return;
    // Exclude the trigger element (when the anchor is a ref) so its own click
    // toggles instead of closing-then-reopening.
    const triggerEl = isRefAnchor(anchor) ? anchor.current : null;
    const onDown = (e: Event) => {
      const target = e.target as Node;
      if (ref.current?.contains(target)) return;
      if (triggerEl?.contains(target)) return;
      dismiss(!isContextMenu);
    };
    if (isContextMenu) document.addEventListener("pointerdown", onDown, true);
    else document.addEventListener("mousedown", onDown);
    let scroll: (() => void) | null = null;
    if (closeOnScroll) {
      scroll = () => dismiss();
      window.addEventListener("scroll", scroll, true);
    }
    return () => {
      if (isContextMenu) document.removeEventListener("pointerdown", onDown, true);
      else document.removeEventListener("mousedown", onDown);
      if (scroll) window.removeEventListener("scroll", scroll, true);
    };
  }, [open, dismiss, anchor, closeOnScroll, isContextMenu]);

  // The mock dismisses its cursor menu when the window loses its geometry;
  // unlike generic trigger popovers it does not re-anchor on resize.
  useEffect(() => {
    if (!isContextMenu || !open) return;
    const closeWithoutFocusRestore = () => dismiss(false);
    window.addEventListener("blur", closeWithoutFocusRestore);
    window.addEventListener("resize", closeWithoutFocusRestore);
    return () => {
      window.removeEventListener("blur", closeWithoutFocusRestore);
      window.removeEventListener("resize", closeWithoutFocusRestore);
    };
  }, [dismiss, isContextMenu, open]);

  const moveMenuFocus = useCallback((key: string) => {
    const items = Array.from(ref.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
    if (items.length === 0) return false;
    const idx = items.indexOf(document.activeElement as HTMLElement);
    const next =
      key === "Home" ? 0
      : key === "End" ? items.length - 1
      : key === "ArrowDown" ? (idx + 1) % items.length
      : idx <= 0 ? items.length - 1 : idx - 1;
    items[next]?.focus();
    return true;
  }, []);

  // Right-click leaves focus at the invocation target, so these keys must be
  // observed at window level rather than only on the menu element.
  useEffect(() => {
    if (!isContextMenu || !open) return;
    const onContextMenuKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || !["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
      if (moveMenuFocus(event.key)) event.preventDefault();
    };
    window.addEventListener("keydown", onContextMenuKeyDown);
    return () => window.removeEventListener("keydown", onContextMenuKeyDown);
  }, [isContextMenu, moveMenuFocus, open]);

  const onMenuKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (!isMenu) return;
    if (e.key === "Escape") {
      e.preventDefault();
      dismiss(true);
      return;
    }
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
    if (moveMenuFocus(e.key)) e.preventDefault();
  };

  const root = document.querySelector("#app");
  if (!root) return null;

  const hiddenPose = reducedMotion ? { opacity: 0 } : POSE_PORTALED_POPOVER_IN;
  const visiblePose = reducedMotion ? { opacity: 1 } : POSE_PORTALED_POPOVER_VISIBLE;
  const exitPose = reducedMotion
    ? { opacity: 0, transition: { duration: MOTION.reduced } }
    : { ...POSE_PORTALED_POPOVER_OUT, transition: POPOVER_ENTER_TRANSITION };

  return createPortal(
    <AnimatePresence>
      {open && (
        <motion.div
          key={pointKey ?? undefined}
          ref={ref}
          id={id}
          initial={hiddenPose}
          animate={pos.ready ? visiblePose : hiddenPose}
          exit={exitPose}
          transition={reducedMotion ? { duration: MOTION.reduced } : POPOVER_ENTER_TRANSITION}
          className={clsx("surface-panel surface-popover fixed", isContextMenu && "arden-context-menu", className)}
          data-overlay-depth={nested ? "nested" : "page"}
          style={{
            left: pos.left,
            top: pos.top,
            zIndex: isContextMenu || nested ? "var(--z-nested)" : "var(--z-popover)",
            transformOrigin: pos.transformOrigin,
          }}
          onContextMenu={isMenu ? (e) => e.preventDefault() : undefined}
          role={isMenu ? "menu" : role}
          tabIndex={role === "tooltip" ? undefined : -1}
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
