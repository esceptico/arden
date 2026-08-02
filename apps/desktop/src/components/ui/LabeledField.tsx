import type { ReactNode } from "react";

/** Wraps a caller-provided control with a label. Use when the control isn't a
 *  plain text input (a select, a toggle group, a custom editor) — shared app-wide
 *  so it lives in components/ui, not a feature. */
export function LabeledField({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div role="group" aria-label={label} className="grid gap-1.5">
      <span className="text-sm font-medium tracking-[var(--tracking-tight)] text-ink-soft">{label}</span>
      {children}
    </div>
  );
}
