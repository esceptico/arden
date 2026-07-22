import { useRef, type KeyboardEvent, type PointerEvent } from "react";
import { formatTime12, scheduleLabel, type Schedule } from "@/features/automations/lib/schedule";
import "./AutomationScheduleLoops.css";

const WIDTH = 1138;
const HEIGHT = 986;
const CX = 594;
const CY = 490;
const OUTER_R = 379;
const CADENCE_R = 278;
const TIME_R = 159;

const DAYS = [
  { value: "mon", short: "M", label: "Monday" },
  { value: "tue", short: "T", label: "Tuesday" },
  { value: "wed", short: "W", label: "Wednesday" },
  { value: "thu", short: "T", label: "Thursday" },
  { value: "fri", short: "F", label: "Friday" },
  { value: "sat", short: "S", label: "Saturday" },
  { value: "sun", short: "S", label: "Sunday" },
] as const;

const CADENCES = [
  { value: "15m", label: "15m" },
  { value: "30m", label: "30m" },
  { value: "1h", label: "1h" },
  { value: "2h", label: "2h" },
  { value: "4h", label: "4h" },
  { value: "6h", label: "6h" },
  { value: "12h", label: "12h" },
  { value: "1d", label: "1d" },
] as const;

const DECORATIVE_ARCS = [
  { radius: 401, start: 20, end: 80 },
  { radius: 401, start: 224, end: 300 },
  { radius: 302, start: 292, end: 338 },
  { radius: 302, start: 132, end: 205 },
] as const;

function polar(radius: number, degrees: number): [number, number] {
  const angle = (degrees - 90) * Math.PI / 180;
  return [CX + Math.cos(angle) * radius, CY + Math.sin(angle) * radius];
}

function arcPath(radius: number, start: number, end: number): string {
  const [startX, startY] = polar(radius, end);
  const [endX, endY] = polar(radius, start);
  return `M ${startX} ${startY} A ${radius} ${radius} 0 ${end - start > 180 ? 1 : 0} 0 ${endX} ${endY}`;
}

function parseDays(raw: string): Set<string> {
  const value = raw.trim().toLowerCase();
  if (!value || value === "daily" || value === "*") return new Set(DAYS.map((day) => day.value));
  if (value === "weekdays") return new Set(DAYS.slice(0, 5).map((day) => day.value));
  if (value === "weekends") return new Set(DAYS.slice(5).map((day) => day.value));
  return new Set(value.split(",").map((day) => day.trim()).filter(Boolean));
}

function serializeDays(days: Set<string>): string {
  if (days.size === 7) return "daily";
  if (DAYS.slice(0, 5).every((day) => days.has(day.value)) && days.size === 5) return "weekdays";
  if (DAYS.slice(5).every((day) => days.has(day.value)) && days.size === 2) return "weekends";
  return DAYS.filter((day) => days.has(day.value)).map((day) => day.value).join(",");
}

function periodLabel(days: Set<string>): string {
  if (days.size === 7) return "Daily";
  if (DAYS.slice(0, 5).every((day) => days.has(day.value)) && days.size === 5) return "Weekdays";
  if (DAYS.slice(5).every((day) => days.has(day.value)) && days.size === 2) return "Weekends";
  return DAYS.filter((day) => days.has(day.value)).map((day) => day.short).join(" · ");
}

function hourFromTime(value: string, fallback: number): number {
  const match = /^(\d{1,2}):/.exec(value);
  return match ? Math.min(23, Math.max(0, Number(match[1]))) : fallback;
}

function timeFromHour(hour: number): string {
  return `${String((hour + 24) % 24).padStart(2, "0")}:00`;
}

function pointHour(event: PointerEvent<SVGCircleElement>): number {
  const svg = event.currentTarget.ownerSVGElement;
  if (!svg) return 0;
  const rect = svg.getBoundingClientRect();
  const x = (event.clientX - rect.left) / rect.width * WIDTH;
  const y = (event.clientY - rect.top) / rect.height * HEIGHT;
  const degrees = (Math.atan2(y - CY, x - CX) * 180 / Math.PI + 450) % 360;
  return Math.round(degrees / 15) % 24;
}

function TickRing({ radius, count }: { radius: number; count: number }) {
  return Array.from({ length: count }, (_, index) => {
    const degrees = index * 360 / count;
    const major = index % Math.round(count / 8) === 0;
    const [x1, y1] = polar(radius - (major ? 28 : 12), degrees);
    const [x2, y2] = polar(radius, degrees);
    return <line key={index} className={major ? "automation-radial__tick is-major" : "automation-radial__tick"} x1={x1} y1={y1} x2={x2} y2={y2} />;
  });
}

export function AutomationScheduleLoops({
  value,
  onChange,
}: {
  value: Schedule;
  onChange: (next: Schedule) => void;
}) {
  const dragRef = useRef<"start" | "end" | null>(null);
  const selectedDays = parseDays(value.days);
  const cadenceIndex = Math.max(0, CADENCES.findIndex((cadence) => cadence.value === value.every));
  const cadence = CADENCES[cadenceIndex];
  const startHour = hourFromTime(value.start, 9);
  const endHour = hourFromTime(value.end, 17);
  const startAngle = startHour * 15;
  let endAngle = endHour * 15;
  if (endAngle <= startAngle) endAngle += 360;
  const timeArc = arcPath(TIME_R, startAngle, endAngle);
  const [startX, startY] = polar(TIME_R, startAngle);
  const [endX, endY] = polar(TIME_R, endAngle);

  const toggleDay = (day: string) => {
    const nextDays = new Set(selectedDays);
    if (nextDays.has(day)) {
      if (nextDays.size === 1) return;
      nextDays.delete(day);
    } else {
      nextDays.add(day);
    }
    onChange({ ...value, kind: "every", days: serializeDays(nextDays) });
  };

  const selectCadence = (every: string) => onChange({ ...value, kind: "every", every });

  const setTime = (edge: "start" | "end", hour: number) => {
    onChange({
      ...value,
      kind: "every",
      start: edge === "start" ? timeFromHour(hour) : value.start || timeFromHour(startHour),
      end: edge === "end" ? timeFromHour(hour) : value.end || timeFromHour(endHour),
    });
  };

  const moveTime = (event: PointerEvent<SVGCircleElement>) => {
    if (dragRef.current) setTime(dragRef.current, pointHour(event));
  };

  const timeKey = (edge: "start" | "end", event: KeyboardEvent<SVGCircleElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const current = edge === "start" ? startHour : endHour;
    setTime(edge, current + (event.key === "ArrowRight" ? 1 : -1));
  };

  const canonical: Schedule = {
    ...value,
    kind: "every",
    every: cadence.value,
    days: serializeDays(selectedDays),
    start: timeFromHour(startHour),
    end: timeFromHour(endHour),
  };

  return (
    <section className="automation-radial" aria-label="Automation schedule setup">
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="group" aria-label="Radial automation scheduler">
        <TickRing radius={OUTER_R} count={184} />
        <TickRing radius={CADENCE_R} count={152} />
        {DECORATIVE_ARCS.map(({ radius, start, end }) => (
          <path key={`${radius}-${start}`} className="automation-radial__decorative-arc" d={arcPath(radius, start, end)} />
        ))}

        <text className="automation-radial__ring-title" x="594" y="56" textAnchor="middle">ACTIVE DAYS</text>
        {DAYS.map((day, index) => {
          const angle = index * 360 / DAYS.length;
          const [x, y] = polar(344, angle);
          const active = selectedDays.has(day.value);
          return (
            <g
              key={day.value}
              className={active ? "automation-radial__day is-active" : "automation-radial__day"}
              role="checkbox"
              aria-label={day.label}
              aria-checked={active}
              tabIndex={0}
              onClick={() => toggleDay(day.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  toggleDay(day.value);
                }
              }}
            >
              <circle className="automation-radial__option-hit" cx={x} cy={y} r="29" />
              <text x={x} y={y + 4} textAnchor="middle">{day.short}</text>
            </g>
          );
        })}

        <text className="automation-radial__ring-title" x="594" y="170" textAnchor="middle">CADENCE</text>
        {CADENCES.map((item, index) => {
          const angle = index * 45;
          const [x, y] = polar(234, angle);
          const active = index === cadenceIndex;
          return (
            <g
              key={item.value}
              className={active ? "automation-radial__cadence is-active" : "automation-radial__cadence"}
              role="radio"
              aria-label={`Every ${item.label}`}
              aria-checked={active}
              tabIndex={0}
              onClick={() => selectCadence(item.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  selectCadence(item.value);
                }
              }}
            >
              <circle className="automation-radial__option-hit" cx={x} cy={y} r="32" />
              <text x={x} y={y + 4} textAnchor="middle">{item.label}</text>
            </g>
          );
        })}

        <circle className="automation-radial__time-rule" cx={CX} cy={CY} r={TIME_R} />
        <path className="automation-radial__time-arc" d={timeArc} />
        <text className="automation-radial__summary-kicker" x={CX} y={CY - 31} textAnchor="middle">EVERY</text>
        <text className="automation-radial__summary-value" x={CX} y={CY + 2} textAnchor="middle">{cadence.label}</text>
        <text className="automation-radial__summary-detail" x={CX} y={CY + 28} textAnchor="middle">{periodLabel(selectedDays)}</text>
        <text className="automation-radial__summary-time" x={CX} y={CY + 51} textAnchor="middle">
          {formatTime12(timeFromHour(startHour))}–{formatTime12(timeFromHour(endHour))}
        </text>

        <circle
          className="automation-radial__time-handle"
          cx={startX}
          cy={startY}
          r="8"
          role="slider"
          tabIndex={0}
          aria-label="Window start"
          aria-valuemin={0}
          aria-valuemax={23}
          aria-valuenow={startHour}
          onPointerDown={(event) => {
            dragRef.current = "start";
            event.currentTarget.setPointerCapture(event.pointerId);
          }}
          onPointerMove={moveTime}
          onPointerUp={() => { dragRef.current = null; }}
          onPointerCancel={() => { dragRef.current = null; }}
          onKeyDown={(event) => timeKey("start", event)}
        />
        <circle
          className="automation-radial__time-handle"
          cx={endX}
          cy={endY}
          r="8"
          role="slider"
          tabIndex={0}
          aria-label="Window end"
          aria-valuemin={0}
          aria-valuemax={23}
          aria-valuenow={endHour}
          onPointerDown={(event) => {
            dragRef.current = "end";
            event.currentTarget.setPointerCapture(event.pointerId);
          }}
          onPointerMove={moveTime}
          onPointerUp={() => { dragRef.current = null; }}
          onPointerCancel={() => { dragRef.current = null; }}
          onKeyDown={(event) => timeKey("end", event)}
        />
      </svg>

      <output className="automation-radial__receipt">
        <span>Schedule</span>
        <strong>{scheduleLabel(canonical)}</strong>
      </output>
    </section>
  );
}
