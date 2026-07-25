import {
  ContextMenu,
  type ContextMenuEntry,
  type ContextMenuPosition,
} from "@/components/ui/ContextMenu";

export interface ContextMenuState extends ContextMenuPosition {
  sessionId: string;
}

/** Board session actions with a real desktop route. Deep links are not yet a
 * runtime contract, so the mock's Copy link action is deliberately absent. */
export function SessionContextMenu({
  state,
  onClose,
  onOpen,
  onRename,
  onArchive,
}: {
  state: ContextMenuState | null;
  onClose: () => void;
  onOpen: () => void;
  onRename: () => void;
  onArchive: () => void;
}) {
  const entries: ContextMenuEntry[] = [
    { id: "open", label: "Open chat", onSelect: onOpen },
    { id: "rename", label: "Rename", onSelect: onRename },
    { id: "divider", type: "separator" },
    { id: "archive", label: "Archive", tone: "danger", onSelect: onArchive },
  ];

  return <ContextMenu state={state} onClose={onClose} entries={entries} />;
}
