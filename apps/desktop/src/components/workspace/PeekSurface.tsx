import {
  AnimatePresence,
  motion,
  useIsPresent,
  useReducedMotionConfig,
} from "motion/react";
import {
  useEffect,
  useLayoutEffect,
  useRef,
  type CSSProperties,
  type ReactNode,
} from "react";
import { useOverlayLayer } from "@/lib/overlayStack";
import {
  DISTANCE,
  MOTION,
  PEEK_ENTER_TRANSITION,
  PEEK_EXIT_TRANSITION,
  POSE_PEEK_IN,
  POSE_PEEK_OUT,
  POSE_PEEK_VISIBLE,
} from "@/lib/tokens/motion";

const FOCUSABLE = [
  "[data-peek-close]",
  "[role=tab]",
  "button:not(:disabled)",
  "a[href]",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function isRestorable(target: HTMLElement | null): target is HTMLElement {
  return Boolean(
    target &&
    target.isConnected &&
    !target.hasAttribute("disabled") &&
    !target.closest("[inert]"),
  );
}

type PeekSurfaceProps = {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  ariaLabel?: string;
  ariaLabelledBy?: string;
  className?: string;
  /** Overlay-stack name, used for escape and transient-layer ordering. */
  layer?: string;
  disableEscape?: boolean;
  /** A peek is nonblocking by default; sheets opt in to blocking behavior. */
  blocksInput?: boolean;
  /** Nonblocking command peeks keep the source focus unless this is requested. */
  focusOnOpen?: boolean;
  focusSelector?: string;
  /** Inspector peeks can mirror the mockup's outside-pointer dismissal without
   * teaching every feature its own document listener. */
  closeOnOutsidePointerDown?: boolean;
  /** The shared peek lifecycle has named directions for the board's helper
   * and settings sheet surfaces. Consumers select a role instead of supplying
   * ad-hoc offsets or durations. */
  motionPreset?: "peek" | "peek-down";
  /** Portaled child controls (for example a Select listbox) remain inside the
   * peek interaction even though they are not DOM descendants. */
  outsidePointerDownIgnoreSelector?: string;
  style?: CSSProperties;
  dataMode?: string;
};

/**
 * One interruptible floating surface lifecycle.
 *
 * The child remains registered with the overlay stack while its exit is in
 * flight. That keeps a blocking consumer inert until it is actually gone and
 * makes a close → open reversal reuse the same Motion surface rather than
 * briefly mounting two copies. A closing peek is immediately inert and hidden
 * from assistive technology while its visual exit completes.
 */
export function PeekSurface({
  open,
  onClose,
  children,
  ariaLabel,
  ariaLabelledBy,
  className,
  layer = "peek",
  disableEscape = false,
  blocksInput = false,
  focusOnOpen = false,
  focusSelector = FOCUSABLE,
  closeOnOutsidePointerDown = false,
  motionPreset = "peek",
  outsidePointerDownIgnoreSelector,
  style,
  dataMode,
}: PeekSurfaceProps) {
  const previousOpen = useRef(false);
  const sessionActive = useRef(false);
  const restoreTarget = useRef<HTMLElement | null>(null);
  const focusedInside = useRef(false);
  const latestOpen = useRef(open);
  const restoreFrame = useRef<number | null>(null);
  latestOpen.current = open;

  // Capture before an optional focus handoff. If the peek never takes focus,
  // closing it also leaves the user's current focus alone.
  useLayoutEffect(() => {
    if (open && !previousOpen.current) {
      if (restoreFrame.current !== null) {
        cancelAnimationFrame(restoreFrame.current);
        restoreFrame.current = null;
      }
      // A reopen while the exit is still retained is one continuous session:
      // keep the original trigger instead of treating the closing content as
      // a new focus source.
      if (!sessionActive.current) {
        const active = document.activeElement;
        restoreTarget.current =
          active instanceof HTMLElement && active !== document.body
            ? active
            : null;
        focusedInside.current = false;
        sessionActive.current = true;
      }
    }
    previousOpen.current = open;
  }, [open]);

  const restoreFocus = () => {
    const target = restoreTarget.current;
    const shouldRestore = focusOnOpen || focusedInside.current;
    sessionActive.current = false;
    restoreTarget.current = null;
    focusedInside.current = false;
    if (!target || !shouldRestore) return;

    // This runs after the layer releases background inertness. Guarding again
    // makes a new open in the same frame cancel stale focus restoration.
    restoreFrame.current = requestAnimationFrame(() => {
      restoreFrame.current = null;
      if (latestOpen.current || !isRestorable(target)) return;
      target.focus({ preventScroll: true });
    });
  };

  useLayoutEffect(
    () => () => {
      if (restoreFrame.current !== null)
        cancelAnimationFrame(restoreFrame.current);
    },
    [],
  );

  return (
    <AnimatePresence>
      {open && (
        <PeekSurfaceLayer
          ariaLabel={ariaLabel}
          ariaLabelledBy={ariaLabelledBy}
          blocksInput={blocksInput}
          className={className}
          disableEscape={disableEscape}
          focusOnOpen={focusOnOpen}
          focusSelector={focusSelector}
          closeOnOutsidePointerDown={closeOnOutsidePointerDown}
          motionPreset={motionPreset}
          outsidePointerDownIgnoreSelector={outsidePointerDownIgnoreSelector}
          layer={layer}
          style={style}
          dataMode={dataMode}
          onClose={onClose}
          onFocusInside={() => {
            focusedInside.current = true;
          }}
          onExited={restoreFocus}
        >
          {children}
        </PeekSurfaceLayer>
      )}
    </AnimatePresence>
  );
}

function PeekSurfaceLayer({
  ariaLabel,
  ariaLabelledBy,
  blocksInput,
  children,
  className,
  disableEscape,
  focusOnOpen,
  focusSelector = FOCUSABLE,
  closeOnOutsidePointerDown,
  motionPreset = "peek",
  outsidePointerDownIgnoreSelector,
  layer,
  style,
  dataMode,
  onClose,
  onExited,
  onFocusInside,
}: Omit<PeekSurfaceProps, "open"> & {
  onFocusInside: () => void;
  onExited: () => void;
}) {
  const ref = useRef<HTMLElement>(null);
  const isPresent = useIsPresent();
  const reducedMotion = useReducedMotionConfig() ?? false;
  const hasFocused = useRef(false);
  const onExitedRef = useRef(onExited);
  const isPresentRef = useRef(isPresent);
  onExitedRef.current = onExited;
  isPresentRef.current = isPresent;

  // Keep this entry alive through AnimatePresence's exit. In particular, a
  // blocking future consumer must not release the underlying app early.
  useOverlayLayer(ref, true, onClose, disableEscape, blocksInput);

  // This effect is declared after useOverlayLayer, so the real exit cleanup
  // runs after the stack has released any inert background. `isPresent` also
  // avoids treating React Strict Mode's effect probe as a visual close.
  useEffect(
    () => () => {
      if (!isPresentRef.current) onExitedRef.current();
    },
    [],
  );

  useLayoutEffect(() => {
    if (!isPresent || !focusOnOpen || hasFocused.current) return;
    const node = ref.current;
    if (!node) return;
    const frame = requestAnimationFrame(() => {
      if (!isPresent) return;
      hasFocused.current = true;
      const target = node.querySelector<HTMLElement>(focusSelector);
      (target instanceof HTMLElement ? target : node).focus({
        preventScroll: true,
      });
    });
    return () => cancelAnimationFrame(frame);
  }, [focusOnOpen, focusSelector, isPresent]);

  // A Peek is normally nonblocking, but inspector-style peeks in the mockups
  // dismiss when the user resumes work elsewhere. Keep the boundary here so a
  // portaled Select can opt out through one explicit selector rather than each
  // feature growing its own document-level listener.
  useEffect(() => {
    if (!closeOnOutsidePointerDown || !isPresent) return;
    const onPointerDown = (event: PointerEvent) => {
      const surface = ref.current;
      if (!surface) return;
      const path = event.composedPath();
      if (path.includes(surface)) return;
      if (
        outsidePointerDownIgnoreSelector &&
        path.some(
          (node) =>
            node instanceof Element && node.matches(outsidePointerDownIgnoreSelector),
        )
      ) {
        return;
      }
      onClose();
    };
    window.addEventListener("pointerdown", onPointerDown);
    return () => window.removeEventListener("pointerdown", onPointerDown);
  }, [closeOnOutsidePointerDown, isPresent, onClose, outsidePointerDownIgnoreSelector]);

  const isDownwardPeek = motionPreset === "peek-down";
  const hidden = reducedMotion
    ? { opacity: 0 }
    : isDownwardPeek
        ? { opacity: 0, y: DISTANCE.peekEnter }
        : POSE_PEEK_IN;
  const visible = reducedMotion
    ? { opacity: 1 }
    : isDownwardPeek
      ? { opacity: 1, y: 0 }
      : POSE_PEEK_VISIBLE;
  const exit = reducedMotion
    ? { opacity: 0, transition: { duration: MOTION.reduced } }
    : isDownwardPeek
        ? { opacity: 0, y: DISTANCE.peekExit, transition: PEEK_EXIT_TRANSITION }
        : { ...POSE_PEEK_OUT, transition: PEEK_EXIT_TRANSITION };

  return (
    <motion.aside
      ref={ref}
      data-overlay-layer={layer}
      data-peek-state={isPresent ? "open" : "closing"}
      data-mode={dataMode}
      role="dialog"
      aria-modal={blocksInput ? "true" : "false"}
      aria-label={ariaLabel}
      aria-labelledby={ariaLabelledBy}
      aria-hidden={isPresent ? "false" : "true"}
      inert={isPresent ? undefined : true}
      tabIndex={-1}
      initial={hidden}
      animate={visible}
      exit={exit}
      transition={
        reducedMotion
          ? { duration: MOTION.reduced }
          : PEEK_ENTER_TRANSITION
      }
      onFocusCapture={onFocusInside}
      style={{
        ...style,
        pointerEvents: isPresent ? undefined : "none",
        willChange: reducedMotion ? undefined : "opacity, transform",
      }}
      className={className}
    >
      {children}
    </motion.aside>
  );
}
