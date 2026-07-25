import { useCallback, useRef, type MouseEvent as ReactMouseEvent } from "react";
import {
  RIGHT_PANEL_DEFAULT_WIDTH,
  RIGHT_PANEL_MAX_WIDTH,
  RIGHT_PANEL_MIN_WIDTH,
  RIGHT_PANEL_SNAP_POINTS,
  RIGHT_PANEL_SNAP_THRESHOLD_PX,
  useStore,
} from "@/stores";

export function RightPanelResizeHandle() {
  const setPref = useStore((s) => s.setPref);
  const startXRef = useRef(0);
  const startWidthRef = useRef(0);

  const onMouseDown = useCallback(
    (event: ReactMouseEvent) => {
      event.preventDefault();
      startXRef.current = event.clientX;
      startWidthRef.current = useStore.getState().prefs.rightPanelWidth;
      document.body.style.cursor = "ew-resize";
      document.body.style.userSelect = "none";

      let liveWidth = startWidthRef.current;
      const onMove = (moveEv: globalThis.MouseEvent) => {
        // Left-edge handle: dragging left increases the dock width.
        const next = startWidthRef.current + (startXRef.current - moveEv.clientX);
        liveWidth = Math.max(RIGHT_PANEL_MIN_WIDTH, Math.min(RIGHT_PANEL_MAX_WIDTH, next));
        document.documentElement.style.setProperty("--right-panel-width", `${liveWidth}px`);
      };

      const onUp = () => {
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);

        let nearest = liveWidth;
        let nearestDelta = Infinity;
        for (const point of RIGHT_PANEL_SNAP_POINTS) {
          const delta = Math.abs(liveWidth - point);
          if (delta < nearestDelta) {
            nearest = point;
            nearestDelta = delta;
          }
        }
        const final = nearestDelta <= RIGHT_PANEL_SNAP_THRESHOLD_PX ? nearest : liveWidth;
        setPref("rightPanelWidth", final);
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
      aria-label="Resize right sidebar"
      onMouseDown={onMouseDown}
      onDoubleClick={() => setPref("rightPanelWidth", RIGHT_PANEL_DEFAULT_WIDTH)}
      className="arden-resize-handle"
      data-edge="start"
    />
  );
}
