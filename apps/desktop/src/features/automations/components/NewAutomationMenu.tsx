import type { RefObject } from "react";
import type { AutomationSuggestion, AutomationTrigger, CreateAutomationPayload } from "@/api/types";
import { suggestionToPayload } from "@/api/automations";
import { TEMPLATES } from "@/features/automations/lib/templates";
import { AnchoredPopover } from "@/components/ui/AnchoredPopover";
import { MenuItem } from "@/components/ui/MenuItem";

function SectionLabel({ children }: { children: string }) {
  return (
    <div className="px-2.5 pt-2 pb-1 text-2xs font-[570] leading-none uppercase tracking-[.07em] text-faint">
      {children}
    </div>
  );
}

function cadenceForPayload(payload: Pick<CreateAutomationPayload, "every" | "trigger_type" | "days">): string {
  if (payload.every) return `every ${payload.every}`;
  if (payload.trigger_type === "event") return "on event";
  if (payload.trigger_type === "message") return "on message";
  return payload.days ?? "daily";
}

function cadenceForTrigger(trigger: AutomationTrigger): string {
  if (trigger.type === "time") return trigger.every ? `every ${trigger.every}` : trigger.days ?? "daily";
  if (trigger.type === "event") return "on event";
  if (trigger.type === "message") return "on message";
  return trigger.type;
}

function RichRow({
  title,
  description,
  cadence,
  onClick,
}: {
  title: string;
  description: string;
  cadence?: string;
  onClick: () => void;
}) {
  return (
    <MenuItem
      rich
      role="menuitem"
      className="min-h-10 gap-3 rounded-[var(--r-row)] px-2.5 py-2 text-base active:scale-[var(--press-scale)]"
      trailing={cadence ? <em className="ml-auto shrink-0 whitespace-nowrap font-mono text-[10.5px] font-medium leading-none not-italic text-faint">{cadence}</em> : null}
      onClick={onClick}
    >
      <span className="grid min-w-0 gap-0.5">
        <b className="block text-xs font-[560] leading-[1.55]">{title}</b>
        <small className="block text-2xs leading-[1.35] text-faint">{description}</small>
      </span>
    </MenuItem>
  );
}

/** The "New" flow is deliberately small: an explicit template choice or a
 * blank draft. Nothing is created until the user confirms it in the detail
 * workspace. */
export function NewAutomationMenu({
  open,
  onClose,
  anchor,
  onPick,
  suggestion = null,
}: {
  open: boolean;
  onClose: () => void;
  anchor: RefObject<HTMLElement | null>;
  onPick: (preset: CreateAutomationPayload | null) => void;
  /** The top active server suggestion, if the runtime has one. */
  suggestion?: AutomationSuggestion | null;
}) {
  const pick = (preset: CreateAutomationPayload | null) => {
    onPick(preset);
    onClose();
  };

  return (
    <AnchoredPopover
      open={open}
      onClose={onClose}
      anchor={anchor}
      variant="menu"
      ariaLabel="New automation"
      className="w-[320px] max-w-[calc(100vw-2rem)] max-h-[calc(100vh-2rem)] overflow-auto !bg-[var(--surface-5)] p-[5px] !shadow-[var(--shadow-3)]"
    >
      {suggestion && (
        <>
          <SectionLabel>For you</SectionLabel>
          <RichRow
            title={suggestion.name}
            description={suggestion.rationale}
            cadence={cadenceForTrigger(suggestion.triggers[0])}
            onClick={() => pick(suggestionToPayload(suggestion))}
          />
        </>
      )}
      <SectionLabel>Templates</SectionLabel>
      {TEMPLATES.map((t) => (
        <RichRow
          key={t.id}
          title={t.name}
          description={t.blurb}
          cadence={cadenceForPayload(t.payload)}
          onClick={() => pick(t.payload)}
        />
      ))}
      <RichRow
        title="Start from scratch"
        description="Write instructions and choose a trigger."
        onClick={() => pick(null)}
      />
    </AnchoredPopover>
  );
}
