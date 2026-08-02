import {
  forwardRef,
  useId,
  type InputHTMLAttributes,
  type ReactNode,
} from "react";
import clsx from "clsx";

/**
 * Text input primitive. Wraps the shared `.arden-field` material (single
 * source of truth for input styling — transparent fill, line border,
 * accent focus ring) with the recurring label / help / error scaffold so
 * forms stop re-inlining the same `<label>` + `<input className="...">` +
 * error markup. Works bare (just `<Input />`) or labelled.
 */
interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "size"> {
  label?: ReactNode;
  /** Sub-label hint shown under the label. */
  help?: ReactNode;
  /** Error message; also flips the field to the destructive border. */
  error?: ReactNode;
  /** md = h-8 (default), sm = h-7 (dense grids). */
  size?: "sm" | "md";
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { label, help, error, size = "md", id, className, "aria-describedby": describedBy, ...rest },
  ref,
) {
  const autoId = useId();
  const inputId = id ?? autoId;
  const msgId = error || help ? `${inputId}-msg` : undefined;

  const field = (
    <input
      ref={ref}
      id={inputId}
      aria-invalid={error ? true : undefined}
      aria-describedby={[describedBy, msgId].filter(Boolean).join(" ") || undefined}
      className={clsx(
        "arden-field",
        className,
      )}
      data-size={size}
      {...rest}
    />
  );

  if (!label && !help && !error) return field;

  return (
    <div className="grid gap-1.5">
      {label && (
        <label htmlFor={inputId} className="text-sm font-medium tracking-[var(--tracking-tight)] text-ink-soft">
          {label}
        </label>
      )}
      {help && !error && (
        <span id={msgId} className="text-xs leading-[var(--leading-body)] text-faint">
          {help}
        </span>
      )}
      {field}
      {error && (
        <span id={msgId} className="text-xs leading-[var(--leading-body)] text-bad">
          {error}
        </span>
      )}
    </div>
  );
});
