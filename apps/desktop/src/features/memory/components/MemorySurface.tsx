import { MemoryPane } from "@/features/memory/components/MemoryPane";
import "@/design/memory.css";

/** Memory is a route, not a dialog. It brings its own left rail — the vault
 *  column — which takes the app sidebar's slot (see workspaceOwnsRail), and it
 *  owns an internal page history on ⌘[ / ⌘]. Enter/exit motion, PageEntrance,
 *  and the back control are all shell-level; this is just the room.
 *  The header bar lives in ArtifactMemoryView so breadcrumb and note actions
 *  share one row. */
export function MemorySurface() {
  return (
    <div className="memory-surface absolute inset-0 flex flex-col bg-bg-main">
      <MemoryPane />
    </div>
  );
}
