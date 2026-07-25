import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { InspectOverlay, type InspectTarget } from "@/features/inspect/components/InspectOverlay";
import { useReanchor } from "@/lib/hooks";

interface Pin {
  el: Element;
  color: string;
}

const PALETTE = ["#168cff", "#a855f7", "#16a36a", "#f97316", "#e5484d", "#eab308"];
const HOVER_COLOR = "#94a3b8";

/**
 * Dev-only hover/multi-select element inspector. Cmd+Shift+I toggles it;
 * mounted unconditionally (see App.tsx's `import.meta.env.DEV` guard) so the
 * shortcut always works, but renders nothing until opened.
 *
 * Click pins one element, clearing any prior pins. Shift-click adds/removes
 * from the pinned set — this is the "select several components" case.
 * Escape (or the shortcut again) exits and clears everything.
 */
export function Inspect() {
  const [open, setOpen] = useState(false);
  const [hoverEl, setHoverEl] = useState<Element | null>(null);
  const [pins, setPins] = useState<Pin[]>([]);
  const [, forceRemeasure] = useState(0);

  const openRef = useRef(open);
  openRef.current = open;

  const close = useCallback(() => {
    setOpen(false);
    setHoverEl(null);
    setPins([]);
  }, []);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && openRef.current) {
        e.preventDefault();
        close();
        return;
      }
      // Chorded — never inserts text, so no typing-target guard: the shortcut
      // must work while focus sits in an input (comboboxes, open dropdowns).
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === "i") {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [close]);

  useEffect(() => {
    if (!open) return;
    document.body.classList.add("inspect-active");

    const onMove = (e: MouseEvent) => {
      const el = document.elementFromPoint(e.clientX, e.clientY);
      setHoverEl(el && el !== document.body && el !== document.documentElement ? el : null);
    };

    // Capture phase, and swallowed — while inspecting, a click pins/unpins
    // instead of doing whatever the real element would (send a message,
    // navigate, delete something). Overlay chrome is pointer-events:none
    // (inspect.css), so `target` here is always the real underlying element.
    const onClick = (e: MouseEvent) => {
      const target = e.target;
      if (!(target instanceof Element) || target.closest("[data-inspect-ui]")) return;
      e.preventDefault();
      e.stopPropagation();
      if (e.shiftKey) {
        setPins((prev) => {
          const idx = prev.findIndex((p) => p.el === target);
          if (idx >= 0) return prev.filter((_, i) => i !== idx);
          return [...prev, { el: target, color: PALETTE[prev.length % PALETTE.length] }];
        });
      } else {
        setPins([{ el: target, color: PALETTE[0] }]);
      }
    };

    window.addEventListener("mousemove", onMove);
    window.addEventListener("click", onClick, true);
    return () => {
      document.body.classList.remove("inspect-active");
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("click", onClick, true);
    };
  }, [open]);

  useReanchor(open, () => forceRemeasure((n) => n + 1));

  if (!open) return null;
  const root = document.querySelector("#app");
  if (!root) return null;

  const targets: InspectTarget[] = [
    ...pins.map((p): InspectTarget => ({ el: p.el, color: p.color, pinned: true })),
    ...(hoverEl && !pins.some((p) => p.el === hoverEl) ? [{ el: hoverEl, color: HOVER_COLOR, pinned: false } as InspectTarget] : []),
  ];

  return createPortal(
    <div data-overlay-layer="inspect" data-inspect-ui>
      <InspectOverlay targets={targets} />
    </div>,
    root,
  );
}
