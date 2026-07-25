import { useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { motion, useReducedMotion } from "motion/react";
import { X } from "@/components/icons";
import {
  EASE_DECELERATE,
  MOTION,
  POSE_SHEET_IN,
  POSE_SHEET_OUT,
  POSE_SHEET_VISIBLE,
  SHEET_ENTER_TRANSITION,
  SHEET_EXIT_TRANSITION,
  modalOriginTransform,
} from "@/lib/tokens/motion";
import { useFocusTrap } from "@/lib/hooks";
import { useOverlayLayer } from "@/lib/overlayStack";
import { useRetainedPresence } from "@/components/workspace/useRetainedPresence";
import { IconButton } from "@/components/ui/IconButton";
import { ICON } from "@/lib/icons";

const BACKDROP_DURATION = MOTION.focus;

export interface PageModalHeader {
  /** Main title — usually a string but any node so callers can include
   *  count badges, icons, etc. */
  title: ReactNode;
  /** Optional second line under the title (small, faint, mono). Used by
   *  MarkdownViewer for source paths. */
  subtitle?: ReactNode;
  /** Extra buttons rendered to the left of the standard close X. */
  actions?: ReactNode;
}

export interface PageModalProps {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  /** When provided, PageModal renders a standard header bar with the title,
   *  optional subtitle, any extra actions, and a close X button. Callers
   *  with non-standard headers (e.g. Settings' sidebar layout) omit this
   *  and compose their own header inside `children`. */
  header?: PageModalHeader;
  /** Tailwind size class string for a centered panel — e.g.
   *  `"w-[min(960px,calc(100vw-80px))] h-[min(680px,calc(100vh-80px))]"`.
   *  Defaults to a 960×680 viewport-aware panel. */
  size?: string;
  /** Tailwind grid-template classes for the panel's internal layout.
   *  Defaults to a header + body split (`grid-rows-[auto_minmax(0,1fr)]`).
   *  Settings overrides this with a sidebar + content split. */
  grid?: string;
  /** When true, Escape no longer closes the modal. Useful when a nested
   *  modal owns the Escape key (e.g., automation editor inside the
   *  Automations modal). */
  disableEscape?: boolean;
  /** Render on the higher z-modal-top layer instead of z-modal, so this
   *  modal stacks above another already-open modal (e.g. the automation
   *  editor over the Automations modal). */
  elevated?: boolean;
  /** Accessible name for the dialog. Defaults to the header title when it's a
   *  string; pass explicitly for modals with a custom (headerless) layout. */
  ariaLabel?: string;
  /** Viewport-space point the modal should "grow from" (the trigger button's
   *  center, via {@link originFromEvent} → store `modalOrigin`). When set, the
   *  panel animates in/out from that origin; when null it uses the neutral
 *  sheet rise. PageModal snapshots it at open time so the exit still
   *  knows the origin after the store has cleared it. */
  origin?: { x: number; y: number } | null;
  /** Centered by default. Bottom sheets use the viewer/review contract:
   *  16px viewport sides, max 960px, 82vh cap, and no lower corners. */
  placement?: "center" | "bottom-sheet";
  /** Fill the canonical bottom-sheet cap instead of sizing to content. */
  bottomSheetFill?: boolean;
}

const DEFAULT_SIZE =
  "w-[min(960px,calc(100vw-32px))] h-[min(680px,calc(100vh-32px))] sm:w-[min(960px,calc(100vw-80px))] sm:h-[min(680px,calc(100vh-80px))]";
const DEFAULT_GRID = "grid-rows-[auto_minmax(0,1fr)]";

/** Standard portal+backdrop+panel modal shell used across Settings,
 *  Automations, Archive, Memory. Callers compose their own header / body
 *  inside `children`. Closes on backdrop click and Escape (unless
 *  `disableEscape` is set). Corner radius comes from .surface-radius-lg —
 *  callers cannot override (was the source of per-modal radius drift). */
export function PageModal({
  open,
  onClose,
  children,
  header,
  size = DEFAULT_SIZE,
  grid = DEFAULT_GRID,
  disableEscape,
  ariaLabel,
  origin = null,
  elevated = false,
  placement = "center",
  bottomSheetFill = false,
}: PageModalProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const layerRef = useRef<HTMLDivElement>(null);
  const reducedMotion = useReducedMotion() ?? false;
  const present = useRetainedPresence(
    open,
    (reducedMotion ? MOTION.reduced : BACKDROP_DURATION) * 1_000,
  );
  // A closing sheet remains the active focus/inert boundary until its scrim
  // finishes. Reopening reverses the same surface instead of briefly exposing
  // the page below it.
  useOverlayLayer(layerRef, present, onClose, disableEscape, true, true, true, open);
  useFocusTrap(panelRef, present);

  // Snapshot the origin at open time — exit needs the point the modal grew
  // from even after the store has cleared `modalOrigin`.
  const originRef = useRef<{ x: number; y: number } | null>(null);
  const wasOpenRef = useRef(false);
  if (open && !wasOpenRef.current) originRef.current = origin;
  if (!open && !present) originRef.current = null;
  wasOpenRef.current = open;
  const originDelta = modalOriginTransform(originRef.current);

  const root = document.querySelector("#app");
  if (!root) return null;

  const dialogLabel = ariaLabel ?? (typeof header?.title === "string" ? header.title : undefined);
  const bottomSheet = placement === "bottom-sheet";

  return createPortal(
      present && (
        <div
          ref={layerRef}
          data-overlay-layer="modal"
          data-overlay-state={open ? "open" : "closing"}
          aria-hidden={open ? undefined : "true"}
          inert={open ? undefined : true}
          className="contents"
        >
          <div
            className={`fixed inset-0 grid pointer-events-none ${
              bottomSheet
                ? "page-modal-frame--bottom-sheet"
                : "place-items-center p-4 sm:p-8"
            } ${
              elevated ? "z-[var(--z-modal-top)]" : "z-[var(--z-sheet)]"
            }`}
          >
          <motion.div
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-label={dialogLabel}
            tabIndex={-1}
            className={`surface-panel surface-sheet surface-radius-lg ${
              bottomSheet
                ? `surface-bottom-sheet ${bottomSheetFill ? "surface-bottom-sheet--fill" : ""}`
                : size
            } grid ${grid} overflow-hidden focus:outline-none pointer-events-auto`}
            initial={
              originDelta
                ? {
                    opacity: 0,
                    scale: 0.94,
                    x: originDelta.x,
                    y: originDelta.y,
                  }
                : POSE_SHEET_IN
            }
            animate={
              open
                ? originDelta
                  ? { opacity: 1, scale: 1, x: 0, y: 0 }
                  : POSE_SHEET_VISIBLE
                : originDelta
                  ? {
                      opacity: 0,
                      scale: 0.94,
                      x: originDelta.x * 0.6,
                      y: originDelta.y * 0.6,
                    }
                  : POSE_SHEET_OUT
            }
            transition={
              reducedMotion
                ? { duration: MOTION.reduced }
                : open
                  ? SHEET_ENTER_TRANSITION
                  : SHEET_EXIT_TRANSITION
            }
            onClick={(e) => e.stopPropagation()}
          >
            {header && (
              // min-w-0: the header is a grid item (min-width:auto by default),
              // so a long, nowrap title/subtitle would expand it past the panel
              // and shove the close button off-screen (clipped by overflow-hidden)
              // instead of letting the inner truncate engage.
              <header className="modal-header flex items-start justify-between gap-3 min-w-0">
                <div className="min-w-0">
                  <div className="text-lg font-semibold tracking-[-0.012em] text-ink truncate">
                    {header.title}
                  </div>
                  {header.subtitle && (
                    <div className="mt-0.5 text-xs text-faint font-mono truncate">
                      {header.subtitle}
                    </div>
                  )}
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  {header.actions}
                  <IconButton onClick={onClose} aria-label="Close">
                    <X size={ICON.SM} />
                  </IconButton>
                </div>
              </header>
            )}
            {/* Keep structured header/body shells in one grid cell; callers
                with custom grids supply their own children directly. */}
            {header ? (
              <motion.div
                // grid-cols-[minmax(0,1fr)] (not an implicit `auto` column) so
                // wide unwrapped content (long tool args, code) can't stretch
                // this cell past the panel — which would both overflow the body
                // AND, via the shared panel column, shove the header title
                // off-screen. min 0 lets inner scroll/wrap engage.
                className="min-h-0 min-w-0 grid grid-cols-[minmax(0,1fr)] grid-rows-[minmax(0,1fr)]"
                initial={{ opacity: 0 }}
                animate={{ opacity: open ? 1 : 0 }}
                transition={{
                  duration: reducedMotion ? MOTION.reduced : MOTION.palette,
                  ease: EASE_DECELERATE,
                }}
              >
                {children}
              </motion.div>
            ) : (
              children
            )}
          </motion.div>
          </div>
        </div>
      ),
    root,
  );
}
