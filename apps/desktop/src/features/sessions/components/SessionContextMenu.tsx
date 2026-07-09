import { Archive, FolderInput, Pencil, Pin, PinOff, Sparkles } from "lucide-react";
import type { Project } from "@/api/types";
import { ICON } from "@/lib/icons";
import { MenuItem } from "@/components/ui/MenuItem";
import { AnchoredPopover } from "@/components/ui/AnchoredPopover";

export interface ContextMenuState {
  sessionId: string;
  x: number;
  y: number;
}

export function SessionContextMenu({
  state,
  onClose,
  onRename,
  onCompact,
  onArchive,
  onMoveProject,
  onTogglePin,
  isPinned,
  projects,
}: {
  state: ContextMenuState | null;
  onClose: () => void;
  onRename: () => void;
  onCompact: () => void;
  onArchive: () => void;
  onMoveProject: (projectId: string | null) => void;
  onTogglePin: () => void;
  isPinned: boolean;
  projects: Project[];
}) {
  return (
    <AnchoredPopover
      open={!!state}
      onClose={onClose}
      anchor={state ? { x: state.x, y: state.y } : { x: 0, y: 0 }}
      variant="menu"
      ariaLabel="Session actions"
      closeOnScroll
      className="w-[220px] py-1"
    >
      <ContextItem
        icon={isPinned ? <PinOff size={ICON.MD} strokeWidth={2} /> : <Pin size={ICON.MD} strokeWidth={2} />}
        label={isPinned ? "Unpin" : "Pin to top"}
        onClick={onTogglePin}
      />
      <ContextItem icon={<Pencil size={ICON.MD} strokeWidth={2} />} label="Rename…" onClick={onRename} />
      <ContextItem icon={<Sparkles size={ICON.MD} strokeWidth={2} />} label="Compact context" onClick={onCompact} />
      <ContextItem icon={<Archive size={ICON.MD} strokeWidth={2} />} label="Archive" onClick={onArchive} />
      <div className="my-1 h-px bg-line-soft" />
      <ContextItem icon={<FolderInput size={ICON.MD} strokeWidth={2} />} label="Move to Inbox" onClick={() => onMoveProject(null)} />
      {projects.map((project) => (
        <ContextItem
          key={project.project_id}
          icon={<FolderInput size={ICON.MD} strokeWidth={2} />}
          label={project.name}
          onClick={() => onMoveProject(project.project_id)}
        />
      ))}
    </AnchoredPopover>
  );
}

function ContextItem({
  icon,
  label,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <MenuItem role="menuitem" tabIndex={-1} onClick={onClick} leading={<span className="text-faint">{icon}</span>}>
      {label}
    </MenuItem>
  );
}
