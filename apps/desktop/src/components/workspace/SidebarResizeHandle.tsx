import { useCallback, useRef } from "react";
import {
  useStore,
  SIDEBAR_MIN_WIDTH,
  SIDEBAR_MAX_WIDTH,
  SIDEBAR_SNAP_POINTS,
  SIDEBAR_SNAP_THRESHOLD_PX,
} from "@/stores";

/** Magnetic drag-to-resize handle on the sidebar's right edge. The
 *  visible affordance is a 4px column of `cursor: ew-resize`; while
 *  dragging, the cursor stays as ew-resize and the sidebar width
 *  updates in real time. On release, the width snaps to the nearest
 *  common point (220/244/288/320) if within 12px. */
export function SidebarResizeHandle() {
  const setPref = useStore((s) => s.setPref);
  const startXRef = useRef(0);
  const startWidthRef = useRef(0);

  const onMouseDown = useCallback(
    (event: React.MouseEvent) => {
      event.preventDefault();
      startXRef.current = event.clientX;
      startWidthRef.current = useStore.getState().prefs.sidebarWidth;
      document.body.style.cursor = "ew-resize";
      document.body.style.userSelect = "none";

      // During drag, write the CSS variable imperatively and skip the store
      // entirely. Going through Zustand on every mousemove rerenders every
      // subscriber of prefs.sidebarWidth (App, both sidebars) per pixel —
      // pure waste when the only consumers of the live value are CSS readers
      // that already use var(--sidebar-width). Per the motion tier list,
      // CSS-var writes trigger paint, so we still want this scoped, but
      // moving it off React drops 60+ rerenders per second during drag.
      let liveWidth = startWidthRef.current;
      const onMove = (moveEv: MouseEvent) => {
        const next = startWidthRef.current + (moveEv.clientX - startXRef.current);
        liveWidth = Math.max(SIDEBAR_MIN_WIDTH, Math.min(SIDEBAR_MAX_WIDTH, next));
        document.documentElement.style.setProperty("--sidebar-width", `${liveWidth}px`);
      };

      const onUp = () => {
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);

        let nearest = liveWidth;
        let nearestDelta = Infinity;
        for (const point of SIDEBAR_SNAP_POINTS) {
          const delta = Math.abs(liveWidth - point);
          if (delta < nearestDelta) {
            nearest = point;
            nearestDelta = delta;
          }
        }
        const final =
          nearestDelta <= SIDEBAR_SNAP_THRESHOLD_PX ? nearest : liveWidth;
        setPref("sidebarWidth", final);
      };

      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    },
    [setPref],
  );

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize sidebar"
      onMouseDown={onMouseDown}
      onDoubleClick={() => setPref("sidebarWidth", 288)}
      className="workspace-rail__resize-handle arden-resize-handle"
      data-edge="end"
    />
  );
}
