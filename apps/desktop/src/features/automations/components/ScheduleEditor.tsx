import { useId, useMemo, useRef, useState, type ReactNode } from "react";
import clsx from "clsx";
import { motion } from "motion/react";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Tab, Tabs } from "@/components/ui/Tabs";
import { TabPanels, useTabDirection } from "@/components/ui/TabPanels";
import { Tooltip } from "@/components/ui/Tooltip";
import type { EventType, Schedule, ScheduleKind } from "@/features/automations/lib/schedule";
import { EASE_OUT, MOTION } from "@/lib/tokens/motion";

// ─── Time / days helpers ────────────────────────────────────────────

const DAY_MIN = 24 * 60;
/** "HH:MM" → minutes since midnight, or null when empty (no bound). */
const hhmmToMin = (s: string): number | null => {
  if (!s) return null;
  const [h, m] = s.split(":").map(Number);
  return Number.isFinite(h) && Number.isFinite(m) ? h * 60 + m : null;
};
const minToHhmm = (n: number): string =>
  `${String(Math.floor(n / 60) % 24).padStart(2, "0")}:${String(n % 60).padStart(2, "0")}`;

/** Interval string ("30m" · "2h" · "1d") → minutes, or null when unparsable. */
const parseInterval = (s: string): number | null => {
  const m = s.trim().match(/^(\d+(?:\.\d+)?)\s*(m|min|h|d)$/i);
  if (!m) return null;
  const n = parseFloat(m[1]);
  const unit = m[2].toLowerCase();
  return unit === "h" ? n * 60 : unit === "d" ? n * DAY_MIN : n;
};

// The mock's deliberately small cadence ladder keeps keyboard and wheel
// changes predictable while still serializing through the server's `every`.
const CADENCE_STOPS = Object.freeze([5, 10, 15, 30, 60, 120, 180, 360, 720]);

function cadenceLabel(minutes: number): string {
  if (minutes < 60) return `${minutes}m`;
  if (minutes % 60 === 0) return `${minutes / 60}h`;
  return `${Math.floor(minutes / 60)}h${String(minutes % 60).padStart(2, "0")}`;
}

function nextCadence(value: string, direction: number): string | null {
  const current = parseInterval(value) || 30;
  const next = direction > 0
    ? CADENCE_STOPS.find((stop) => stop > current)
    : [...CADENCE_STOPS].reverse().find((stop) => stop < current);
  return next == null ? null : cadenceLabel(next);
}

const DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const DAY_LETTERS = ["M", "T", "W", "T", "F", "S", "S"];

function parseDays(s: string): Set<number> {
  const v = s.trim().toLowerCase();
  if (!v || v === "daily" || v === "*") return new Set([0, 1, 2, 3, 4, 5, 6]);
  if (v === "weekdays") return new Set([0, 1, 2, 3, 4]);
  if (v === "weekends") return new Set([5, 6]);
  const out = new Set<number>();
  for (const part of v.split(",")) {
    const i = DAY_NAMES.findIndex((d) => part.trim().startsWith(d.slice(0, 3)));
    if (i >= 0) out.add(i);
  }
  return out.size > 0 ? out : new Set([0, 1, 2, 3, 4, 5, 6]);
}

function serializeDays(days: Set<number>): string {
  if (days.size === 7) return "daily";
  if (days.size === 5 && [0, 1, 2, 3, 4].every((d) => days.has(d))) return "weekdays";
  if (days.size === 2 && days.has(5) && days.has(6)) return "weekends";
  return [...days].sort((a, b) => a - b).map((d) => DAY_NAMES[d]).join(",");
}

// ─── Shared trigger editor ──────────────────────────────────────────

/**
 * The one schedule editor used by the Trigger Peek. It is intentionally
 * controlled: its owner decides when a draft becomes the automation's real
 * trigger, so closing the Peek cannot leak partial edits into the form.
 */
export function ScheduleEditor({
  schedule,
  onChange,
  className,
}: {
  schedule: Schedule;
  onChange: (next: Schedule) => void;
  className?: string;
}) {
  const set = (patch: Partial<Schedule>) => onChange({ ...schedule, ...patch });

  // Remember whether the Schedule tab meant "at" or "every", so visiting
  // On message / On event and coming back doesn't degrade every → at.
  const lastScheduleKind = useRef<"at" | "every">(
    schedule.kind === "every" ? "every" : "at",
  );
  if (schedule.kind === "at" || schedule.kind === "every") lastScheduleKind.current = schedule.kind;
  // Same memory for the Activity tab's idle/count split.
  const lastActivityKind = useRef<"idle" | "count">(schedule.kind === "count" ? "count" : "idle");
  if (schedule.kind === "idle" || schedule.kind === "count") lastActivityKind.current = schedule.kind;

  const segment =
    schedule.kind === "message" ? "message"
    : schedule.kind === "event" ? "event"
    : schedule.kind === "idle" || schedule.kind === "count" ? "activity"
    : "schedule";
  const segmentDirection = useTabDirection(["schedule", "message", "event", "activity"], segment);

  return (
    <div className={clsx("automation-trigger-editor", className)}>
      <Tabs
        variant="segmented"
        size="sm"
        value={segment}
        label="Trigger type"
        className="automation-trigger-editor__tabs"
        onChange={(value) =>
          set({
            kind:
              value === "message" ? "message"
              : value === "event" ? "event"
              : value === "activity"
                ? (schedule.kind === "idle" || schedule.kind === "count" ? schedule.kind : lastActivityKind.current)
              : schedule.kind === "at" || schedule.kind === "every" ? schedule.kind
              : lastScheduleKind.current,
          })
        }
      >
        <Tab value="schedule">Schedule</Tab>
        <Tab value="message">Message</Tab>
        <Tab value="event">Event</Tab>
        <Tab value="activity">Activity</Tab>
      </Tabs>

      <div className="automation-trigger-editor__body">
        <TabPanels
          value={segment}
          direction={segmentDirection}
          className="automation-trigger-editor__panel"
        >
          {segment === "schedule" && (
            <div className="automation-trigger-editor__form automation-trigger-editor__schedule-form">
              <div className="automation-trigger-editor__mode-row">
                <span>Fires</span>
                <Select
                  value={schedule.kind}
                  onChange={(value) => set({ kind: value as ScheduleKind })}
                  options={[
                    { value: "at", label: "at a time" },
                    { value: "every", label: "every" },
                  ]}
                  aria-label="Schedule mode"
                  className="automation-trigger-editor__mode-select"
                />
                <span aria-hidden="true">·</span>
                {schedule.kind === "at" ? (
                  <>
                    <TokenInput
                      value={schedule.at}
                      onChange={(value) => set({ at: value })}
                      ariaLabel="Time"
                    />
                    <HelpTip label="24-hour time (HH:MM) — or drag the marker on the tape below." />
                  </>
                ) : (
                  <>
                    <TokenInput
                      value={schedule.every}
                      onChange={(value) => set({ every: value })}
                      placeholder="30m"
                      ariaLabel="Interval"
                    />
                    <HelpTip label="How often it fires: a number plus m / h / d — 30m, 2h, 1d. The tape below previews the day." />
                    <RunsPerDay schedule={schedule} />
                  </>
                )}
              </div>

              <DayTape schedule={schedule} onChangeSchedule={set} />

              <DayRow days={schedule.days} onChange={(days) => set({ days })} />
            </div>
          )}

          {segment === "message" && (
            <div className="automation-trigger-editor__form">
              <TriggerInputField
                label="Channel"
                value={schedule.channel}
                onChange={(value) => set({ channel: value })}
                placeholder="release"
                hint="Comma-separated · without # · e.g. release, launch"
              />
              <TriggerInputField
                label="From user"
                value={schedule.fromUser}
                onChange={(value) => set({ fromUser: value })}
                placeholder="anyone"
                hint="Optional · without @ · e.g. tim"
              />
              <TriggerInputField
                label="Matching"
                value={schedule.keywords}
                onChange={(value) => set({ keywords: value })}
                placeholder="anything"
                hint="Optional · comma-separated words or phrases"
              />
            </div>
          )}

          {segment === "event" && (
            <div className="automation-trigger-editor__form">
              <TriggerField label="Calendar event">
                <Select
                  value={schedule.event}
                  onChange={(value) => set({ event: value as EventType })}
                  options={[
                    { value: "starts", label: "Starts" },
                    { value: "ends", label: "Ends" },
                    { value: "approaching", label: "Is approaching" },
                  ]}
                  aria-label="Calendar event"
                  className="automation-trigger-editor__select"
                />
              </TriggerField>
              <TriggerInputField
                label="Lead time"
                value={schedule.lead}
                onChange={(value) => set({ lead: value })}
                placeholder="15"
                inputMode="numeric"
              />
            </div>
          )}

          {segment === "activity" && (
            <div className="automation-trigger-editor__form">
              <TriggerField label="Activity">
                <Select
                  value={schedule.kind === "count" ? "count" : "idle"}
                  onChange={(value) => set({ kind: value as ScheduleKind })}
                  options={[
                    { value: "idle", label: "After inactivity" },
                    { value: "count", label: "Every N chat turns" },
                  ]}
                  aria-label="Activity trigger"
                  className="automation-trigger-editor__select"
                />
              </TriggerField>
              {schedule.kind === "count" ? (
                <TriggerInputField
                  label="Turns"
                  value={schedule.everyN}
                  onChange={(value) => set({ everyN: value })}
                  placeholder="10"
                  inputMode="numeric"
                />
              ) : (
                <TriggerInputField
                  label="Idle minutes"
                  value={schedule.idleMinutes}
                  onChange={(value) => set({ idleMinutes: value })}
                  placeholder="10"
                  inputMode="numeric"
                />
              )}
            </div>
          )}
        </TabPanels>
      </div>
    </div>
  );
}

// ─── Atoms ──────────────────────────────────────────────────────────

function TriggerField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="automation-trigger-editor__field" role="group" aria-label={label}>
      <span className="automation-trigger-editor__field-label">{label}</span>
      {children}
    </div>
  );
}

function TriggerInputField({
  label,
  value,
  onChange,
  placeholder,
  hint,
  inputMode,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  hint?: string;
  inputMode?: "numeric";
}) {
  const hintId = useId();
  return (
    <div className="automation-trigger-editor__field">
      <Input
        label={label}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        spellCheck={false}
        autoComplete="off"
        inputMode={inputMode}
        aria-describedby={hint ? hintId : undefined}
        className="automation-trigger-editor__input"
      />
      {hint && (
        <span id={hintId} className="automation-trigger-editor__hint">
          {hint}
        </span>
      )}
    </div>
  );
}

/** An editable value INSIDE the sentence: a tonal mono chip that is always
 *  an input (field-sizing keeps its width hugging the content). */
function TokenInput({
  value,
  onChange,
  placeholder,
  prefix,
  ariaLabel,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  prefix?: string;
  ariaLabel: string;
}) {
  return (
    <span className="automation-trigger-editor__token-field">
      {prefix && <span className="automation-trigger-editor__token-prefix">{prefix}</span>}
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        spellCheck={false}
        aria-label={ariaLabel}
        className="automation-trigger-editor__token-input field-sizing-content"
      />
    </span>
  );
}

/** "?" glyph with the house tooltip — help only where the answer isn't
 *  already on screen. */
function HelpTip({ label }: { label: string }) {
  return (
    <Tooltip label={label}>
      <span
        tabIndex={0}
        className="automation-trigger-editor__help"
      >
        ?
      </span>
    </Tooltip>
  );
}

/** Live "N runs / day" readout for interval schedules. */
function RunsPerDay({ schedule }: { schedule: Schedule }) {
  const count = useMemo(() => {
    const iv = parseInterval(schedule.every);
    if (!iv || iv <= 0) return null;
    const start = hhmmToMin(schedule.start) ?? 0;
    const end = hhmmToMin(schedule.end) ?? DAY_MIN;
    const span = start === 0 && end === DAY_MIN ? DAY_MIN - 1 : end - start;
    return Math.floor(span / iv) + 1;
  }, [schedule.every, schedule.start, schedule.end]);
  if (count == null) return null;
  return (
    <span className="automation-trigger-editor__runs-per-day">
      {count} runs / day
    </span>
  );
}

/** Seven day marks + quick presets. Never lets the set go empty — a
 *  schedule that fires on no days is a lie the receipt can't tell. */
function DayRow({ days, onChange }: { days: string; onChange: (days: string) => void }) {
  const set = parseDays(days);
  const toggle = (i: number) => {
    const next = new Set(set);
    if (next.has(i)) {
      if (next.size === 1) return;
      next.delete(i);
    } else next.add(i);
    onChange(serializeDays(next));
  };
  const isDaily = set.size === 7;
  const isWeekdays = set.size === 5 && [0, 1, 2, 3, 4].every((d) => set.has(d));
  return (
    <div className="automation-trigger-editor__days" role="group" aria-label="Days">
      {DAY_LETTERS.map((l, i) => (
        <button
          key={i}
          type="button"
          onClick={() => toggle(i)}
          aria-pressed={set.has(i)}
          aria-label={DAY_NAMES[i]}
          className="automation-trigger-editor__day"
        >
          {l}
        </button>
      ))}
      <span className="automation-trigger-editor__day-presets">
        <button
          type="button"
          onClick={() => onChange("daily")}
          className="automation-trigger-editor__day-preset"
          aria-pressed={isDaily}
        >
          Daily
        </button>
        <button
          type="button"
          onClick={() => onChange("weekdays")}
          className="automation-trigger-editor__day-preset"
          aria-pressed={isWeekdays}
        >
          Weekdays
        </button>
      </span>
    </div>
  );
}

// ─── The instrument: a 24h day tape ─────────────────────────────────
// The mockup's fixed viewBox lets the ruler keep its rhythm while its host
// stretches. The server has exactly one non-overnight interval window, so the
// instrument deliberately renders and edits one window rather than inventing
// client-only multi-window state.

const DAY_RULER = Object.freeze({ width: 320, inset: 8, axisY: 34, snap: 5, intervalSnap: 15 });
const TAPE_H = 72;

type RulerDrag =
  | { kind: "at" }
  | { kind: "start" }
  | { kind: "end" }
  | { kind: "body"; offset: number };

function DayTape({
  schedule,
  onChangeSchedule,
}: {
  schedule: Schedule;
  onChangeSchedule: (patch: Partial<Schedule>) => void;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const drag = useRef<RulerDrag | null>(null);
  const wheelAcc = useRef(0);
  const [dragging, setDragging] = useState(false);
  const [hover, setHover] = useState<"at" | "edge" | "body" | "track" | null>(null);

  const X = (min: number) =>
    DAY_RULER.inset + Math.max(0, Math.min(DAY_MIN, min)) / DAY_MIN * (DAY_RULER.width - DAY_RULER.inset * 2);
  const atMin = hhmmToMin(schedule.at) ?? 540;
  const winA = hhmmToMin(schedule.start) ?? 0;
  const winB = hhmmToMin(schedule.end) ?? DAY_MIN;
  const fullDay = !schedule.start && !schedule.end;
  const transition = { duration: dragging ? MOTION.reduced : MOTION.content, ease: EASE_OUT } as const;

  const minAt = (e: React.PointerEvent) => {
    const r = wrapRef.current?.getBoundingClientRect();
    if (!r) return 0;
    return Math.max(0, Math.min(DAY_MIN, ((e.clientX - r.left) / r.width) * DAY_MIN));
  };

  const patchWindow = (start: number, end: number) => onChangeSchedule({
    start: start <= 0 ? "" : minToHhmm(start),
    end: end >= DAY_MIN ? "" : minToHhmm(end),
  });

  const applyWindow = (edge: "start" | "end", m: number) => {
    const v = Math.round(m / DAY_RULER.intervalSnap) * DAY_RULER.intervalSnap;
    if (edge === "start") {
      const lo = Math.min(v, winB - 15);
      patchWindow(Math.max(0, lo), winB);
    } else {
      const hi = Math.max(v, winA + 15);
      patchWindow(winA, Math.min(DAY_MIN, hi));
    }
  };

  const moveWindow = (m: number, offset: number) => {
    const duration = winB - winA;
    const start = Math.max(
      0,
      Math.min(DAY_MIN - duration, Math.round((m - offset) / DAY_RULER.intervalSnap) * DAY_RULER.intervalSnap),
    );
    patchWindow(start, start + duration);
  };

  const hitAt = (m: number): "at" | "edge" | "body" | "track" => {
    const tolerance = DAY_MIN * 16 / Math.max(1, wrapRef.current?.getBoundingClientRect().width ?? 1);
    if (schedule.kind === "at") return Math.abs(m - atMin) <= tolerance ? "at" : "track";
    if (Math.abs(m - winA) <= tolerance || Math.abs(m - winB) <= tolerance) return "edge";
    if (!fullDay && m > winA && m < winB) return "body";
    return "track";
  };

  const onPointerDown = (e: React.PointerEvent) => {
    e.preventDefault();
    try {
      wrapRef.current?.setPointerCapture(e.pointerId);
    } catch {
      /* synthetic pointer */
    }
    const m = minAt(e);
    if (schedule.kind === "at") {
      drag.current = { kind: "at" };
      onChangeSchedule({ at: minToHhmm(Math.min(1435, Math.round(m / DAY_RULER.snap) * DAY_RULER.snap)) });
    } else {
      const hit = hitAt(m);
      if (hit === "body") {
        drag.current = { kind: "body", offset: m - winA };
        moveWindow(m, drag.current.offset);
      } else {
        const edge = Math.abs(m - winA) <= Math.abs(m - winB) ? "start" : "end";
        drag.current = { kind: edge };
        applyWindow(edge, m);
      }
    }
    setDragging(true);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag.current) {
      setHover(hitAt(minAt(e)));
      return;
    }
    const m = minAt(e);
    if (drag.current.kind === "at") {
      onChangeSchedule({ at: minToHhmm(Math.min(1435, Math.round(m / DAY_RULER.snap) * DAY_RULER.snap)) });
    } else if (drag.current.kind === "body") {
      moveWindow(m, drag.current.offset);
    } else {
      applyWindow(drag.current.kind, m);
    }
  };
  const endDrag = (e?: React.PointerEvent) => {
    try {
      if (e?.pointerId != null) wrapRef.current?.releasePointerCapture?.(e.pointerId);
    } catch {
      /* synthetic pointer */
    }
    drag.current = null;
    setDragging(false);
  };

  const adjustCadence = (direction: number) => {
    if (schedule.kind !== "every") return;
    const every = nextCadence(schedule.every, direction);
    if (every) onChangeSchedule({ every });
  };

  const onWheel = (event: React.WheelEvent<HTMLDivElement>) => {
    if (schedule.kind !== "every") return;
    event.preventDefault();
    if (event.deltaY === 0) return;
    // Trackpads stream small deltas: the mock only commits one stop per detent.
    if (Math.sign(event.deltaY) !== Math.sign(wheelAcc.current)) wheelAcc.current = 0;
    wheelAcc.current += event.deltaY;
    if (Math.abs(wheelAcc.current) < 80) return;
    const direction = Math.sign(wheelAcc.current);
    wheelAcc.current = 0;
    adjustCadence(direction);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if ((event.key === "ArrowUp" || event.key === "ArrowDown") && schedule.kind === "every") {
      event.preventDefault();
      adjustCadence(event.key === "ArrowUp" ? 1 : -1);
      return;
    }
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const step = (event.shiftKey ? 60 : DAY_RULER.snap) * (event.key === "ArrowLeft" ? -1 : 1);
    if (schedule.kind === "at") {
      onChangeSchedule({ at: minToHhmm(Math.max(0, Math.min(1435, atMin + step))) });
      return;
    }
    const duration = winB - winA;
    const nextStart = Math.max(0, Math.min(DAY_MIN - duration, winA + step));
    patchWindow(nextStart, nextStart + duration);
  };

  const firings = useMemo(() => {
    if (schedule.kind !== "every") return [];
    const iv = parseInterval(schedule.every);
    if (!iv || iv <= 0) return [];
    const out: number[] = [];
    const end = winA === 0 && winB === DAY_MIN ? DAY_MIN - 1 : winB;
    for (let t = winA; t <= end; t += iv) out.push(t);
    return out.slice(0, 400);
  }, [schedule.kind, schedule.every, winA, winB]);

  const now = new Date();
  const nowMin = now.getHours() * 60 + now.getMinutes();
  const nextFiring = firings.find((minute) => minute >= nowMin) ?? firings[0];
  const windowLabel = `${minToHhmm(winA)}–${winB >= DAY_MIN ? "24:00" : minToHhmm(winB)}`;

  return (
    <div className="automation-trigger-editor__day-ruler-editor">
      <div
        ref={wrapRef}
        tabIndex={0}
        aria-label={
          schedule.kind === "at"
            ? `Fires at ${minToHhmm(atMin)}. Click or drag to set the time; arrow keys nudge it.`
            : `Runs every ${schedule.every || "30m"}, ${fullDay ? "around the clock" : `during ${windowLabel}`}. Drag the active window or its edges; left and right arrows nudge it, up and down arrows change cadence.`
        }
        className={clsx("automation-trigger-editor__day-ruler", dragging && "is-dragging")}
        data-mode={schedule.kind === "every" ? "every" : "at"}
        data-hover={hover ?? undefined}
        onKeyDown={onKeyDown}
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerLeave={() => setHover(null)}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
      >
        <svg
          className="automation-trigger-editor__day-ruler-svg"
          viewBox={`0 0 ${DAY_RULER.width} ${TAPE_H}`}
          aria-hidden="true"
        >
          <line
            className="automation-trigger-editor__day-ruler-axis"
            x1={DAY_RULER.inset}
            y1={DAY_RULER.axisY}
            x2={DAY_RULER.width - DAY_RULER.inset}
            y2={DAY_RULER.axisY}
          />
          {Array.from({ length: 25 }, (_, hour) => {
            const major = hour % 6 === 0;
            const x = X(hour * 60);
            return (
              <g key={hour}>
                <line
                  className={clsx("automation-trigger-editor__day-ruler-tick", major && "is-major")}
                  x1={x}
                  y1={38}
                  x2={x}
                  y2={major ? 48 : 44}
                />
                {major && (
                  <text
                    className="automation-trigger-editor__day-ruler-label"
                    x={x}
                    y={62}
                    textAnchor={hour === 0 ? "start" : hour === 24 ? "end" : "middle"}
                  >
                    {String(hour).padStart(2, "0")}
                  </text>
                )}
              </g>
            );
          })}
          <path className="automation-trigger-editor__day-ruler-now" d={`M ${X(nowMin)} 46 l -3.5 5 h 7 z`} />

          {schedule.kind === "every" && (
            <>
              <rect
                className={clsx("automation-trigger-editor__day-ruler-band", fullDay && "is-full")}
                x={X(winA)}
                width={Math.max(0, X(winB) - X(winA))}
                y={28}
                height={12}
              />
              <g className="automation-trigger-editor__day-ruler-firings">
                {firings.map((minute) => {
                  const isNext = minute === nextFiring;
                  const major = minute % 60 === 0;
                  return (
                    <line
                      key={minute}
                      className={clsx(major && "is-major", isNext && "is-next")}
                      x1={X(minute)}
                      y1={isNext ? 29 : major ? 30 : 31.5}
                      x2={X(minute)}
                      y2={isNext ? 39 : major ? 38 : 36.5}
                    />
                  );
                })}
              </g>
              {!fullDay && (
                <g className="automation-trigger-editor__day-ruler-window">
                  <rect
                    className="automation-trigger-editor__day-ruler-handle"
                    x={X(winA) - 2}
                    y={26}
                    width={4}
                    height={16}
                    rx={2}
                  />
                  <rect
                    className="automation-trigger-editor__day-ruler-handle"
                    x={X(winB) - 2}
                    y={26}
                    width={4}
                    height={16}
                    rx={2}
                  />
                  {hover === "body" && (
                    <text
                      className="automation-trigger-editor__day-ruler-window-label"
                      x={Math.max(24, Math.min(DAY_RULER.width - 24, X((winA + winB) / 2)))}
                      y={12}
                      textAnchor="middle"
                    >
                      {windowLabel}
                    </text>
                  )}
                </g>
              )}
            </>
          )}

          <motion.g
            initial={false}
            className="automation-trigger-editor__day-ruler-marker"
            animate={{ x: X(atMin), opacity: schedule.kind === "at" ? 1 : 0 }}
            transition={transition}
          >
            <line x1={0} y1={21} x2={0} y2={47} />
            <circle cy={DAY_RULER.axisY} r={4.5} />
            <text x={atMin > 1260 ? -9 : 9} y={12} textAnchor={atMin > 1260 ? "end" : "start"}>
              {minToHhmm(atMin)}
            </text>
          </motion.g>
        </svg>
      </div>
      <div className="automation-trigger-editor__day-ruler-caption" aria-hidden="true">
        {schedule.kind === "every" ? "drag the active window edges to set active hours" : "click or drag to set the time"}
      </div>
    </div>
  );
}
