import { ArrowLeft } from "@/components/icons";
import { switchSession } from "@/actions/sessions";
import { ICON } from "@/lib/icons";

// Back-to-parent chip shown while viewing a child agent session — turns the
// hub into the agent's breadcrumb + sibling switcher.
export function ParentBreadcrumb({
  parentId,
  parentName,
}: {
  parentId: string;
  parentName: string | null;
}) {
  return (
    <button
      type="button"
      onClick={() => void switchSession(parentId)}
      title={parentName ? `Back to ${parentName}` : "Back to parent session"}
      className="group/bc flex w-full items-center gap-1.5 rounded-[8px] px-1.5 py-1 text-left text-xs text-muted transition-[background-color,color,scale] duration-row ease-out hover:bg-surface-soft/60 hover:text-ink active:scale-[0.985]"
    >
      <ArrowLeft
        size={ICON.SM}
        strokeWidth={2}
        className="shrink-0 text-faint transition-colors duration-row ease-out group-hover/bc:text-ink"
      />
      <span className="truncate">{parentName ?? "Parent session"}</span>
    </button>
  );
}
