import { expect, test } from "bun:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import type { Automation } from "@/api/types";
import { ScheduleEditor } from "@/features/automations/components/ScheduleEditor";
import { ScheduleTriggerPeek } from "@/features/automations/components/ScheduleTriggerPeek";
import {
  buildPayload,
  formFromAutomation,
  scheduleFromTrigger,
  scheduleLabel,
  triggerFromSchedule,
  triggerSummary,
  type Schedule,
} from "@/features/automations/lib/schedule";

function automation(triggers: Automation["triggers"]): Automation {
  return {
    task_id: "builtin-memory-capture",
    name: "Memory Capture",
    description: "Capture durable user-stated facts from recent chats.",
    prompt: "Run Memory Capture to completion.",
    triggers,
    enabled: true,
    auto_approve: true,
    builtin: true,
  } as Automation;
}

const CAPTURE_TRIGGERS: Automation["triggers"] = [
  { type: "idle", idle_minutes: 10 },
  { type: "count", every_n: 10 },
  { type: "time", at: "02:30", days: "daily" },
];

test("idle trigger parses into an editable schedule; the summary spells out every trigger", () => {
  const form = formFromAutomation(automation(CAPTURE_TRIGGERS));
  expect(form.schedule.kind).toBe("idle");
  expect(form.schedule.idleMinutes).toBe("10");
  expect(form.schedule.otherTriggers).toHaveLength(2);
  expect(scheduleLabel(form.schedule)).toBe("After 10m idle");
  expect(triggerSummary(form.schedule)).toBe("After 10m idle · every 10 turns · at 02:30 · daily");
});

test("count trigger parses and labels turns", () => {
  const form = formFromAutomation(automation([{ type: "count", every_n: 7 }]));
  expect(form.schedule.kind).toBe("count");
  expect(scheduleLabel(form.schedule)).toBe("Every 7 turns");
});

test("saving a multi-trigger automation preserves its untouched triggers", () => {
  const form = formFromAutomation(automation(CAPTURE_TRIGGERS));
  form.schedule.idleMinutes = "15";
  const payload = buildPayload(form, { includeDescription: false });
  expect(payload.trigger_type).toBeUndefined();
  expect(payload.triggers).toEqual([
    { type: "idle", idle_minutes: 15 },
    { type: "count", every_n: 10 },
    { type: "time", at: "02:30", days: "daily" },
  ]);
});

test("a single activity trigger round-trips through trigger fields", () => {
  const form = formFromAutomation(automation([{ type: "count", every_n: 7 }]));
  form.schedule.everyN = "12";
  const payload = buildPayload(form, { includeDescription: false });
  expect(payload.trigger_type).toBe("count");
  expect(payload.every_n).toBe(12);
  expect(payload.triggers).toBeUndefined();
});

test("every trigger kind survives a schedule round-trip", () => {
  for (const trigger of [
    { type: "idle", idle_minutes: 40 },
    { type: "count", every_n: 12 },
    { type: "time", at: "02:30", days: "daily" },
    { type: "time", every: "2h", days: "weekdays", start: "09:00", end: "18:00" },
    { type: "event", event_type: "approaching", lead_minutes: 15 },
  ] as Automation["triggers"]) {
    expect(triggerFromSchedule(scheduleFromTrigger(trigger))).toEqual(trigger);
  }
});

test("the trigger peek lists every trigger as an editable row", async () => {
  const form = formFromAutomation(automation(CAPTURE_TRIGGERS));
  const element = document.createElement("div");
  document.body.append(element);
  const root = createRoot(element);
  let draft: Schedule = form.schedule;
  try {
    await act(async () => {
      root.render(
        <ScheduleTriggerPeek
          open
          schedule={draft}
          onChange={(next) => { draft = next; }}
          onSave={() => {}}
          onClose={() => {}}
        />,
      );
    });
    const labels = [...element.querySelectorAll(".automation-trigger-peek__row-label")].map(
      (el) => el.textContent,
    );
    expect(labels).toEqual(["After 10m idle", "Every 10 turns", "Daily at 2:30 AM"]);
    // Every row keeps a measurable editor mounted (height-collapse contract);
    // exactly one row is expanded.
    expect(element.querySelectorAll(".automation-trigger-peek__row-editor")).toHaveLength(3);
    expect(element.querySelectorAll('[aria-expanded="true"]')).toHaveLength(1);
    expect(element.textContent).toContain("Triggers · 3");
    // Every row has a remove control; there is an add control.
    expect(element.querySelectorAll('[aria-label^="Remove trigger"]')).toHaveLength(3);
    expect(element.querySelector('[aria-label="Add trigger"]')).not.toBeNull();
  } finally {
    await act(async () => root.unmount());
    element.remove();
  }
});

test("ScheduleEditor offers an editable Activity panel", async () => {
  const form = formFromAutomation(automation(CAPTURE_TRIGGERS));
  const element = document.createElement("div");
  document.body.append(element);
  const root = createRoot(element);
  try {
    await act(async () => {
      root.render(<ScheduleEditor schedule={form.schedule} onChange={() => {}} />);
    });
    const markup = element.innerHTML;
    expect(markup).toContain("Activity");
    expect(markup).toContain("Idle minutes");
    const inputs = [...element.querySelectorAll("input")];
    expect(inputs.some((input) => input.value === "10")).toBe(true);
  } finally {
    await act(async () => root.unmount());
    element.remove();
  }
});
