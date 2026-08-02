import { Input } from "@/components/ui/Input";
import { SliderComfortable } from "@/components/ui/Slider";

/** Pips for small discrete ranges (clicking a pip is exact), a scrubber for
 *  large ones — the readable breakpoint is ~16 pips. */
export function sliderVariant(min: number, max: number, step: number): "pips" | "scrubber" {
  return Math.round((max - min) / step) + 1 <= 16 ? "pips" : "scrubber";
}

/** Labelled text/password input for settings forms. Thin convenience wrapper
 *  over {@link Input} with the settings defaults (no spellcheck/autocomplete)
 *  and a string-valued onChange. */
export function Field({
  label,
  value,
  onChange,
  placeholder,
  help,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  help?: string;
  type?: "text" | "password";
}) {
  const helpId = help ? `field-${label.replace(/\s+/g, "-").toLowerCase()}-help` : undefined;
  return (
    <div className="grid gap-1.5">
      <Input
        label={label}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        spellCheck={false}
        autoComplete="off"
        aria-describedby={helpId}
      />
      {help && (
        <span id={helpId} className="text-xs leading-[var(--leading-body)] text-faint">
          {help}
        </span>
      )}
    </div>
  );
}

/** Numeric setting rendered as Fluid Functionalism's "Comfortable" selector —
 *  a self-contained labelled row: discrete pips for small ranges, a continuous
 *  scrubber for large ones. Exact entry three ways: click a pip, step with
 *  keyboard arrows, or click the value to type a precise number. */
export function NumberField({
  label,
  value,
  onChange,
  min,
  max,
  step = 1,
  help,
  suffix,
}: {
  label: string;
  value: number;
  onChange: (next: number) => void;
  min: number;
  max: number;
  step?: number;
  help?: string;
  suffix?: string;
}) {
  const format = suffix ? (v: number) => `${v} ${suffix}` : String;
  return (
    <div className="grid gap-1.5">
      <SliderComfortable
        label={label}
        aria-label={label}
        value={value}
        min={min}
        max={max}
        step={step}
        variant={sliderVariant(min, max, step)}
        formatValue={format}
        onChange={onChange}
      />
      {help && <span className="text-xs leading-[var(--leading-body)] text-faint">{help}</span>}
    </div>
  );
}

/** Edits a 0..1 fraction as an integer percent (0..100). Saves the fraction.
 *  Uses the Comfortable scrubber — percent ranges are too dense for pips. */
export function PercentField({
  label,
  value,
  onChange,
  help,
  min = 0,
  max = 100,
  step = 1,
}: {
  label: string;
  value: number;
  onChange: (next: number) => void;
  help?: string;
  min?: number;
  max?: number;
  step?: number;
}) {
  const percent = Math.round(value * 100);
  return (
    <div className="grid gap-1.5">
      <SliderComfortable
        label={label}
        aria-label={label}
        value={percent}
        min={min}
        max={max}
        step={step}
        variant={sliderVariant(min, max, step)}
        formatValue={(v) => `${v}%`}
        onChange={(n) => onChange(n / 100)}
      />
      {help && <span className="text-xs leading-[var(--leading-body)] text-faint">{help}</span>}
    </div>
  );
}
