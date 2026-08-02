import clsx from "clsx";
import type { ReactNode } from "react";

/** Uppercase 2xs micro-caption — the section label above content blocks
 *  (Task / Result / Activity in chat; Active / Tasks / Workflows in the sidebars).
 *  Distinct from SectionHeader, which is the larger labelled row with
 *  count / detail / action slots. Single source for this caption styling so the
 *  same `text-2xs uppercase` markup stops being re-typed per file.
 *  `tone`: faint (default, on content) or muted (slightly stronger, on sidebars). */
export function Caption({
  children,
  tone = "faint",
  as: Tag = "h3",
  className,
}: {
  children: ReactNode;
  tone?: "faint" | "muted";
  /** h3 for content section labels (default); div for presentational labels
   *  inside a menu/listbox, where a heading would be semantically wrong. */
  as?: "h3" | "div";
  className?: string;
}) {
  return (
    <Tag
      className={clsx(
        "m-0 text-2xs font-medium uppercase tracking-[var(--tracking-caps)]",
        tone === "muted" ? "text-muted" : "text-faint",
        className,
      )}
    >
      {children}
    </Tag>
  );
}
