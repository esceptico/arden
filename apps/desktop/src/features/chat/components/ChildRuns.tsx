import { ArrowRight } from "@/components/icons";
import { useStore, type ActivityItem } from "@/stores";
import { ICON } from "@/lib/icons";
import { Caption } from "@/components/ui/Caption";

export function ChildRuns({ items }: { items: ActivityItem[] }) {
  const setViewing = useStore((s) => s.setViewingTool);
  return (
    <section className="grid grid-cols-[minmax(0,1fr)] gap-1.5 min-w-0">
      <Caption>Child runs</Caption>
      <ul className="grid gap-px m-0 p-0 list-none rounded-[10px] border border-line-soft bg-surface overflow-hidden">
        {items.map((child) => (
          <li key={child.id} className="contents">
            <button
              type="button"
              onClick={() => setViewing(child)}
              className="app-row flex items-center gap-2 w-full px-3 py-1.5 text-left bg-transparent border-0 text-ink-soft"
            >
              <ArrowRight size={ICON.XS} strokeWidth={2} className="text-whisper shrink-0" />
              <span className="text-sm font-medium text-ink-soft shrink-0">{child.kind}</span>
              <span className="text-xs text-faint font-mono truncate min-w-0 flex-1">
                {child.target}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
