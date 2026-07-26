import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { motion, useReducedMotion } from "motion/react";
import { useStore } from "@/stores";
import { useFocusTrap } from "@/lib/hooks";
import {
  MOTION,
  POSE_SHEET_IN,
  POSE_SHEET_OUT,
  POSE_SHEET_VISIBLE,
  SHEET_ENTER_TRANSITION,
  SHEET_EXIT_TRANSITION,
} from "@/lib/tokens/motion";
import { PaletteBody } from "@/features/command-palette/components/PaletteBody";
import type { Crumb } from "@/lib/commandEntries/types";
import {
  useOverlayLayer,
} from "@/lib/overlayStack";
import { useRetainedPresence } from "@/components/workspace/useRetainedPresence";

export function CommandPalette() {
  const open = useStore((s) => s.paletteOpen);
  const close = useStore((s) => s.closePalette);
  const togglePalette = useStore((s) => s.togglePalette);
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const [crumbs, setCrumbs] = useState<Crumb[]>([]);
  const panelRef = useRef<HTMLDivElement>(null);
  const layerRef = useRef<HTMLDivElement>(null);
  const reducedMotion = useReducedMotion() ?? false;
  // Keep the layer active for the entire longer scrim/panel exit, so focus
  // restoration and background inertness never get ahead of the visible UI.
  const present = useRetainedPresence(
    open,
    (reducedMotion ? MOTION.reduced : Math.max(MOTION.focus, SHEET_EXIT_TRANSITION.duration)) * 1_000,
  );
  useOverlayLayer(layerRef, present, close, false, true, true, true, open);
  useFocusTrap(panelRef, present);

  // Global Cmd/Ctrl+K toggle. Escape is owned by the shared overlay stack.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        togglePalette();
        return;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, togglePalette]);

  // Reset state on open.
  useEffect(() => {
    if (open) {
      setQuery("");
      setIndex(0);
      setCrumbs([]);
    }
  }, [open]);

  const root = document.querySelector("#app");
  if (!root || !present) return null;

  return createPortal(
    <div
      ref={layerRef}
      data-overlay-layer="command-palette"
      data-overlay-state={open ? "open" : "closing"}
      aria-hidden={open ? undefined : "true"}
      inert={open ? undefined : true}
      className="contents"
    >
      <div className="fixed inset-0 z-[var(--z-sheet)] pointer-events-none">
        <motion.div
          ref={panelRef}
          role="dialog"
          aria-modal="true"
          aria-label="Command palette"
          tabIndex={-1}
          className="surface-panel surface-sheet surface-command-palette grid grid-rows-[auto_minmax(0,1fr)_auto] origin-top pointer-events-auto"
          initial={reducedMotion ? { opacity: 0 } : POSE_SHEET_IN}
          animate={
            open
              ? reducedMotion
                ? { opacity: 1 }
                : POSE_SHEET_VISIBLE
              : reducedMotion
                ? { opacity: 0 }
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
          <PaletteBody
            query={query}
            setQuery={setQuery}
            index={index}
            setIndex={setIndex}
            crumbs={crumbs}
            setCrumbs={setCrumbs}
            onClose={close}
          />
        </motion.div>
      </div>
    </div>,
    root,
  );
}
