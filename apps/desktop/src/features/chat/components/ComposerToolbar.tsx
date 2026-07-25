import { ArrowUp, ImagePlus, ShieldOff, ShieldCheck, Stop } from "@/components/icons";
import { Chip } from "@/components/ui/Chip";
import { IconSwap } from "@/components/ui/IconSwap";
import { ModelReasoningChip } from "@/components/ui/ModelPickers";
import { IconButton } from "@/components/ui/IconButton";
import { GoalStatusBar } from "@/features/chat/components/GoalStrip";
import { LoopStatusBar } from "@/features/chat/components/LoopStatus";
import { BudgetDial } from "@/features/chat/components/BudgetDial";
import { ICON } from "@/lib/icons";
import type { ThinkingAnimation, ThinkingIntensity } from "@/stores";

export type ComposerAction = "send" | "queue" | "stop";

export function ComposerToolbar({
  onAttach,
  skipApprovals,
  onToggleAuto,
  action,
  sendDisabled,
  queueFull = false,
  working,
  thinkingAnimation,
  thinkingIntensity,
  onStop,
}: {
  onAttach: () => void;
  skipApprovals: boolean;
  onToggleAuto: () => void;
  action: ComposerAction;
  sendDisabled: boolean;
  queueFull?: boolean;
  working: boolean;
  thinkingAnimation: ThinkingAnimation;
  thinkingIntensity: ThinkingIntensity;
  onStop: () => void;
}) {
  const stopping = action === "stop";
  const actionLabel = stopping ? "Stop response" : action === "queue" ? "Queue message" : "Send";
  const actionTitle = queueFull
    ? "Queue full · 10 messages maximum · ⌘Enter to steer"
    : action === "queue"
      ? "Queue message · ⌘Enter to steer"
      : actionLabel;
  return (
    <div className="board-composer__toolbar flex items-center gap-1.5 px-2 pt-1.5 pb-2">
      <IconButton
        shape="circle"
        className="composer-tool-button composer-toolbar-control"
        onClick={onAttach}
        aria-label="Attach image"
        title="Attach image"
      >
        <ImagePlus size={ICON.LG} />
      </IconButton>
      <Chip
        size="sm"
        className="composer-tool-button composer-toolbar-control composer-mode-button"
        active={skipApprovals}
        tone="accent"
        leading={
          <IconSwap
            state={skipApprovals ? "b" : "a"}
            iconA={<ShieldCheck size={ICON.SM} />}
            iconB={<ShieldOff size={ICON.SM} />}
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
      {working && <span className="board-composer__status arden-status" aria-hidden>working</span>}
      <span className="flex-1" />
      <BudgetDial />
      <ModelReasoningChip />
      {/* One persistent button; the shared icon primitive keeps both glyphs
          mounted and changes only their visual state. */}
      <button
        type={stopping ? "button" : "submit"}
        onClick={stopping ? onStop : undefined}
        disabled={!stopping && sendDisabled}
        aria-label={actionLabel}
        title={actionTitle}
        data-mode={action}
        data-thinking-animation={thinkingAnimation}
        data-thinking-intensity={thinkingIntensity}
        data-working={working ? "true" : "false"}
        className="board-composer__send hover:opacity-90 disabled:opacity-[0.45] disabled:shadow-none transition-[opacity,scale] duration-fast ease-out active:scale-[0.97]"
      >
        <IconSwap
          state={stopping ? "b" : "a"}
          iconA={<ArrowUp size={ICON.LG} />}
          iconB={<Stop size={ICON.SM} />}
        />
      </button>
    </div>
  );
}
