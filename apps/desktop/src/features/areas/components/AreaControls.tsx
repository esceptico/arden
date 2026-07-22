import { useRef, useState } from "react";
import clsx from "clsx";
import { Settings2 } from "@/components/icons";
import type { AreaAgentStatus, AreaDetail } from "@/api/areas";
import { updateAreaSettings } from "@/actions/areas";
import { useStore } from "@/stores";
import { formatRelativeFuture, formatRelativePast } from "@/lib/format";
import { ICON } from "@/lib/icons";
import { AnchoredPopover } from "@/components/ui/AnchoredPopover";
import { RadioGroup, RadioGroupItem } from "@/components/ui/RadioGroup";
import { SwitchControl } from "@/components/ui/SwitchControl";

const ATTENTION_OPTIONS = [
  { value: "active", label: "Active" },
  { value: "ambient", label: "Ambient" },
  { value: "dormant", label: "Dormant" },
] as const;

const INTERRUPT_OPTIONS = [
  { value: "asks", label: "Questions & reviews" },
  { value: "all", label: "Everything" },
  { value: "none", label: "Never" },
] as const;

/** The custodian's liveness line — always present when an agent exists.
 *  A silent-stalled agent is the documented trust killer, so the room
 *  states plainly when it last looked, when it looks next, and why. */
export function agentExceptionalStatus(
  paused: boolean,
  availability: AreaAgentStatus["availability"],
  lastError: string | null,
): string | null {
  if (paused) return "Paused — the agent isn’t watching this area.";
  if (availability === "unavailable") return "Custodian unavailable — no check is scheduled.";
  if (availability === "error") return `Last check failed — ${lastError || "unknown error"}`;
  return null;
}

export function AgentStatusLine({ detail }: { detail: AreaDetail }) {
  const agent = detail.agent;
  if (!agent) return null;
  const exceptional = agentExceptionalStatus(detail.paused, agent.availability, agent.last_error);
  if (exceptional) {
    return <p className="m-0 text-xs text-faint">{exceptional}</p>;
  }
  if (agent.running_since) {
    return <p className="m-0 text-xs text-faint">Checking now…</p>;
  }
  const parts: string[] = [];
  if (agent.last_checked) parts.push(`Checked ${formatRelativePast(agent.last_checked)} ago`);
  if (agent.next_check) parts.push(`next ${formatRelativeFuture(agent.next_check)}`);
  const line = parts.join(" · ");
  if (!line && !agent.next_check_reason) return null;
  return (
    <p className="m-0 min-w-0 truncate text-xs text-faint" title={agent.woken_by.length ? `Last wake: ${agent.woken_by.join("; ")}` : undefined}>
      {line}
      {agent.next_check_reason && <span className="text-whisper"> — {agent.next_check_reason}</span>}
    </p>
  );
}

/** Mini-settings per area (the operator panel): attention, interrupts,
 *  pause, and the run budget. The defaults are the product — this popover
 *  is the escape hatch, one compact panel in the room. */
export function AreaSettingsButton({ detail }: { detail: AreaDetail }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);

  const apply = async (patch: Parameters<typeof updateAreaSettings>[1]) => {
    if (busy) return;
    setBusy(true);
    try {
      await updateAreaSettings(detail.key, patch);
    } catch {
      useStore.getState().pushToast({
        id: `area-settings-fail:${detail.key}`,
        title: "Couldn’t update area settings",
        status: "failed",
        target: { kind: "automation" },
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Area settings"
        aria-haspopup="menu"
        aria-expanded={open}
        title="Area settings"
        className={clsx(
          "grid h-7 w-7 shrink-0 place-items-center rounded-full border transition-[color,border-color,scale] duration-check ease-out active:scale-[0.97]",
          open ? "border-line-strong text-ink" : "border-line-soft text-muted hover:border-line-strong hover:text-ink",
        )}
      >
        <Settings2 size={ICON.SM} strokeWidth={2} />
      </button>
      <AnchoredPopover
        open={open}
        onClose={() => setOpen(false)}
        anchor={triggerRef}
        className="w-[220px] py-1.5"
      >
        <SectionLabel>Attention</SectionLabel>
        <RadioGroup
          dense
          value={detail.attention}
          onChange={(v) => void apply({ attention: v as AreaDetail["attention"] })}
          aria-label="Agent attention level"
          className="px-1"
        >
          {ATTENTION_OPTIONS.map((opt, i) => (
            <RadioGroupItem key={opt.value} index={i} value={opt.value} label={opt.label} indicator="check" />
          ))}
        </RadioGroup>
        <div className="my-1 h-px bg-line-soft" />
        <SectionLabel>Notify me about</SectionLabel>
        <RadioGroup
          dense
          value={detail.interrupts}
          onChange={(v) => void apply({ interrupts: v as AreaDetail["interrupts"] })}
          aria-label="Which asks send a notification"
          className="px-1"
        >
          {INTERRUPT_OPTIONS.map((opt, i) => (
            <RadioGroupItem key={opt.value} index={i} value={opt.value} label={opt.label} indicator="check" />
          ))}
        </RadioGroup>
        <div className="my-1 h-px bg-line-soft" />
        <div className="flex items-center justify-between gap-3 px-2.5 py-1 text-sm text-ink select-none">
          <span>Paused</span>
          <SwitchControl
            size="sm"
            checked={detail.paused}
            onChange={(next) => void apply({ paused: next })}
            aria-label="Pause the agent"
          />
        </div>
        {detail.agent && (
          <p className="m-0 px-2.5 pt-1 pb-0.5 text-2xs text-whisper tabular-nums">
            {detail.agent.runs_today} of {detail.agent.runs_cap} runs today
          </p>
        )}
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
