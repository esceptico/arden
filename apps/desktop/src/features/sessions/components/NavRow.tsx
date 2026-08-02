import type { ContextMenuPosition } from "@/components/ui/ContextMenu";

/** Primary nav row — matches the SessionRow grid (16px icon column +
 *  label) so the top and bottom blocks of the sidebar read as the
 *  same visual rhythm. No boxed icon container; flat stroked icon
 *  inherits the row's text color for hover/active states. */
export function NavRow({
  icon,
  label,
  onClick,
  active = false,
  trailing,
  hint,
  hintVisible = false,
  onMenu,
}: {
  icon: React.ReactNode;
  label: string;
  // Receives the click event so callers can capture the trigger position
  // for modal spatial-origin animations.
  onClick: (event: React.MouseEvent<HTMLButtonElement>) => void;
  active?: boolean;
  trailing?: React.ReactNode;
  /** Keyboard-shortcut kbd shown in the trailing column while the
   *  long-⌘-hold gesture is active (see useCmdHeld). */
  hint?: string;
  hintVisible?: boolean;
  onMenu?: (position: ContextMenuPosition) => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      onContextMenu={onMenu ? (event) => {
        event.preventDefault();
        onMenu({
          x: event.clientX,
          y: event.clientY,
          trigger: event.currentTarget,
          source: "pointer",
        });
      } : undefined}
      onKeyDown={onMenu ? (event) => {
        if (event.key !== "ContextMenu" && !(event.shiftKey && event.key === "F10")) return;
        event.preventDefault();
        const rect = event.currentTarget.getBoundingClientRect();
        onMenu({
          x: rect.left + 12,
          y: rect.bottom - 4,
          trigger: event.currentTarget,
          source: "keyboard",
        });
      } : undefined}
      data-active={active ? "true" : undefined}
      aria-current={active ? "page" : undefined}
      className="workspace-rail__nav-row arden-row app-row grid grid-cols-[16px_minmax(0,1fr)_auto] items-center gap-2 w-full px-2 py-1 text-base text-ink-soft text-left"
    >
      <span className="grid place-items-center w-4 h-4 shrink-0">
        {icon}
      </span>
      <span className="truncate">{label}</span>
      {trailing ? <span className="workspace-rail__row-meta">{trailing}</span> : null}
      {!trailing && hint ? (
        <kbd
          className="arden-kbd arden-kbd--chord workspace-rail__nav-hint"
          data-visible={hintVisible || undefined}
          aria-hidden={!hintVisible}
        >
          {hint}
        </kbd>
      ) : null}
    </button>
  );
}
