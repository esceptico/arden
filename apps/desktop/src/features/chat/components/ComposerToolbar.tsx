import { ArrowUp, ImagePlus, ShieldOff, ShieldCheck, Stop } from "@/components/icons";
import clsx from "clsx";
import { Chip } from "@/components/ui/Chip";
import { IconSwap } from "@/components/ui/IconSwap";
import { ModelReasoningChip } from "@/components/ui/ComposerSelectors";
import { IconButton } from "@/components/ui/IconButton";
import { GoalStatusBar } from "@/features/chat/components/GoalStrip";
import { LoopStatusBar } from "@/features/chat/components/LoopStatus";
import { BudgetDial } from "@/features/chat/components/BudgetDial";
import { ICON } from "@/lib/icons";

export function ComposerToolbar({
  onAttach,
  skipApprovals,
  onToggleAuto,
  running,
  sendDisabled,
  sendPressing,
  onStop,
}: {
  onAttach: () => void;
  skipApprovals: boolean;
  onToggleAuto: () => void;
  running: boolean;
  sendDisabled: boolean;
  sendPressing: boolean;
  onStop: () => void;
}) {
  return (
    <div className="composer-toolbar flex items-center gap-1.5 px-2 pt-1.5 pb-2">
      <IconButton
        shape="circle"
        onClick={onAttach}
        aria-label="Attach image"
        title="Attach image"
      >
        <ImagePlus size={ICON.LG} strokeWidth={2} />
      </IconButton>
      <Chip
        size="sm"
        active={skipApprovals}
        tone="accent"
        leading={
          <IconSwap
            state={skipApprovals ? "b" : "a"}
            iconA={<ShieldCheck size={ICON.SM} strokeWidth={2} />}
            iconB={<ShieldOff size={ICON.SM} strokeWidth={2} />}
          />
        }
        onClick={onToggleAuto}
        title={skipApprovals ? "Auto-approving every tool call. Click to require approval." : "Approvals required for sensitive tools. Click to enable Auto mode."}
        aria-label={skipApprovals ? "Auto-approve enabled — click to require approval" : "Click to enable auto-approve"}
      >
        <span className="composer-chip-label">{skipApprovals ? "Auto" : "Approve"}</span>
      </Chip>
      <LoopStatusBar />
      <GoalStatusBar />
      <span className="flex-1" />
      <BudgetDial />
      <ModelReasoningChip />
      {/* One persistent button; the shared icon primitive keeps both glyphs
          mounted and changes only their visual state. */}
      <button
        type={running ? "button" : "submit"}
        onClick={running ? onStop : undefined}
        disabled={!running && sendDisabled}
        data-send={running ? undefined : "true"}
        aria-label={running ? "Stop" : "Send"}
        title={running ? "Stop (Esc)" : undefined}
        // active:scale handles mouse press; sendPressing covers keyboard
        // Enter (form-submit doesn't fire :active). Both look identical.
        className={clsx(
          "grid place-items-center w-7 h-7 rounded-full bg-ink text-on-ink shadow-sm hover:opacity-90 disabled:opacity-[0.45] disabled:shadow-none transition-[opacity,scale] duration-fast ease-out active:scale-[0.97]",
          sendPressing && "scale-[0.97]",
        )}
      >
        <IconSwap
          state={running ? "b" : "a"}
          iconA={<ArrowUp size={ICON.LG} strokeWidth={2.4} />}
          iconB={<Stop size={ICON.SM} />}
        />
      </button>
    </div>
  );
}
