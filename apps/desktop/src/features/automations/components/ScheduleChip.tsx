import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import clsx from "clsx";
import { CalendarClock, ChevronDown, Clock, MessageSquare } from "@/components/icons";
import { EASE_OUT, SPRING_POPOVER, MOTION } from "@/lib/tokens/motion";
import { ICON } from "@/lib/icons";
import { useReanchor } from "@/lib/hooks";
import { IconSwap } from "@/components/ui/IconSwap";
import { Button } from "@/components/ui/Button";
import { Chip } from "@/components/ui/Chip";
import { Select } from "@/components/ui/Select";
import { Tab, Tabs } from "@/components/ui/Tabs";
import { Tooltip } from "@/components/ui/Tooltip";
import type { EventType, Schedule, ScheduleKind } from "@/features/automations/lib/schedule";
import { scheduleLabel, splitKeywords } from "@/features/automations/lib/schedule";

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

// ─── Schedule chip + popover ────────────────────────────────────────

export function ScheduleChip({
  schedule,
  onChange,
}: {
  schedule: Schedule;
  onChange: (next: Schedule) => void;
}) {
  const [open, setOpen] = useState(false);
  /** Edits are STAGED here — nothing reaches `onChange` until Save. Esc,
   *  Cancel, or clicking out discards. */
  const [staged, setStaged] = useState<Schedule>(schedule);
  const wrapRef = useRef<HTMLDivElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  // Anchor the portaled popover off the chip's rect. Portaling to #app
  // lets it escape the modal's `overflow-hidden`. Opens above-left.
  const [coords, setCoords] = useState<{ bottom: number; left: number } | null>(null);

  const openPopover = () => {
    setStaged(schedule);
    setOpen(true);
  };

  useReanchor(open, () => {
    const el = wrapRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    setCoords({ bottom: window.innerHeight - r.top + 6, left: r.left });
  });

  // Outside click / Escape both DISCARD the staged edits.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (wrapRef.current?.contains(t)) return;
      if (popoverRef.current?.contains(t)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    // Capture phase so the automations modal underneath never sees this Esc.
    window.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey, true);
    };
  }, [open]);

  const save = () => {
    onChange(staged);
    setOpen(false);
  };

  const set = (patch: Partial<Schedule>) => setStaged((p) => ({ ...p, ...patch }));

  // Remember whether the Schedule tab meant "at" or "every", so visiting
  // On message / On event and coming back doesn't degrade every → at.
  const lastScheduleKind = useRef<"at" | "every">(
    schedule.kind === "every" ? "every" : "at",
  );
  if (staged.kind === "at" || staged.kind === "every") lastScheduleKind.current = staged.kind;

  const seg = staged.kind === "message" ? "message" : staged.kind === "event" ? "event" : "schedule";
  const valid = staged.kind !== "message" || splitKeywords(staged.channel).length > 0;

  return (
    <div ref={wrapRef} className="relative">
      <Chip
        size="md"
        active={open}
        leading={
          <IconSwap
            state={schedule.kind === "message" ? "b" : "a"}
            iconA={<Clock size={ICON.XS} strokeWidth={2} />}
            iconB={<MessageSquare size={ICON.XS} strokeWidth={2} />}
          />
        }
        trailing={<ChevronDown size={ICON.XS} strokeWidth={2} className="opacity-60" />}
        onClick={() => (open ? setOpen(false) : openPopover())}
      >
        <span className="truncate max-w-[210px]">{scheduleLabel(schedule)}</span>
      </Chip>

      {createPortal(
        <AnimatePresence>
          {open && coords && (
            <motion.div
              ref={popoverRef}
              initial={{ opacity: 0, scale: 0.97, y: 4 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{
                opacity: 0,
                scale: 0.97,
                y: 2,
                transition: { duration: MOTION.fast, ease: EASE_OUT },
              }}
              transition={SPRING_POPOVER}
              style={{
                position: "fixed",
                bottom: coords.bottom,
                left: coords.left,
                // ABOVE the automations modal (--z-modal) but BELOW the popover
                // tier, so a nested Select's listbox layers above this.
                zIndex: "var(--z-modal-top)",
                transformOrigin: "bottom left",
              }}
              className="surface-panel surface-popover w-[420px]"
            >
              {/* ── one rhythm: every row h-8, every gap 12px ── */}
              <div className="grid gap-3 p-3 pb-0">
                <Tabs
                  variant="segmented"
                  size="sm"
                  value={seg}
                  onChange={(v) =>
                    set({
                      kind:
                        v === "message" ? "message"
                        : v === "event" ? "event"
                        : staged.kind === "at" || staged.kind === "every" ? staged.kind
                        : lastScheduleKind.current,
                    })
                  }
                >
                  <Tab value="schedule">Schedule</Tab>
                  <Tab value="message">On message</Tab>
                  <Tab value="event">On event</Tab>
                </Tabs>

                {/* Panels size to content; the height change GLIDES (smooth-out
                    tween) so the bottom-anchored popover slides the tab row
                    instead of snapping it — no dead space at rest, no jump
                    on switch. */}
                <PanelArea>
                {seg === "schedule" && (
                  <>
                    <div className="flex items-center gap-2 h-8">
                      <span className="text-base text-muted tracking-[-0.005em]">Fires</span>
                      <Select
                        value={staged.kind}
                        onChange={(v) => set({ kind: v as ScheduleKind })}
                        options={[
                          { value: "at", label: "at a time" },
                          { value: "every", label: "every" },
                        ]}
                        aria-label="Schedule mode"
                        className="!h-6 !rounded-[6px] !border-transparent !bg-surface-soft !px-2 !gap-1 !text-sm !font-mono hover:!bg-fill-hover"
                      />
                      <span className="text-muted">·</span>
                      {staged.kind === "at" ? (
                        <>
                          <TokenInput
                            value={staged.at}
                            onChange={(v) => set({ at: v })}
                            ariaLabel="Time"
                          />
                          <HelpTip label="24-hour time (HH:MM) — or just drag the marker on the tape below." />
                        </>
                      ) : (
                        <>
                          <TokenInput
                            value={staged.every}
                            onChange={(v) => set({ every: v })}
                            placeholder="30m"
                            ariaLabel="Interval"
                          />
                          <HelpTip label="How often it fires: a number plus m / h / d — 30m, 2h, 1d. The tape below previews the day." />
                          <RunsPerDay schedule={staged} />
                        </>
                      )}
                    </div>

                    <DayTape schedule={staged} onChangeSchedule={set} />

                    <DayRow days={staged.days} onChange={(days) => set({ days })} />
                  </>
                )}

                {seg === "message" && (
                  <>
                    <div className="flex items-center gap-2 h-8 min-w-0">
                      <span className="text-base text-muted tracking-[-0.005em]">In</span>
                      <TokenInput
                        value={staged.channel}
                        onChange={(v) => set({ channel: v })}
                        placeholder="channel"
                        prefix="#"
                        ariaLabel="Channels"
                      />
                      <HelpTip label="Slack channel names, comma-separated, without the # — e.g. eng-bugs, feel-good-inc. A message in any of them can fire this." />
                    </div>
                    <div className="flex items-center gap-2 h-8 min-w-0">
                      <span className="text-base text-muted tracking-[-0.005em]">from</span>
                      <TokenInput
                        value={staged.fromUser}
                        onChange={(v) => set({ fromUser: v })}
                        placeholder="anyone"
                        prefix="@"
                        ariaLabel="From user"
                      />
                      <span className="text-base text-muted tracking-[-0.005em]">matching</span>
                      <TokenInput
                        value={staged.keywords}
                        onChange={(v) => set({ keywords: v })}
                        placeholder="anything"
                        ariaLabel="Keywords"
                      />
                      <HelpTip label="Both are optional gates. From limits it to one sender — set it if Auto-Approve is on, or anyone in the channel can drive an unattended run. Matching fires only when a message contains any of the comma-separated keywords." />
                    </div>
                  </>
                )}

                {seg === "event" && (
                  <div className="flex items-center gap-2 h-8">
                    <span className="text-base text-muted tracking-[-0.005em] whitespace-nowrap">Calendar event</span>
                    <Select
                      value={staged.event}
                      onChange={(v) => set({ event: v as EventType })}
                      options={[
                        { value: "starts", label: "starts" },
                        { value: "ends", label: "ends" },
                        { value: "approaching", label: "is approaching" },
                      ]}
                      aria-label="Event"
                      className="!h-6 !rounded-[6px] !border-transparent !bg-surface-soft !px-2 !gap-1 !text-sm !font-mono hover:!bg-fill-hover"
                    />
                    {staged.event === "approaching" && (
                      <>
                        <span className="text-muted">·</span>
                        <TokenInput
                          value={staged.lead}
                          onChange={(v) => set({ lead: v })}
                          placeholder="15"
                          ariaLabel="Lead minutes"
                        />
                        <span className="text-base text-muted tracking-[-0.005em] whitespace-nowrap">min before</span>
                      </>
                    )}
                  </div>
                )}
                </PanelArea>
              </div>

              {/* receipt + explicit commit — the popover's tonal footer */}
              <div className="flex items-center gap-2 mt-3 px-3 py-2.5 bg-surface-soft/40 rounded-b-[inherit]">
                <div
                  className="flex-1 min-w-0 flex items-center gap-1.5 text-xs text-muted"
                  title={scheduleLabel(staged)}
                >
                  <IconSwap
                    state={staged.kind === "message" ? "b" : "a"}
                    iconA={<CalendarClock size={ICON.XS} strokeWidth={2} className="shrink-0 text-faint" />}
                    iconB={<MessageSquare size={ICON.XS} strokeWidth={2} className="shrink-0 text-faint" />}
                  />
                  <span className="truncate">{scheduleLabel(staged)}</span>
                </div>
                <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
                  Cancel
                </Button>
                <Button variant="primary" size="sm" disabled={!valid} onClick={save}>
                  Save
                </Button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>,
        document.querySelector("#app") ?? document.body,
      )}
    </div>
  );
}

// ─── Atoms ──────────────────────────────────────────────────────────

/** Measured-height shell for the tab panels: the inner content renders at
 *  natural size, the outer box tweens to its measured height — the tab row
 *  above glides instead of jumping when panels differ. */
function PanelArea({ children }: { children: React.ReactNode }) {
  const innerRef = useRef<HTMLDivElement>(null);
  const reduced = useReducedMotion();
  const [h, setH] = useState<number | null>(null);
  useEffect(() => {
    const el = innerRef.current;
    if (!el) return;
    const measure = () => setH(el.offsetHeight);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return (
    <motion.div
      animate={h != null ? { height: h } : undefined}
      transition={{ duration: reduced ? 0 : 0.25, ease: [0.22, 1, 0.36, 1] }}
      className="overflow-hidden"
      style={h == null ? { height: "auto" } : undefined}
    >
      <div ref={innerRef} className="grid gap-3 content-start">
        {children}
      </div>
    </motion.div>
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
    <span className="inline-flex items-center h-6 px-2 rounded-[6px] bg-surface-soft transition-[background-color,box-shadow] duration-check focus-within:[box-shadow:var(--focus-ring)] hover:[&:not(:focus-within)]:bg-fill-hover">
      {prefix && <span className="text-faint font-mono text-sm">{prefix}</span>}
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        spellCheck={false}
        aria-label={ariaLabel}
        className="field-sizing-content min-w-[3ch] max-w-[190px] bg-transparent border-0 outline-none font-mono tabular-nums text-sm text-ink placeholder:text-faint"
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
        className="grid place-items-center w-4 h-4 shrink-0 rounded-full bg-surface-soft font-mono text-[10px] text-faint cursor-help transition-colors hover:text-ink hover:bg-fill-hover"
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
    <span className="ml-auto font-mono tabular-nums text-2xs text-faint whitespace-nowrap">
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
    <div className="flex items-center gap-1 h-8">
      {DAY_LETTERS.map((l, i) => (
        <button
          key={i}
          type="button"
          onClick={() => toggle(i)}
          aria-pressed={set.has(i)}
          aria-label={DAY_NAMES[i]}
          className={clsx(
            "grid place-items-center w-6 h-6 rounded-[6px] font-mono text-xs transition-colors duration-check",
            set.has(i) ? "bg-ink text-on-ink" : "text-faint hover:bg-fill-hover hover:text-ink",
          )}
        >
          {l}
        </button>
      ))}
      <span className="ml-auto inline-flex gap-0.5">
        <button
          type="button"
          onClick={() => onChange("daily")}
          className={clsx(
            "h-6 px-2 rounded-[6px] text-xs transition-colors duration-check",
            isDaily ? "text-ink bg-surface-soft" : "text-faint hover:bg-fill-hover hover:text-ink",
          )}
        >
          Daily
        </button>
        <button
          type="button"
          onClick={() => onChange("weekdays")}
          className={clsx(
            "h-6 px-2 rounded-[6px] text-xs transition-colors duration-check",
            isWeekdays ? "text-ink bg-surface-soft" : "text-faint hover:bg-fill-hover hover:text-ink",
          )}
        >
          Weekdays
        </button>
      </span>
    </div>
  );
}

// ─── The instrument: a 24h day tape ─────────────────────────────────
// For "at": one ink marker — drag it (5-min snap) to set the time.
// For "every": the firings are PREVIEWED as ticks and the active window is
// a band with draggable edge handles (15-min snap). Direct manipulation on
// the same ruler language as the run history above.

const TAPE_H = 64;
const TAPE_BASE = 36;

function DayTape({
  schedule,
  onChangeSchedule,
}: {
  schedule: Schedule;
  onChangeSchedule: (patch: Partial<Schedule>) => void;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const drag = useRef<"at" | "a" | "b" | null>(null);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const X = (min: number) => (min / DAY_MIN) * width;
  const atMin = hhmmToMin(schedule.at) ?? 540;
  const winA = hhmmToMin(schedule.start) ?? 0;
  const winB = hhmmToMin(schedule.end) ?? DAY_MIN;

  const minAt = (e: React.PointerEvent) => {
    const r = wrapRef.current?.getBoundingClientRect();
    if (!r) return 0;
    return Math.max(0, Math.min(DAY_MIN, ((e.clientX - r.left) / r.width) * DAY_MIN));
  };

  const applyWindow = (edge: "a" | "b", m: number) => {
    const v = Math.round(m / 15) * 15;
    if (edge === "a") {
      const lo = Math.min(v, winB - 15);
      onChangeSchedule({ start: lo <= 0 ? "" : minToHhmm(lo) });
    } else {
      const hi = Math.max(v, winA + 15);
      onChangeSchedule({ end: hi >= DAY_MIN ? "" : minToHhmm(hi) });
    }
  };

  const onPointerDown = (e: React.PointerEvent) => {
    try {
      wrapRef.current?.setPointerCapture(e.pointerId);
    } catch {
      /* synthetic pointer */
    }
    const m = minAt(e);
    if (schedule.kind === "at") {
      drag.current = "at";
      onChangeSchedule({ at: minToHhmm(Math.round(m / 5) * 5) });
    } else {
      drag.current = Math.abs(m - winA) <= Math.abs(m - winB) ? "a" : "b";
      applyWindow(drag.current, m);
    }
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag.current) return;
    const m = minAt(e);
    if (drag.current === "at") onChangeSchedule({ at: minToHhmm(Math.round(m / 5) * 5) });
    else applyWindow(drag.current, m);
  };
  const endDrag = () => {
    drag.current = null;
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

  return (
    <div
      ref={wrapRef}
      className={clsx(
        "relative h-16 touch-none select-none",
        schedule.kind === "at" ? "cursor-grab" : "cursor-default",
      )}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
    >
      {width > 0 && (
        <svg
          className="absolute inset-0 w-full h-full overflow-visible"
          viewBox={`0 0 ${width} ${TAPE_H}`}
          aria-hidden="true"
        >
          {schedule.kind === "every" && (
            <rect
              x={X(winA)}
              y={TAPE_BASE - 12}
              width={Math.max(0, X(winB) - X(winA))}
              height={24}
              rx={5}
              fill="var(--color-fill-hover)"
            />
          )}
          <line
            x1={0}
            y1={TAPE_BASE}
            x2={width}
            y2={TAPE_BASE}
            stroke="var(--color-whisper)"
            strokeWidth={1}
            strokeDasharray="1 4"
          />
          {[0, 6, 12, 18, 24].map((h) => (
            <g key={h}>
              <line
                x1={X(h * 60)}
                y1={TAPE_BASE - 6}
                x2={X(h * 60)}
                y2={TAPE_BASE + 6}
                stroke="var(--color-line-strong)"
                strokeWidth={1}
              />
              <text
                x={X(h * 60)}
                y={TAPE_BASE + 24}
                textAnchor="middle"
                fontSize={10}
                className="font-mono"
                fill="var(--color-faint)"
              >
                {String(h).padStart(2, "0")}
              </text>
            </g>
          ))}
          {schedule.kind === "at" ? (
            <>
              <rect
                x={X(atMin) - 1.2}
                y={TAPE_BASE - 10}
                width={2.4}
                height={20}
                rx={1.2}
                fill="var(--color-ink)"
              />
              <text
                x={X(atMin)}
                y={TAPE_BASE - 18}
                textAnchor="middle"
                fontSize={10.5}
                className="font-mono"
                fill="var(--color-ink)"
              >
                {minToHhmm(atMin)}
              </text>
              {/* wide invisible grab zone — the hairline mark is the visual,
                  not the hitbox */}
              <rect
                x={X(atMin) - 11}
                y={TAPE_BASE - 16}
                width={22}
                height={32}
                fill="transparent"
                style={{ cursor: "grab" }}
              />
            </>
          ) : (
            <>
              {firings.map((t) => (
                <rect
                  key={t}
                  x={X(t) - 0.8}
                  y={TAPE_BASE - 5}
                  width={1.6}
                  height={10}
                  rx={0.8}
                  fill="var(--color-ink)"
                  opacity={0.55}
                />
              ))}
              <rect x={X(winA) - 1.5} y={TAPE_BASE - 12} width={3} height={24} rx={1.5} fill="var(--color-ink)" />
              <rect x={X(winB) - 1.5} y={TAPE_BASE - 12} width={3} height={24} rx={1.5} fill="var(--color-ink)" />
              <text
                x={X(winA)}
                y={TAPE_BASE - 18}
                textAnchor="middle"
                fontSize={10.5}
                className="font-mono"
                fill="var(--color-ink)"
              >
                {minToHhmm(winA)}
              </text>
              <text
                x={X(winB)}
                y={TAPE_BASE - 18}
                textAnchor="middle"
                fontSize={10.5}
                className="font-mono"
                fill="var(--color-ink)"
              >
                {winB >= DAY_MIN ? "24:00" : minToHhmm(winB)}
              </text>
              <rect
                x={X(winA) - 11}
                y={TAPE_BASE - 16}
                width={22}
                height={32}
                fill="transparent"
                style={{ cursor: "ew-resize" }}
              />
              <rect
                x={X(winB) - 11}
                y={TAPE_BASE - 16}
                width={22}
                height={32}
                fill="transparent"
                style={{ cursor: "ew-resize" }}
              />
            </>
          )}
        </svg>
      )}
    </div>
  );
}
