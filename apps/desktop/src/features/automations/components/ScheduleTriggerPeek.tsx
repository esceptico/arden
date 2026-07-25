import { useRef } from "react";
import { X } from "@/components/icons";
import { Button } from "@/components/ui/Button";
import { IconButton } from "@/components/ui/IconButton";
import { PeekSurface } from "@/components/workspace/PeekSurface";
import { ICON } from "@/lib/icons";
import {
  formatTime12,
  humanDays,
  splitKeywords,
  type Schedule,
} from "@/features/automations/lib/schedule";
import { ScheduleEditor } from "@/features/automations/components/ScheduleEditor";

function receiptFor(schedule: Schedule): { main: string; meta: string } {
  if (schedule.kind === "at") {
    return {
      main: formatTime12(schedule.at) || "Choose a time",
      meta: humanDays(schedule.days) || "Daily",
    };
  }

  if (schedule.kind === "every") {
    const window = schedule.start && schedule.end
      ? `${formatTime12(schedule.start)}–${formatTime12(schedule.end)}`
      : "Around the clock";
    return {
      main: schedule.every ? `Every ${schedule.every}` : "Choose an interval",
      meta: `${humanDays(schedule.days) || "Daily"} · ${window}`,
    };
  }

  if (schedule.kind === "message") {
    const channels = splitKeywords(schedule.channel);
    const main = channels.length === 0
      ? "Choose a channel"
      : channels.length === 1
        ? `#${channels[0]}`
        : `#${channels[0]} +${channels.length - 1}`;
    const gates = [
      schedule.fromUser.trim() ? `from @${schedule.fromUser.trim()}` : "any sender",
      splitKeywords(schedule.keywords).length > 0 ? "matching terms" : "any message",
    ];
    return { main, meta: gates.join(" · ") };
  }

  if (schedule.event === "approaching") {
    return {
      main: `${schedule.lead.trim() || "15"} min before events`,
      meta: "Calendar",
    };
  }

  return {
    main: schedule.event === "starts" ? "When events start" : "When events end",
    meta: "Calendar",
  };
}

/**
 * A full-height, nonblocking Trigger Peek. Its caller owns `schedule` as a
 * draft, so Escape, close, and Cancel preserve the saved automation exactly.
 */
export function ScheduleTriggerPeek({
  open,
  schedule,
  onChange,
  onSave,
  onClose,
}: {
  open: boolean;
  schedule: Schedule;
  onChange: (next: Schedule) => void;
  onSave: (next: Schedule) => void;
  onClose: () => void;
}) {
  // PeekSurface retains its exiting child. Keep the last draft available until
  // its visual exit finishes instead of flashing back to the saved value.
  const retainedSchedule = useRef(schedule);
  if (open) retainedSchedule.current = schedule;
  const visibleSchedule = retainedSchedule.current;
  const valid = visibleSchedule.kind !== "message" || splitKeywords(visibleSchedule.channel).length > 0;
  const receipt = receiptFor(visibleSchedule);

  return (
    <PeekSurface
      open={open}
      onClose={onClose}
      className="automation-trigger-peek surface-peek"
      ariaLabel="Edit trigger"
      layer="automation-trigger-peek"
      closeOnOutsidePointerDown
      outsidePointerDownIgnoreSelector=".surface-popover, [data-trigger-peek-owner]"
    >
      <header className="automation-trigger-peek__header arden-peek-rule-below">
        <b>Trigger</b>
        <IconButton
          data-peek-close
          onClick={onClose}
          aria-label="Close trigger setup"
          title="Close trigger setup"
        >
          <X size={ICON.SM} />
        </IconButton>
      </header>

      <ScheduleEditor schedule={visibleSchedule} onChange={onChange} />

      <footer className="automation-trigger-peek__actions arden-peek-rule-above">
        <div className="automation-trigger-peek__summary">
          <span>Starts</span>
          <div className="automation-trigger-peek__receipt" title={`${receipt.main} · ${receipt.meta}`}>
            <b>{receipt.main}</b>
            <span>{receipt.meta}</span>
          </div>
        </div>
        <div className="automation-trigger-peek__footer">
          <Button variant="quiet" size="md" onClick={onClose}>Cancel</Button>
          <Button variant="primary" size="md" disabled={!valid} onClick={() => onSave(visibleSchedule)}>
            Save trigger
          </Button>
        </div>
      </footer>
    </PeekSurface>
  );
}
