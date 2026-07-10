import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useStore } from "@/stores";
import { IconButton } from "@/components/ui/IconButton";
import { BlurSwap } from "@/components/ui/BlurSwap";
import { ICON } from "@/lib/icons";

/** The fixed-viewport sidebar collapse control, anchored near the macOS
 *  traffic lights (`.sidebar-toggle`). Rendered once at the app level so it
 *  is present on every screen — Chat, Home, and the area rooms — rather
 *  than only where Chat mounts (⌘B was otherwise the sole way back). */
export function SidebarToggle() {
  const sidebarHidden = useStore((s) => s.prefs.sidebarHidden);
  const toggleSidebar = useStore((s) => s.toggleSidebar);
  return (
    <IconButton
      size="xs"
      className="sidebar-toggle"
      onClick={toggleSidebar}
      title={sidebarHidden ? "Show sidebar (⌘B)" : "Hide sidebar (⌘B)"}
      aria-label={sidebarHidden ? "Show sidebar" : "Hide sidebar"}
    >
      <BlurSwap swapKey={sidebarHidden ? "show" : "hide"} scaleFrom={0.25}>
        {sidebarHidden ? (
          <PanelLeftOpen size={ICON.MD} strokeWidth={2} />
        ) : (
          <PanelLeftClose size={ICON.MD} strokeWidth={2} />
        )}
      </BlurSwap>
    </IconButton>
  );
}
