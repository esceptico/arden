import type { Area } from "@/api/types";
import {
  ContextMenu,
  type ContextMenuAction,
  type ContextMenuEntry,
  type ContextMenuPosition,
} from "@/components/ui/ContextMenu";

export interface ContextMenuState extends ContextMenuPosition {
  sessionId: string;
}

/** Board session actions with a real desktop route. Deep links are not yet a
 * runtime contract, so the mock's Copy link action is deliberately absent.
 * "Move to" is a hover submenu (the shared primitive's one nesting level). */
export function SessionContextMenu({
  state,
  areas,
  currentAreaId,
  pinned,
  onClose,
  onOpen,
  onRename,
  onTogglePin,
  onMove,
  onArchive,
}: {
  state: ContextMenuState | null;
  areas: Area[];
  currentAreaId: string | null;
  pinned: boolean;
  onClose: () => void;
  onOpen: () => void;
  onRename: () => void;
  onTogglePin: () => void;
  onMove: (areaId: string | null) => void;
  onArchive: () => void;
}) {
  const targets: ContextMenuAction[] = [
    ...(currentAreaId !== null
      ? [{ id: "move:inbox", label: "Inbox (no area)", onSelect: () => onMove(null) }]
      : []),
    ...areas
      .filter((area) => area.area_id !== currentAreaId)
      .map((area) => ({
        id: `move:${area.area_id}`,
        label: area.name,
        onSelect: () => onMove(area.area_id),
      })),
  ];

  const entries: ContextMenuEntry[] = [
    { id: "open", label: "Open chat", onSelect: onOpen },
    { id: "rename", label: "Rename", onSelect: onRename },
    { id: "pin", label: pinned ? "Unpin" : "Pin", onSelect: onTogglePin },
    ...(targets.length > 0 ? [{ id: "move", label: "Move to", children: targets }] : []),
    { id: "divider", type: "separator" as const },
    { id: "archive", label: "Archive", tone: "danger" as const, onSelect: onArchive },
  ];

  return <ContextMenu state={state} onClose={onClose} entries={entries} />;
}
