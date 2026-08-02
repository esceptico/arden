import {
  forwardRef,
  useId,
  type ReactNode,
  type TextareaHTMLAttributes,
} from "react";
import clsx from "clsx";

/**
 * Multiline text input primitive — sibling to {@link Input}. Wraps the shared
 * `.arden-field` material (single source of truth for input chrome: shared
 * fill, line border, accent focus ring) plus the optional label / help / error
 * scaffold, so forms stop re-deriving textarea chrome.
 * Unlike Input it does not force `w-full` — callers control width/min-height via
 * `className`, matching how the raw textareas were laid out.
 */
interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: ReactNode;
  /** Sub-label hint shown under the label. */
  help?: ReactNode;
  /** Error message; also flips the field to the destructive border. */
  error?: ReactNode;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { label, help, error, id, className, "aria-describedby": describedBy, ...rest },
  ref,
) {
  const autoId = useId();
  const fieldId = id ?? autoId;
  const msgId = error || help ? `${fieldId}-msg` : undefined;

  const field = (
    <textarea
      ref={ref}
      id={fieldId}
      aria-invalid={error ? true : undefined}
      aria-describedby={[describedBy, msgId].filter(Boolean).join(" ") || undefined}
      className={clsx("arden-field", className)}
      {...rest}
    />
  );

  if (!label && !help && !error) return field;

  return (
    <div className="grid gap-1.5">
      {label && (
        <label htmlFor={fieldId} className="text-sm font-medium tracking-[var(--tracking-tight)] text-ink-soft">
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
