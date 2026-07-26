import { useCallback, type RefObject } from "react";

/** Drag-to-resize handle for workspace panes (memory rail/context,
 *  automations rail), following the SidebarResizeHandle pattern: during
 *  drag the CSS variable is written imperatively (no React re-render per
 *  pixel); the final width persists to localStorage. Double-click resets
 *  to the default. */
export function PaneResizeHandle({
  layoutRef,
  cssVar,
  storageKey,
  min,
  max,
  defaultWidth,
  edge,
  label,
}: {
  layoutRef: RefObject<HTMLElement | null>;
  cssVar: string;
  storageKey: string;
  min: number;
  max: number;
  defaultWidth: number;
  /** "end" = handle on the pane's right edge (drag right grows it);
   *  "start" = handle on the pane's left edge (drag right shrinks it). */
  edge: "start" | "end";
  label: string;
}) {
  const onMouseDown = useCallback((event: React.MouseEvent) => {
    event.preventDefault();
    const layout = layoutRef.current;
    if (!layout) return;
    const startX = event.clientX;
    const current = parseInt(getComputedStyle(layout).getPropertyValue(cssVar), 10);
    const startWidth = Number.isFinite(current) && current > 0 ? current : defaultWidth;
    document.body.style.cursor = "ew-resize";
    document.body.style.userSelect = "none";
    let live = startWidth;
    const onMove = (moveEv: MouseEvent) => {
      const dx = moveEv.clientX - startX;
      live = Math.max(min, Math.min(max, edge === "end" ? startWidth + dx : startWidth - dx));
      layout.style.setProperty(cssVar, `${live}px`);
    };
    const onUp = () => {
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      try {
        localStorage.setItem(storageKey, String(live));
      } catch {
        /* localStorage unavailable — non-fatal */
      }
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }, [cssVar, defaultWidth, edge, layoutRef, max, min, storageKey]);

  const reset = useCallback(() => {
    layoutRef.current?.style.setProperty(cssVar, `${defaultWidth}px`);
    try {
      localStorage.removeItem(storageKey);
    } catch {
      /* non-fatal */
    }
  }, [cssVar, defaultWidth, layoutRef, storageKey]);

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      onMouseDown={onMouseDown}
      onDoubleClick={reset}
      className="arden-resize-handle max-[900px]:hidden"
      data-edge={edge}
    />
  );
}
