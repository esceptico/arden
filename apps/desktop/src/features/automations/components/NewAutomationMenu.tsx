import { useMemo, type RefObject } from "react";
import { useStore } from "@/stores";
import type { CreateAutomationPayload } from "@/api/types";
import { suggestionToPayload } from "@/api/automations";
import { formatTrigger } from "@/lib/agentRun";
import { TEMPLATES } from "@/features/automations/lib/templates";
import { AnchoredPopover } from "@/components/ui/AnchoredPopover";
import { MenuItem } from "@/components/ui/MenuItem";

function SectionLabel({ children }: { children: string }) {
  return (
    <div className="px-2.5 pt-2 pb-1 text-2xs font-medium uppercase tracking-[0.08em] text-faint">
      {children}
    </div>
  );
}

/** The "New" flow, demoted from a destination tab to a menu: personalized
 *  suggestions first (with the why), then templates, then scratch. Picking
 *  anything seeds a draft in the detail pane — nothing is created until the
 *  user hits Create there. */
export function NewAutomationMenu({
  open,
  onClose,
  anchor,
  onPick,
}: {
  open: boolean;
  onClose: () => void;
  anchor: RefObject<HTMLElement | null>;
  onPick: (preset: CreateAutomationPayload | null) => void;
}) {
  const suggestions = useStore((s) => s.automationSuggestions);
  const top = useMemo(() => (suggestions ?? []).slice(0, 3), [suggestions]);

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
      className="w-[320px] p-1.5"
    >
      {top.length > 0 && (
        <>
          <SectionLabel>For you</SectionLabel>
          {top.map((s) => (
            <MenuItem
              key={s.id}
              role="menuitem"
              className="rounded-[7px]"
              onClick={() => pick(suggestionToPayload(s))}
            >
              <span className="grid gap-0.5 py-0.5 min-w-0">
                <span className="flex items-baseline gap-2 min-w-0">
                  <span className="truncate">{s.name}</span>
                  <span className="ml-auto shrink-0 font-mono tabular-nums text-2xs text-faint">
                    {s.triggers[0] ? formatTrigger(s.triggers[0]) : ""}
                  </span>
                </span>
                <span className="text-xs text-muted leading-[1.45] line-clamp-2 whitespace-normal">
                  {s.rationale}
                </span>
              </span>
            </MenuItem>
          ))}
          <div className="h-1.5" />
        </>
      )}
      <SectionLabel>Templates</SectionLabel>
      {TEMPLATES.map((t) => (
        <MenuItem
          key={t.id}
          role="menuitem"
          dense
          className="rounded-[7px]"
          leading={<t.icon size={13} strokeWidth={2} className="text-faint" />}
          onClick={() => pick(t.payload)}
        >
          <span className="flex items-baseline gap-2 min-w-0 w-full">
            <span className="truncate">{t.name}</span>
            <span className="ml-auto shrink-0 font-mono tabular-nums text-2xs text-faint">
              {t.payload.every
                ? `every ${t.payload.every}`
                : t.payload.trigger_type === "message"
                  ? "on message"
                  : (t.payload.days ?? "daily")}
            </span>
          </span>
        </MenuItem>
      ))}
      <div className="h-1.5" />
      <MenuItem role="menuitem" className="rounded-[7px] text-muted" onClick={() => pick(null)}>
        Start from scratch…
      </MenuItem>
    </AnchoredPopover>
  );
}
