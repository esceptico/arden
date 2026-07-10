import { useState } from "react";
import { Button } from "@/components/ui/Button";

/** OPEN LOOPS rows: v1 loops are plain strings (no id/state), so a row is
 *  collapsed to one truncated line and expands IN PLACE to its full wrapped
 *  text — one copy of the text, toggled between truncate and wrap. Every
 *  wrapper down the chain needs min-w-0: these are grid/flex items whose
 *  min-content size is the untruncated line, and without it long loops blow
 *  the track past the 640px column (bit us with real vault data). No
 *  per-row "agent completed" dimming yet — the shape carries no such flag;
 *  hook point for whenever that lands server-side. */
// Rooms with sprawling pages (dex: 21 loops) become walls of text — cap
// the resting view; "Show all" reveals the rest in place.
const VISIBLE_CAP = 7;

export function OpenLoops({
  loops,
  onDiscuss,
}: {
  loops: string[];
  onDiscuss?: (loop: string) => void;
}) {
  const [openIndex, setOpenIndex] = useState<number | null>(null);
  const [showAll, setShowAll] = useState(false);

  if (loops.length === 0) return null;
  const visible = showAll ? loops : loops.slice(0, VISIBLE_CAP);

  return (
    <div className="grid min-w-0 gap-2">
      <span className="text-2xs font-semibold tracking-wide text-faint uppercase">Open loops</span>
      {/* Quiet hairline list (mock: separators, no tonal bars) — tone is
          reserved for things that need attention; loops are ambient. */}
      <div className="grid min-w-0">
        {visible.map((loop, index) => {
          const open = openIndex === index;
          return (
            <div key={index} className={`min-w-0 ${index > 0 ? "border-t border-line-soft" : ""}`}>
              <button
                type="button"
                onClick={() => setOpenIndex(open ? null : index)}
                className="group flex w-full min-w-0 py-2.5 text-left text-sm text-ink-soft transition-colors hover:text-ink"
              >
                <span className={open ? "min-w-0 flex-1 whitespace-normal" : "min-w-0 flex-1 truncate"}>
                  {loop}
                </span>
              </button>
              {open && onDiscuss && (
                <div className="pb-2.5">
                  <Button variant="secondary" size="sm" onClick={() => onDiscuss(loop)}>
                    Discuss
                  </Button>
                </div>
              )}
            </div>
          );
        })}
      </div>
      {loops.length > VISIBLE_CAP && !showAll && (
        <button
          type="button"
          onClick={() => setShowAll(true)}
          className="justify-self-start text-xs text-faint hover:text-ink-soft"
        >
          Show all {loops.length}
        </button>
      )}
    </div>
  );
}
