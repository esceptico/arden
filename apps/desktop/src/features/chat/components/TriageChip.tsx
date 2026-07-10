import { X } from "lucide-react";
import { useStore } from "@/stores";
import { acceptTriage, dismissTriage } from "@/actions/triage";
import { ICON } from "@/lib/icons";
import { Collapse } from "@/components/ui/Collapse";

/** Quiet filing proposal for a just-started chat: a single tonal pill above
 *  the composer (same chip family as the area suggestions). The body
 *  accepts, the ✕ dismisses; the rationale rides the tooltip. Silent —
 *  nothing renders until triage returns a confident move/create.
 *
 *  Wrapped in Collapse (small, bounded content) so the measured bottom
 *  stack grows and shrinks smoothly — a raw mount/unmount here snaps the
 *  composer clearance by the chip's height. */
export function TriageChip() {
  const sessionId = useStore((s) => s.currentSessionId);
  const proposal = useStore((s) =>
    s.currentSessionId ? s.triage.proposalBySession[s.currentSessionId] : undefined,
  );

  const label =
    proposal?.decision === "move"
      ? proposal.target?.title
      : proposal?.decision === "create"
        ? proposal.new_title
        : null;
  const verb = proposal?.decision === "move" ? "Move to" : "New";
  const show = Boolean(sessionId && proposal && label);

  return (
    <Collapse open={show}>
      <div className="flex justify-center pb-2">
        {sessionId && (
          <div className="inline-flex max-w-full items-center gap-1 rounded-full bg-surface-soft py-1 pr-1.5 pl-3 text-xs text-ink-soft">
            <button
              type="button"
              onClick={() => void acceptTriage(sessionId)}
              title={proposal?.rationale}
              className="inline-flex min-w-0 items-center transition-transform hover:text-ink active:scale-[0.98]"
            >
              <span className="truncate">
                {verb} <span className="font-medium text-ink">{label}</span>?
              </span>
            </button>
            <button
              type="button"
              onClick={() => dismissTriage(sessionId)}
              aria-label="Dismiss suggestion"
              className="grid size-5 shrink-0 place-items-center rounded-full text-whisper transition-colors hover:bg-surface hover:text-ink-soft"
            >
              <X size={ICON.XS} strokeWidth={2} />
            </button>
          </div>
        )}
      </div>
    </Collapse>
  );
}
