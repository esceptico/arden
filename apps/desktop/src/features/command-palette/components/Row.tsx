import { ChevronRight } from "@/components/icons";
import { Command } from "cmdk";
import { ICON } from "@/lib/icons";
import type { CommandEntry } from "@/features/command-palette/types";

export function Row({
  entry,
  optionId,
  onClick,
}: {
  entry: CommandEntry;
  optionId?: string;
  onClick: () => void;
}) {
  const Icon = entry.icon;
  const shortcutParts = entry.shortcut?.startsWith("⌘") && entry.shortcut.length > 1
    ? ["⌘", entry.shortcut.slice(1)]
    : entry.shortcut
      ? [entry.shortcut]
      : [];
  return (
      <Command.Item
        value={entry.id}
        keywords={entry.search.split(" ")}
        id={optionId}
        onMouseDown={(e) => e.preventDefault()}
        onSelect={onClick}
        className="command-palette__row"
      >
        <Icon size={ICON.MD} strokeWidth={2} className="command-palette__row-icon" />
        <span className="command-palette__row-label">{entry.label}</span>
        <span className="command-palette__row-trailing">
          {entry.hint && (
            <span className="command-palette__row-hint">{entry.hint}</span>
          )}
          {entry.shortcut && (
            <span className="command-palette__row-shortcut arden-kbd-group" aria-label={entry.shortcut}>
              {shortcutParts.map((part) => <kbd className="arden-kbd" key={part}>{part}</kbd>)}
            </span>
          )}
          {entry.children && (
            <ChevronRight
              size={ICON.XS}
              strokeWidth={2}
              className="command-palette__row-chevron"
              aria-hidden
            />
          )}
        </span>
      </Command.Item>
  );
}
