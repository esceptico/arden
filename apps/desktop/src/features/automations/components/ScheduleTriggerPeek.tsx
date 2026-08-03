import { useRef, useState } from "react";
import { ChevronDown, Plus, X } from "@/components/icons";
import { Button } from "@/components/ui/Button";
import { Collapse } from "@/components/ui/Collapse";
import { IconButton } from "@/components/ui/IconButton";
import { PeekSurface } from "@/components/workspace/PeekSurface";
import { ICON } from "@/lib/icons";
import {
  scheduleFromTrigger,
  scheduleLabel,
  scheduleValid,
  triggerFromSchedule,
  type Schedule,
} from "@/features/automations/lib/schedule";
import { ScheduleEditor } from "@/features/automations/components/ScheduleEditor";

/** The peek edits the automation's WHOLE trigger list — one editable schedule
 *  per row, the selected row expanding into the editor beneath it. */
function rowsFromDraft(draft: Schedule): Schedule[] {
  const head = { ...draft, otherTriggers: [] };
  return [head, ...draft.otherTriggers.map((trigger) => scheduleFromTrigger(trigger))];
}

function draftFromRows(rows: Schedule[]): Schedule {
  return { ...rows[0], otherTriggers: rows.slice(1).map((row) => triggerFromSchedule(row)) };
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
  const rows = rowsFromDraft(retainedSchedule.current);
  const [selected, setSelected] = useState(0);
  // Each opening starts at the first trigger, whatever was selected last time.
  const wasOpen = useRef(open);
  if (open !== wasOpen.current) {
    wasOpen.current = open;
    if (open) setSelected(0);
  }
  const selectedIndex = Math.min(selected, rows.length - 1);

  const commit = (nextRows: Schedule[]) => onChange(draftFromRows(nextRows));
  const setRow = (index: number, next: Schedule) => {
    commit(rows.map((row, i) => (i === index ? { ...next, otherTriggers: [] } : row)));
  };
  const removeRow = (index: number) => {
    commit(rows.filter((_, i) => i !== index));
    setSelected((current) => Math.max(0, current > index ? current - 1 : Math.min(current, rows.length - 2)));
  };
  const addRow = () => {
    commit([...rows, scheduleFromTrigger({ type: "time", at: "09:00", days: "daily" })]);
    setSelected(rows.length);
  };

  const valid = rows.every((row) => scheduleValid(row));

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
        <b>{rows.length === 1 ? "Trigger" : `Triggers · ${rows.length}`}</b>
        <span className="automation-trigger-peek__header-actions">
          <IconButton
            tone="primary"
            shape="circle"
            onClick={addRow}
            aria-label="Add trigger"
            title="Add trigger"
          >
            <Plus size={ICON.XS} />
          </IconButton>
          <IconButton
            data-peek-close
            onClick={onClose}
            aria-label="Close trigger setup"
            title="Close trigger setup"
          >
            <X size={ICON.SM} />
          </IconButton>
        </span>
      </header>

      <div className="automation-trigger-peek__rows">
        <div className="automation-trigger-peek__list">
          {rows.map((row, index) => (
            <div
              key={index}
              className="automation-trigger-peek__row"
              data-open={index === selectedIndex || undefined}
            >
              <div className="automation-trigger-peek__row-head">
                <button
                  type="button"
                  className="automation-trigger-peek__row-label"
                  aria-expanded={index === selectedIndex}
                  onClick={() => setSelected(index)}
                >
                  {scheduleLabel(row)}
                </button>
                {rows.length > 1 && (
                  <IconButton
                    className="automation-trigger-peek__row-remove"
                    aria-label={`Remove trigger ${index + 1}`}
                    title="Remove this trigger"
                    onClick={() => removeRow(index)}
                  >
                    <X size={ICON.XS} />
                  </IconButton>
                )}
                <ChevronDown className="automation-trigger-peek__row-chevron" size={12} aria-hidden />
              </div>
              <Collapse open={index === selectedIndex} mode="height">
                <div className="automation-trigger-peek__row-editor">
                  <ScheduleEditor schedule={row} onChange={(next) => setRow(index, next)} />
                </div>
              </Collapse>
            </div>
          ))}
        </div>
      </div>

      <footer className="automation-trigger-peek__actions arden-peek-rule-above">
        <div className="automation-trigger-peek__footer">
          <Button variant="quiet" size="md" onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            size="md"
            disabled={!valid}
            onClick={() => onSave(draftFromRows(rows))}
          >
            {rows.length === 1 ? "Save trigger" : "Save triggers"}
          </Button>
        </div>
      </footer>
    </PeekSurface>
  );
}
