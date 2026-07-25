import clsx from "clsx";
import type { ComponentPropsWithoutRef } from "react";

export interface CodeWellProps extends ComponentPropsWithoutRef<"pre"> {
  padded?: boolean;
}

/** Shared raw-data surface for tool payloads, previews, and diffs. */
export function CodeWell({
  padded = true,
  className,
  ...props
}: CodeWellProps) {
  return (
    <pre
      {...props}
      className={clsx(
        "arden-code-well",
        padded && "arden-code-well--padded",
        className,
      )}
    />
  );
}
