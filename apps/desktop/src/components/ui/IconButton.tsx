import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import clsx from "clsx";
import { Tooltip } from "@/components/ui/Tooltip";

type IconButtonSize = "xs" | "sm" | "md" | "lg";
type IconButtonTone = "muted" | "faint" | "primary" | "secondary";
type IconButtonShape = "square" | "circle";

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode;
  /** xs=22 (tight inline rows), sm=24, md=28 (default — headers/toolbars),
   *  lg=32 (prominent). Pick by role, not pixel. */
  size?: IconButtonSize;
  /** Resting icon color. `muted` (default), quieter `faint`, or solid `primary`
   *  (ink slab — round send/accept buttons). */
  tone?: IconButtonTone;
  /** Both currently render the same corner-profile-aware radius
   *  (`--r-icon`) — kept as a separate axis from `tone` for callers that
   *  want to reason about "circular action button" vs "toolbar icon"
   *  semantically, even though they're visually identical today. */
  shape?: IconButtonShape;
  /** Hover resolves to destructive instead of ink. */
  danger?: boolean;
  /** Force the pressed/engaged look + sets aria-pressed (e.g. the deny-with-
   *  reason toggle, a filter trigger while its menu is open). */
  active?: boolean;
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  function IconButton(
    {
      className,
      size = "md",
      tone = "muted",
      shape = "square",
      danger = false,
      active = false,
      type,
      children,
      title,
      ...rest
    },
    ref,
  ) {
    // `title` becomes an animated Tooltip instead of the OS bubble. The
    // Tooltip only wires aria-describedby while open, so an icon-only button
    // with just a `title` would otherwise have no accessible name — derive
    // one from the title. An explicit aria-label in `rest` still wins.
    const accessibleName =
      (rest["aria-label"] as string | undefined) ??
      (typeof title === "string" ? title : undefined);
    // A button showing nothing but an icon has to say what it does on hover,
    // and it already carries the sentence: its accessible name. Labelling one
    // for screen readers and leaving it mute for everyone else was a split
    // nobody chose — 15 of 46 call sites had drifted to that state. `title`
    // still wins where the hint should read differently from the name
    // ("Capture a screenshot" over "Capture screen").
    const tooltipLabel = title ?? accessibleName;
    const button = (
      <button
        ref={ref}
        type={type ?? "button"}
        aria-label={accessibleName}
        aria-pressed={active || undefined}
        data-active={active ? "true" : undefined}
        data-size={size}
        data-tone={tone}
        data-shape={shape}
        data-danger={danger ? "true" : undefined}
        className={clsx(
          "arden-icon-button",
          tone === "faint" && "text-faint",
          className,
        )}
        {...rest}
      >
        {children}
      </button>
    );
    return tooltipLabel ? <Tooltip label={tooltipLabel}>{button}</Tooltip> : button;
  },
);
