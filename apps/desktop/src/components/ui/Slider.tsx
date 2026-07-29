import {
  useCallback,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
  type KeyboardEvent,
  type PointerEvent,
  type Ref,
} from "react";
import clsx from "clsx";
import { textLaneOpacity } from "@/lib/textLane";

const clamp = (value: number, min: number, max: number) =>
  Math.max(min, Math.min(max, value));

/** Fade the marker before it crosses either protected text lane. The rule is
 *  shared with the composer's working strip — same band, same lanes. */
export const sliderMarkerOpacity = textLaneOpacity;

/** Center shared by a discrete dot, fill edge, and marker. */
export function sliderPipStopCenter(
  progress: number,
  width: number,
  edgeInset: number,
): number {
  const ratio = clamp(progress, 0, 1);
  return edgeInset + ratio * Math.max(0, width - edgeInset * 2);
}

/** Pointer input follows the mock's min-anchored step grid. */
export function snapSliderInput(
  raw: number,
  min: number,
  max: number,
  step: number,
): number {
  const safeStep = Number.isFinite(step) && step > 0 ? step : 1;
  const decimals = Math.max(
    (String(min).split(".")[1] ?? "").length,
    (String(safeStep).split(".")[1] ?? "").length,
  );
  const snapped = Math.round((clamp(raw, min, max) - min) / safeStep) * safeStep + min;
  return clamp(Number(snapped.toFixed(decimals)), min, max);
}

interface SliderComfortableProps {
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  /** `pips` is for small discrete ranges; `scrubber` is for dense ranges. */
  variant?: "pips" | "scrubber";
  label?: string;
  formatValue?: (value: number) => string;
  disabled?: boolean;
  className?: string;
  "aria-label"?: string;
  ref?: Ref<HTMLDivElement>;
}

type SliderStyle = CSSProperties & {
  "--slider-fill-x": string;
  "--slider-marker-x": string;
  "--slider-marker-opacity": number;
};

function readCssLength(element: HTMLElement, property: string): number {
  const raw = getComputedStyle(element).getPropertyValue(property).trim();
  const value = Number.parseFloat(raw);
  if (!Number.isFinite(value)) return 0;
  if (raw.endsWith("rem")) {
    const rootSize = Number.parseFloat(getComputedStyle(document.documentElement).fontSize);
    return value * (Number.isFinite(rootSize) ? rootSize : 16);
  }
  if (raw.endsWith("em")) {
    const elementSize = Number.parseFloat(getComputedStyle(element).fontSize);
    return value * (Number.isFinite(elementSize) ? elementSize : 16);
  }
  return value;
}

function assignRef<T>(ref: Ref<T> | undefined, value: T | null) {
  if (typeof ref === "function") ref(value);
  else if (ref) ref.current = value;
}

/**
 * The settings mock's Comfortable slider.
 *
 * A real, invisible range input owns focus, pointer capture, and platform
 * semantics. The shared CSS owns shape/material/motion; this component only
 * measures the label lanes and writes the exact stop coordinates.
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
  const boundedValue = clamp(value, min, max);
  const rootRef = useRef<HTMLDivElement>(null);
  const rangeRef = useRef<HTMLInputElement>(null);
  const labelRef = useRef<HTMLSpanElement>(null);
  const valueRef = useRef<HTMLSpanElement>(null);
  const numberRef = useRef<HTMLInputElement>(null);
  const pipRefs = useRef<Array<HTMLSpanElement | null>>([]);
  const latestValueRef = useRef(boundedValue);
  const latestOnChangeRef = useRef(onChange);
  const latestFormatRef = useRef(formatValue);
  const lastEmittedRef = useRef(boundedValue);
  const pointerActiveRef = useRef(false);
  const pointerStartRef = useRef(0);
  const reconcileFrameRef = useRef<number | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  latestValueRef.current = boundedValue;
  latestOnChangeRef.current = onChange;
  latestFormatRef.current = formatValue;

  const safeStep = Number.isFinite(step) && step > 0 ? step : 1;
  const nativeStep =
    snapSliderInput(boundedValue, min, max, safeStep) === boundedValue
      ? safeStep
      : Math.min(safeStep, 1);
  const pipValues = useMemo(() => {
    const count = Math.max(1, Math.round((max - min) / safeStep) + 1);
    const values = Array.from({ length: count }, (_, index) =>
      clamp(min + index * safeStep, min, max),
    );
    return values.filter((pipValue, index) => index === 0 || pipValue !== values[index - 1]);
  }, [max, min, safeStep]);

  const progress = max === min ? 0 : (boundedValue - min) / (max - min);
  const initialStop = `${progress * 100}%`;
  const initialStyle: SliderStyle = {
    "--slider-fill-x": initialStop,
    "--slider-marker-x": `calc(${initialStop} - var(--range-marker-width) / 2)`,
    "--slider-marker-opacity": 1,
  };

  const setRootRef = useCallback(
    (node: HTMLDivElement | null) => {
      rootRef.current = node;
      assignRef(ref, node);
    },
    [ref],
  );

  const syncGeometry = useCallback(
    (requestedValue: number) => {
      const root = rootRef.current;
      const range = rangeRef.current;
      if (!root || !range) return;

      const next = clamp(requestedValue, min, max);
      const ratio = max === min ? 0 : (next - min) / (max - min);
      const controlRect = root.getBoundingClientRect();
      const edgeInset = variant === "pips" ? readCssLength(root, "--range-edge-inset") : 0;
      const markerWidth = readCssLength(root, "--range-marker-width");
      const textClearance = readCssLength(root, "--range-text-clearance");
      const opacityDistance = readCssLength(root, "--range-opacity-distance");
      const usableWidth = Math.max(0, controlRect.width - edgeInset * 2);
      const stopCenter = edgeInset + ratio * usableWidth;
      const markerLeft = stopCenter - markerWidth / 2;

      range.value = String(next);
      range.setAttribute("aria-valuenow", String(next));
      range.setAttribute("aria-valuetext", latestFormatRef.current(next));
      root.dataset.value = String(next);
      root.style.setProperty("--slider-fill-x", `${stopCenter}px`);
      root.style.setProperty("--slider-marker-x", `${markerLeft}px`);

      const labelRect = labelRef.current?.getBoundingClientRect();
      const valueNode = numberRef.current ?? valueRef.current;
      const valueRect = valueNode?.getBoundingClientRect();
      const labelEnd = labelRect
        ? labelRect.right - controlRect.left + textClearance
        : 0;
      const valueStart = valueRect
        ? valueRect.left - controlRect.left - textClearance
        : controlRect.width;
      const markerOpacity = controlRect.width
        ? sliderMarkerOpacity(markerLeft, labelEnd, valueStart, opacityDistance)
        : 1;
      root.style.setProperty("--slider-marker-opacity", String(markerOpacity));

      if (variant === "pips") {
        const active = Math.round((next - min) / safeStep);
        pipRefs.current.forEach((pip, index) => {
          if (!pip) return;
          const pipRatio = pipValues.length === 1 ? 0 : index / (pipValues.length - 1);
          const pipX = controlRect.left + edgeInset + pipRatio * usableWidth;
          const clearOfText =
            (!labelRect || pipX > labelRect.right + textClearance) &&
            (!valueRect || pipX < valueRect.left - textClearance);
          pip.dataset.visible = String(index > active && clearOfText);
        });
      }
    },
    [max, min, pipValues.length, safeStep, variant],
  );

  const syncInstant = useCallback(
    (next: number) => {
      const root = rootRef.current;
      if (!root) return;
      root.classList.add("is-instant");
      syncGeometry(next);
      void root.offsetWidth;
      root.classList.remove("is-instant");
    },
    [syncGeometry],
  );

  useLayoutEffect(() => {
    lastEmittedRef.current = boundedValue;
    syncGeometry(boundedValue);
  }, [boundedValue, formatValue, syncGeometry]);

  useLayoutEffect(() => {
    syncInstant(latestValueRef.current);
    if (typeof ResizeObserver === "undefined" || !rootRef.current) return;
    const observer = new ResizeObserver(() => syncInstant(latestValueRef.current));
    observer.observe(rootRef.current);
    return () => observer.disconnect();
  }, [syncInstant]);

  useLayoutEffect(() => {
    if (!editing) {
      syncGeometry(latestValueRef.current);
      return;
    }
    const frame = requestAnimationFrame(() => {
      numberRef.current?.select();
      syncInstant(latestValueRef.current);
    });
    return () => cancelAnimationFrame(frame);
  }, [editing, syncGeometry, syncInstant]);

  useLayoutEffect(
    () => () => {
      if (reconcileFrameRef.current !== null) {
        cancelAnimationFrame(reconcileFrameRef.current);
      }
    },
    [],
  );

  const emitValue = useCallback(
    (next: number, instant = false) => {
      if (instant) syncInstant(next);
      else syncGeometry(next);
      if (next === lastEmittedRef.current) return;
      lastEmittedRef.current = next;
      latestOnChangeRef.current(next);
    },
    [syncGeometry, syncInstant],
  );

  const handleInput = useCallback(
    (event: FormEvent<HTMLInputElement>) => {
      const next = snapSliderInput(Number(event.currentTarget.value), min, max, safeStep);
      event.currentTarget.value = String(next);
      emitValue(next);
    },
    [emitValue, max, min, safeStep],
  );

  const handleRangeKeyDown = useCallback(
    (event: KeyboardEvent<HTMLInputElement>) => {
      if (disabled) return;
      const current = Number(rangeRef.current?.value ?? latestValueRef.current);
      const delta = {
        ArrowRight: safeStep,
        ArrowUp: safeStep,
        ArrowLeft: -safeStep,
        ArrowDown: -safeStep,
        PageUp: safeStep * 10,
        PageDown: -safeStep * 10,
      }[event.key];
      let next: number | null = null;
      if (delta !== undefined) next = clamp(current + delta, min, max);
      else if (event.key === "Home") next = min;
      else if (event.key === "End") next = max;
      if (next === null) return;
      event.preventDefault();
      emitValue(next, true);
    },
    [disabled, emitValue, max, min, safeStep],
  );

  const handlePointerDown = useCallback(
    (event: PointerEvent<HTMLInputElement>) => {
      if (disabled || (event.pointerType === "mouse" && event.button !== 0)) return;
      pointerActiveRef.current = true;
      pointerStartRef.current = event.clientX;
      rootRef.current?.classList.remove("is-dragging");
    },
    [disabled],
  );

  const handlePointerMove = useCallback((event: PointerEvent<HTMLInputElement>) => {
    const root = rootRef.current;
    if (!pointerActiveRef.current || !root || root.classList.contains("is-dragging")) return;
    const threshold = readCssLength(root, "--range-drag-threshold");
    if (Math.abs(event.clientX - pointerStartRef.current) > threshold) {
      root.classList.add("is-dragging");
    }
  }, []);

  const finishPointer = useCallback(() => {
    if (!pointerActiveRef.current) return;
    pointerActiveRef.current = false;
    rootRef.current?.classList.remove("is-dragging");
    if (reconcileFrameRef.current !== null) cancelAnimationFrame(reconcileFrameRef.current);
    reconcileFrameRef.current = requestAnimationFrame(() => {
      reconcileFrameRef.current = null;
      syncGeometry(latestValueRef.current);
    });
  }, [syncGeometry]);

  const beginEdit = useCallback(() => {
    if (disabled) return;
    setDraft(String(latestValueRef.current));
    setEditing(true);
  }, [disabled]);

  const finishEdit = useCallback(
    (cancel: boolean) => {
      if (!cancel) {
        const parsed = Number.parseFloat(draft);
        if (Number.isFinite(parsed) && draft.trim()) {
          // Exact entry follows the mock: clamp and integer-round, but do not
          // snap against the pointer grid. Existing values such as 1500, 30,
          // and 8192 are intentionally valid off-grid defaults.
          emitValue(Math.round(clamp(parsed, min, max)));
        }
      }
      setEditing(false);
    },
    [draft, emitValue, max, min],
  );

  const valueLabel = formatValue(boundedValue);

  return (
    <div
      ref={setRootRef}
      className={clsx("arden-slider", disabled && "is-disabled", className)}
      data-variant={variant}
      data-value={boundedValue}
      style={initialStyle}
    >
      {variant === "pips" && (
        <span className="arden-slider__pips" aria-hidden="true">
          {pipValues.map((pipValue, index) => (
            <span
              key={`${pipValue}-${index}`}
              ref={(node) => {
                pipRefs.current[index] = node;
              }}
              className="arden-slider__pip"
              data-visible="false"
            />
          ))}
        </span>
      )}

      <input
        ref={rangeRef}
        className="arden-slider__range"
        type="range"
        role="slider"
        min={min}
        max={max}
        step={nativeStep}
        defaultValue={boundedValue}
        disabled={disabled}
        aria-label={ariaLabel ?? label}
        aria-valuemin={min}
        aria-valuemax={max}
        aria-valuenow={boundedValue}
        aria-valuetext={valueLabel}
        onInput={handleInput}
        onKeyDown={handleRangeKeyDown}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={finishPointer}
        onPointerCancel={finishPointer}
        onLostPointerCapture={finishPointer}
      />

      {label && (
        <span ref={labelRef} className="arden-slider__label">
          {label}
        </span>
      )}

      {editing ? (
        <input
          ref={numberRef}
          className="arden-slider__number"
          type="number"
          value={draft}
          min={min}
          max={max}
          step={safeStep}
          aria-label={label ? `Edit ${label}` : "Edit value"}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={() => finishEdit(false)}
          onKeyDown={(event) => {
            event.stopPropagation();
            if (event.key === "Enter") {
              event.preventDefault();
              finishEdit(false);
            } else if (event.key === "Escape") {
              event.preventDefault();
              finishEdit(true);
            }
          }}
        />
      ) : (
        <span
          ref={valueRef}
          className="arden-slider__value"
          onClick={disabled ? undefined : beginEdit}
        >
          {valueLabel}
        </span>
      )}
    </div>
  );
}
