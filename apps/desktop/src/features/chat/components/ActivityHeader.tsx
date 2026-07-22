import { ChevronDown, SquareTerminal } from "@/components/icons";
import clsx from "clsx";
import { useStore, type ActivityLabel } from "@/stores";
import { RollingToken } from "@/components/ui/RollingToken";
import { Marker, MarkerIcon } from "@/components/ui/Marker";
import { ICON } from "@/lib/icons";

export function ActivityHeader({
  done,
  label,
  count,
  activeCount = 0,
  backgrounded = false,
  motionDisabled,
  onToggle,
  expanded,
}: {
  done: boolean;
  label?: ActivityLabel;
  count: number;
  activeCount?: number;
  backgrounded?: boolean;
  motionDisabled?: boolean;
  onToggle?: () => void;
  expanded?: boolean;
}) {
  const word = count === 1 ? "call" : "calls";
  const heading = backgrounded
    ? "Backgrounded"
    : label === "Stopped"
      ? "Stopped"
      : activeCount > 0
        ? "Running"
        : done
          ? "Executed"
          : "Calling";
  const interactive = !!onToggle;
  const streamReplaying = useStore((s) => s.streamReplaying);
  const suppressMotion = motionDisabled ?? streamReplaying;

  return (
    <Marker
      as="button"
      type={interactive ? "button" : undefined}
      onClick={onToggle}
      disabled={!interactive}
      aria-expanded={interactive ? expanded : undefined}
      className={clsx(
        "h-[18px] leading-[1.4] text-faint!",
        interactive
          ? "transition-colors hover:text-muted! active:text-ink-soft!"
          : "cursor-default!",
      )}
    >
      <MarkerIcon>
        <SquareTerminal strokeWidth={2} />
      </MarkerIcon>
      {/* Three odometer slots so the label flip ("Running" → "Done"),
          the digit roll (5 → 6 as another tool starts), and the
          singular/plural switch ("tool" / "tools") each animate
          independently instead of the whole string snapping. */}
      <span aria-live="polite" className="mr-1.5 inline-flex h-full items-center leading-none">
        <RollingToken value={heading} motionDisabled={suppressMotion} />
      </span>
      <span className="inline-flex h-full items-center gap-1 leading-none">
        <RollingToken value={String(count)} mono motionDisabled={suppressMotion} />
        <RollingToken value={word} motionDisabled={suppressMotion} />
      </span>
      {activeCount > 0 && (
        <span className="inline-flex h-full items-center gap-1.5 leading-none">
          <RollingToken value={String(activeCount)} mono motionDisabled={suppressMotion} />
          <span>active</span>
        </span>
      )}
      {interactive && (
        <ChevronDown
          size={ICON.SM}
          strokeWidth={2}
          className={clsx(
            "ml-1 self-center transition-transform duration-trace ease-out text-faint",
            expanded && "rotate-180",
          )}
        />
      )}
    </Marker>
  );
}
