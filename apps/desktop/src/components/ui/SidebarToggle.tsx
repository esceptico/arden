import { PanelLeftClose, PanelLeftOpen } from "@/components/icons";
import { useStore } from "@/stores";
import { IconButton } from "@/components/ui/IconButton";
import { IconSwap } from "@/components/ui/IconSwap";
import { ICON } from "@/lib/icons";

type SidebarToggleLabels = {
  hide: string;
  show: string;
  /** `false` suppresses the tooltip for a local shell control. */
  shortcut?: string | false;
};

const DEFAULT_LABELS: SidebarToggleLabels = {
  hide: "Hide sidebar",
  show: "Show sidebar",
  shortcut: "⌘B",
};

/** The fixed-viewport sidebar collapse control, anchored near the macOS
 *  traffic lights (`.sidebar-toggle`). Rendered once at the app level so it
 *  is present on every screen — Chat, Home, and the area rooms — rather
 *  than only where Chat mounts (⌘B was otherwise the sole way back). */
export function SidebarToggle({
  hidden,
  onToggle,
  labels = DEFAULT_LABELS,
}: {
  hidden?: boolean;
  onToggle?: () => void;
  labels?: SidebarToggleLabels;
} = {}) {
  const storedHidden = useStore((s) => s.prefs.sidebarHidden);
  const toggleSidebar = useStore((s) => s.toggleSidebar);
  const sidebarHidden = hidden ?? storedHidden;
  const actionLabel = sidebarHidden ? labels.show : labels.hide;
  const title = labels.shortcut === false
    ? undefined
    : `${actionLabel} (${labels.shortcut ?? DEFAULT_LABELS.shortcut})`;
  return (
    <IconButton
      size="xs"
      shape="circle"
      className="sidebar-toggle shell-control"
      onClick={onToggle ?? toggleSidebar}
      title={title}
      aria-label={actionLabel}
      aria-expanded={!sidebarHidden}
    >
      <IconSwap
        state={sidebarHidden ? "b" : "a"}
        iconA={<PanelLeftClose size={ICON.MD} />}
        iconB={<PanelLeftOpen size={ICON.MD} />}
      />
    </IconButton>
  );
}
