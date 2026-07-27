import { useEffect, useRef, useState } from "react";
import { AnchoredPopover } from "@/components/ui/AnchoredPopover";
import { MenuItem } from "@/components/ui/MenuItem";

/** Invocation details shared by every Board-style context-menu consumer. */
export interface ContextMenuPosition {
  x: number;
  y: number;
  trigger: HTMLElement;
  source: "pointer" | "keyboard";
}

export type ContextMenuAction = {
  id: string;
  label: string;
  onSelect: () => void;
  tone?: "danger";
  shortcut?: string;
  /** Drill-in entries swap the list in place instead of dismissing. */
  keepOpen?: boolean;
};

export type ContextMenuEntry =
  | ContextMenuAction
  /** One nesting level: a flyout of actions on hover / ArrowRight
   *  (the macOS submenu idiom; DESIGN.md's "nested popover" tier). */
  | { id: string; label: string; children: ContextMenuAction[] }
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
  const [sub, setSub] = useState<{ id: string; x: number; y: number; keyboard: boolean } | null>(null);
  const subCloseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Root gone → no orphaned flyout.
  useEffect(() => {
    if (!state) setSub(null);
  }, [state]);

  const cancelSubClose = () => {
    if (subCloseTimer.current !== null) {
      clearTimeout(subCloseTimer.current);
      subCloseTimer.current = null;
    }
  };

  /** Grace window so the pointer can travel diagonally into the flyout. */
  const scheduleSubClose = () => {
    cancelSubClose();
    subCloseTimer.current = setTimeout(() => setSub(null), 160);
  };

  const openSub = (entry: { id: string }, element: HTMLElement, keyboard: boolean) => {
    cancelSubClose();
    const rect = element.getBoundingClientRect();
    setSub({ id: entry.id, x: rect.right - 4, y: rect.top - 5, keyboard });
  };

  const subEntry = sub
    ? entries.find((entry): entry is Extract<ContextMenuEntry, { children: ContextMenuAction[] }> =>
      "children" in entry && entry.id === sub.id)
    : undefined;

  return (
    <>
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
          if ("children" in entry) {
            return (
              <MenuItem
                key={entry.id}
                context
                role="menuitem"
                tabIndex={-1}
                aria-haspopup="menu"
                aria-expanded={sub?.id === entry.id}
                trailing={<span aria-hidden className="arden-context-menu__sub-glyph">›</span>}
                onMouseEnter={(event) => openSub(entry, event.currentTarget, false)}
                onMouseLeave={scheduleSubClose}
                onClick={(event) => openSub(entry, event.currentTarget, false)}
                onKeyDown={(event) => {
                  if (event.key !== "ArrowRight" && event.key !== "Enter") return;
                  event.preventDefault();
                  event.stopPropagation();
                  openSub(entry, event.currentTarget, true);
                }}
              >
                {entry.label}
              </MenuItem>
            );
          }
          return (
            <MenuItem
              key={entry.id}
              context
              role="menuitem"
              tabIndex={-1}
              data-tone={entry.tone}
              trailing={entry.shortcut ? <ContextMenuShortcut shortcut={entry.shortcut} /> : null}
              onMouseEnter={scheduleSubClose}
              onClick={() => {
                if (!entry.keepOpen) onClose();
                entry.onSelect();
              }}
            >
              {entry.label}
            </MenuItem>
          );
        })}
      </AnchoredPopover>

      <AnchoredPopover
        open={!!state && !!subEntry}
        onClose={() => setSub(null)}
        anchor={sub ? { x: sub.x, y: sub.y } : { x: 0, y: 0 }}
        variant="context-menu"
        ariaLabel={subEntry?.label ?? "Submenu"}
        focusOnOpen={sub?.keyboard ?? false}
      >
        <div onMouseEnter={cancelSubClose} onMouseLeave={scheduleSubClose}>
          {(subEntry?.children ?? []).map((child) => (
            <MenuItem
              key={child.id}
              context
              role="menuitem"
              tabIndex={-1}
              data-tone={child.tone}
              onClick={() => {
                onClose();
                child.onSelect();
              }}
              onKeyDown={(event) => {
                if (event.key !== "ArrowLeft") return;
                event.preventDefault();
                event.stopPropagation();
                setSub(null);
              }}
            >
              {child.label}
            </MenuItem>
          ))}
        </div>
      </AnchoredPopover>
    </>
  );
}
