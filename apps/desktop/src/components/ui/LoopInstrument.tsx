import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent,
} from "react";
import "./LoopInstrument.css";

export type LoopInstrumentMode = "cycle" | "timeline";
export type LoopValues = readonly [number, number, number];

export interface LoopChangeDetail {
  ring: number;
  label: string;
  value: number;
}

export interface LoopInstrumentProps {
  mode?: LoopInstrumentMode;
  value?: LoopValues;
  defaultValue?: LoopValues;
  onChange?: (value: LoopValues, detail: LoopChangeDetail) => void;
  ringLabels?: readonly [string, string, string];
  stepValues?: LoopValues;
  formatRingValue?: (ring: number, value: number) => string;
  showReadout?: boolean;
  detented?: boolean;
  onRingEngage?: (ring: number) => void;
  className?: string;
  ariaLabel?: string;
}

interface RingDefinition {
  radius: number;
  ticks: number;
  majorEvery: number;
  minorLength: number;
  majorLength: number;
  dotted?: boolean;
}

const VIEWBOX_WIDTH = 1138;
const VIEWBOX_HEIGHT = 986;
const CENTER_X = 594;
const CENTER_Y = 490;
const DEFAULT_VALUES: LoopValues = [0.28, 0.64, 0.82];
const RINGS: readonly RingDefinition[] = [
  { radius: 379, ticks: 184, majorEvery: 23, minorLength: 12, majorLength: 39 },
  { radius: 278, ticks: 152, majorEvery: 19, minorLength: 11, majorLength: 28 },
  { radius: 159, ticks: 0, majorEvery: 1, minorLength: 0, majorLength: 0, dotted: true },
];

const STATIC_ARCS = [
  { radius: 401, start: 20, end: 80 },
  { radius: 401, start: 224, end: 300 },
  { radius: 302, start: 292, end: 338 },
  { radius: 302, start: 132, end: 205 },
] as const;

const MARKERS = [
  { ring: 0, radius: 309, angle: 20 },
  { ring: 0, radius: 317, angle: 220 },
  { ring: 1, radius: 231, angle: 290 },
  { ring: 1, radius: 231, angle: 130 },
] as const;

const CYCLE_LABELS = ["Plan", "Build", "Review"] as const;
const TIME_LABELS = ["Weeks", "Days", "Hours"] as const;
const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export function angleDelta(next: number, previous: number): number {
  let delta = next - previous;
  if (delta > 180) delta -= 360;
  if (delta < -180) delta += 360;
  return delta;
}

export function valueFromAngle(angle: number, mode: LoopInstrumentMode): number {
  if (mode === "cycle") return Math.min(1, Math.max(0, angle / 360));
  return ((angle % 360) + 360) % 360 / 360;
}

function prefersReducedMotion(): boolean {
  return typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function labelsFor(mode: LoopInstrumentMode) {
  return mode === "cycle" ? CYCLE_LABELS : TIME_LABELS;
}

function stepFor(mode: LoopInstrumentMode, ring: number): number {
  if (mode === "cycle") return 0.05;
  return [1 / 12, 1 / 7, 1 / 24][ring];
}

function formatValue(mode: LoopInstrumentMode, ring: number, value: number): string {
  if (mode === "cycle") return `${Math.round(value * 100)}%`;
  if (ring === 0) return `Week ${Math.floor(value * 12) + 1}`;
  if (ring === 1) return WEEKDAYS[Math.min(6, Math.floor(value * 7))];
  return `${String(Math.floor(value * 24)).padStart(2, "0")}:00`;
}

function polar(radius: number, degrees: number): [number, number] {
  const angle = (degrees - 90) * Math.PI / 180;
  return [CENTER_X + Math.cos(angle) * radius, CENTER_Y + Math.sin(angle) * radius];
}

function arcPath(radius: number, start: number, end: number): string {
  const [startX, startY] = polar(radius, end);
  const [endX, endY] = polar(radius, start);
  return `M ${startX} ${startY} A ${radius} ${radius} 0 ${end - start <= 180 ? 0 : 1} 0 ${endX} ${endY}`;
}

function pointAngle(event: PointerEvent<SVGCircleElement>): number {
  const rect = event.currentTarget.ownerSVGElement?.getBoundingClientRect();
  if (!rect) return 0;
  const x = (event.clientX - rect.left) / rect.width * VIEWBOX_WIDTH;
  const y = (event.clientY - rect.top) / rect.height * VIEWBOX_HEIGHT;
  return Math.atan2(y - CENTER_Y, x - CENTER_X) * 180 / Math.PI;
}

function RingTicks({ definition }: { definition: RingDefinition }) {
  return Array.from({ length: definition.ticks }, (_, index) => {
    const degrees = index * 360 / definition.ticks;
    const major = index % definition.majorEvery === 0;
    const length = major ? definition.majorLength : definition.minorLength;
    const [x1, y1] = polar(definition.radius - length, degrees);
    const [x2, y2] = polar(definition.radius, degrees);
    return (
      <line
        key={index}
        className={major ? "loop-instrument__tick loop-instrument__tick--major" : "loop-instrument__tick"}
        x1={x1}
        y1={y1}
        x2={x2}
        y2={y2}
      />
    );
  });
}

export function LoopInstrument({
  mode = "cycle",
  value,
  defaultValue = DEFAULT_VALUES,
  onChange,
  ringLabels,
  stepValues,
  formatRingValue,
  showReadout = true,
  detented = false,
  onRingEngage,
  className = "",
  ariaLabel = "Interactive loop instrument",
}: LoopInstrumentProps) {
  const [internalValue, setInternalValue] = useState<LoopValues>(defaultValue);
  const [activeRing, setActiveRing] = useState(1);
  const [engagedRing, setEngagedRing] = useState<number | null>(null);
  const actualValue = value ?? internalValue;
  const valuesRef = useRef<LoopValues>(actualValue);
  const rootRef = useRef<HTMLDivElement>(null);
  const frameRef = useRef<number | null>(null);
  const hoverFrameRef = useRef<number | null>(null);
  const dragRef = useRef<{
    ring: number;
    pointerId: number;
    angle: number;
    lastPointerAngle: number;
    lastTime: number;
    velocity: number;
  } | null>(null);

  valuesRef.current = actualValue;
  const labels = ringLabels ?? labelsFor(mode);
  const displayValue = (ring: number, nextValue: number) =>
    formatRingValue?.(ring, nextValue) ?? formatValue(mode, ring, nextValue);
  const engageRing = (ring: number) => {
    setEngagedRing(ring);
    onRingEngage?.(ring);
  };

  const publish = (ring: number, angle: number) => {
    const nextValue = valueFromAngle(angle, mode);
    const next = [...valuesRef.current] as [number, number, number];
    next[ring] = nextValue;
    valuesRef.current = next;
    if (value === undefined) setInternalValue(next);
    onChange?.(next, { ring, label: labels[ring], value: nextValue });
  };

  const stopAnimation = () => {
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    frameRef.current = null;
  };

  useEffect(() => stopAnimation, []);

  const settle = (ring: number, initialAngle: number, initialVelocity: number) => {
    stopAnimation();
    const step = (stepValues?.[ring] ?? stepFor(mode, ring)) * 360;
    if (prefersReducedMotion()) {
      publish(ring, Math.round(initialAngle / step) * step);
      return;
    }

    let angle = initialAngle;
    let velocity = initialVelocity;
    let previous = performance.now();
    let snapping = false;
    let target = angle;

    const frame = (now: number) => {
      const elapsed = Math.min(32, Math.max(1, now - previous));
      previous = now;

      if (!snapping) {
        angle += velocity * elapsed;
        if (mode === "cycle") {
          if (angle <= 0 || angle >= 360) velocity = 0;
          angle = Math.min(360, Math.max(0, angle));
        }
        velocity *= Math.pow(0.91, elapsed / 16.67);
        publish(ring, angle);
        if (Math.abs(velocity) < 0.018) {
          snapping = true;
          target = Math.round(angle / step) * step;
        }
      } else {
        const delta = target - angle;
        velocity = velocity * 0.68 + delta * 0.045;
        angle += velocity;
        publish(ring, angle);
        if (Math.abs(delta) < 0.04 && Math.abs(velocity) < 0.04) {
          publish(ring, target);
          frameRef.current = null;
          return;
        }
      }
      frameRef.current = requestAnimationFrame(frame);
    };
    frameRef.current = requestAnimationFrame(frame);
  };

  const startDrag = (ring: number, event: PointerEvent<SVGCircleElement>) => {
    stopAnimation();
    event.currentTarget.setPointerCapture(event.pointerId);
    setActiveRing(ring);
    dragRef.current = {
      ring,
      pointerId: event.pointerId,
      angle: valuesRef.current[ring] * 360,
      lastPointerAngle: pointAngle(event),
      lastTime: performance.now(),
      velocity: 0,
    };
    rootRef.current?.setAttribute("data-dragging", "true");
  };

  const moveDrag = (event: PointerEvent<SVGCircleElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const now = performance.now();
    const pointer = pointAngle(event);
    const delta = angleDelta(pointer, drag.lastPointerAngle);
    const elapsed = Math.max(1, now - drag.lastTime);
    drag.angle += delta;
    if (mode === "cycle") drag.angle = Math.min(360, Math.max(0, drag.angle));
    drag.velocity = drag.velocity * 0.45 + (delta / elapsed) * 0.55;
    drag.lastPointerAngle = pointer;
    drag.lastTime = now;
    const step = (stepValues?.[drag.ring] ?? stepFor(mode, drag.ring)) * 360;
    publish(drag.ring, detented ? Math.round(drag.angle / step) * step : drag.angle);
  };

  const endDrag = (event: PointerEvent<SVGCircleElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    dragRef.current = null;
    rootRef.current?.removeAttribute("data-dragging");
    if (detented) {
      const step = (stepValues?.[drag.ring] ?? stepFor(mode, drag.ring)) * 360;
      publish(drag.ring, Math.round(drag.angle / step) * step);
    } else {
      settle(drag.ring, drag.angle, drag.velocity);
    }
  };

  const handleKey = (ring: number, event: KeyboardEvent<SVGCircleElement>) => {
    const step = stepValues?.[ring] ?? stepFor(mode, ring);
    let next: number | null = null;
    if (event.key === "ArrowRight" || event.key === "ArrowUp") next = valuesRef.current[ring] + step;
    if (event.key === "ArrowLeft" || event.key === "ArrowDown") next = valuesRef.current[ring] - step;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = mode === "cycle" ? 1 : 1 - step;
    if (next === null) return;
    event.preventDefault();
    setActiveRing(ring);
    publish(ring, next * 360);
  };

  const updateHover = (event: PointerEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = (event.clientX - rect.left) / rect.width * VIEWBOX_WIDTH;
    const y = (event.clientY - rect.top) / rect.height * VIEWBOX_HEIGHT;
    const degrees = Math.atan2(y - CENTER_Y, x - CENTER_X) * 180 / Math.PI + 90;
    if (hoverFrameRef.current !== null) cancelAnimationFrame(hoverFrameRef.current);
    hoverFrameRef.current = requestAnimationFrame(() => {
      rootRef.current?.style.setProperty("--loop-pointer-angle", `${degrees}deg`);
      hoverFrameRef.current = null;
    });
  };

  const arcPaths = useMemo(
    () => STATIC_ARCS.map(({ radius, start, end }) => arcPath(radius, start, end)),
    [],
  );

  const activeValue = actualValue[activeRing];
  const rootStyle = { "--loop-pointer-angle": "0deg" } as CSSProperties;

  return (
    <div
      ref={rootRef}
      className={`loop-instrument ${className}`.trim()}
      style={rootStyle}
      data-mode={mode}
      data-active-ring={activeRing}
      data-engaged-ring={engagedRing ?? undefined}
      data-detented={detented || undefined}
      onPointerLeave={() => setEngagedRing(null)}
    >
      <svg
        className="loop-instrument__dial"
        viewBox={`0 0 ${VIEWBOX_WIDTH} ${VIEWBOX_HEIGHT}`}
        role="group"
        aria-label={ariaLabel}
        onPointerMove={updateHover}
      >
        <defs>
          <filter id="loop-soft-glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="2.4" result="blur" />
            <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
        </defs>

        {RINGS.map((ring, ringIndex) => (
          <g key={ring.radius}>
            {ring.dotted ? (
              <circle className="loop-instrument__core-rule" cx={CENTER_X} cy={CENTER_Y} r={ring.radius} />
            ) : (
              <g
                className="loop-instrument__rotor"
                data-ring={ringIndex}
                style={{ transform: `rotate(${actualValue[ringIndex] * 360}deg)` }}
              >
                <RingTicks definition={ring} />
                {MARKERS.filter((marker) => marker.ring === ringIndex).map((marker) => (
                  <path
                    key={`${marker.radius}-${marker.angle}`}
                    className="loop-instrument__marker"
                    d={`M ${CENTER_X + 7} ${CENTER_Y - marker.radius} L ${CENTER_X - 7} ${CENTER_Y - marker.radius - 6} L ${CENTER_X - 7} ${CENTER_Y - marker.radius + 6} Z`}
                    transform={`rotate(${marker.angle - DEFAULT_VALUES[ringIndex] * 360} ${CENTER_X} ${CENTER_Y})`}
                  />
                ))}
              </g>
            )}
            <circle
              className="loop-instrument__focus-arc"
              data-ring={ringIndex}
              cx={CENTER_X}
              cy={CENTER_Y}
              r={ring.radius}
              pathLength="100"
              strokeDasharray="7 93"
            />
            <circle
              className="loop-instrument__hit"
              cx={CENTER_X}
              cy={CENTER_Y}
              r={ring.radius}
              role="slider"
              tabIndex={0}
              aria-label={labels[ringIndex]}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(actualValue[ringIndex] * 100)}
              aria-valuetext={displayValue(ringIndex, actualValue[ringIndex])}
              onPointerEnter={() => engageRing(ringIndex)}
              onPointerDown={(event) => {
                engageRing(ringIndex);
                startDrag(ringIndex, event);
              }}
              onPointerMove={moveDrag}
              onPointerUp={endDrag}
              onPointerCancel={endDrag}
              onKeyDown={(event) => handleKey(ringIndex, event)}
              onFocus={() => engageRing(ringIndex)}
              onBlur={() => setEngagedRing(null)}
            />
          </g>
        ))}
        {arcPaths.map((path) => <path key={path} className="loop-instrument__arc" d={path} />)}
        <circle
          className="loop-instrument__core-value"
          cx={CENTER_X}
          cy={CENTER_Y}
          r={RINGS[2].radius}
          pathLength="100"
          strokeDasharray={`${actualValue[2] * 100} 100`}
        />
      </svg>

      {showReadout && (
        <div className="loop-instrument__readout" aria-hidden={engagedRing === null}>
          <span>{engagedRing === null ? "" : labels[engagedRing]}</span>
          <strong>{engagedRing === null ? "" : displayValue(engagedRing, actualValue[engagedRing])}</strong>
          <small>{engagedRing === null ? "" : "drag to adjust"}</small>
        </div>
      )}
      <output className="loop-instrument__sr-readout" aria-live="polite">
        {labels[activeRing]} {displayValue(activeRing, activeValue)}
      </output>
    </div>
  );
}
