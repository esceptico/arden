import type { ComponentProps } from "react";
import clsx from "clsx";

/** Inline conversation marker (shadcn/ui Marker recipe on arden tokens):
 *  a quiet one-line icon + text row for status notes in a message stream.
 *  `separator` centers the label between hairlines; `border` underlines
 *  the row. Compose: <Marker><MarkerIcon>…</MarkerIcon><MarkerContent>…
 */
export type MarkerVariant = "default" | "separator" | "border";

export function Marker({
  variant = "default",
  as = "div",
  className,
  ...props
}: Omit<ComponentProps<"button">, "ref"> & { variant?: MarkerVariant; as?: "div" | "button" }) {
  // Widen to a single component type: the div/button prop mismatch (ref,
  // event-handler element types) is irrelevant for what Marker forwards.
  const Tag = as as "button";
  return (
    <Tag
      data-variant={variant}
      className={clsx(
        "group/marker flex min-h-4 w-full items-center gap-2 text-left text-xs text-muted",
        variant === "separator" &&
          "before:mr-1 before:h-px before:min-w-0 before:flex-1 before:bg-line after:ml-1 after:h-px after:min-w-0 after:flex-1 after:bg-line",
        variant === "border" && "border-b border-line pb-2",
        Tag === "button" && "select-none",
        className,
      )}
      {...props}
    />
  );
}

export function MarkerIcon({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      aria-hidden="true"
      className={clsx("grid size-3.5 shrink-0 place-items-center [&_svg]:size-3.5", className)}
      {...props}
    />
  );
}

export function MarkerContent({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      className={clsx(
        "min-w-0 break-words group-data-[variant=separator]/marker:flex-none group-data-[variant=separator]/marker:text-center",
        className,
      )}
      {...props}
    />
  );
}
