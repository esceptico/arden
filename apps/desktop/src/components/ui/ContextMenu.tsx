import { AnchoredPopover } from "@/components/ui/AnchoredPopover";
import { MenuItem } from "@/components/ui/MenuItem";

/** Invocation details shared by every Board-style context-menu consumer. */
export interface ContextMenuPosition {
  x: number;
  y: number;
  trigger: HTMLElement;
  source: "pointer" | "keyboard";
}

export type ContextMenuEntry =
  | {
    id: string;
    label: string;
    onSelect: () => void;
    tone?: "danger";
    shortcut?: string;
  }
  | { id: string; type: "separator" };

function shortcutParts(shortcut: string): string[] {
  if (shortcut.includes(" ")) return shortcut.split(" ").filter(Boolean);
  const modifiers = shortcut.match(/[⌘⇧⌥⌃]/g) ?? [];
  const key = shortcut.replace(/[⌘⇧⌥⌃]/g, "");
  return [...modifiers, key].filter(Boolean);
}

function ContextMenuShortcut({ shortcut }: { shortcut: string }) {
  return (
    <span className="arden-context-menu__shortcut" aria-hidden="true">
      <span className="arden-kbd-group">
        {shortcutParts(shortcut).map((part, index) => (
          <kbd className="arden-kbd" key={`${part}-${index}`}>{part}</kbd>
        ))}
      </span>
    </span>
  );
}

/**
 * Exact Board context menu shell. It deliberately has no icon slot: actions
 * are text-first, 28px rows and keyboard focus only enters on a keyboard
 * invocation. Keep target/action routing in the consumer, not this primitive.
 */
export function ContextMenu({
  state,
  onClose,
  entries,
  ariaLabel = "Context actions",
}: {
  state: ContextMenuPosition | null;
  onClose: () => void;
  entries: ContextMenuEntry[];
  ariaLabel?: string;
}) {
  return (
    <AnchoredPopover
      open={!!state}
      onClose={onClose}
      anchor={state ? { x: state.x, y: state.y } : { x: 0, y: 0 }}
      variant="context-menu"
      ariaLabel={ariaLabel}
      focusOnOpen={state?.source === "keyboard"}
      restoreFocusTarget={state?.trigger ?? null}
    >
      {entries.map((entry) => {
        if ("type" in entry) {
          return <div key={entry.id} className="arden-context-menu__separator" role="separator" />;
        }
        return (
          <MenuItem
            key={entry.id}
            context
            role="menuitem"
            tabIndex={-1}
            data-tone={entry.tone}
            trailing={entry.shortcut ? <ContextMenuShortcut shortcut={entry.shortcut} /> : null}
            onClick={() => {
              onClose();
              entry.onSelect();
            }}
          >
            {entry.label}
          </MenuItem>
        );
      })}
    </AnchoredPopover>
  );
}
