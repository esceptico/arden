import { ChevronRight, Pin } from "lucide-react";
import type { MemoryArtifactDetail } from "@/features/memory/lib/notebookTypes";
import { kindLabel } from "@/features/memory/lib/format";

// The page's raw record timeline (the machine layer under the compiled prose),
// shown as collapsed evidence. Dense rows: date, then the claim with kind ·
// source as a quiet trailing token — the claim always gets the width.
export function TimelineDisclosure({ timeline }: { timeline?: MemoryArtifactDetail["timeline"] }) {
  const records = (timeline ?? []).filter((l) => !l.superseded);
  const supersededCount = (timeline ?? []).length - records.length;
  if (records.length === 0) return null;
  const label = records.length === 1 ? "record" : "records";
  return (
    <details className="group mt-8 border-t border-line pt-3">
      <summary className="flex select-none items-center gap-1.5 text-xs font-medium text-muted transition-colors hover:text-ink [&::-webkit-details-marker]:hidden">
        <ChevronRight
          className="h-3 w-3 shrink-0 text-faint transition-transform duration-150 group-open:rotate-90"
          strokeWidth={2}
          aria-hidden
        />
        Evidence
        <span className="font-normal tabular-nums text-faint">
          {records.length} {label}
          {supersededCount > 0 && ` · ${supersededCount} superseded`}
        </span>
      </summary>
      <ol className="mt-2">
        {records.map((l) => (
          <li
            key={l.id}
            className="grid grid-cols-[auto_minmax(0,1fr)] items-baseline gap-x-2.5 rounded-md px-1.5 py-1 text-xs transition-colors hover:bg-surface-soft"
          >
            <span className="font-mono tabular-nums text-faint">{l.date}</span>
            <span className="min-w-0 break-words leading-relaxed text-ink-soft">
              {l.pinned && (
                <Pin className="mr-1 inline h-3 w-3 -translate-y-px text-muted" strokeWidth={2} aria-label="Pinned" />
              )}
              {l.text}
              <span className="ml-1.5 whitespace-nowrap text-[10px] text-faint">
                {kindLabel(l.kind)} · {l.src}
              </span>
            </span>
          </li>
        ))}
      </ol>
    </details>
  );
}
