import { CheckCircle2, Pause, Play, Target } from "@/components/icons";
import { clearGoal, updateGoal } from "@/actions/goals";
import { useStore } from "@/stores";
import { ICON } from "@/lib/icons";
import { IconSwap } from "@/components/ui/IconSwap";
import { Badge } from "@/components/ui/Badge";
import { Chip } from "@/components/ui/Chip";
import { IconButton } from "@/components/ui/IconButton";
import { ConfirmDeleteButton } from "@/components/ui/ConfirmDeleteButton";
import { HoverPopover } from "@/components/ui/HoverPopover";

export function GoalStatusBar() {
  const sessionId = useStore((s) => s.currentSessionId);
  const goal = useStore((s) => (sessionId ? s.goals[sessionId] : null));

  if (!goal) return null;

  const paused = goal.status === "paused";
  const complete = goal.status === "complete";

  return (
    <div className="relative flex items-center">
      <HoverPopover
        className="w-[360px] p-3"
        trigger={({ ref, toggle, hoverProps }) => (
          <Chip
            ref={ref}
            size="sm"
            tone="accent"
            variant="ghost"
            active={!complete}
            leading={<Target size={ICON.SM} strokeWidth={2} />}
            {...hoverProps}
            onClick={toggle}
            aria-label="Session goal"
            title={goal.objective}
            className="max-w-[220px]"
          >
            <span className="truncate">
              Goal · {goal.status.replace("_", " ")}
            </span>
          </Chip>
        )}
      >
        <div className="mb-2 flex items-center gap-2">
          <Target size={ICON.SM} strokeWidth={2} className="text-accent" />
          <span className="text-xs font-medium text-muted">Goal</span>
          <Badge variant="outline" tone="neutral" shape="rounded" className="ml-auto">
            {goal.status.replace("_", " ")}
          </Badge>
        </div>
        <div className="scroll-thin max-h-36 overflow-auto text-sm text-ink-soft whitespace-pre-wrap">
          {goal.objective}
        </div>
        <div className="mt-3 flex items-center gap-1">
          {!complete && (
            <IconButton
              tone="faint"
              onClick={() => void updateGoal(paused ? "active" : "paused")}
              title={paused ? "Resume goal" : "Pause goal"}
              aria-label={paused ? "Resume goal" : "Pause goal"}
            >
              <IconSwap
                state={paused ? "b" : "a"}
                iconA={<Pause size={ICON.SM} />}
                iconB={<Play size={ICON.SM} />}
              />
            </IconButton>
          )}
          {!complete && (
            <IconButton
              tone="faint"
              onClick={() => void updateGoal("complete")}
              title="Mark complete"
              aria-label="Mark complete"
            >
              <CheckCircle2 size={ICON.SM} />
            </IconButton>
          )}
          <ConfirmDeleteButton
            size="sm"
            label="Clear goal"
            className="ml-auto"
            onConfirm={() => void clearGoal()}
          />
        </div>
      </HoverPopover>
    </div>
  );
}

export function GoalStrip() {
  return <GoalStatusBar />;
}
