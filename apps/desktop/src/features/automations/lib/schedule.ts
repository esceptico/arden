import type { Automation, AutomationTrigger, CreateAutomationPayload } from "@/api/types";
import { formatTrigger } from "@/lib/agentRun";

export type EditorSeed =
  | { kind: "create"; preset?: CreateAutomationPayload }
  | { kind: "edit"; automation: Automation };

export type ScheduleKind = "at" | "every" | "event" | "message" | "idle" | "count";
export type EventType = "starts" | "ends" | "approaching";

export interface Schedule {
  kind: ScheduleKind;
  at: string;
  every: string;
  days: string;
  start: string;
  end: string;
  event: EventType;
  lead: string;
  /** Message trigger (Slack watcher). Source is fixed to "slack" in v1.
   *  We collect display names here; the server resolves them to ids at save. */
  channel: string;
  fromUser: string;
  /** Raw comma-separated keywords; split into a `contains` any-of list on save. */
  keywords: string;
  /** Activity triggers: fire on inactivity (idle) or per N chat turns (count). */
  idleMinutes: string;
  everyN: string;
  /** Triggers beyond the first, preserved verbatim on save. The editor edits
   *  only the first trigger; without this a save would silently rewrite a
   *  multi-trigger automation into a single trigger. */
  otherTriggers: AutomationTrigger[];
}

const DEFAULT_SCHEDULE: Schedule = {
  kind: "at",
  at: "09:00",
  every: "30m",
  days: "daily",
  start: "",
  end: "",
  event: "approaching",
  lead: "15",
  channel: "",
  fromUser: "",
  keywords: "",
  idleMinutes: "10",
  everyN: "10",
  otherTriggers: [],
};

export function splitKeywords(raw: string): string[] {
  return raw
    .split(",")
    .map((k) => k.trim())
    .filter(Boolean);
}

/** Stored message triggers echo channels back as {id,name}; the editor edits
 *  them as a comma-separated list of names. */
export function channelsToInput(channels?: (string | { id: string; name: string })[]): string {
  return (channels ?? []).map((c) => (typeof c === "string" ? c : c.name)).join(", ");
}

export interface FormState {
  name: string;
  /** Concise display copy. Null means the server should generate it. */
  description: string | null;
  prompt: string;
  schedule: Schedule;
  auto_approve: boolean;
  /** Explicit model override; null = the session default. */
  model: string | null;
}

export function emptyForm(): FormState {
  return {
    name: "",
    description: null,
    prompt: "",
    schedule: { ...DEFAULT_SCHEDULE },
    auto_approve: false,
    model: null,
  };
}

export function formFromPreset(p: CreateAutomationPayload): FormState {
  const f = emptyForm();
  f.name = p.name ?? "";
  f.description = p.description?.trim() || null;
  f.prompt = p.prompt ?? "";
  f.model = p.model ?? null;
  if (p.auto_approve) f.auto_approve = true;
  const msg = p.trigger_type === "message" ? p.triggers?.[0] : undefined;
  if (msg && msg.type === "message") {
    f.schedule = {
      ...f.schedule,
      kind: "message",
      channel: channelsToInput(msg.channels),
      fromUser: msg.from_user_name ?? msg.from_user ?? "",
      keywords: (msg.contains ?? []).join(", "),
    };
  } else if (p.trigger_type === "event") {
    f.schedule = {
      ...f.schedule,
      kind: "event",
      event: (p.event_type as EventType) ?? "approaching",
      lead: p.lead_minutes != null ? String(p.lead_minutes) : f.schedule.lead,
    };
  } else if (p.every) {
    f.schedule = {
      ...f.schedule,
      kind: "every",
      every: p.every,
      days: p.days ?? "",
      start: p.start ?? "",
      end: p.end ?? "",
    };
  } else if (p.at) {
    f.schedule = { ...f.schedule, kind: "at", at: p.at, days: p.days ?? "" };
  }
  return f;
}

/** One stored trigger opened as an editable schedule (otherTriggers empty —
 *  the peek owns list membership; this maps a single row). */
export function scheduleFromTrigger(t: AutomationTrigger): Schedule {
  const base = { ...DEFAULT_SCHEDULE };
  if (t.type === "time" && t.every) {
    return { ...base, kind: "every", every: t.every, days: t.days ?? "", start: t.start ?? "", end: t.end ?? "" };
  }
  if (t.type === "time" && t.at) {
    return { ...base, kind: "at", at: t.at, days: t.days ?? "" };
  }
  if (t.type === "event") {
    return {
      ...base,
      kind: "event",
      event: (t.event_type as EventType) ?? "approaching",
      lead: t.lead_minutes != null ? String(t.lead_minutes) : base.lead,
    };
  }
  if (t.type === "message") {
    return {
      ...base,
      kind: "message",
      channel: channelsToInput(t.channels),
      fromUser: t.from_user_name ?? t.from_user ?? "",
      keywords: (t.contains ?? []).join(", "),
    };
  }
  if (t.type === "idle") {
    return { ...base, kind: "idle", idleMinutes: t.idle_minutes != null ? String(t.idle_minutes) : base.idleMinutes };
  }
  if (t.type === "count") {
    return { ...base, kind: "count", everyN: t.every_n != null ? String(t.every_n) : base.everyN };
  }
  return base;
}

export function formFromAutomation(a: Automation): FormState {
  const t = a.triggers[0];
  const f = emptyForm();
  f.name = a.name;
  f.description = a.description;
  f.prompt = a.prompt;
  f.auto_approve = a.auto_approve;
  f.model = a.model;
  if (!t) return f;
  f.schedule = { ...scheduleFromTrigger(t), otherTriggers: a.triggers.slice(1) };
  return f;
}

/** The stored form of one editable schedule row. */
export function triggerFromSchedule(s: Schedule): AutomationTrigger {
  if (s.kind === "idle") return { type: "idle", idle_minutes: Number(s.idleMinutes) };
  if (s.kind === "count") return { type: "count", every_n: Number(s.everyN) };
  if (s.kind === "message") {
    const trigger: AutomationTrigger = { type: "message", source: "slack", channels: splitKeywords(s.channel) };
    const fromUser = s.fromUser.trim();
    if (fromUser) trigger.from_user = fromUser;
    const contains = splitKeywords(s.keywords);
    if (contains.length) trigger.contains = contains;
    return trigger;
  }
  if (s.kind === "event") {
    const trigger: AutomationTrigger = { type: "event", event_type: s.event };
    const lead = s.lead.trim();
    if (lead) trigger.lead_minutes = Number(lead);
    return trigger;
  }
  const trigger: AutomationTrigger = { type: "time" };
  const days = s.days.trim();
  if (days) trigger.days = days;
  if (s.kind === "every") {
    trigger.every = s.every.trim();
    if (s.start) trigger.start = s.start;
    if (s.end) trigger.end = s.end;
  } else {
    trigger.at = s.at;
  }
  return trigger;
}

export function buildPayload(
  f: FormState,
  { includeDescription = true }: { includeDescription?: boolean } = {},
): CreateAutomationPayload {
  const p: CreateAutomationPayload = {
    name: f.name.trim() || "Untitled automation",
    prompt: f.prompt.trim(),
    auto_approve: f.auto_approve,
    model: f.model,
  };
  const description = f.description?.trim();
  if (includeDescription && description) p.description = description;
  const s = f.schedule;
  if (s.otherTriggers.length > 0) {
    // Multi-trigger automation: replace the full list so the untouched
    // triggers survive the save instead of being collapsed into one.
    p.triggers = [triggerFromSchedule(s), ...s.otherTriggers];
    return p;
  }
  if (s.kind === "idle") {
    p.trigger_type = "idle";
    p.idle_minutes = Number(s.idleMinutes);
    return p;
  }
  if (s.kind === "count") {
    p.trigger_type = "count";
    p.every_n = Number(s.everyN);
    return p;
  }
  if (s.kind === "at") {
    p.trigger_type = "time";
    p.at = s.at;
    const days = s.days.trim();
    if (days) p.days = days;
  } else if (s.kind === "every") {
    p.trigger_type = "time";
    p.every = s.every.trim();
    const days = s.days.trim();
    if (days) p.days = days;
    if (s.start) p.start = s.start;
    if (s.end) p.end = s.end;
  } else if (s.kind === "message") {
    p.trigger_type = "message";
    const trigger: AutomationTrigger = {
      type: "message",
      source: "slack",
      channels: splitKeywords(s.channel),
    };
    const fromUser = s.fromUser.trim();
    if (fromUser) trigger.from_user = fromUser;
    const contains = splitKeywords(s.keywords);
    if (contains.length) trigger.contains = contains;
    p.triggers = [trigger];
  } else {
    p.trigger_type = "event";
    p.event_type = s.event;
    const lead = s.lead.trim();
    if (lead) p.lead_minutes = lead;
  }
  return p;
}

/** Calendar distance, not elapsed time: a 4 PM reminder set the night before
 *  is "Tomorrow", never "in 17h". */
function relativeDay(iso: string, now: Date): string | null {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return null;
  const startOf = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((startOf(then) - startOf(now)) / 86_400_000);
  if (days === 0) return "Today";
  if (days === 1) return "Tomorrow";
  return then.toLocaleDateString(undefined, then.getFullYear() === now.getFullYear()
    ? { month: "short", day: "numeric" }
    : { month: "short", day: "numeric", year: "numeric" });
}

/** `nextRunAt` names the day a one-shot actually fires. Without days a time
 *  trigger runs ONCE — the server's own `one_shot` is `at && !days`, and it
 *  disables the automation afterwards — so calling it "Daily" claimed the
 *  opposite of what the schedule commits to. */
export function scheduleLabel(s: Schedule, nextRunAt?: string | null): string {
  if (s.kind === "idle") return `After ${s.idleMinutes}m idle`;
  if (s.kind === "count") return `Every ${s.everyN} turns`;
  if (s.kind === "message") {
    const chans = splitKeywords(s.channel);
    if (!chans.length) return "On Slack message";
    const head = chans.length === 1 ? `#${chans[0]}` : `#${chans[0]} +${chans.length - 1}`;
    const from = s.fromUser.trim();
    return from ? `${head} from @${from}` : head;
  }
  if (s.kind === "at") {
    const time = formatTime12(s.at);
    const days = humanDays(s.days);
    if (days) return `${days} at ${time}`;
    const day = nextRunAt ? relativeDay(nextRunAt, new Date()) : null;
    return day ? `${day} at ${time}` : `Once at ${time}`;
  }
  if (s.kind === "every") {
    const win = s.start && s.end ? ` (${formatTime12(s.start)}–${formatTime12(s.end)})` : "";
    const days = humanDays(s.days);
    return `Every ${s.every}${win}${days ? ` · ${days}` : ""}`;
  }
  const lead = s.event === "approaching" && s.lead ? ` (${s.lead}m)` : "";
  return `On event ${s.event}${lead}`;
}

/** Whether one schedule row is complete enough to save. */
export function scheduleValid(s: Schedule): boolean {
  if (s.kind === "message") return splitKeywords(s.channel).length > 0;
  if (s.kind === "idle") return /^\d+$/.test(s.idleMinutes.trim()) && Number(s.idleMinutes) >= 1;
  if (s.kind === "count") return /^\d+$/.test(s.everyN.trim()) && Number(s.everyN) >= 1;
  return true;
}

/** Every trigger spelled out — never a "+N" the reader has to guess at. */
export function triggerSummary(s: Schedule, nextRunAt?: string | null): string {
  const first = scheduleLabel(s, nextRunAt);
  if (s.otherTriggers.length === 0) return first;
  return [first, ...s.otherTriggers.map((t) => formatTrigger(t))].join(" · ");
}

export function humanDays(days: string): string {
  const v = days.trim().toLowerCase();
  if (!v) return "";
  if (v === "daily" || v === "*") return "Daily";
  if (v === "weekdays") return "Weekdays";
  if (v === "weekends") return "Weekends";
  return days;
}

export function formatTime12(hhmm: string): string {
  const m = hhmm.match(/^(\d{1,2}):(\d{2})$/);
  if (!m) return hhmm;
  const hour = Number(m[1]);
  const period = hour >= 12 ? "PM" : "AM";
  const h12 = ((hour + 11) % 12) + 1;
  return `${h12}:${m[2]} ${period}`;
}
