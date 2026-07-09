import clsx from "clsx";
import { Plus, X } from "lucide-react";
import type { SliceSuggestion, SliceSummary } from "@/api/slices";
import { useStore } from "@/stores";
import { dismissSliceSuggestion, promoteSuggestedSlice } from "@/actions/slices";
import { ICON } from "@/lib/icons";

/** Horizontal strip of every slice as a tonal chip: a live dot marks slices
 *  with active asks or a running agent; quiet slices sit at 55% opacity so
 *  the live ones read as "where attention currently is."
 *
 *  Suggested slices (the daily suggester's picks from unpromoted topic
 *  pages) render as ghost chips at the end — dashed, with a plus; click
 *  promotes the page into a real slice, the ✕ dismisses it permanently.
 *  The rationale rides the tooltip so accepting is an informed one-click. */
export function SlicesStrip({
  slices,
  suggested = [],
}: {
  slices: SliceSummary[];
  suggested?: SliceSuggestion[];
}) {
  const openSlice = useStore((s) => s.openSlice);

  if (slices.length === 0 && suggested.length === 0) return null;

  return (
    <div className="grid gap-2">
      <span className="text-2xs font-semibold tracking-wide text-faint uppercase">Slices</span>
      <div className="flex flex-wrap gap-1.5">
        {slices.map((slice) => (
          <button
            key={slice.key}
            type="button"
            onClick={() => openSlice(slice.key)}
            className={clsx(
              "inline-flex h-8 items-center rounded-full bg-surface-soft px-3 text-xs font-medium text-ink",
              "transition-[opacity,background-color,scale] duration-check ease-out",
              "hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent active:scale-[0.96]",
              !slice.live && "opacity-50 hover:opacity-100",
            )}
          >
            <span className="truncate">{slice.title}</span>
          </button>
        ))}
        {suggested.map((s) => (
          <span
            key={s.key}
            className="inline-flex h-8 items-center gap-1 rounded-full border border-dashed border-line pr-1.5 pl-3 text-xs text-muted"
            title={s.rationale}
          >
            <button
              type="button"
              onClick={() => void promoteSuggestedSlice(s.title, s.page_path)}
              className="inline-flex items-center gap-1 hover:text-ink"
              title={`${s.rationale} — click to add`}
            >
              <Plus size={ICON.XS} strokeWidth={2} />
              <span className="truncate">{s.title}</span>
            </button>
            <button
              type="button"
              onClick={() => void dismissSliceSuggestion(s.key)}
              aria-label={`Dismiss ${s.title} suggestion`}
              className="grid size-5 place-items-center rounded-full text-whisper hover:bg-surface-soft hover:text-ink-soft"
            >
              <X size={ICON.XS} strokeWidth={2} />
            </button>
          </span>
        ))}
      </div>
    </div>
  );
}
