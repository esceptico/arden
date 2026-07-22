import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
  type Ref,
} from "react";
import {
  motion,
  useMotionValue,
  useTransform,
  animate,
  useReducedMotion,
  AnimatePresence,
} from "motion/react";
import clsx from "clsx";
import { SPRING_TAP } from "@/lib/tokens/motion";

const THUMB_REST = 16;
const THUMB_PRESS = 20;
const TRACK_HEIGHT = 6;

/**
 * Map a pointer's clientX to a stepped, clamped slider value. Pure so the
 * pointer-drag math is unit-testable without a layout engine: `rect` is the
 * track's bounding box, the thumb travels between the track edges.
 */
export function valueFromPosition(
  clientX: number,
  rect: { left: number; width: number },
  min: number,
  max: number,
  step: number,
): number {
  if (rect.width <= 0 || max <= min) return min;
  const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
  const raw = min + ratio * (max - min);
  const snapped = Math.round((raw - min) / step) * step + min;
  return Math.max(min, Math.min(max, snapped));
}

const clamp = (v: number, min: number, max: number) => Math.max(min, Math.min(max, v));

const MARKER_FADE_DISTANCE = 8;
const MARKER_TEXT_GAP = 2;

/** Fade the narrow marker before it crosses either protected text lane. */
export function sliderMarkerOpacity(
  position: number,
  labelEnd: number,
  valueStart: number,
  fadeDistance = MARKER_FADE_DISTANCE,
): number {
  if (position <= labelEnd || position >= valueStart) return 0;
  if (fadeDistance <= 0) return 1;
  return clamp(Math.min(position - labelEnd, valueStart - position) / fadeDistance, 0, 1);
}

interface SliderProps {
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
  /** Larger jump for PageUp / PageDown. Defaults to 10× step. */
  pageStep?: number;
  /** Render a small value readout next to the track. */
  formatValue?: (value: number) => string;
  className?: string;
  "aria-label"?: string;
  "aria-labelledby"?: string;
  ref?: Ref<HTMLDivElement>;
}

/**
 * Single-value horizontal slider — track, filled range, and a draggable thumb
 * that grows on press. Ported (minimal) from Fluid Functionalism: the fill
 * width and thumb position ride motion values on the hot path (no React state
 * per pointermove), and the grow/spring is gated on reduced motion. Pointer
 * drag, keyboard stepping, and ARIA are hand-rolled (no radix).
 */
export function Slider({
  value,
  onChange,
  min = 0,
  max = 100,
  step = 1,
  disabled = false,
  pageStep,
  formatValue,
  className,
  "aria-label": ariaLabel,
  "aria-labelledby": ariaLabelledby,
  ref,
}: SliderProps) {
  const reduced = !!useReducedMotion();
  const trackRef = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);

  // Hot-path: 0..1 fill fraction drives both the fill width and thumb offset.
  const fraction = useMotionValue(max > min ? clamp((value - min) / (max - min), 0, 1) : 0);
  const fillWidth = useTransform(fraction, (f) => `${f * 100}%`);
  const thumbLeft = useTransform(fraction, (f) => `${f * 100}%`);
  const thumbSize = useMotionValue(THUMB_REST);

  // Keep the motion fraction in sync with controlled value changes (keyboard,
  // programmatic) — but never fight an in-progress drag.
  useLayoutEffect(() => {
    if (dragging.current) return;
    fraction.set(max > min ? clamp((value - min) / (max - min), 0, 1) : 0);
  }, [value, min, max, fraction]);

  const setFromPointer = useCallback(
    (clientX: number) => {
      const rect = trackRef.current?.getBoundingClientRect();
      if (!rect) return;
      const next = valueFromPosition(clientX, rect, min, max, step);
      // Update the fill/thumb immediately (no React state on the hot path),
      // then notify. The controlled value re-render confirms it.
      fraction.set(max > min ? clamp((next - min) / (max - min), 0, 1) : 0);
      if (next !== value) onChange(next);
    },
    [min, max, step, value, onChange, fraction],
  );

  const grow = useCallback(
    (pressed: boolean) => {
      const target = pressed ? THUMB_PRESS : THUMB_REST;
      if (reduced) thumbSize.set(target);
      else animate(thumbSize, target, SPRING_TAP);
    },
    [reduced, thumbSize],
  );

  const onPointerDown = useCallback(
    (e: PointerEvent<HTMLDivElement>) => {
      if (disabled || (e.pointerType === "mouse" && e.button !== 0)) return;
      e.preventDefault();
      dragging.current = true;
      grow(true);
      e.currentTarget.setPointerCapture(e.pointerId);
      setFromPointer(e.clientX);
    },
    [disabled, grow, setFromPointer],
  );

  const onPointerMove = useCallback(
    (e: PointerEvent<HTMLDivElement>) => {
      if (!dragging.current) return;
      setFromPointer(e.clientX);
    },
    [setFromPointer],
  );

  const endDrag = useCallback(() => {
    if (!dragging.current) return;
    dragging.current = false;
    grow(false);
  }, [grow]);

  const onKeyDown = useCallback(
    (e: KeyboardEvent<HTMLDivElement>) => {
      if (disabled) return;
      const big = pageStep ?? step * 10;
      let next: number | null = null;
      switch (e.key) {
        case "ArrowRight":
        case "ArrowUp":
          next = value + step;
          break;
        case "ArrowLeft":
        case "ArrowDown":
          next = value - step;
          break;
        case "PageUp":
          next = value + big;
          break;
        case "PageDown":
          next = value - big;
          break;
        case "Home":
          next = min;
          break;
        case "End":
          next = max;
          break;
        default:
          return;
      }
      e.preventDefault();
      const clamped = clamp(next, min, max);
      if (clamped !== value) onChange(clamped);
    },
    [disabled, pageStep, step, value, min, max, onChange],
  );

  // Press-grow follows keyboard focus too (focus-visible only).
  const [focused, setFocused] = useState(false);
  useEffect(() => {
    if (focused && !dragging.current) grow(true);
    else if (!focused && !dragging.current) grow(false);
  }, [focused, grow]);

  const readout = formatValue ? formatValue(value) : null;

  return (
    <div
      ref={ref}
      className={clsx("flex items-center gap-3 select-none", disabled && "opacity-50", className)}
    >
      <div
        ref={trackRef}
        className="relative h-5 flex-1 cursor-ew-resize touch-none"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
      >
        {/* Track */}
        <div
          className="absolute inset-x-0 top-1/2 -translate-y-1/2 rounded-full bg-surface-soft"
          style={{
            height: TRACK_HEIGHT,
            boxShadow: "inset 0 0 0 1px var(--color-line)",
          }}
        >
          {/* Filled range */}
          <motion.div
            className="absolute inset-y-0 left-0 rounded-full bg-accent"
            style={{ width: fillWidth }}
          />
        </div>

        {/* Thumb — the focusable, ARIA-bearing control */}
        <motion.div
          role="slider"
          tabIndex={disabled ? -1 : 0}
          aria-orientation="horizontal"
          aria-valuemin={min}
          aria-valuemax={max}
          aria-valuenow={value}
          aria-label={ariaLabel}
          aria-labelledby={ariaLabelledby}
          aria-disabled={disabled || undefined}
          onKeyDown={onKeyDown}
          onFocus={(e) => setFocused(e.currentTarget.matches(":focus-visible"))}
          onBlur={() => setFocused(false)}
          className="absolute top-1/2 rounded-full bg-surface-1 shadow-[0_1px_3px_rgba(0,0,0,0.18)] outline-none focus-visible:ring-2 focus-visible:ring-accent"
          style={{
            left: thumbLeft,
            width: thumbSize,
            height: thumbSize,
            x: "-50%",
            y: "-50%",
            border: "1px solid var(--color-line-strong)",
            transition: reduced ? "none" : undefined,
          }}
        />
      </div>

      {readout !== null && (
        <span className="min-w-[3ch] text-right text-[13px] tabular-nums text-ink-soft">
          {readout}
        </span>
      )}
    </div>
  );
}

// ─── Range (two-thumb) ──────────────────────────────────────────────────────

interface RangeSliderProps {
  /** [low, high] — kept ordered (low ≤ high) by the component. */
  value: [number, number];
  onChange: (value: [number, number]) => void;
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
  /** Formats each end for the readout, e.g. minutes → "HH:MM". */
  formatValue?: (value: number) => string;
  className?: string;
  "aria-label"?: string;
  ref?: Ref<HTMLDivElement>;
}

/** Clamp `value` to [low, high], keeping the two ends ordered. */
export function orderRange(
  thumb: 0 | 1,
  next: number,
  [low, high]: [number, number],
  min: number,
  max: number,
): [number, number] {
  const v = clamp(next, min, max);
  return thumb === 0 ? [Math.min(v, high), high] : [low, Math.max(v, low)];
}

/**
 * Two-thumb range slider — same track/thumb/`valueFromPosition` as {@link Slider},
 * with the fill drawn BETWEEN the thumbs. Pointer grabs the nearer thumb; each
 * thumb is its own `role="slider"` (Arrow/Page/Home/End), bounded by the other so
 * they can't cross. Position is controlled (no per-move React-free hot path —
 * a settings range slider isn't dragged continuously like a primary control).
 */
export function RangeSlider({
  value,
  onChange,
  min = 0,
  max = 100,
  step = 1,
  disabled = false,
  formatValue,
  className,
  "aria-label": ariaLabel,
  ref,
}: RangeSliderProps) {
  const trackRef = useRef<HTMLDivElement>(null);
  const dragging = useRef<0 | 1 | null>(null);
  const [low, high] = value;
  const pct = (v: number) => (max > min ? clamp((v - min) / (max - min), 0, 1) * 100 : 0);

  const move = (thumb: 0 | 1, next: number) => {
    const ordered = orderRange(thumb, next, value, min, max);
    if (ordered[0] !== low || ordered[1] !== high) onChange(ordered);
  };

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (disabled || (e.pointerType === "mouse" && e.button !== 0)) return;
    const rect = trackRef.current?.getBoundingClientRect();
    if (!rect) return;
    e.preventDefault();
    const v = valueFromPosition(e.clientX, rect, min, max, step);
    // Grab the nearer thumb; tie goes to the one the pointer is past.
    const thumb: 0 | 1 = Math.abs(v - low) < Math.abs(v - high) || v < low ? 0 : 1;
    dragging.current = thumb;
    e.currentTarget.setPointerCapture(e.pointerId);
    move(thumb, v);
  };

  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    if (dragging.current === null) return;
    const rect = trackRef.current?.getBoundingClientRect();
    if (rect) move(dragging.current, valueFromPosition(e.clientX, rect, min, max, step));
  };

  const endDrag = () => {
    dragging.current = null;
  };

  const onThumbKey = (thumb: 0 | 1) => (e: KeyboardEvent<HTMLDivElement>) => {
    if (disabled) return;
    const cur = thumb === 0 ? low : high;
    const big = step * 10;
    let next: number | null = null;
    switch (e.key) {
      case "ArrowRight":
      case "ArrowUp": next = cur + step; break;
      case "ArrowLeft":
      case "ArrowDown": next = cur - step; break;
      case "PageUp": next = cur + big; break;
      case "PageDown": next = cur - big; break;
      case "Home": next = min; break;
      case "End": next = max; break;
      default: return;
    }
    e.preventDefault();
    move(thumb, next);
  };

  return (
    <div
      ref={ref}
      className={clsx("flex items-center gap-3 select-none", disabled && "opacity-50", className)}
    >
      <div
        ref={trackRef}
        className="relative h-5 flex-1 cursor-ew-resize touch-none"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
      >
        <div
          className="absolute inset-x-0 top-1/2 -translate-y-1/2 rounded-full bg-surface-soft"
          style={{ height: TRACK_HEIGHT, boxShadow: "inset 0 0 0 1px var(--color-line)" }}
        >
          {/* Fill BETWEEN the thumbs. */}
          <div
            className="absolute inset-y-0 rounded-full bg-accent"
            style={{ left: `${pct(low)}%`, right: `${100 - pct(high)}%` }}
          />
        </div>
        {([low, high] as const).map((v, i) => {
          const thumb = i as 0 | 1;
          return (
            <div
              key={thumb}
              role="slider"
              tabIndex={disabled ? -1 : 0}
              aria-orientation="horizontal"
              aria-valuemin={thumb === 0 ? min : low}
              aria-valuemax={thumb === 0 ? high : max}
              aria-valuenow={v}
              aria-valuetext={formatValue ? formatValue(v) : undefined}
              aria-label={ariaLabel ? `${ariaLabel} ${thumb === 0 ? "start" : "end"}` : undefined}
              aria-disabled={disabled || undefined}
              onKeyDown={onThumbKey(thumb)}
              className="absolute top-1/2 rounded-full bg-surface-1 shadow-[0_1px_3px_rgba(0,0,0,0.18)] outline-none focus-visible:ring-2 focus-visible:ring-accent"
              style={{
                left: `${pct(v)}%`,
                width: THUMB_REST,
                height: THUMB_REST,
                transform: "translate(-50%,-50%)",
                border: "1px solid var(--color-line-strong)",
              }}
            />
          );
        })}
      </div>

      {formatValue && (
        <span className="whitespace-nowrap text-[13px] tabular-nums text-ink-soft">
          {formatValue(low)}–{formatValue(high)}
        </span>
      )}
    </div>
  );
}

// ─── Comfortable (pips / scrubber discrete selector) ────────────────────────

const PIP_SIZE = 5;
const PIP_PADDING = 12;
const PIP_CENTER_INSET = PIP_PADDING + PIP_SIZE / 2;
const MARKER_WIDTH = 2;
const SPRING_SLIDER = { type: "spring", stiffness: 640, damping: 42, mass: 0.7 } as const;

/** Exact center shared by a discrete dot, fill edge, and marker. */
export function sliderPipStopCenter(progress: number, width: number): number {
  const p = clamp(progress, 0, 1);
  return PIP_CENTER_INSET + p * Math.max(0, width - PIP_CENTER_INSET * 2);
}

interface SliderComfortableProps {
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  /** `pips` = discrete dots (small ranges); `scrubber` = continuous (large ranges). */
  variant?: "pips" | "scrubber";
  label?: string;
  formatValue?: (v: number) => string;
  disabled?: boolean;
  className?: string;
  "aria-label"?: string;
  ref?: Ref<HTMLDivElement>;
}

/**
 * Fluid Functionalism's "Comfortable" slider — a self-contained `h-8` labelled
 * row for settings panels: inline label + value, a fill that grows from the
 * left, and either evenly-spaced discrete pips (`pips`) or a continuous handle
 * (`scrubber`). Ported faithfully from FF's `SliderComfortable`, with Radix
 * swapped for hand-rolled keyboard + ARIA (ntrp is radix-free). The control
 * carries its own `bg-surface` so the pip-occlusion behind the label/value
 * lines up regardless of the panel it sits on.
 */
export function SliderComfortable({
  value,
  onChange,
  min = 0,
  max = 100,
  step = 1,
  variant = "pips",
  label,
  formatValue = String,
  disabled = false,
  className,
  "aria-label": ariaLabel,
  ref,
}: SliderComfortableProps) {
  const reduced = !!useReducedMotion();
  const fast = useMemo(() => (reduced ? { duration: 0 } : SPRING_SLIDER), [reduced]);
  const containerRef = useRef<HTMLDivElement>(null);
  const labelRef = useRef<HTMLSpanElement>(null);
  const valueRef = useRef<HTMLSpanElement>(null);
  const dragging = useRef(false);
  const handleDragging = useRef(false);
  const [isHovered, setIsHovered] = useState(false);
  const [isPressed, setIsPressed] = useState(false);
  const [isFocused, setIsFocused] = useState(false);
  const [hoverPreview, setHoverPreview] = useState<{
    left: number;
    width: number;
    snappedValue: number;
    cursorX: number;
  } | null>(null);
  const [showHoverTooltip, setShowHoverTooltip] = useState(false);
  const hoverDelayRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Click-to-edit the value for an exact entry (FF's compact-slider pattern) —
  // essential for precise fields the scrubber can't reach (e.g. token counts).
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) requestAnimationFrame(() => inputRef.current?.select());
  }, [editing]);

  // Hover tooltip appears after a 100ms dwell.
  useEffect(() => {
    if (isHovered) {
      hoverDelayRef.current = setTimeout(() => setShowHoverTooltip(true), 100);
    } else {
      if (hoverDelayRef.current) clearTimeout(hoverDelayRef.current);
      setShowHoverTooltip(false);
    }
    return () => {
      if (hoverDelayRef.current) clearTimeout(hoverDelayRef.current);
    };
  }, [isHovered]);

  const pipSteps = useMemo(
    () => Array.from({ length: Math.round((max - min) / step) + 1 }, (_, i) => min + i * step),
    [min, max, step],
  );
  const pipCount = pipSteps.length;

  const fillPercent = useMotionValue(
    max === min ? 0 : Math.max(0, Math.min(1, (value - min) / (max - min))),
  );
  const controlWidth = useMotionValue(0);
  const labelEnd = useMotionValue(0);
  const valueStart = useMotionValue(Number.POSITIVE_INFINITY);
  // Scrubbers need a small offset at min so the handle stays inside the edge.
  // Pips use their exact stop-center geometry and need no correction.
  const zeroTarget = variant === "pips" ? 0 : 17;
  const zeroOffset = useMotionValue(value === min ? zeroTarget : 0);

  // Functional-form transforms auto-track the motion values read via `.get()`.
  // Visible travel stays on compositor-friendly transform/clip-path properties.
  const fillClipStyle = useTransform(() => {
    const p = fillPercent.get();
    return `inset(0 ${100 - p * 100}% 0 0)`;
  });
  const handleTranslateStyle = useTransform(() => {
    const x = fillPercent.get() * controlWidth.get() - 8 + zeroOffset.get();
    return `translateX(${x}px)`;
  });
  // Pips, fill, and marker share one exact stop-center coordinate.
  const pipsFillClipStyle = useTransform(() => {
    const p = fillPercent.get();
    const centerOffset = PIP_CENTER_INSET * (1 - 2 * p);
    return `inset(0 calc(${(1 - p) * 100}% - ${centerOffset}px) 0 0)`;
  });
  const markerTranslateStyle = useTransform(() => {
    const p = fillPercent.get();
    const width = controlWidth.get();
    const x =
      variant === "pips"
        ? sliderPipStopCenter(p, width) - MARKER_WIDTH / 2
        : p * width - 9 + zeroOffset.get();
    return `translateX(${x}px)`;
  });
  const markerOpacityStyle = useTransform(() => {
    const width = controlWidth.get();
    if (width <= 0) return 1;
    const p = fillPercent.get();
    const markerX =
      variant === "pips"
        ? sliderPipStopCenter(p, width) - MARKER_WIDTH / 2
        : p * width - 9 + zeroOffset.get();
    return sliderMarkerOpacity(markerX, labelEnd.get(), valueStart.get());
  });

  const measureProtectedLanes = useCallback(() => {
    const control = containerRef.current;
    if (!control) return;
    const controlRect = control.getBoundingClientRect();
    if (controlRect.width <= 0) return;
    controlWidth.set(controlRect.width);

    const labelRect = labelRef.current?.getBoundingClientRect();
    const valueRect = valueRef.current?.getBoundingClientRect();
    labelEnd.set(labelRect ? labelRect.right - controlRect.left + MARKER_TEXT_GAP : 0);
    valueStart.set(
      valueRect
        ? valueRect.left - controlRect.left - MARKER_TEXT_GAP
        : controlRect.width,
    );
  }, [controlWidth, labelEnd, valueStart]);

  useLayoutEffect(() => {
    measureProtectedLanes();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measureProtectedLanes);
    if (containerRef.current) observer.observe(containerRef.current);
    if (labelRef.current) observer.observe(labelRef.current);
    if (valueRef.current) observer.observe(valueRef.current);
    return () => observer.disconnect();
  }, [editing, label, measureProtectedLanes, value]);

  const computeHoverPreview = useCallback(
    (clientX: number) => {
      const el = containerRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const w = el.clientWidth;
      if (w <= 0 || rect.width <= 0) return;
      const scale = rect.width / el.offsetWidth;
      const borderLeftLayout = (el.offsetWidth - w) / 2;
      const layoutX = (clientX - rect.left) / scale - borderLeftLayout;
      const clamped = Math.max(0, Math.min(w, layoutX));
      let snappedVal: number;
      if (variant === "pips") {
        if (pipCount <= 1) return;
        const index = Math.max(0, Math.min(pipCount - 1, Math.round((clamped / w) * (pipCount - 1))));
        snappedVal = pipSteps[index];
      } else {
        const raw = min + (clamped / w) * (max - min);
        snappedVal = Math.max(min, Math.min(max, Math.round((raw - min) / step) * step + min));
      }
      const snappedPercent = max === min ? 0 : (snappedVal - min) / (max - min);
      const snappedX = snappedPercent * w;
      const currentPercent = fillPercent.get();
      const handleX =
        variant === "pips"
          ? currentPercent * w + (20 - 20 * currentPercent - zeroOffset.get() * 2.5)
          : currentPercent * w;
      const edgeX = snappedVal === min ? 0 : snappedVal === max ? w : snappedX;
      setHoverPreview({
        left: Math.min(handleX, edgeX),
        width: Math.abs(edgeX - handleX),
        snappedValue: snappedVal,
        cursorX: snappedX,
      });
    },
    [variant, pipSteps, pipCount, min, max, step, fillPercent, zeroOffset],
  );

  // Sync fill on programmatic / keyboard value change (never fight a drag).
  useEffect(() => {
    if (dragging.current || handleDragging.current) return;
    const percent = max === min ? 0 : Math.max(0, Math.min(1, (value - min) / (max - min)));
    animate(fillPercent, percent, fast);
    animate(zeroOffset, value === min ? zeroTarget : 0, fast);
  }, [value, min, max, variant, fast, fillPercent, zeroOffset, zeroTarget]);

  const valueFromX = useCallback(
    (clientX: number) => {
      const rect = containerRef.current?.getBoundingClientRect();
      if (!rect) return min;
      const clamped = Math.max(0, Math.min(rect.width, clientX - rect.left));
      if (variant === "pips") {
        if (pipCount <= 1) return min;
        const index = Math.max(0, Math.min(pipCount - 1, Math.round((clamped / rect.width) * (pipCount - 1))));
        return pipSteps[index];
      }
      const raw = min + (clamped / rect.width) * (max - min);
      return Math.max(min, Math.min(max, Math.round((raw - min) / step) * step + min));
    },
    [variant, pipSteps, pipCount, min, max, step],
  );

  const toPercent = (v: number) => (max === min ? 0 : Math.max(0, Math.min(1, (v - min) / (max - min))));

  const onPointerDown = useCallback(
    (e: PointerEvent<HTMLDivElement>) => {
      if (disabled || (e.pointerType === "mouse" && e.button !== 0)) return;
      e.preventDefault();
      dragging.current = true;
      setIsPressed(true);
      const next = valueFromX(e.clientX);
      onChange(next);
      animate(fillPercent, toPercent(next), fast);
      animate(zeroOffset, next === min ? zeroTarget : 0, fast);
      e.currentTarget.setPointerCapture(e.pointerId);
    },
    [disabled, valueFromX, onChange, fillPercent, zeroOffset, zeroTarget, min, max, fast],
  );

  const onPointerMove = useCallback(
    (e: PointerEvent<HTMLDivElement>) => {
      if (!dragging.current) return;
      const next = valueFromX(e.clientX);
      onChange(next);
      // Direct manipulation tracks the pointer exactly; springs are reserved
      // for click/programmatic travel where they clarify the state change.
      fillPercent.jump(toPercent(next));
      zeroOffset.jump(next === min ? zeroTarget : 0);
    },
    [valueFromX, onChange, fillPercent, zeroOffset, zeroTarget, min, max],
  );

  const onPointerUp = useCallback(() => {
    dragging.current = false;
    setIsPressed(false);
    setHoverPreview(null);
  }, []);

  // Scrubber drag handle — tracks the cursor directly (no spring) for tight feel.
  const onResizeDown = useCallback(
    (e: PointerEvent<HTMLDivElement>) => {
      if (disabled || (e.pointerType === "mouse" && e.button !== 0)) return;
      e.preventDefault();
      e.stopPropagation();
      handleDragging.current = true;
      setIsPressed(true);
      const next = valueFromX(e.clientX);
      onChange(next);
      fillPercent.jump(toPercent(next));
      zeroOffset.jump(next === min ? zeroTarget : 0);
      e.currentTarget.setPointerCapture(e.pointerId);
    },
    [disabled, valueFromX, onChange, fillPercent, zeroOffset, zeroTarget, min, max],
  );

  const onResizeMove = useCallback(
    (e: PointerEvent<HTMLDivElement>) => {
      if (!handleDragging.current) return;
      const next = valueFromX(e.clientX);
      onChange(next);
      fillPercent.jump(toPercent(next));
      zeroOffset.jump(next === min ? zeroTarget : 0);
    },
    [valueFromX, onChange, fillPercent, zeroOffset, zeroTarget, min, max],
  );

  const onResizeUp = useCallback(() => {
    handleDragging.current = false;
    setIsPressed(false);
    setHoverPreview(null);
  }, []);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (disabled) return;
    let next: number | null = null;
    switch (e.key) {
      case "ArrowRight":
      case "ArrowUp":
        next = value + step;
        break;
      case "ArrowLeft":
      case "ArrowDown":
        next = value - step;
        break;
      case "PageUp":
        next = value + step * 10;
        break;
      case "PageDown":
        next = value - step * 10;
        break;
      case "Home":
        next = min;
        break;
      case "End":
        next = max;
        break;
      default:
        return;
    }
    e.preventDefault();
    const clamped = Math.max(min, Math.min(max, next));
    if (clamped !== value) {
      // Keyboard changes should feel immediate, not animated.
      fillPercent.jump(toPercent(clamped));
      zeroOffset.jump(clamped === min ? zeroTarget : 0);
      onChange(clamped);
    }
  };

  const isActive = isHovered || isFocused;
  const valueLabel = formatValue(value);
  const valueMinWidth = `${String(formatValue(max)).length}ch`;
  const hoverPreviewClip = hoverPreview
    ? `inset(0 calc(100% - ${hoverPreview.left + hoverPreview.width}px) 0 ${hoverPreview.left}px)`
    : "inset(0 100% 0 0)";
  const lineColor = isFocused
    ? "var(--color-ink)"
    : isHovered
      ? "color-mix(in srgb, var(--color-ink) 50%, transparent)"
      : "color-mix(in srgb, var(--color-ink) 25%, transparent)";
  const textColor = (active: boolean) => (active ? "var(--color-ink)" : "var(--color-muted)");

  const commitEdit = () => {
    const n = parseFloat(draft);
    if (Number.isFinite(n) && draft.trim() !== "") {
      const snapped = Math.round((Math.max(min, Math.min(max, n)) - min) / step) * step + min;
      const clamped = Math.max(min, Math.min(max, snapped));
      if (clamped !== value) onChange(clamped); // skip a redundant save on no-op edits
    }
    setEditing(false);
  };
  const stopEvt = (e: { stopPropagation: () => void }) => e.stopPropagation();
  // Shared editable readout — a clickable value that swaps to a numeric input.
  // pointer-events re-enabled (the text layers are pointer-events-none) and
  // events stopped so editing never triggers a slider drag.
  const valueEl = editing ? (
    <input
      ref={inputRef}
      type="number"
      value={draft}
      min={min}
      max={max}
      step={step}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commitEdit}
      onKeyDown={(e) => {
        stopEvt(e);
        // preventDefault so Enter/Escape commit the inline edit without also
        // implicitly submitting the enclosing settings <form>.
        if (e.key === "Enter") {
          e.preventDefault();
          commitEdit();
        } else if (e.key === "Escape") {
          e.preventDefault();
          setEditing(false);
        }
      }}
      onPointerDown={stopEvt}
      aria-label={label ? `Edit ${label}` : "Edit value"}
      className="bg-transparent text-right tabular-nums text-[13px] text-ink outline-none border-b border-line [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
      style={{ width: `${String(max).length + 1}ch`, pointerEvents: "auto" }}
    />
  ) : (
    <span
      onPointerDown={disabled ? undefined : stopEvt}
      onClick={
        disabled
          ? undefined
          : (e) => {
              stopEvt(e);
              setDraft(String(value));
              setEditing(true);
            }
      }
      className={disabled ? undefined : "cursor-text"}
      // pointer-events:auto re-enables editing inside the text layer, but must
      // NOT defeat the wrapper's pointer-events-none when the slider is disabled.
      style={{ pointerEvents: disabled ? "none" : "auto" }}
      title={disabled ? undefined : "Click to type an exact value"}
    >
      {valueLabel}
    </span>
  );

  return (
    <div
      ref={ref}
      className={clsx("relative w-full touch-none", disabled && "opacity-50 pointer-events-none", className)}
      onPointerEnter={() => {
        if (!disabled) setIsHovered(true);
      }}
      onPointerLeave={() => {
        if (!disabled) {
          setIsHovered(false);
          setHoverPreview(null);
        }
      }}
      onMouseMove={(e) => {
        if (disabled || dragging.current || handleDragging.current) return;
        computeHoverPreview(e.clientX);
      }}
    >
      {/* Extended hit area — 8px beyond each edge */}
      <div
        className="absolute cursor-ew-resize"
        style={{ left: -8, right: -8, top: 0, bottom: 0 }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
      />

      {/* Hover value bubble — outside the overflow-hidden control */}
      <AnimatePresence>
        {hoverPreview && showHoverTooltip && !isPressed && (
          <motion.div
            key="hover-tooltip"
            className="absolute pointer-events-none z-20"
            initial={{ opacity: 0, transform: "translate(-50%, 4px)" }}
            animate={{ opacity: 1, transform: "translate(-50%, 0px)" }}
            exit={{
              opacity: 0,
              transform: "translate(-50%, 4px)",
              transition: { duration: 0.1 },
            }}
            transition={fast}
            style={{ left: hoverPreview.cursorX, top: -30 }}
          >
            <span
              className="rounded-md px-2 py-1 text-[12px] font-medium tabular-nums whitespace-nowrap"
              style={{ backgroundColor: "var(--color-ink)", color: "var(--color-bg-main)" }}
            >
              {formatValue(hoverPreview.snappedValue)}
            </span>
          </motion.div>
        )}
      </AnimatePresence>

      <div
        ref={containerRef}
        role="slider"
        tabIndex={disabled ? -1 : 0}
        aria-orientation="horizontal"
        aria-valuemin={min}
        aria-valuemax={max}
        aria-valuenow={value}
        aria-valuetext={valueLabel}
        aria-label={ariaLabel ?? label}
        aria-disabled={disabled || undefined}
        onKeyDown={onKeyDown}
        onFocus={(e) => {
          if (e.currentTarget.matches(":focus-visible")) setIsFocused(true);
        }}
        onBlur={() => setIsFocused(false)}
        className={clsx(
          "relative w-full h-8 select-none touch-none rounded-md border border-line bg-surface overflow-hidden outline-none focus-visible:border-ink/25",
          variant === "scrubber" ? "flex items-center gap-3 px-4 cursor-ew-resize" : "cursor-ew-resize",
        )}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
      >
        {/* Hover preview band — z-3 */}
        <motion.div
          className="absolute inset-0 pointer-events-none z-[3]"
          initial={false}
          animate={{ opacity: hoverPreview && !isPressed ? 1 : 0 }}
          transition={{ opacity: { duration: 0.15 } }}
          style={{
            clipPath: hoverPreviewClip,
            backgroundColor: "color-mix(in srgb, var(--color-accent) 40%, transparent)",
          }}
        />

        {variant === "pips" && (
          <>
            {/* Only future stops remain visible; the marker owns current state. */}
            <div
              className="absolute inset-0 flex justify-between items-center px-3 pointer-events-none z-[1]"
            >
              {pipSteps.map((pipValue) => (
                <div
                  key={pipValue}
                  className="relative flex items-center justify-center"
                  style={{ width: PIP_SIZE, height: PIP_SIZE }}
                >
                  <div
                    className={clsx(
                      "rounded-full transition-opacity duration-100 motion-reduce:transition-none",
                      pipValue > value ? "opacity-30" : "opacity-0",
                    )}
                    style={{ width: PIP_SIZE, height: PIP_SIZE, backgroundColor: "var(--color-muted)" }}
                  />
                </div>
              ))}
            </div>

            {/* Label + value occlusion — z-2 (paints the control bg over pips behind text) */}
            <div className="absolute inset-0 flex items-center px-2 z-[2] pointer-events-none" aria-hidden>
              {label && (
                <span className="px-2 text-[13px] bg-surface text-transparent select-none">{label}</span>
              )}
              <span
                className="ml-auto px-2 text-[13px] tabular-nums bg-surface text-transparent select-none"
                style={{ minWidth: valueMinWidth }}
              >
                {valueLabel}
              </span>
            </div>

            {/* Fill — z-3 */}
            <motion.div
              className="absolute inset-0 pointer-events-none z-[3]"
              style={{ clipPath: pipsFillClipStyle, backgroundColor: "color-mix(in srgb, var(--color-accent) 18%, transparent)" }}
            />

            {/* Handle line — z-3 */}
            <motion.div
              className="absolute left-0 top-2 bottom-2 pointer-events-none z-[3]"
              style={{ width: MARKER_WIDTH, opacity: markerOpacityStyle, transform: markerTranslateStyle }}
            >
              <motion.div
                className="h-full w-full origin-center rounded-full"
                initial={false}
                animate={{
                  transform: isActive ? "scaleY(1.125)" : "scaleY(1)",
                  backgroundColor: lineColor,
                }}
                transition={fast}
              />
            </motion.div>

            {/* Label + value text — z-4 */}
            <div className="absolute inset-0 flex items-center px-2 z-[4] pointer-events-none">
              {label && (
                <motion.span
                  ref={labelRef}
                  className="px-2 text-[13px]"
                  initial={false}
                  animate={{ color: textColor(isActive) }}
                  transition={fast}
                >
                  {label}
                </motion.span>
              )}
              <motion.span
                ref={valueRef}
                className="ml-auto px-2 text-[13px] tabular-nums"
                initial={false}
                animate={{ color: textColor(isActive) }}
                transition={fast}
                style={{ minWidth: valueMinWidth, textAlign: "right" }}
              >
                {valueEl}
              </motion.span>
            </div>
          </>
        )}

        {variant === "scrubber" && (
          <>
            {/* Fill */}
            <motion.div
              className="absolute inset-0 pointer-events-none"
              style={{ clipPath: fillClipStyle, backgroundColor: "color-mix(in srgb, var(--color-accent) 18%, transparent)" }}
            />
            {/* Handle line */}
            <motion.div
              className="absolute left-0 top-2 bottom-2 pointer-events-none z-10"
              style={{ width: MARKER_WIDTH, opacity: markerOpacityStyle, transform: markerTranslateStyle }}
            >
              <motion.div
                className="h-full w-full origin-center rounded-full"
                initial={false}
                animate={{
                  transform: isActive ? "scaleY(1.125)" : "scaleY(1)",
                  backgroundColor: lineColor,
                }}
                transition={fast}
              />
            </motion.div>
            {label && (
              <motion.span
                ref={labelRef}
                className="shrink-0 text-[13px] z-10"
                initial={false}
                animate={{ color: textColor(isActive) }}
                transition={fast}
              >
                {label}
              </motion.span>
            )}
            <div className="flex-1" />
            <motion.span
              ref={valueRef}
              className="shrink-0 text-[13px] tabular-nums text-right z-10"
              initial={false}
              animate={{ color: textColor(isActive) }}
              transition={fast}
              style={{ minWidth: valueMinWidth }}
            >
              {valueEl}
            </motion.span>
            {/* Resize handle */}
            <motion.div
              className="absolute left-0 top-0 bottom-0 w-2 cursor-ew-resize z-20"
              style={{ transform: handleTranslateStyle }}
              onPointerDown={onResizeDown}
              onPointerMove={onResizeMove}
              onPointerUp={onResizeUp}
            />
          </>
        )}
      </div>
    </div>
  );
}

export default Slider;
