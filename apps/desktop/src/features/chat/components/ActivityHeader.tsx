import { ChevronDown, SquareTerminal } from "@/components/icons";
import { useStore, type ActivityLabel } from "@/stores";
import { BlurSwap } from "@/components/ui/BlurSwap";
import { RollingToken } from "@/components/ui/RollingToken";
import { formatTurnDuration } from "@/features/chat/lib/turnHeader";

export function ActivityHeader({
  done,
  label,
  count,
  activeCount = 0,
  backgrounded = false,
  durationMs,
  motionDisabled,
  onToggle,
  expanded,
  railAnchor = false,
  railLabel,
}: {
  done: boolean;
  label?: ActivityLabel;
  count: number;
  activeCount?: number;
  backgrounded?: boolean;
  durationMs?: number | null;
  motionDisabled?: boolean;
  onToggle?: () => void;
  expanded?: boolean;
  railAnchor?: boolean;
  railLabel?: string;
}) {
  const word = count === 1 ? "call" : "calls";
  const heading = backgrounded
    ? "Backgrounded"
    : label === "Stopped"
      ? "Stopped"
      : done
        ? "Worked"
        : "Working";
  const interactive = !!onToggle;
  const streamReplaying = useStore((s) => s.streamReplaying);
  const suppressMotion = motionDisabled ?? streamReplaying;

  return (
    <button
      type={interactive ? "button" : undefined}
      onClick={onToggle}
      disabled={!interactive}
      aria-expanded={interactive ? expanded : undefined}
      data-chat-rail-anchor={railAnchor ? "" : undefined}
      data-chat-rail-label={railLabel}
      className="board-trace__toggle"
    >
      <SquareTerminal aria-hidden />
      <span aria-live="polite">
        {suppressMotion
          ? heading
          : <BlurSwap swapKey={heading}>{heading}</BlurSwap>}
      </span>
      <span aria-hidden>·</span>
      <span className="board-trace__count">
        <RollingToken value={String(count)} mono motionDisabled={suppressMotion} />
        <RollingToken value={word} motionDisabled={suppressMotion} />
        {durationMs != null && (
          <>
            <span aria-hidden>·</span>
            <span>{formatTurnDuration(durationMs)}</span>
          </>
        )}
      </span>
      {activeCount > 0 && (
        <span className="board-trace__count">
          <RollingToken value={String(activeCount)} mono motionDisabled={suppressMotion} />
          <span>active</span>
        </span>
      )}
      {interactive && (
        <ChevronDown
          aria-hidden
          className="board-trace__chevron"
        />
      )}
    </button>
  );
}
