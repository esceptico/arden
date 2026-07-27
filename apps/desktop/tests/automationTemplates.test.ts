import { expect, test } from "bun:test";
import { suggestionToPayload } from "@/api/automations";
import type { Automation } from "@/api/types";
import {
  buildPayload,
  formFromAutomation,
  formFromPreset,
  scheduleLabel,
  type Schedule,
} from "@/features/automations/lib/schedule";

test("templates do not use keyword-signal suggestions", async () => {
  const source = await Bun.file(
    new URL("../src/features/automations/lib/templates.ts", import.meta.url),
  ).text();

  expect(source).not.toContain("TEMPLATE_SIGNALS");
  expect(source).not.toContain("suggestTemplatesForContext");
  expect(source).not.toContain("RegExp");
});

test("a server suggestion retains its trigger and accepted provenance through the draft form", () => {
  const preset = suggestionToPayload({
    id: "suggestion-1",
    name: "Prepare every meeting",
    description: "Prepares focused context before each external meeting.",
    prompt: "Create a focused brief before each external meeting.",
    triggers: [{ type: "event", event_type: "approaching", lead_minutes: 15 }],
    rationale: "Calendar context suggests a 15-minute prep brief.",
    evidence: [],
    category: "Calendar",
    icon: null,
  });

  expect(preset).toMatchObject({
    trigger_type: "event",
    event_type: "approaching",
    lead_minutes: 15,
    from_suggestion_id: "suggestion-1",
  });
  expect(buildPayload(formFromPreset(preset))).toMatchObject({
    name: "Prepare every meeting",
    description: "Prepares focused context before each external meeting.",
    prompt: "Create a focused brief before each external meeting.",
    trigger_type: "event",
    event_type: "approaching",
    lead_minutes: "15",
    from_suggestion_id: "suggestion-1",
  });
});

test("an existing automation keeps display copy separate from execution instructions", () => {
  const form = formFromAutomation({
    name: "Inbox brief",
    description: "Sorts overnight messages into a focused brief.",
    prompt: "FULL EXECUTION PROMPT SENTINEL",
    description_source: "generated",
    triggers: [],
    auto_approve: false,
    model: null,
  } as Automation);

  expect(form.description).toBe("Sorts overnight messages into a focused brief.");
  expect(form.prompt).toBe("FULL EXECUTION PROMPT SENTINEL");
  expect(buildPayload(form, { includeDescription: false })).toEqual({
    name: "Inbox brief",
    prompt: "FULL EXECUTION PROMPT SENTINEL",
    auto_approve: false,
    model: null,
    trigger_type: "time",
    at: "09:00",
    days: "daily",
  });
});

test("a migrated suggestion never substitutes its execution prompt for missing display copy", () => {
  const preset = suggestionToPayload({
    id: "legacy-suggestion",
    name: "Legacy suggestion",
    description: null,
    prompt: "FULL LEGACY EXECUTION PROMPT",
    triggers: [{ type: "time", at: "09:00", days: "daily" }],
    rationale: "Migrated",
    evidence: [],
    category: "Work",
    icon: null,
  });

  expect(preset.description).toBeUndefined();
  expect(preset.prompt).toBe("FULL LEGACY EXECUTION PROMPT");
});


const AT_SCHEDULE = (over: Partial<Schedule> = {}): Schedule => ({
  kind: "at",
  at: "16:00",
  every: "30m",
  days: "",
  start: "",
  end: "",
  event: "starts",
  lead: "",
  channel: "",
  fromUser: "",
  keywords: "",
  ...over,
});

test("a time trigger with no days is a ONE-SHOT and says which day it fires", () => {
  // The server's own definition is `at && !days` -> one_shot, and it disables
  // the automation after the run. Labelling that "Daily" claimed the opposite
  // of what the schedule commits to.
  const now = new Date();
  const tomorrow = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 16, 0).toISOString();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 16, 0).toISOString();

  expect(scheduleLabel(AT_SCHEDULE(), tomorrow)).toBe("Tomorrow at 4:00 PM");
  expect(scheduleLabel(AT_SCHEDULE(), today)).toBe("Today at 4:00 PM");
  // Further out falls back to a date rather than counting days.
  const later = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 9, 16, 0);
  expect(scheduleLabel(AT_SCHEDULE(), later.toISOString())).toContain("at 4:00 PM");
  expect(scheduleLabel(AT_SCHEDULE(), later.toISOString())).not.toContain("Daily");
  // Not yet saved, or already fired and disabled: still honest about running once.
  expect(scheduleLabel(AT_SCHEDULE(), null)).toBe("Once at 4:00 PM");
  expect(scheduleLabel(AT_SCHEDULE())).toBe("Once at 4:00 PM");
});

test("a time trigger WITH days keeps its recurrence label", () => {
  expect(scheduleLabel(AT_SCHEDULE({ days: "daily" }), null)).toBe("Daily at 4:00 PM");
  // The next run never overrides a stated recurrence.
  expect(scheduleLabel(AT_SCHEDULE({ days: "daily" }), new Date().toISOString())).toBe("Daily at 4:00 PM");
});
