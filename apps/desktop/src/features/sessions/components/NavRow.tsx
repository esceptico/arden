/** Primary nav row — matches the SessionRow grid (16px icon column +
 *  label) so the top and bottom blocks of the sidebar read as the
 *  same visual rhythm. No boxed icon container; flat stroked icon
 *  inherits the row's text color for hover/active states. */
export function NavRow({
  icon,
  label,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  // Receives the click event so callers can capture the trigger position
  // for modal spatial-origin animations.
  onClick: (event: React.MouseEvent<HTMLButtonElement>) => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="app-row grid grid-cols-[16px_minmax(0,1fr)] items-center gap-2 w-full px-2 py-1 rounded-lg text-base text-ink-soft text-left tracking-[-0.005em]"
    >
      <span className="grid place-items-center w-4 h-4 shrink-0">
        {icon}
      </span>
      <span className="truncate">{label}</span>
    </button>
  );
}
