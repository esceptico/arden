import type { ReactNode } from "react";
import { PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen } from "@/components/icons";
import { useStore } from "@/stores";
import { IconButton } from "@/components/ui/IconButton";
import { IconSwap } from "@/components/ui/IconSwap";
import { ICON } from "@/lib/icons";

type SidebarToggleLabels = {
  hide: string;
  show: string;
  /** `false` drops the chord from the tooltip where the panel has none. */
  shortcut?: string | false;
};

const DEFAULT_LABELS: SidebarToggleLabels = {
  hide: "Hide sidebar",
  show: "Show sidebar",
  shortcut: "⌘B",
};

/** One icon vocabulary for every collapsible panel: the glyph points at the
 *  edge its panel lives on, and it always swaps with state — open offers the
 *  close action, closed offers the open one. A control that shows the same
 *  glyph in both states reports nothing. */
const PANEL_ICONS = {
  left: { close: PanelLeftClose, open: PanelLeftOpen },
  right: { close: PanelRightClose, open: PanelRightOpen },
} as const;

/** The fixed-viewport panel collapse control — near the macOS traffic lights
 *  on the left (`.sidebar-toggle`), the opposite corner on the right.
 *
 *  Every rail routes through here: the shell sidebar, the Settings rail, the
 *  Memory vault column, the automations list, the agent inspector. Callers
 *  pick a side and name their own panel; glyph, geometry and swap behaviour
 *  are not theirs to vary, which is what kept them from drifting apart. */
export function SidebarToggle({
  hidden,
  onToggle,
  labels = DEFAULT_LABELS,
  side = "left",
  className = "",
  children,
  ...rest
}: {
  hidden?: boolean;
  onToggle?: () => void;
  labels?: SidebarToggleLabels;
  side?: "left" | "right";
  className?: string;
  /** Status shown beside the glyph, e.g. the inspector's active count. */
  children?: ReactNode;
} & Record<string, unknown> = {}) {
  const storedHidden = useStore((s) => s.prefs.sidebarHidden);
  const toggleSidebar = useStore((s) => s.toggleSidebar);
  const panelHidden = hidden ?? storedHidden;
  const actionLabel = panelHidden ? labels.show : labels.hide;
  const title = labels.shortcut === false
    ? actionLabel
    : `${actionLabel} (${labels.shortcut ?? DEFAULT_LABELS.shortcut})`;
  const { close: Close, open: Open } = PANEL_ICONS[side];
  const slot = side === "left" ? "sidebar-toggle" : "right-sidebar-toggle";
  return (
    <IconButton
      size="xs"
      shape="circle"
      className={`${slot} shell-control ${className}`.trim()}
      onClick={onToggle ?? toggleSidebar}
      title={title}
      aria-label={actionLabel}
      aria-expanded={!panelHidden}
      data-open={panelHidden ? "false" : "true"}
      {...rest}
    >
      {children}
      <IconSwap
        state={panelHidden ? "b" : "a"}
        iconA={<Close size={ICON.MD} />}
        iconB={<Open size={ICON.MD} />}
      />
    </IconButton>
  );
}
