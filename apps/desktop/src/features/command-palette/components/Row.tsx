import { ChevronRight } from "@/components/icons";
import clsx from "clsx";
import { Command } from "cmdk";
import { ICON } from "@/lib/icons";
import type { CommandEntry } from "@/features/command-palette/types";

export function Row({
  entry,
  active,
  optionId,
  onClick,
}: {
  entry: CommandEntry;
  active: boolean;
  optionId?: string;
  onClick: () => void;
}) {
  const Icon = entry.icon;
  return (
      <Command.Item
        value={entry.id}
        keywords={entry.search.split(" ")}
        id={optionId}
        onMouseDown={(e) => e.preventDefault()}
        onSelect={onClick}
        className="app-row flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-ink-soft outline-none"
      >
        <span
          className={clsx(
            "grid place-items-center w-5 h-5 rounded-md shrink-0 transition-colors duration-check ease-out",
            active ? "bg-accent-soft text-accent-strong" : "text-muted",
          )}
        >
          <Icon size={ICON.SM} strokeWidth={2} />
        </span>
        <span className="text-base text-ink truncate flex-1">{entry.label}</span>
        {entry.hint && (
          <span className="text-xs text-faint tabular-nums shrink-0">{entry.hint}</span>
        )}
        {entry.shortcut && (
          <kbd className="text-2xs text-faint font-mono shrink-0 ml-1">{entry.shortcut}</kbd>
        )}
        {entry.children && (
          <ChevronRight
            size={ICON.XS}
            strokeWidth={2}
            className="text-faint shrink-0 ml-1"
            aria-hidden
          />
        )}
      </Command.Item>
  );
}
