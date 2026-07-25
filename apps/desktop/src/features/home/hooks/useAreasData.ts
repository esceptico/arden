import { useCallback, useEffect } from "react";
import { useStore } from "@/stores";
import { fetchAreasOverview } from "@/actions/areas";

/** Fetches the areas overview whenever the server connection comes up.
 *  Keyed on `connected`, not mount: on a fresh boot the mount fires before
 *  the handshake, the fetch fails silently, and Home would show a permanent
 *  false "All clear." Connect-keying also covers server restarts. Live
 *  refetches on `areas_changed`/`automation_finished` are handled by
 *  `useAutomationEvents` (the hook that owns the automation SSE stream). */
export function useAreasData() {
  const overview = useStore((s) => s.areas.overview);
  const phase = useStore((s) => s.areas.overviewPhase);
  const connected = useStore((s) => s.connected);
  const reload = useCallback(() => fetchAreasOverview(), []);

  useEffect(() => {
    if (connected) void reload();
  }, [connected, reload]);

  return { overview, phase, reload };
}
