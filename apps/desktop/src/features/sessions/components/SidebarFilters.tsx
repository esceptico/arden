import { useRef, useState } from "react";
import { SlidersHorizontal } from "lucide-react";
import clsx from "clsx";
import { useStore } from "@/stores";
import type { SidebarGroupBy } from "@/stores/types";
import { ICON } from "@/lib/icons";
import { AnchoredPopover } from "@/components/ui/AnchoredPopover";
import { RadioGroup, RadioGroupItem } from "@/components/ui/RadioGroup";
import { SwitchControl } from "@/components/ui/SwitchControl";

const GROUP_OPTIONS: { value: SidebarGroupBy; label: string }[] = [
  { value: "project", label: "Slice" },
  { value: "time", label: "Time" },
  { value: "type", label: "Type" },
  { value: "status", label: "Status" },
];

export function SidebarFilters() {
  const groupBy = useStore((s) => s.prefs.sidebarGroupBy);
  const unreadOnly = useStore((s) => s.prefs.sidebarUnreadOnly);
  const channelsOnly = useStore((s) => s.prefs.sidebarChannelsOnly);
  const setPref = useStore((s) => s.setPref);

  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);

  // Non-default view = surfaced on the trigger so the user knows a filter
  // is hiding sessions even when the popover is closed.
  const active = groupBy !== "project" || unreadOnly || channelsOnly;

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Filter and group sessions"
        aria-haspopup="menu"
        aria-expanded={open}
        title="Filter & group"
        className={clsx(
          "grid place-items-center w-[26px] h-[22px] rounded-[5px] transition-colors",
          active || open
            ? "text-ink bg-surface-soft/80"
            : "text-faint hover:text-ink hover:bg-surface-soft/70",
        )}
      >
        <SlidersHorizontal size={ICON.SM} strokeWidth={2} />
      </button>
      <AnchoredPopover
        open={open}
        onClose={() => setOpen(false)}
        anchor={triggerRef}
        className="w-[200px] py-1.5"
      >
        <SectionLabel>Group by</SectionLabel>
        <RadioGroup
          dense
          value={groupBy}
          onChange={(v) => setPref("sidebarGroupBy", v as SidebarGroupBy)}
          aria-label="Group sessions by"
          className="px-1"
        >
          {GROUP_OPTIONS.map((opt, i) => (
            <RadioGroupItem key={opt.value} index={i} value={opt.value} label={opt.label} indicator="check" />
          ))}
        </RadioGroup>
        <div className="my-1 h-px bg-line-soft" />
        <SectionLabel>Filter</SectionLabel>
        <div className="px-2.5 py-0.5">
          <FilterSwitch
            label="Unread only"
            checked={unreadOnly}
            onChange={(next) => setPref("sidebarUnreadOnly", next)}
          />
        </div>
        <div className="my-1 h-px bg-line-soft" />
        <SectionLabel>Show</SectionLabel>
        {/* Codex-style checkmark pair: both poles named, the ✓ marks which
            list you're on — nothing to misread as additive. */}
        <RadioGroup
          dense
          value={channelsOnly ? "agents" : "chats"}
          onChange={(v) => setPref("sidebarChannelsOnly", v === "agents")}
          aria-label="Show in the sidebar"
          className="px-1"
        >
          <RadioGroupItem index={0} value="chats" label="Your chats" indicator="check" />
          <RadioGroupItem index={1} value="agents" label="Agent activity" indicator="check" />
        </RadioGroup>
      </AnchoredPopover>
    </>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-2.5 pt-1 pb-0.5 text-2xs font-medium uppercase tracking-[0.06em] text-muted select-none">
      {children}
    </div>
  );
}

function FilterSwitch({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-3 py-1 text-sm text-ink select-none">
      <span>{label}</span>
      <SwitchControl size="sm" checked={checked} onChange={onChange} aria-label={label} />
    </div>
  );
}
