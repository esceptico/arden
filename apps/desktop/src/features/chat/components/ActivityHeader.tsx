import { ChevronRight, SquareTerminal } from "@/components/icons";
import { useStore, type ActivityLabel } from "@/stores";
import { BlurSwap } from "@/components/ui/BlurSwap";
import { RollingToken } from "@/components/ui/RollingToken";
import { turnHeaderLabel } from "@/features/chat/lib/turnHeader";

export function ActivityHeader({
  done,
  label,
  count,
  activeCount = 0,
  backgrounded = false,
  liveHeading,
  durationMs,
  motionDisabled,
  onToggle,
  expanded,
}: {
  done: boolean;
  label?: ActivityLabel;
  count: number;
  activeCount?: number;
  backgrounded?: boolean;
  liveHeading?: string;
  durationMs?: number | null;
  motionDisabled?: boolean;
  onToggle?: () => void;
  expanded?: boolean;
}) {
  const word = count === 1 ? "call" : "calls";
  // A settled turn reads as one quiet sentence — "Worked for 1m 59s" /
  // "Stopped after 40s" — with the call count left to the expanded rows.
  // Live turns keep their working numbers: those are information, not recap.
  const settled = !backgrounded && (done || label === "Stopped");
  const heading = backgrounded
    ? "Backgrounded"
    : settled
      ? turnHeaderLabel(durationMs, label === "Stopped")
      : liveHeading ?? "Working";
  const interactive = !!onToggle;
  const streamReplaying = useStore((s) => s.streamReplaying);
  const suppressMotion = motionDisabled ?? streamReplaying;

  return (
    <button
      type={interactive ? "button" : undefined}
      onClick={onToggle}
      disabled={!interactive}
      aria-expanded={interactive ? expanded : undefined}
      className="board-trace__toggle"
    >
      <SquareTerminal aria-hidden />
      <span aria-live="polite">
        {suppressMotion
          ? heading
          : <BlurSwap swapKey={heading}>{heading}</BlurSwap>}
      </span>
      {!settled && (
        <>
          <span aria-hidden>·</span>
          <span className="board-trace__count">
            <RollingToken value={String(count)} motionDisabled={suppressMotion} />
            <RollingToken value={word} motionDisabled={suppressMotion} />
          </span>
        </>
      )}
      {!settled && activeCount > 0 && (
        <span className="board-trace__count">
          <RollingToken value={String(activeCount)} motionDisabled={suppressMotion} />
          <span>active</span>
        </span>
      )}
      {interactive && (
        <ChevronRight
          aria-hidden
          className="board-trace__chevron"
        />
      )}
    </button>
  );
}
